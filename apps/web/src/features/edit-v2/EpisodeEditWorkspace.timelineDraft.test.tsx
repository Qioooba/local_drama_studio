import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createEpisodeTimelineDraftV2, freezeEpisodeTimelineV2, getEpisodeEditWorkspaceV2 } from "../../generated/api";
import { draftRegistry } from "../drafts/draftRegistry";
import { settleDirtyDrafts } from "../drafts/settleDirtyDrafts";
import { EpisodeEditWorkspace } from "./EpisodeEditWorkspace";

/**
 * FE-07: the dialogue / BGM-SFX / model-voice / subtitle switches are part of
 * the timeline snapshot, participate in dirty, block freezing the previous
 * revision, are initialised from the server revision and are restored by
 * discard. FE-08: "回到开头", the ruler and clip clicks all seek the real media
 * element through one conversion helper. FE-02: the editor registers a dirty
 * owner in the shared draftRegistry.
 */
vi.mock("../../generated/api", () => ({ getEpisodeEditWorkspaceV2: vi.fn(), createEpisodeTimelineDraftV2: vi.fn(), freezeEpisodeTimelineV2: vi.fn() }));
vi.mock("../production/SubtitleRevisionPanel", () => ({ SubtitleRevisionPanel: () => <div>字幕编辑器</div> }));
vi.mock("../timeline-v2/TimelineExportPanel", () => ({ TimelineExportPanel: () => <div>专业导出</div> }));

type RevisionOptions = {
  include_dialogue?: boolean;
  include_music_and_sfx?: boolean;
  include_source_audio?: boolean;
  include_subtitles?: boolean;
};

function workspaceFact(options: RevisionOptions | null = { include_dialogue: true, include_music_and_sfx: true, include_source_audio: false, include_subtitles: true }) {
  return {
    episode_id: "e1", project_id: "p1", episode_code: "EP01", episode_title: "第一集", freshness: "CURRENT", upstream_fingerprint: "a".repeat(64),
    latest_revision: {
      id: "tl-1", revision_no: 1, status: "DRAFT", revision_hash: "b".repeat(64), duration_us: 2_000_000,
      video_count: 2, audio_count: 1, subtitle_revision_id: "sub-1", upstream_fingerprint: "a".repeat(64),
      created_at: "2026-08-26T00:00:00Z", created_by: "local-user", ...(options ?? {}),
    },
    history: [], history_has_more: false,
    video_clips: [
      { shot_id: "s1", shot_code: "S001", media_version_id: "v1", source_name: "one.mp4", source_duration_ms: 1000, start_us: 0, end_us: 1_000_000, source_start_us: 0, transition_in: "CUT", continuity_status: "OK" },
      { shot_id: "s2", shot_code: "S002", media_version_id: "v2", source_name: "two.mp4", source_duration_ms: 1000, start_us: 1_000_000, end_us: 2_000_000, source_start_us: 0, transition_in: "DISSOLVE", continuity_status: "OK" },
    ],
    audio_clips: [{ id: "a1", lane: "BGM", media_version_id: "a1", source_name: "music.wav", start_us: 0, end_us: 2_000_000, gain_db: -4, source_revision: 1, owner_route: "POST_AUDIO" }],
    subtitle: { revision_id: "sub-1", revision_no: 2, cue_count: 4, format: "SRT", content_hash: "c".repeat(64) },
    issues: [], duration_us: 2_000_000, allowed_actions: ["CREATE_DRAFT", "FREEZE_LATEST_DRAFT"],
  };
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><EpisodeEditWorkspace projectId="p1" episodeId="e1" /></MemoryRouter></QueryClientProvider>);
}

const checkboxes = () => ({
  dialogue: screen.getByRole("checkbox", { name: "对白" }) as HTMLInputElement,
  music: screen.getByRole("checkbox", { name: "BGM / SFX" }) as HTMLInputElement,
  source: screen.getByRole("checkbox", { name: "模型原声" }) as HTMLInputElement,
  subtitles: screen.getByRole("checkbox", { name: "字幕" }) as HTMLInputElement,
});

async function mountWithData(options: RevisionOptions | null | undefined = undefined) {
  vi.mocked(getEpisodeEditWorkspaceV2).mockResolvedValue({ workspace: workspaceFact(options) } as never);
  mount();
  await screen.findByRole("heading", { name: "EP01 时间线" });
}

describe("EpisodeEditWorkspace timeline snapshot (FE-07)", () => {
  beforeEach(() => {
    draftRegistry.clear();
    vi.clearAllMocks();
    vi.mocked(createEpisodeTimelineDraftV2).mockResolvedValue({
      timeline: { id: "tl-2", episode_id: "e1", revision_no: 2, status: "DRAFT", revision_hash: "d".repeat(64), outcome: "DRAFT_CREATED", idempotent_replay: false },
    } as never);
  });

  afterEach(() => {
    cleanup();
    draftRegistry.clear();
  });

  it.each([
    ["对白", "dialogue"],
    ["BGM / SFX", "music"],
    ["模型原声", "source"],
    ["字幕", "subtitles"],
  ] as const)("counts the %s switch as an unsaved change", async (label, key) => {
    await mountWithData();
    const box = screen.getByRole("checkbox", { name: label }) as HTMLInputElement;
    const original = box.checked;
    expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(false);

    fireEvent.click(box);

    expect((checkboxes()[key] as HTMLInputElement).checked).toBe(!original);
    expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(true);
    expect((screen.getByRole("button", { name: "放弃调整" }) as HTMLButtonElement).disabled).toBe(false);
    // Freezing must not lock the previously saved revision.
    expect((screen.getByRole("button", { name: "冻结当前草稿" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/冻结会锁定音轨与字幕开关/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "放弃调整" }));
    expect((checkboxes()[key] as HTMLInputElement).checked).toBe(original);
    expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(false);
    expect((screen.getByRole("button", { name: "冻结当前草稿" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("initialises the switches from the server revision instead of hardcoded defaults", async () => {
    await mountWithData({ include_dialogue: false, include_music_and_sfx: true, include_source_audio: true, include_subtitles: false });
    const boxes = checkboxes();
    expect(boxes.dialogue.checked).toBe(false);
    expect(boxes.music.checked).toBe(true);
    expect(boxes.source.checked).toBe(true);
    expect(boxes.subtitles.checked).toBe(false);
    expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(false);
    expect(screen.queryByText(/没有返回对白/)).toBeNull();
  });

  it("treats a server revision without switch fields as unknown, not as a silent default", async () => {
    await mountWithData(null);
    // Display falls back to the documented backend defaults...
    expect(checkboxes().dialogue.checked).toBe(true);
    expect(checkboxes().source.checked).toBe(false);
    // ...but the unknown state is stated explicitly and the editor is clean.
    expect(screen.getByText(/没有返回对白/)).toBeTruthy();
    expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(false);

    fireEvent.click(checkboxes().source);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "保存新草稿" }));
    });
    await waitFor(() => expect(createEpisodeTimelineDraftV2).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(createEpisodeTimelineDraftV2).mock.calls[0][1] as Record<string, unknown>;
    expect(payload.include_source_audio).toBe(true);
    // Unknown fields stay unspecified so the server keeps authority.
    expect(payload.include_dialogue).toBeUndefined();
    expect(payload.include_subtitles).toBeUndefined();
  });

  it("saves the switched options and only then allows freezing", async () => {
    await mountWithData();
    fireEvent.click(checkboxes().dialogue);
    expect((screen.getByRole("button", { name: "冻结当前草稿" }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "保存新草稿" }));
    });
    await waitFor(() => expect(createEpisodeTimelineDraftV2).toHaveBeenCalledTimes(1));
    expect(createEpisodeTimelineDraftV2).toHaveBeenCalledWith("e1", expect.objectContaining({
      include_dialogue: false,
      include_music_and_sfx: true,
      include_source_audio: false,
      include_subtitles: true,
    }));
    await waitFor(() => expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(false));
    await waitFor(() => expect((screen.getByRole("button", { name: "冻结当前草稿" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("registers the timeline as a shared dirty draft with save and discard (FE-02)", async () => {
    await mountWithData();
    expect(draftRegistry.get("timeline-edit:e1")?.entityKey).toBe("第 EP01 集时间线");

    fireEvent.change(screen.getByLabelText("成片时长（秒）"), { target: { value: "1.5" } });
    const owner = draftRegistry.get("timeline-edit:e1")!;
    expect(owner.dirty).toBe(true);

    let saved: unknown;
    await act(async () => { saved = await owner.save!(owner.version); });
    expect(saved).toMatchObject({ status: "saved" });
    expect(createEpisodeTimelineDraftV2).toHaveBeenCalledWith("e1", expect.objectContaining({
      clips: expect.arrayContaining([expect.objectContaining({ shot_id: "s1", duration_us: 1_500_000 })]),
    }));
    await waitFor(() => expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(false));

    fireEvent.change(screen.getByLabelText("成片时长（秒）"), { target: { value: "1.8" } });
    const dirtyOwner = draftRegistry.get("timeline-edit:e1")!;
    let discarded: unknown;
    await act(async () => { discarded = await dirtyOwner.discard!(dirtyOwner.version); });
    expect(discarded).toMatchObject({ status: "discarded" });
    expect((screen.getByLabelText("成片时长（秒）") as HTMLInputElement).value).toBe("1.50");
    expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(false);
  });

  it("settles a dirty timeline through the shared navigation coordinator", async () => {
    await mountWithData();
    fireEvent.click(checkboxes().subtitles);

    const saved = await act(async () => settleDirtyDrafts(draftRegistry, "save"));
    expect(saved.allowed).toBe(true);
    expect(createEpisodeTimelineDraftV2).toHaveBeenCalledWith("e1", expect.objectContaining({ include_subtitles: false }));
    expect(draftRegistry.getDirty().length).toBe(0);

    fireEvent.click(checkboxes().music);
    const discarded = await act(async () => settleDirtyDrafts(draftRegistry, "discard"));
    expect(discarded.allowed).toBe(true);
    expect(checkboxes().music.checked).toBe(true);
    expect(draftRegistry.getDirty().length).toBe(0);
  });
});

describe("EpisodeEditWorkspace first save and upstream refresh (TM-01 / TM-02)", () => {
  beforeEach(() => {
    draftRegistry.clear();
    vi.clearAllMocks();
    vi.mocked(createEpisodeTimelineDraftV2).mockResolvedValue({
      timeline: { id: "tl-9", episode_id: "e1", revision_no: 1, status: "DRAFT", revision_hash: "e".repeat(64), outcome: "DRAFT_CREATED", idempotent_replay: false },
    } as never);
  });

  afterEach(() => {
    cleanup();
    draftRegistry.clear();
  });

  it("creates the first persisted revision when the episode has no timeline yet", async () => {
    // A newly entered episode with material but ``latest_revision = null``: the
    // loaded upstream suggestion becomes the baseline, so the old
    // "signature === baseline ⇒ saved" shortcut answered *saved* without calling
    // the API and the episode never got a freezable v1.
    const fact = { ...workspaceFact(), latest_revision: null, allowed_actions: ["CREATE_DRAFT", "FREEZE_TIMELINE"] };
    vi.mocked(getEpisodeEditWorkspaceV2).mockResolvedValue({ workspace: fact } as never);
    mount();
    await screen.findByRole("heading", { name: "EP01 时间线" });

    expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(false);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "保存新草稿" }));
    });
    await waitFor(() => expect(createEpisodeTimelineDraftV2).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(createEpisodeTimelineDraftV2).mock.calls[0][1] as Record<string, unknown>;
    expect(payload.expected_latest_revision_id).toBeNull();
    expect(payload.clips).toHaveLength(2);
  });

  it("creates a new revision when the loaded revision is stale", async () => {
    const fact = { ...workspaceFact(), freshness: "STALE", allowed_actions: ["CREATE_DRAFT", "FREEZE_TIMELINE"] };
    vi.mocked(getEpisodeEditWorkspaceV2).mockResolvedValue({ workspace: fact } as never);
    mount();
    await screen.findByRole("heading", { name: "EP01 时间线" });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "保存新草稿" }));
    });
    await waitFor(() => expect(createEpisodeTimelineDraftV2).toHaveBeenCalledTimes(1));
  });

  it("keeps unsaved edits when only the upstream fingerprint changed", async () => {
    // Same revision id, different upstream_fingerprint: the old guard compared
    // revision ids only, so this refresh silently reset the local edit to 1s.
    const first = { ...workspaceFact() };
    const second = { ...workspaceFact(), upstream_fingerprint: "f".repeat(64) };
    vi.mocked(getEpisodeEditWorkspaceV2).mockResolvedValue({ workspace: first } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter><EpisodeEditWorkspace projectId="p1" episodeId="e1" /></MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByRole("heading", { name: "EP01 时间线" });

    fireEvent.change(screen.getByLabelText("成片时长（秒）"), { target: { value: "1.5" } });
    expect((screen.getByLabelText("成片时长（秒）") as HTMLInputElement).value).toBe("1.50");
    expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(true);

    // A background refresh returns the same revision id with new upstream inputs.
    vi.mocked(getEpisodeEditWorkspaceV2).mockResolvedValue({ workspace: second } as never);
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["post-edit-v2", "e1"] });
    });

    // The local 1.5s edit must survive, keep dirty, and be reported as a server move.
    await waitFor(() => expect(screen.getByText(/未保存编排已保留/)).toBeTruthy());
    expect((screen.getByLabelText("成片时长（秒）") as HTMLInputElement).value).toBe("1.50");
    expect(draftRegistry.get("timeline-edit:e1")?.dirty).toBe(true);
  });
});

describe("EpisodeEditWorkspace media seek (FE-08)", () => {
  beforeEach(() => {
    draftRegistry.clear();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    draftRegistry.clear();
  });

  it("moves the real media position when returning to the beginning", async () => {
    await mountWithData();
    const video = screen.getByLabelText("S001 视频预览") as HTMLVideoElement;
    let currentTime = 0.7;
    Object.defineProperty(video, "currentTime", {
      configurable: true,
      get: () => currentTime,
      set: (value: number) => { currentTime = value; },
    });

    fireEvent.timeUpdate(video);
    // The playhead element carries role="slider" but is marked aria-hidden by
    // the timeline lane markup, so it is read directly from the DOM.
    const playhead = () => document.querySelector(".edit-playhead") as HTMLElement;
    expect(playhead()).toHaveAttribute("aria-valuenow", "700");

    fireEvent.click(screen.getByRole("button", { name: "回到开头" }));
    expect(currentTime).toBe(0);
    expect(playhead()).toHaveAttribute("aria-valuenow", "0");
  });

  it("converts a timeline time into the selected clip's source time", async () => {
    await mountWithData();
    const video = screen.getByLabelText("S001 视频预览") as HTMLVideoElement;
    let currentTime = 0.2;
    Object.defineProperty(video, "currentTime", {
      configurable: true,
      get: () => currentTime,
      set: (value: number) => { currentTime = value; },
    });

    // The first clip starts 0.5s into its source media.
    fireEvent.change(screen.getByLabelText("源视频入点（秒）"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "回到开头" }));
    expect(currentTime).toBe(0.5);
  });
});

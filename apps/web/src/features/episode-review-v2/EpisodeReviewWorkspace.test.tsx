import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createReviewDecisionV2,
  getEpisodePostOverviewV2,
  listEpisodeReviewTargetsV2,
  revokeReviewDecisionV2,
  runMachineCheck,
  type EpisodeReviewTarget,
} from "../../generated/api";
import { EpisodeReviewWorkspace } from "./EpisodeReviewWorkspace";

vi.mock("../../generated/api", () => ({
  createReviewDecisionV2: vi.fn(),
  getEpisodePostOverviewV2: vi.fn(),
  listEpisodeReviewTargetsV2: vi.fn(),
  revokeReviewDecisionV2: vi.fn(),
  runMachineCheck: vi.fn(),
}));

const mediaTarget: EpisodeReviewTarget = {
  target_kind: "MEDIA_VERSION",
  target_id: "media-1",
  project_id: "project-1",
  episode_id: "episode-1",
  shot_id: "shot-1",
  label: "S001",
  media_kind: "IMAGE",
  stage: "KEYFRAME",
  is_adopted: false,
  duration_ms: null,
  subject_revision: 3,
  integrity_status: "VERIFIED",
  machine_status: "PASS",
  template_version_id: "template-image",
  template_code: "image_asset",
  template_items: [{ id: "identity", label: "人物身份", required: true }],
  latest_decision_id: null,
  latest_decision: null,
  latest_decision_revision: null,
  latest_decision_stale: false,
  blocker_codes: [],
  allowed_actions: ["SUBMIT_REVIEW_DECISION"],
  created_at: "2026-08-26T00:00:00+00:00",
};

const audioTarget: EpisodeReviewTarget = {
  target_kind: "MEDIA_VERSION",
  target_id: "audio-1",
  project_id: "project-1",
  episode_id: "episode-1",
  shot_id: "shot-2-3",
  label: "AI-DL-0001 TTS",
  media_kind: "AUDIO",
  stage: "FORMAL",
  is_adopted: true,
  duration_ms: 8467,
  subject_revision: 2,
  integrity_status: "VERIFIED",
  machine_status: "NOT_RUN",
  template_version_id: "template-audio",
  template_code: "audio_dialogue",
  template_items: [
    { id: "waveform", label: "波形可复核", required: true },
    { id: "loudness", label: "综合响度", required: true },
  ],
  latest_decision_id: null,
  latest_decision: null,
  latest_decision_revision: null,
  latest_decision_stale: false,
  blocker_codes: ["MACHINE_QC_REQUIRED"],
  allowed_actions: ["SUBMIT_REVIEW_DECISION"],
  created_at: "2026-08-26T00:00:00+00:00",
};

let targetQueue: EpisodeReviewTarget[] = [mediaTarget];
let episodeRenderQueue: EpisodeReviewTarget[] = [];
let laterReviewPages: EpisodeReviewTarget[][] = [];
const deepLinkTarget: EpisodeReviewTarget = { ...mediaTarget, target_id: "deep-media", label: "Deep linked target" };
const makeEpisodeRenderTarget = (targetId: string, label: string, latestDecision: EpisodeReviewTarget["latest_decision"]): EpisodeReviewTarget => ({
  ...mediaTarget,
  target_kind: "EPISODE_RENDER_VERSION",
  target_id: targetId,
  shot_id: null,
  label,
  media_kind: null,
  stage: null,
  latest_decision_id: latestDecision ? `${targetId}-decision` : null,
  latest_decision: latestDecision,
  latest_decision_revision: latestDecision ? 1 : null,
});

function mount(path = "/") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><EpisodeReviewWorkspace projectId="project-1" episodeId="episode-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("EpisodeReviewWorkspace v2", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    targetQueue = [mediaTarget];
    episodeRenderQueue = [];
    laterReviewPages = [];
    vi.mocked(getEpisodePostOverviewV2).mockResolvedValue({
      overview: {
        episode_id: "episode-1", project_id: "project-1", episode_code: "EP01", episode_title: "第一集", next_action: "OPEN_REVIEW",
        review: { state: "ATTENTION", target_count: 1, pending_count: 1, blocked_count: 0, stale_count: 0, approved_render_id: null },
        audio: { state: "EMPTY", dialogue_line_count: 0, adopted_tts_count: 0, binding_count: 0, verified_license_count: 0 },
        edit: { state: "EMPTY", timeline_revision_count: 0, latest_timeline_id: null, latest_timeline_revision_no: null, latest_timeline_status: null, frozen_timeline_id: null, subtitle_revision_count: 0, latest_subtitle_id: null },
        delivery: { state: "EMPTY", render_count: 0, verified_render_count: 0, latest_render_id: null, latest_render_revision: null, latest_render_integrity: null, package_count: 0, latest_package_id: null, latest_package_status: null },
        blockers: [], allowed_actions: ["OPEN_REVIEW"],
      }, read_only: true, request_shape: "episode_post_overview_v2",
    });
    vi.mocked(listEpisodeReviewTargetsV2).mockImplementation(async (_episode, options) => {
      const resolved = options ?? {};
      if (resolved.targetId) {
        return {
          items: resolved.targetId === deepLinkTarget.target_id ? [deepLinkTarget] : [],
          cursor: 0, limit: 1, total: resolved.targetId === deepLinkTarget.target_id ? 1 : 0,
          next_cursor: null, target_kinds: resolved.targetKinds ?? [], include_resolved: Boolean(resolved.includeResolved),
          read_only: true, request_shape: "bounded_episode_review_targets_v2",
        };
      }
      const queue = resolved.targetKinds?.[0] === "EPISODE_RENDER_VERSION" ? episodeRenderQueue : targetQueue;
      const page = resolved.cursor ? (laterReviewPages[(resolved.cursor / 100) - 1] ?? []) : queue;
      return {
        items: page,
        cursor: resolved.cursor ?? 0, limit: 100, total: queue.length + laterReviewPages.flat().length,
        next_cursor: resolved.cursor ? null : laterReviewPages.length > 0 ? 100 : null,
        target_kinds: resolved.targetKinds ?? [], include_resolved: Boolean(resolved.includeResolved),
        read_only: true, request_shape: "bounded_episode_review_targets_v2",
      };
    });
    vi.mocked(createReviewDecisionV2).mockResolvedValue({ decision: {
      id: "decision-1", target_kind: "MEDIA_VERSION", target_id: "media-1", decision: "APPROVED",
      subject_revision: 3, revision: 1, is_stale: false, created_at: "now", updated_at: "now", idempotent_replay: false,
    } });
    vi.mocked(revokeReviewDecisionV2).mockResolvedValue({ decision: {
      id: "decision-1", target_kind: "MEDIA_VERSION", target_id: "media-1", decision: "VOIDED",
      subject_revision: 3, revision: 2, is_stale: true, created_at: "now", updated_at: "later", idempotent_replay: false,
    } });
  });

  it("loads a bounded typed queue and switches target kinds", async () => {
    mount();
    expect(await screen.findByRole("button", { name: /S001/ })).toBeTruthy();
    expect(screen.getByText("人物身份 *")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "整集成片" }));
    expect(await screen.findByText("当前筛选下没有待处理目标。")).toBeTruthy();
    expect(listEpisodeReviewTargetsV2).toHaveBeenLastCalledWith("episode-1", {
      targetKinds: ["EPISODE_RENDER_VERSION"], includeResolved: false, limit: 100,
    });
  });

  it("shows the latest review decision on episode render history cards", async () => {
    episodeRenderQueue = [
      makeEpisodeRenderTarget("render-rejected", "EP01 · revision 1", "REJECTED"),
      makeEpisodeRenderTarget("render-approved", "EP01 · revision 2", "APPROVED"),
      makeEpisodeRenderTarget("render-pending", "EP01 · revision 3", null),
    ];
    mount("/?targetKind=EPISODE_RENDER_VERSION");

    expect(within(await screen.findByRole("button", { name: /EP01 · revision 1/ })).getByText("拒绝")).toBeTruthy();
    expect(within(screen.getByRole("button", { name: /EP01 · revision 2/ })).getByText("批准")).toBeTruthy();
    expect(within(screen.getByRole("button", { name: /EP01 · revision 3/ })).getByText("待决定")).toBeTruthy();
  });

  it("restores an exact typed target deep link", async () => {
    mount("/?targetKind=MEDIA_VERSION&targetId=media-1");
    expect((await screen.findByRole("button", { name: /S001/ })).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("revision 3")).toBeTruthy();
  });

  it("loads a deep-linked target outside the first queue page without falling back", async () => {
    targetQueue = [];
    mount("/?targetKind=MEDIA_VERSION&targetId=deep-media");
    expect(await screen.findByRole("button", { name: /Deep linked target/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Deep linked target/ }).getAttribute("aria-pressed")).toBe("true");
    expect(listEpisodeReviewTargetsV2).toHaveBeenCalledWith("episode-1", {
      targetKinds: ["MEDIA_VERSION"], includeResolved: false, targetId: "deep-media", limit: 1,
    });
  });

  it("loads later review pages so targets beyond the first bounded page remain actionable", async () => {
    const laterTarget: EpisodeReviewTarget = { ...mediaTarget, target_id: "media-101", label: "S101" };
    laterReviewPages = [[laterTarget]];
    mount();
    expect(await screen.findByRole("button", { name: /S001/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /加载更多审核目标/ }));
    expect(await screen.findByRole("button", { name: /S101/ })).toBeTruthy();
    expect(listEpisodeReviewTargetsV2).toHaveBeenLastCalledWith("episode-1", {
      targetKinds: ["MEDIA_VERSION"], includeResolved: false, limit: 100, cursor: 100,
    });
    expect(screen.queryByRole("button", { name: /加载更多审核目标/ })).toBeNull();
  });

  it("shows an explicit not-found state for an invalid deep link", async () => {
    targetQueue = [];
    mount("/?targetKind=MEDIA_VERSION&targetId=missing-media");
    expect((await screen.findByText("审核目标不存在或不属于本集：missing-media")).getAttribute("role")).toBe("status");
    expect(screen.queryByRole("heading", { name: "S001" })).toBeNull();
  });

  it("requires the structured checklist before approving and writes through v2", async () => {
    mount();
    const save = await screen.findByRole("button", { name: "保存批准" });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "人物身份 *" }));
    expect((save as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(createReviewDecisionV2).toHaveBeenCalled());
    expect(vi.mocked(createReviewDecisionV2).mock.calls[0][0]).toMatchObject({
      target_kind: "MEDIA_VERSION", target_id: "media-1", template_version_id: "template-image",
      expected_revision: 3, decision: "APPROVED", checks: [{ item_id: "identity", result: "PASS" }],
    });
    expect(await screen.findByText("审核决定已保存并写入审计记录。")).toBeTruthy();
  });

  it("runs machine QC for adopted audio before unlocking the human checklist", async () => {
    targetQueue = [audioTarget];
    vi.mocked(runMachineCheck).mockResolvedValue({ machine_check: {
      id: "machine-check-1",
      subject_type: "MEDIA_VERSION",
      subject_id: "audio-1",
      policy_version: "g8_audio_qc_v1",
      status: "PASS",
      results: [
        { item_id: "duration", result: "PASS", details: { duration_ms: 8467 } },
        { item_id: "codec", result: "PASS", details: { codec_name: "pcm_s16le" } },
        { item_id: "sample_rate", result: "PASS", details: { sample_rate_hz: 48000 } },
        { item_id: "channels", result: "PASS", details: { channels: 1 } },
        { item_id: "peak", result: "PASS", details: { value_dbfs: -12.4 } },
        { item_id: "clipping", result: "PASS", details: { detected: false } },
        { item_id: "silence", result: "PASS", details: { detected: false, segment_count: 0 } },
      ],
    } });
    mount("/?targetKind=MEDIA_VERSION&targetId=audio-1");
    expect(await screen.findByRole("button", { name: /AI-DL-0001 TTS/ })).toBeTruthy();
    const run = await screen.findByRole("button", { name: "运行机器 QC" });
    const waveform = screen.getByRole("checkbox", { name: "波形可复核 *" }) as HTMLInputElement;
    const save = screen.getByRole("button", { name: "保存批准" }) as HTMLButtonElement;
    expect((run as HTMLButtonElement).disabled).toBe(false);
    expect(waveform.disabled).toBe(true);
    expect(save.disabled).toBe(true);

    fireEvent.click(run);
    await waitFor(() => expect(runMachineCheck).toHaveBeenCalledWith("MEDIA_VERSION", "audio-1", { policy_version: "g8_audio_qc_v1" }));
    expect(await screen.findByText("48000 Hz")).toBeTruthy();
    expect(waveform.disabled).toBe(false);
    fireEvent.click(waveform);
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "综合响度 *" }));
    expect(save.disabled).toBe(false);
  });
});

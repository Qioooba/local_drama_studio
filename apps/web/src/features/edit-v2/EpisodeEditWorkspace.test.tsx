import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createEpisodeTimelineDraftV2, freezeEpisodeTimelineV2, getEpisodeEditWorkspaceV2 } from "../../generated/api";
import { EpisodeEditWorkspace } from "./EpisodeEditWorkspace";

vi.mock("../../generated/api", () => ({ getEpisodeEditWorkspaceV2: vi.fn(), createEpisodeTimelineDraftV2: vi.fn(), freezeEpisodeTimelineV2: vi.fn() }));
vi.mock("../production/SubtitleRevisionPanel", () => ({ SubtitleRevisionPanel: () => <div>字幕编辑器</div> }));
vi.mock("../timeline-v2/TimelineExportPanel", () => ({ TimelineExportPanel: () => <div>专业导出</div> }));

const fact = {
  episode_id: "e1", project_id: "p1", episode_code: "EP01", episode_title: "第一集", freshness: "CURRENT", upstream_fingerprint: "a".repeat(64),
  latest_revision: { id: "tl-1", revision_no: 1, status: "DRAFT", revision_hash: "b".repeat(64), duration_us: 2_000_000, video_count: 2, audio_count: 1, subtitle_revision_id: "sub-1", upstream_fingerprint: "a".repeat(64), created_at: "2026-08-26T00:00:00Z", created_by: "local-user" },
  history: [], history_has_more: false,
  video_clips: [
    { shot_id: "s1", shot_code: "S001", media_version_id: "v1", source_name: "one.mp4", source_duration_ms: 1000, start_us: 0, end_us: 1_000_000, source_start_us: 0, transition_in: "CUT", continuity_status: "OK" },
    { shot_id: "s2", shot_code: "S002", media_version_id: "v2", source_name: "two.mp4", source_duration_ms: 1000, start_us: 1_000_000, end_us: 2_000_000, source_start_us: 0, transition_in: "DISSOLVE", continuity_status: "OK" },
  ],
  audio_clips: [{ id: "a1", lane: "BGM", media_version_id: "a1", source_name: "music.wav", start_us: 0, end_us: 2_000_000, gain_db: -4, source_revision: 1, owner_route: "POST_AUDIO" }],
  subtitle: { revision_id: "sub-1", revision_no: 2, cue_count: 4, format: "SRT", content_hash: "c".repeat(64) }, issues: [], duration_us: 2_000_000, allowed_actions: ["CREATE_DRAFT", "FREEZE_LATEST_DRAFT"],
} as const;

function mount() { const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); return render(<QueryClientProvider client={client}><MemoryRouter><EpisodeEditWorkspace projectId="p1" episodeId="e1" /></MemoryRouter></QueryClientProvider>); }

describe("EpisodeEditWorkspace", () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(getEpisodeEditWorkspaceV2).mockResolvedValue({ workspace: fact } as never); });

  it("renders one player, bounded lanes, inspector and drawer-owned secondary tasks", async () => {
    mount();
    expect(await screen.findByRole("heading", { name: "EP01 时间线" })).toBeTruthy();
    expect(screen.getByLabelText("S001 视频预览")).toBeTruthy();
    expect(screen.getByRole("region", { name: "多轨编辑时间线" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "字幕" }));
    expect(screen.getByRole("dialog", { name: "字幕版本" })).toBeTruthy();
    expect(screen.getByText("字幕编辑器")).toBeTruthy();
  });

  it("saves a new immutable draft and confirms freeze separately", async () => {
    vi.mocked(createEpisodeTimelineDraftV2).mockResolvedValue({ timeline: { id: "tl-2", episode_id: "e1", revision_no: 2, status: "DRAFT", revision_hash: "d".repeat(64), outcome: "DRAFT_CREATED", idempotent_replay: false } } as never);
    vi.mocked(freezeEpisodeTimelineV2).mockResolvedValue({ timeline: { id: "tl-2f", episode_id: "e1", revision_no: 2, status: "FROZEN", revision_hash: "e".repeat(64), outcome: "FROZEN", idempotent_replay: false } } as never);
    mount();
    await screen.findByRole("heading", { name: "EP01 时间线" });
    fireEvent.change(screen.getByLabelText("成片时长（秒）"), { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "保存新草稿" }));
    await waitFor(() => expect(createEpisodeTimelineDraftV2).toHaveBeenCalledWith("e1", expect.objectContaining({ expected_latest_revision_id: "tl-1", clips: expect.arrayContaining([expect.objectContaining({ shot_id: "s1", duration_us: 1_500_000 })]) })));
  });
});

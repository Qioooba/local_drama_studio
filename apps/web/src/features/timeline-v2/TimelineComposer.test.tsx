import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TimelineComposer } from "./TimelineComposer";
import { getShotGroupWorkspace } from "../episode-plan-v2/shotGroupsApi";
import { createTimelineRevision, getEpisodeTimelineSelections, getTimelineRevision, listEpisodeAudioBindings } from "../../generated/api";

vi.mock("../episode-plan-v2/shotGroupsApi", () => ({ getShotGroupWorkspace: vi.fn() }));
vi.mock("../../generated/api", () => ({
  createTimelineRevision: vi.fn(),
  getEpisodeTimelineSelections: vi.fn(),
  getTimelineRevision: vi.fn(),
  listEpisodeAudioBindings: vi.fn(),
}));

describe("TimelineComposer", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("shows a task-oriented empty state without querying Director Desk when the episode has no shots", async () => {
    vi.mocked(getShotGroupWorkspace).mockResolvedValue({ groups: [], shots: [] } as never);
    vi.mocked(listEpisodeAudioBindings).mockResolvedValue({ items: [] } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <TimelineComposer
            projectId="p1"
            episodeId="e1"
            status={{ timeline: { revision_count: 0, latest: null }, subtitles: { revision_count: 0, latest: null } } as never}
            onCreated={vi.fn()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("本集还没有镜头")).toBeTruthy();
    expect(screen.getByRole("link", { name: "前往分集策划" }).getAttribute("href")).toBe("/projects/p1/episodes/e1/plan");
    await waitFor(() => expect(listEpisodeAudioBindings).toHaveBeenCalledWith("e1"));
    expect(getEpisodeTimelineSelections).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("restores a valid uncommitted episode draft and lets the user discard it", async () => {
    vi.mocked(getShotGroupWorkspace).mockResolvedValue({ groups: [], shots: [{ id: "s1", code: "SHOT-1", target_duration_ms: 1_000, archived_at: null }] } as never);
    vi.mocked(listEpisodeAudioBindings).mockResolvedValue({ items: [] } as never);
    vi.mocked(getEpisodeTimelineSelections).mockResolvedValue({ items: [{ id: "s1", code: "SHOT-1", order_key: "1", status: "DIRECTED", target_duration_ms: 1_000, current_video_media_version_id: null, continuity_status: "READY" }], total: 1, has_more: false, limit: 500, read_only: true, request_shape: "bounded_timeline_selection_read_model" });
    window.sessionStorage.setItem("localdrama:timeline-draft:v2:p1:e1", JSON.stringify({
      baseFingerprint: "older-upstream",
      shots: [{ shotId: "s1", code: "SHOT-1", mediaVersionId: "media-draft", durationUs: 1_500_000, transition: "CUT", selectionSource: "CURRENT_MEDIA", continuityStatus: "READY" }],
      includeAudio: false,
      includeSubtitles: true,
      savedAt: "2026-08-22T00:00:00Z",
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><TimelineComposer projectId="p1" episodeId="e1" status={{ timeline: { revision_count: 0, latest: null }, subtitles: { revision_count: 0, latest: null } } as never} onCreated={vi.fn()} /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByText("已恢复本浏览器中尚未保存的时间线草稿。")).toBeTruthy();
    expect(screen.getByRole("button", { name: "更换项目视频" })).toBeTruthy();
    expect(screen.getByRole("spinbutton", { name: "时长（秒）" }).getAttribute("value")).toBe("1.5");
    expect((screen.getByRole("checkbox", { name: "包含已授权音轨" }) as HTMLInputElement).checked).toBe(false);
    expect(window.sessionStorage.getItem("localdrama:timeline-draft:v2:p1:e1")).toBeNull();
    expect(window.localStorage.getItem("localdrama:timeline-draft:v2:p1:e1")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "放弃未保存草稿" }));
    expect(screen.getByRole("button", { name: "选择项目视频" })).toBeTruthy();
    expect(screen.getByText("已放弃本浏览器中的未保存草稿，并恢复最近一次已保存的时间线基线。")).toBeTruthy();
    expect(window.localStorage.getItem("localdrama:timeline-draft:v2:p1:e1")).toBeNull();
  });

  it("restores manual shot choices from the latest revision and clears dirty state after saving", async () => {
    vi.mocked(getShotGroupWorkspace).mockResolvedValue({ groups: [], shots: [{ id: "s2", code: "SHOT-2", target_duration_ms: 1_000, archived_at: null }] } as never);
    vi.mocked(listEpisodeAudioBindings).mockResolvedValue({ items: [] } as never);
    vi.mocked(getEpisodeTimelineSelections).mockResolvedValue({ items: [{ id: "s2", code: "SHOT-2", order_key: "1", status: "DIRECTED", target_duration_ms: 1_000, current_video_media_version_id: null, continuity_status: "MISSING" }], total: 1, has_more: false, limit: 500, read_only: true, request_shape: "bounded_timeline_selection_read_model" });
    vi.mocked(getTimelineRevision).mockResolvedValue({ timeline: {
      id: "rev-1", episode_id: "e2", revision_no: 1, status: "FROZEN",
      input_snapshot: { upstream_selection_fingerprint: "saved-fingerprint", audio_binding_ids: [], subtitle_revision_id: null },
      items: [{ track_type: "VIDEO", media_version_id: "manual-video", start_us: 0, end_us: 1_500_000, parameters: { shot_id: "s2", shot_code: "SHOT-2", transition_in: "CUT" } }],
    } } as never);
    vi.mocked(createTimelineRevision).mockResolvedValue({ timeline: { id: "rev-2", revision_no: 2, status: "DRAFT" } } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><TimelineComposer projectId="p1" episodeId="e2" status={{ timeline: { revision_count: 1, latest: { id: "rev-1" } }, subtitles: { revision_count: 0, latest: null } } as never} onCreated={vi.fn()} /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByRole("button", { name: "更换项目视频" })).toBeTruthy();
    expect(screen.getByText("手工选片")).toBeTruthy();
    expect(screen.getByText("视频已选择")).toBeTruthy();
    expect(screen.getByRole("spinbutton", { name: "时长（秒）" }).getAttribute("value")).toBe("1.5");
    expect(screen.getByRole("button", { name: "放弃未保存草稿" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "载入最新上游为新草稿" }));
    expect(screen.getByRole("button", { name: "更换项目视频" })).toBeTruthy();
    expect(screen.getByText("已载入最新采用、音轨与字幕；同镜头保留当前时长与转场，没有上游视频的镜头保留人工选择。历史 revision 未被修改。")).toBeTruthy();
    fireEvent.change(screen.getByRole("spinbutton", { name: "时长（秒）" }), { target: { value: "2" } });
    expect(screen.getByText(/有未保存修改/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存新草稿 revision" }));
    expect(await screen.findByText("已创建不可变 TimelineRevision v2 · DRAFT")).toBeTruthy();
    expect(screen.getByRole("button", { name: "放弃未保存草稿" }).hasAttribute("disabled")).toBe(true);
    expect(window.localStorage.getItem("localdrama:timeline-draft:v2:p1:e2")).toBeNull();
  });

  it("updates the selected media without resetting the edited duration for the same shot", async () => {
    vi.mocked(getShotGroupWorkspace).mockResolvedValue({ groups: [], shots: [{ id: "s3", code: "SHOT-3", target_duration_ms: 20_000, archived_at: null }] } as never);
    vi.mocked(listEpisodeAudioBindings).mockResolvedValue({ items: [] } as never);
    vi.mocked(getEpisodeTimelineSelections).mockResolvedValue({ items: [{ id: "s3", code: "SHOT-3", order_key: "1", status: "DIRECTED", target_duration_ms: 20_000, current_video_media_version_id: "video-new", continuity_status: "READY" }], total: 1, has_more: false, limit: 500, read_only: true, request_shape: "bounded_timeline_selection_read_model" });
    vi.mocked(getTimelineRevision).mockResolvedValue({ timeline: {
      id: "rev-old", episode_id: "e3", revision_no: 1, status: "FROZEN",
      input_snapshot: { upstream_selection_fingerprint: "older-selection", audio_binding_ids: [], subtitle_revision_id: null },
      items: [{ track_type: "VIDEO", media_version_id: "video-old", start_us: 0, end_us: 5_000_000, parameters: { shot_id: "s3", shot_code: "SHOT-3", transition_in: "DISSOLVE" } }],
    } } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><TimelineComposer projectId="p1" episodeId="e3" status={{ timeline: { revision_count: 1, latest: { id: "rev-old" } }, subtitles: { revision_count: 0, latest: null } } as never} onCreated={vi.fn()} /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByRole("spinbutton", { name: "时长（秒）" })).toHaveProperty("value", "5");
    fireEvent.click(screen.getByRole("button", { name: "载入最新上游为新草稿" }));
    expect(screen.getByRole("spinbutton", { name: "时长（秒）" })).toHaveProperty("value", "5");
    expect(screen.getByText("精确采用")).toBeTruthy();
  });

  it("creates an auditable automatic rough cut as DRAFT only", async () => {
    vi.mocked(getShotGroupWorkspace).mockResolvedValue({ groups: [], shots: [{ id: "s4", code: "SHOT-4", target_duration_ms: 2_000, archived_at: null }] } as never);
    vi.mocked(listEpisodeAudioBindings).mockResolvedValue({ items: [{ id: "audio-1", start_us: 0, end_us: 2_000_000 }] } as never);
    vi.mocked(getEpisodeTimelineSelections).mockResolvedValue({ items: [{ id: "s4", code: "SHOT-4", order_key: "1", status: "DIRECTED", target_duration_ms: 2_000, current_video_media_version_id: "video-4", continuity_status: "READY" }], total: 1, has_more: false, limit: 500, read_only: true, request_shape: "bounded_timeline_selection_read_model" });
    vi.mocked(createTimelineRevision).mockResolvedValue({ timeline: { id: "rev-auto", revision_no: 1, status: "DRAFT" } } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><TimelineComposer projectId="p1" episodeId="e4" status={{ timeline: { revision_count: 0, latest: null }, subtitles: { revision_count: 1, latest: { id: "sub-1" } } } as never} onCreated={vi.fn()} /></MemoryRouter></QueryClientProvider>);

    fireEvent.click(await screen.findByRole("button", { name: "生成自动初剪草稿 revision" }));
    await waitFor(() => expect(createTimelineRevision).toHaveBeenCalledWith("e4", expect.objectContaining({
      status: "DRAFT",
      input_snapshot: expect.objectContaining({ source: "P10_AUTO_ROUGH_CUT", auto_rough_cut: true, requires_human_freeze_confirmation: true, subtitle_revision_id: "sub-1" }),
    })));
    expect(await screen.findByText("已创建自动初剪 TimelineRevision v1 · DRAFT；仍需人工核对后才能冻结。")).toBeTruthy();
    expect(screen.getByText(/对白、BGM、SFX 按各自 start\/end 与 V1 并行混合/)).toBeTruthy();
  });
});

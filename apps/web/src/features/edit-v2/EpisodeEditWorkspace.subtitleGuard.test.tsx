import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { useEffect } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getEpisodeEditWorkspaceV2 } from "../../generated/api";
import { EpisodeEditWorkspace } from "./EpisodeEditWorkspace";

/**
 * FE-02 (timeline part): the subtitle drawer previously had no dirty guard, so
 * closing it silently discarded the local subtitle draft that the drawer owns.
 */
vi.mock("../../generated/api", () => ({ getEpisodeEditWorkspaceV2: vi.fn(), createEpisodeTimelineDraftV2: vi.fn(), freezeEpisodeTimelineV2: vi.fn() }));
vi.mock("../timeline-v2/TimelineExportPanel", () => ({ TimelineExportPanel: () => <div>专业导出</div> }));
vi.mock("../production/SubtitleRevisionPanel", () => ({
  SubtitleRevisionPanel: ({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) => {
    useEffect(() => { onDirtyChange?.(true); }, [onDirtyChange]);
    return <div>字幕编辑器</div>;
  },
}));

const fact = {
  episode_id: "e1", project_id: "p1", episode_code: "EP01", episode_title: "第一集", freshness: "CURRENT", upstream_fingerprint: "a".repeat(64),
  latest_revision: {
    id: "tl-1", revision_no: 1, status: "DRAFT", revision_hash: "b".repeat(64), duration_us: 2_000_000,
    video_count: 1, audio_count: 0, subtitle_revision_id: "sub-1", upstream_fingerprint: "a".repeat(64),
    created_at: "2026-08-26T00:00:00Z", created_by: "local-user",
  },
  history: [], history_has_more: false,
  video_clips: [
    { shot_id: "s1", shot_code: "S001", media_version_id: "v1", source_name: "one.mp4", source_duration_ms: 2000, start_us: 0, end_us: 2_000_000, source_start_us: 0, transition_in: "CUT", continuity_status: "OK" },
  ],
  audio_clips: [], subtitle: null, issues: [], duration_us: 2_000_000, allowed_actions: ["CREATE_DRAFT", "FREEZE_LATEST_DRAFT"],
};

describe("EpisodeEditWorkspace subtitle drawer guard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getEpisodeEditWorkspaceV2).mockResolvedValue({ workspace: fact } as never);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("asks before closing the subtitle drawer while its draft is dirty", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><EpisodeEditWorkspace projectId="p1" episodeId="e1" /></MemoryRouter></QueryClientProvider>);
    await screen.findByRole("heading", { name: "EP01 时间线" });

    fireEvent.click(screen.getByRole("button", { name: "字幕" }));
    expect(screen.getByRole("dialog", { name: "字幕版本" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog", { name: "字幕版本" })).toBeTruthy();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));
    expect(screen.queryByRole("dialog", { name: "字幕版本" })).toBeNull();
  });
});

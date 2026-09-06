import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getEpisodeEditWorkspaceV2 } from "../../generated/api";
import { DirectorTimelinePreview } from "./DirectorTimelinePreview";

vi.mock("../../generated/api", () => ({ getEpisodeEditWorkspaceV2: vi.fn() }));

const workspace = {
  episode_id: "e1", project_id: "p1", episode_code: "EP01", episode_title: "第一集", freshness: "CURRENT", upstream_fingerprint: "a".repeat(64),
  latest_revision: { id: "tl-1", revision_no: 2, status: "DRAFT", revision_hash: "b".repeat(64), duration_us: 2_000_000, video_count: 2, audio_count: 1, subtitle_revision_id: "sub-1", upstream_fingerprint: "a".repeat(64), created_at: "2026-08-30T00:00:00Z", created_by: "local-user" },
  history: [], history_has_more: false,
  video_clips: [
    { shot_id: "s1", shot_code: "S001", media_version_id: "v1", source_name: "one.mp4", source_duration_ms: 1000, start_us: 0, end_us: 1_000_000, source_start_us: 0, transition_in: "CUT", continuity_status: "OK" },
    { shot_id: "s2", shot_code: "S002", media_version_id: "v2", source_name: "two.mp4", source_duration_ms: 1000, start_us: 1_000_000, end_us: 2_000_000, source_start_us: 0, transition_in: "DISSOLVE", continuity_status: "OK" },
  ],
  audio_clips: [{ id: "a1", lane: "DIALOGUE", media_version_id: "a1", source_name: "line.wav", start_us: 0, end_us: 1_000_000, gain_db: 0, source_revision: 1, owner_route: "SHOT_STUDIO" }],
  subtitle: { revision_id: "sub-1", revision_no: 1, cue_count: 2, format: "SRT", content_hash: "c".repeat(64) },
  issues: [], duration_us: 2_000_000, allowed_actions: ["CREATE_DRAFT"],
} as const;

describe("DirectorTimelinePreview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getEpisodeEditWorkspaceV2).mockResolvedValue({ workspace } as never);
  });

  it("lazy-loads the canonical read-only workspace and uses shared synchronized lanes", async () => {
    const onSelectShot = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><DirectorTimelinePreview projectId="p1" episodeId="e1" currentShotId="s2" onSelectShot={onSelectShot} /></MemoryRouter></QueryClientProvider>);

    expect(getEpisodeEditWorkspaceV2).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("本集同步预览"));
    expect(await screen.findByRole("region", { name: "导演台只读同步时间线" })).toBeTruthy();
    expect(getEpisodeEditWorkspaceV2).toHaveBeenCalledWith("e1");
    await waitFor(() => expect(screen.getAllByText("00:01.0 / 00:02.0").length).toBeGreaterThan(0));
    expect(screen.getByRole("link", { name: "进入后期编辑" }).getAttribute("href")).toBe("/projects/p1/episodes/e1/post/edit");

    fireEvent.click(screen.getByRole("button", { name: /S001/ }));
    expect(onSelectShot).toHaveBeenCalledWith("s1");
  });
});

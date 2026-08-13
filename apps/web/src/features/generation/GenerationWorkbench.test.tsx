import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createFrameAnchor, createKeyframeCandidate } from "../../generated/api";
import { GenerationWorkbench } from "./GenerationWorkbench";

vi.mock("../../generated/api", () => ({ createFrameAnchor: vi.fn(), createKeyframeCandidate: vi.fn() }));

const video = { media_version_id: "video-123456789", media_asset_id: "asset-1", project_id: "project-1", media_kind: "VIDEO", stage: "PROXY", decision: null, is_stale: null };

function renderWorkbench(selectedShotId: string | null = null, onOpenReviews = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><GenerationWorkbench profiles={[]} videos={[video]} shots={selectedShotId ? [{ id: selectedShotId, code: "SH-001", status: "DRAFT" }] : []} selectedShotId={selectedShotId} onSelectShot={vi.fn()} onOpenProfiles={vi.fn()} onOpenReviews={onOpenReviews} /></QueryClientProvider>);
}

describe("GenerationWorkbench FrameAnchor actions", () => {
  beforeEach(() => {
    vi.mocked(createFrameAnchor).mockReset().mockImplementation(async (_id, payload) => ({ frame_anchor: {
      id: "anchor-1", source_media_version_id: video.media_version_id, source_time_us: payload.source_time_us ?? (payload.position_mode === "LAST_FRAME" ? 2_000_000 : 0), source_frame_index: payload.position_mode === "LAST_FRAME" ? 2 : payload.source_time_us ? 1 : 0,
      extracted_media_version_id: "frame-1", role_hint: payload.role_hint, sha256: "abcdef1234567890", requested_time_us: payload.source_time_us ?? null, resolved_time_us: payload.source_time_us ?? (payload.position_mode === "LAST_FRAME" ? 2_000_000 : 0), source_sha256: "source-sha", extraction_method: "FFPROBE_PTS_FRAME_INDEX",
    } }));
    vi.mocked(createKeyframeCandidate).mockReset().mockResolvedValue({ media: { id: "keyframe-1", media_asset_id: "asset-kf", project_id: "project-1", owner_type: "SHOT", owner_id: "shot-1", media_kind: "IMAGE", stage: "KEYFRAME", parent_version_id: "frame-1", duplicate: false } });
  });

  it("sends first, current and last requests with mutually exclusive positions", async () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("button", { name: "用作首帧" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenLastCalledWith(video.media_version_id, { position_mode: "FIRST_FRAME", role_hint: "FIRST_FRAME" }));
    fireEvent.change(screen.getByLabelText("当前时间（秒）"), { target: { value: "1.25" } });
    fireEvent.click(screen.getByRole("button", { name: "用作当前帧" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenLastCalledWith(video.media_version_id, { source_time_us: 1_250_000, role_hint: "CURRENT_FRAME" }));
    fireEvent.click(screen.getByRole("button", { name: "用作末帧" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenLastCalledWith(video.media_version_id, { position_mode: "LAST_FRAME", role_hint: "LAST_FRAME" }));
    expect(await screen.findByText(/末帧已注册并填入当前未提交输入槽/)).toBeTruthy();
    expect(screen.getByText(/FFPROBE_PTS_FRAME_INDEX/)).toBeTruthy();
  });

  it("creates a shot-owned candidate and opens review without auto approval", async () => {
    const openReviews = vi.fn();
    renderWorkbench("shot-1", openReviews);
    fireEvent.click(screen.getByRole("button", { name: "用作首帧" }));
    await screen.findByText(/首帧已注册并填入当前未提交输入槽/);
    fireEvent.click(screen.getByRole("button", { name: "创建关键帧候选并进入人工审核" }));
    await waitFor(() => expect(createKeyframeCandidate).toHaveBeenCalledWith("frame-1", "shot-1"));
    expect(openReviews).toHaveBeenCalledWith("keyframe-1");
    expect(screen.getByText(/不会自动选择或批准/)).toBeTruthy();
  });
});

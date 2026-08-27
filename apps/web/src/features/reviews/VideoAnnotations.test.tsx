import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createReviewAnnotationV2, listReviewAnnotationsV2 } from "../../generated/api";
import { VideoAnnotations } from "./VideoAnnotations";

vi.mock("../../generated/api", () => ({ createReviewAnnotationV2: vi.fn(), listReviewAnnotationsV2: vi.fn() }));

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><VideoAnnotations mediaVersionId="video-1" expectedRevision={3} durationMs={1_000} currentTimeMs={500} /></QueryClientProvider>);
}

describe("VideoAnnotations", () => {
  beforeEach(() => {
    vi.mocked(listReviewAnnotationsV2).mockReset().mockResolvedValue({ items: [], cursor: 0, limit: 100, total: 0, next_cursor: null, read_only: true, request_shape: "bounded_review_annotations_v2" });
    vi.mocked(createReviewAnnotationV2).mockReset().mockResolvedValue({ annotation: {
      id: "annotation-1", target_kind: "MEDIA_VERSION", target_id: "video-1", timecode_ms: 500, category: "FLICKER", comment: "亮度跳变",
      snapshot_media_version_id: null, rework_job_id: null, created_at: "now", created_by: "tester", idempotent_replay: false,
    } });
  });

  it("captures the player time automatically and submits a creator-facing issue category", async () => {
    renderPanel();
    const submit = screen.getByRole("button", { name: "标记播放器当前画面" });
    expect(submit.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("问题分类"), { target: { value: "FLICKER" } });
    fireEvent.change(screen.getByLabelText("问题描述"), { target: { value: "亮度跳变" } });
    fireEvent.click(submit);
    await waitFor(() => expect(createReviewAnnotationV2).toHaveBeenCalledWith("MEDIA_VERSION", "video-1", expect.objectContaining({ expected_revision: 3, timecode_ms: 500, category: "FLICKER", comment: "亮度跳变", idempotency_key: expect.stringMatching(/^review-annotation:/) })));
  });

  it("shows immutable markers returned by the API", async () => {
    vi.mocked(listReviewAnnotationsV2).mockResolvedValueOnce({ items: [{
      id: "annotation-2", target_kind: "MEDIA_VERSION", target_id: "video-1", timecode_ms: 125, category: "MOTION", comment: "动作断裂",
      snapshot_media_version_id: null, rework_job_id: "job-1", created_at: "now", created_by: "tester", idempotent_replay: false,
    }], cursor: 0, limit: 100, total: 1, next_cursor: null, read_only: true, request_shape: "bounded_review_annotations_v2" });
    renderPanel();
    expect((await screen.findByText("00:00.125 · 动作或运镜")).textContent).toContain("动作或运镜");
    expect(screen.getByText("动作断裂")).toBeTruthy();
    expect(screen.getByText(/已关联返工/)).toBeTruthy();
  });

  it("seeks the player to an existing marker without exposing millisecond input", async () => {
    const onSeek = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    vi.mocked(listReviewAnnotationsV2).mockResolvedValueOnce({ items: [{
      id: "annotation-2", target_kind: "MEDIA_VERSION", target_id: "video-1", timecode_ms: 125, category: "MOTION", comment: "动作断裂",
      snapshot_media_version_id: null, rework_job_id: null, created_at: "now", created_by: "tester", idempotent_replay: false,
    }], cursor: 0, limit: 100, total: 1, next_cursor: null, read_only: true, request_shape: "bounded_review_annotations_v2" });
    render(<QueryClientProvider client={client}><VideoAnnotations mediaVersionId="video-1" expectedRevision={3} durationMs={1_000} currentTimeMs={638} onSeek={onSeek} /></QueryClientProvider>);
    expect(screen.queryByLabelText("时间码（毫秒）")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "00:00.125 · 动作或运镜" }));
    expect(onSeek).toHaveBeenCalledWith(125);
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createVideoAnnotation, listVideoAnnotations } from "../../generated/api";
import { VideoAnnotations } from "./VideoAnnotations";

vi.mock("../../generated/api", () => ({ createVideoAnnotation: vi.fn(), listVideoAnnotations: vi.fn() }));

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><VideoAnnotations mediaVersionId="video-1" durationMs={1_000} /></QueryClientProvider>);
}

describe("VideoAnnotations", () => {
  beforeEach(() => {
    vi.mocked(listVideoAnnotations).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(createVideoAnnotation).mockReset().mockResolvedValue({ annotation: {
      id: "annotation-1", media_version_id: "video-1", timecode_ms: 500, category: "FLICKER", comment: "亮度跳变",
      snapshot_media_version_id: null, rework_job_id: null, created_at: "now", created_by: "tester", schema_version: "v2",
    } });
  });

  it("requires a note, bounds the timecode, and submits an explicit issue category", async () => {
    renderPanel();
    const submit = screen.getByRole("button", { name: "保存标记" });
    expect(submit.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("时间码（毫秒）"), { target: { value: "500" } });
    fireEvent.change(screen.getByLabelText("问题分类"), { target: { value: "FLICKER" } });
    fireEvent.change(screen.getByLabelText("问题备注"), { target: { value: "亮度跳变" } });
    fireEvent.click(submit);
    await waitFor(() => expect(createVideoAnnotation).toHaveBeenCalledWith("video-1", { timecode_ms: 500, category: "FLICKER", comment: "亮度跳变" }));
  });

  it("shows immutable markers returned by the API", async () => {
    vi.mocked(listVideoAnnotations).mockResolvedValueOnce({ items: [{
      id: "annotation-2", media_version_id: "video-1", timecode_ms: 125, category: "MOTION", comment: "动作断裂",
      snapshot_media_version_id: null, rework_job_id: "job-1", created_at: "now", created_by: "tester", schema_version: "v2",
    }] });
    renderPanel();
    expect((await screen.findByText("00:00.125 · MOTION")).textContent).toContain("MOTION");
    expect(screen.getByText("动作断裂")).toBeTruthy();
    expect(screen.getByText(/返工 job-1/)).toBeTruthy();
  });
});

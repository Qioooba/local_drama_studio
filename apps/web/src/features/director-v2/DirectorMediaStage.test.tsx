import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DirectorMediaStage } from "./DirectorMediaStage";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("DirectorMediaStage", () => {
  it("uses only the thumbnail endpoint for image media", () => {
    render(<DirectorMediaStage label="S01" media={{ mediaVersionId: "image 1", mediaKind: "IMAGE" }} />);
    const image = screen.getByRole("img") as HTMLImageElement;
    expect(image.src).toContain("/thumbnail?size=medium&frame=poster");
    expect(image.src).not.toContain("/content");
  });

  it("uses the proxy first, retains a Range fallback and keeps a thumbnail-only poster", () => {
    render(<DirectorMediaStage label="S02" media={{ mediaVersionId: "video 1", mediaKind: "VIDEO", durationMs: 2500 }} />);
    const video = screen.getByLabelText("S02 视频预览") as HTMLVideoElement;
    expect(video.getAttribute("src")).toBe("/api/v1/media-versions/video%201/proxy");
    expect(video.dataset.originalSrc).toBe("/api/v1/media-versions/video%201/content");
    expect(video.getAttribute("poster")).toContain("/thumbnail?size=medium&frame=poster");
    expect(video.getAttribute("preload")).toBe("none");
    expect(screen.getByText("0:00 / 0:02")).toBeTruthy();
  });

  it("falls back once to immutable original content when a historical proxy is not ready", () => {
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
    render(<DirectorMediaStage label="S02" media={{ mediaVersionId: "video", mediaKind: "VIDEO" }} />);
    const video = screen.getByLabelText("S02 视频预览") as HTMLVideoElement;
    fireEvent.error(video);
    expect(video.getAttribute("src")).toBe("/api/v1/media-versions/video/content");
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.error(video);
    expect(screen.getByRole("alert").textContent).toContain("无法播放当前视频");
  });

  it("toggles playback with Space but ignores input focus", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    render(<><DirectorMediaStage label="S03" media={{ mediaVersionId: "video", mediaKind: "VIDEO" }} /><input aria-label="intent field" /></>);
    const video = screen.getByLabelText("S03 视频预览") as HTMLVideoElement;
    Object.defineProperty(video, "paused", { configurable: true, value: true });
    await act(async () => { fireEvent.keyDown(window, { code: "Space" }); await Promise.resolve(); });
    expect(play).toHaveBeenCalledTimes(1);
    const input = screen.getByLabelText("intent field"); input.focus();
    await act(async () => { fireEvent.keyDown(input, { code: "Space" }); await Promise.resolve(); });
    expect(play).toHaveBeenCalledTimes(1);
  });

  it("does not handle Space while a Director modal owns the keyboard scope", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    render(<DirectorMediaStage label="S04" media={{ mediaVersionId: "video", mediaKind: "VIDEO" }} keyboardShortcutsEnabled={false} />);
    await act(async () => { fireEvent.keyDown(window, { code: "Space" }); await Promise.resolve(); });
    expect(play).not.toHaveBeenCalled();
  });

  it("shows an actionable empty state", () => {
    const action = vi.fn(); render(<DirectorMediaStage label="empty" media={null} onEmptyAction={action} />);
    fireEvent.click(screen.getByRole("button", { name: "生成候选" }));
    expect(action).toHaveBeenCalledOnce();
  });

  it("provides fit, detail, grid and safe-frame inspection without loading original image content", () => {
    const { container } = render(<DirectorMediaStage label="inspect" media={{ mediaVersionId: "image-main", mediaKind: "IMAGE" }} />);
    fireEvent.click(screen.getByRole("button", { name: "100%" }));
    fireEvent.click(screen.getByRole("button", { name: "细节 ×2" }));
    fireEvent.click(screen.getByRole("button", { name: "九宫格" }));
    fireEvent.click(screen.getByRole("button", { name: "安全框" }));
    expect(container.querySelector(".director-media-stage")?.className).toContain("detail-zoom");
    expect(container.querySelector(".director-media-stage__grid")).toBeTruthy();
    expect(container.querySelector(".director-media-stage__safe-frame")).toBeTruthy();
    expect((screen.getByRole("img") as HTMLImageElement).src).not.toContain("/content");
  });

  it("flickers between two thumbnail-only image candidates while held", () => {
    render(<DirectorMediaStage label="compare" media={{ mediaVersionId: "image-a", mediaKind: "IMAGE" }} comparisonMedia={{ mediaVersionId: "image-b", mediaKind: "IMAGE" }} />);
    const flicker = screen.getByRole("button", { name: "按住闪切" });
    fireEvent.pointerDown(flicker);
    expect((screen.getByRole("img") as HTMLImageElement).src).toContain("image-b/thumbnail");
    fireEvent.pointerUp(flicker);
    expect((screen.getByRole("img") as HTMLImageElement).src).toContain("image-a/thumbnail");
  });
});

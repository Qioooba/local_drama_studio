import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createFrameAnchor } from "../../generated/api";
import { setFrameBridgeCurrentFrame, setFrameBridgeSourceFrame } from "./frameBridgeClient";
import { FrameBridgeControls } from "./FrameBridgeControls";
import type { DirectorDeskResponse } from "./types";

vi.mock("../../generated/api", () => ({ createFrameAnchor: vi.fn() }));
vi.mock("./frameBridgeClient", async (original) => {
  const actual = await original<typeof import("./frameBridgeClient")>();
  return { ...actual, inheritFrameBridge: vi.fn(), setFrameBridgeCurrentFrame: vi.fn(), setFrameBridgeLocked: vi.fn(), setFrameBridgeSourceFrame: vi.fn() };
});

const anchor = (id: string) => ({ anchor_id: id, inherited_from_anchor_id: null, source_media_version_id: "video-1", media_version_id: `image-${id}`, rel_path: null, role_hint: "LAST_FRAME", source: "EXTRACTED" as const, status: "EXPLICIT" as const, stale: false, stale_reason: null });
const boundary = (id: string, from: string, to: string) => ({ transition_id: id, boundary_revision: 1, from_shot_id: from, from_shot_code: from, to_shot_id: to, to_shot_code: to, enforcement: "ADVISORY", compatibility: "READY", stale: false, stale_reason: null, previous_end: anchor(`${id}-end`), current_start: null });

describe("FrameBridgeControls tail extraction", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(createFrameAnchor).mockResolvedValue({ frame_anchor: { id: "new-tail-anchor" } as never });
    vi.mocked(setFrameBridgeSourceFrame).mockResolvedValue({ boundary_revision: 2 } as never);
    vi.mocked(setFrameBridgeCurrentFrame).mockResolvedValue({ boundary_revision: 2 } as never);
  });

  it("extracts LAST_FRAME from the selected verified video and binds the next boundary", async () => {
    const frameBridge: DirectorDeskResponse["current_shot"]["frame_bridge"] = { previous: boundary("prev", "S001", "S002"), current_start: null, current_end: null, next: boundary("next", "S002", "S003"), compatibility: "READY", stale: false };
    render(<FrameBridgeControls frameBridge={frameBridge} currentCandidate={{ media_version_id: "video-current", media_kind: "VIDEO", integrity_status: "VERIFIED", is_stale: false }} />);
    expect(screen.getByAltText("上一镜尾帧缩略图").getAttribute("src")).toContain("/thumbnail?size=small&frame=poster");
    expect(screen.getByAltText("上一镜尾帧缩略图").getAttribute("src")).not.toContain("/content");
    fireEvent.click(screen.getByRole("button", { name: "从当前视频提取尾帧" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenCalledWith("video-current", { position_mode: "LAST_FRAME", role_hint: "LAST_FRAME" }));
    await waitFor(() => expect(setFrameBridgeSourceFrame).toHaveBeenCalledWith("next", 1, "new-tail-anchor"));
  });

  it("opens confirmation on image drop and uses the same revisioned command as the keyboard-accessible button", async () => {
    const frameBridge: DirectorDeskResponse["current_shot"]["frame_bridge"] = { previous: boundary("prev", "S001", "S002"), current_start: null, current_end: null, next: boundary("next", "S002", "S003"), compatibility: "READY", stale: false };
    const candidate = { media_version_id: "image-current", media_kind: "IMAGE", integrity_status: "VERIFIED", is_stale: false } as const;
    render(<FrameBridgeControls frameBridge={frameBridge} currentCandidate={candidate} />);

    const dropTarget = screen.getByLabelText("拖放候选到本镜首帧");
    fireEvent.drop(dropTarget, { dataTransfer: { getData: () => JSON.stringify(candidate), dropEffect: "copy" } });
    expect(screen.getByRole("alertdialog")).not.toBeNull();
    expect(setFrameBridgeCurrentFrame).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    await waitFor(() => expect(setFrameBridgeCurrentFrame).toHaveBeenCalledWith("prev", 1, { mediaVersionId: "image-current" }));

    vi.mocked(setFrameBridgeCurrentFrame).mockClear();
    const button = screen.getByRole("button", { name: "用当前候选帧" });
    expect(button.tagName).toBe("BUTTON");
    button.focus();
    fireEvent.click(button);
    await waitFor(() => expect(setFrameBridgeCurrentFrame).toHaveBeenCalledWith("prev", 1, { mediaVersionId: "image-current" }));
  });

  it("extracts FIRST_FRAME when a verified video is dropped on the start target", async () => {
    const frameBridge: DirectorDeskResponse["current_shot"]["frame_bridge"] = { previous: boundary("prev", "S001", "S002"), current_start: null, current_end: null, next: boundary("next", "S002", "S003"), compatibility: "READY", stale: false };
    const candidate = { media_version_id: "video-current", media_kind: "VIDEO", integrity_status: "VERIFIED", is_stale: false } as const;
    render(<FrameBridgeControls frameBridge={frameBridge} currentCandidate={candidate} />);
    fireEvent.drop(screen.getByLabelText("拖放候选到本镜首帧"), { dataTransfer: { getData: () => JSON.stringify(candidate), dropEffect: "copy" } });
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenCalledWith("video-current", { position_mode: "FIRST_FRAME", role_hint: "FIRST_FRAME" }));
    await waitFor(() => expect(setFrameBridgeCurrentFrame).toHaveBeenCalledWith("prev", 1, { frameAnchorId: "new-tail-anchor" }));
  });
});

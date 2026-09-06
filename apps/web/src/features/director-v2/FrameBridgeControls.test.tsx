import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createFrameAnchor, createShotTransitionConstraint, setFrameBridgeCurrentFrameV2, setFrameBridgeSourceFrameV2 } from "../../generated/api";
import { FrameBridgeControls } from "./FrameBridgeControls";
import type { ShotStudio } from "../../generated/api";

vi.mock("../../generated/api", async (original) => {
  const actual = await original<typeof import("../../generated/api")>();
  return { ...actual, createFrameAnchor: vi.fn(), createShotTransitionConstraint: vi.fn(), inheritFrameBridgeV2: vi.fn(), setFrameBridgeCurrentFrameV2: vi.fn(), setFrameBridgeLockV2: vi.fn(), setFrameBridgeSourceFrameV2: vi.fn() };
});

const anchor = (id: string) => ({ anchor_id: id, inherited_from_anchor_id: null, source_media_version_id: "video-1", media_version_id: `image-${id}`, rel_path: null, role_hint: "LAST_FRAME", source: "EXTRACTED" as const, status: "EXPLICIT" as const, stale: false, stale_reason: null });
const boundary = (id: string, from: string, to: string) => ({ transition_id: id, boundary_revision: 1, from_shot_id: from, from_shot_code: from, to_shot_id: to, to_shot_code: to, enforcement: "ADVISORY", compatibility: "READY", stale: false, stale_reason: null, previous_end: anchor(`${id}-end`), current_start: null, inheritance_recommended: true, inheritance_reason: "同场连续镜且上一镜尾帧有效，建议继承后复核再锁定。" });

describe("FrameBridgeControls tail extraction", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(createFrameAnchor).mockResolvedValue({ frame_anchor: { id: "new-tail-anchor" } as never });
    vi.mocked(setFrameBridgeSourceFrameV2).mockResolvedValue({ frame_bridge: { boundary_revision: 2 } } as never);
    vi.mocked(setFrameBridgeCurrentFrameV2).mockResolvedValue({ frame_bridge: { boundary_revision: 2 } } as never);
    vi.mocked(createShotTransitionConstraint).mockResolvedValue({ constraint: { id: "created-transition" } as never });
  });

  it("extracts LAST_FRAME from the selected verified video and binds the next boundary", async () => {
    const frameBridge: ShotStudio["current_shot"]["frame_bridge"] = { previous: boundary("prev", "S001", "S002"), current_start: null, current_end: null, next: boundary("next", "S002", "S003"), compatibility: "READY", stale: false };
    render(<FrameBridgeControls frameBridge={frameBridge} currentCandidate={{ media_version_id: "video-current", media_kind: "VIDEO", integrity_status: "VERIFIED", is_stale: false }} currentShotId="S002" previousShotId="S001" nextShotId="S003" />);
    expect(screen.getByAltText("上一镜尾帧缩略图").getAttribute("src")).toContain("/thumbnail?size=small&frame=poster");
    expect(screen.getByAltText("上一镜尾帧缩略图").getAttribute("src")).not.toContain("/content");
    expect(screen.getByText("建议继承").closest("aside")?.textContent).toContain("同场连续镜");
    fireEvent.click(screen.getByRole("button", { name: "将当前候选设为尾帧" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenCalledWith("video-current", { position_mode: "LAST_FRAME", role_hint: "LAST_FRAME" }));
    await waitFor(() => expect(setFrameBridgeSourceFrameV2).toHaveBeenCalledWith("next", expect.objectContaining({ expected_boundary_revision: 1, frame_anchor_id: "new-tail-anchor", idempotency_key: expect.any(String) })));
  });

  it("keeps cross-scene inheritance explicit instead of presenting it as the default", () => {
    const previous = { ...boundary("prev", "S001", "S002"), inheritance_recommended: false, inheritance_reason: "相邻镜头不属于同一场景，不建议默认继承；仍可由导演显式选择。" };
    const frameBridge: ShotStudio["current_shot"]["frame_bridge"] = { previous, current_start: null, current_end: null, next: null, compatibility: "READY", stale: false };
    render(<FrameBridgeControls frameBridge={frameBridge} currentCandidate={null} currentShotId="S002" previousShotId="S001" />);
    expect(screen.getByText("需要导演判断").closest("aside")?.textContent).toContain("不属于同一场景");
    expect(screen.getByRole("button", { name: "继承上一镜尾帧" }).className).not.toContain("primary");
  });

  it("opens confirmation on image drop and uses the same revisioned command as the keyboard-accessible button", async () => {
    const frameBridge: ShotStudio["current_shot"]["frame_bridge"] = { previous: boundary("prev", "S001", "S002"), current_start: null, current_end: null, next: boundary("next", "S002", "S003"), compatibility: "READY", stale: false };
    const candidate = { media_version_id: "image-current", media_kind: "IMAGE", integrity_status: "VERIFIED", is_stale: false } as const;
    render(<FrameBridgeControls frameBridge={frameBridge} currentCandidate={candidate} currentShotId="S002" previousShotId="S001" nextShotId="S003" />);

    const dropTarget = screen.getByLabelText("拖放候选到本镜首帧");
    fireEvent.drop(dropTarget, { dataTransfer: { getData: () => JSON.stringify(candidate), dropEffect: "copy" } });
    expect(screen.getByRole("alertdialog")).not.toBeNull();
    expect(setFrameBridgeCurrentFrameV2).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    await waitFor(() => expect(setFrameBridgeCurrentFrameV2).toHaveBeenCalledWith("prev", expect.objectContaining({ expected_boundary_revision: 1, media_version_id: "image-current", idempotency_key: expect.any(String) })));

    vi.mocked(setFrameBridgeCurrentFrameV2).mockClear();
    const button = screen.getByRole("button", { name: "用当前候选帧" });
    expect(button.tagName).toBe("BUTTON");
    button.focus();
    fireEvent.click(button);
    await waitFor(() => expect(setFrameBridgeCurrentFrameV2).toHaveBeenCalledWith("prev", expect.objectContaining({ expected_boundary_revision: 1, media_version_id: "image-current", idempotency_key: expect.any(String) })));
  });

  it("extracts FIRST_FRAME when a verified video is dropped on the start target", async () => {
    const frameBridge: ShotStudio["current_shot"]["frame_bridge"] = { previous: boundary("prev", "S001", "S002"), current_start: null, current_end: null, next: boundary("next", "S002", "S003"), compatibility: "READY", stale: false };
    const candidate = { media_version_id: "video-current", media_kind: "VIDEO", integrity_status: "VERIFIED", is_stale: false } as const;
    render(<FrameBridgeControls frameBridge={frameBridge} currentCandidate={candidate} currentShotId="S002" previousShotId="S001" nextShotId="S003" />);
    fireEvent.drop(screen.getByLabelText("拖放候选到本镜首帧"), { dataTransfer: { getData: () => JSON.stringify(candidate), dropEffect: "copy" } });
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenCalledWith("video-current", { position_mode: "FIRST_FRAME", role_hint: "FIRST_FRAME" }));
    await waitFor(() => expect(setFrameBridgeCurrentFrameV2).toHaveBeenCalledWith("prev", expect.objectContaining({ expected_boundary_revision: 1, frame_anchor_id: "new-tail-anchor", idempotency_key: expect.any(String) })));
  });

  it("creates a missing adjacent boundary instead of calling a non-first shot the first shot", async () => {
    const frameBridge: ShotStudio["current_shot"]["frame_bridge"] = { previous: null, current_start: null, current_end: null, next: null, compatibility: "MISSING", stale: false };
    const onChanged = vi.fn();
    render(<FrameBridgeControls frameBridge={frameBridge} currentCandidate={null} currentShotId="S002" previousShotId="S001" nextShotId="S003" onChanged={onChanged} />);
    expect(screen.getByText("尚未创建上游 Frame Bridge")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "创建上游 Frame Bridge" }));
    await waitFor(() => expect(createShotTransitionConstraint).toHaveBeenCalledWith({
      from_shot_id: "S001",
      to_shot_id: "S002",
      constraint_type: "START_FROM_PREVIOUS_LAST",
      enforcement: "ADVISORY",
      note: "Director Desk Frame Bridge",
    }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });
});

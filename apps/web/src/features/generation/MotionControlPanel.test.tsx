import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMotionControl, listMotionControls } from "../../generated/api";
import { MotionControlPanel } from "./MotionControlPanel";

vi.mock("../../generated/api", () => ({ createMotionControl: vi.fn(), listMotionControls: vi.fn() }));

describe("MotionControlPanel", () => {
  beforeEach(() => {
    vi.mocked(listMotionControls).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(createMotionControl).mockReset().mockResolvedValue({ motion_control: { duplicate: false, id: "control-1", source_media_version_id: "source-1", control_media_version_id: "vector-1", profile_version_id: "profile-1", control_kind: "VECTOR", operation: "MOTION_BRUSH", subject_role: "subject", control_payload: {}, source_media: {}, control_media: {} } });
  });

  it("saves a local immutable control and refreshes the list", async () => {
    render(<MotionControlPanel sourceMediaVersionId="source-1" profileVersionId="profile-1" />);
    await waitFor(() => expect(listMotionControls).toHaveBeenCalledWith("source-1"));
    fireEvent.change(screen.getByLabelText("主体区域"), { target: { value: "face" } });
    fireEvent.click(screen.getByRole("button", { name: "保存不可变控制" }));
    await waitFor(() => expect(createMotionControl).toHaveBeenCalledWith("source-1", expect.objectContaining({ subject_role: "face", profile_version_id: "profile-1" })));
    expect((await screen.findByRole("status")).textContent).toContain("源媒体未修改");
  });

  it("draws a normalized motion trajectory without exposing editable JSON", async () => {
    render(<MotionControlPanel sourceMediaVersionId="source-1" profileVersionId="profile-1" />);
    fireEvent.change(screen.getByLabelText("控制类型"), { target: { value: "VECTOR" } });
    const canvas = screen.getByRole("application", { name: "运动轨迹绘制区域" });
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 200, bottom: 100, width: 200, height: 100, toJSON: () => ({}) });
    const pointer = (type: string, clientX: number, clientY: number) => {
      const event = new Event(type, { bubbles: true });
      Object.defineProperties(event, { clientX: { value: clientX }, clientY: { value: clientY }, pointerId: { value: 1 } });
      fireEvent(canvas, event);
    };
    pointer("pointerdown", 20, 20);
    pointer("pointermove", 160, 70);
    pointer("pointerup", 160, 70);
    expect(screen.getByText(/已记录 2 个轨迹点/)).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: /JSON/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "保存不可变控制" }));
    await waitFor(() => expect(createMotionControl).toHaveBeenCalledWith("source-1", expect.objectContaining({
      control_kind: "VECTOR",
      coordinate_space: "NORMALIZED",
      vector_path: [
        expect.objectContaining({ x: .1, y: .2 }),
        expect.objectContaining({ x: .8, y: .7 }),
      ],
    })));
  });
});

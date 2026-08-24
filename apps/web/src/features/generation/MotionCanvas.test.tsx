import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MotionCanvas } from "./MotionCanvas";

describe("MotionCanvas", () => {
  it("uses a supported derived thumbnail and lets keyboard users draw or undo normalized points", () => {
    const onChange = vi.fn();
    const view = render(<MotionCanvas mediaVersionId="media 1" value={[]} onChange={onChange} />);
    expect(screen.getByAltText("运动轨迹参考画面").getAttribute("src")).toContain("thumbnail?size=medium&frame=poster");
    const canvas = screen.getByRole("application");
    fireEvent.keyDown(canvas, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith([{ x: 0.52, y: 0.5, pressure: 1 }]);

    view.rerender(<MotionCanvas mediaVersionId="media 1" value={[{ x: 0.52, y: 0.5, pressure: 1 }]} onChange={onChange} />);
    fireEvent.keyDown(screen.getByRole("application"), { key: "Backspace" });
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});

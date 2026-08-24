import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TimeRangeSlider } from "./GenerationControlEditors";

describe("TimeRangeSlider", () => {
  it("supports range handles while preserving an ordered interval", () => {
    const onChange = vi.fn();
    render(<TimeRangeSlider startUs={1_000_000} endUs={2_000_000} onChange={onChange} />);

    fireEvent.change(screen.getByRole("slider", { name: "表演开始位置" }), { target: { value: "2" } });
    expect(onChange).toHaveBeenLastCalledWith({ start_us: 1_900_000, end_us: 2_000_000 });

    fireEvent.change(screen.getByRole("slider", { name: "表演结束位置" }), { target: { value: "0.5" } });
    expect(onChange).toHaveBeenLastCalledWith({ start_us: 1_000_000, end_us: 1_100_000 });
  });

  it("keeps exact numeric inputs for precise editing", () => {
    render(<TimeRangeSlider startUs={500_000} endUs={2_500_000} onChange={vi.fn()} />);
    expect(screen.getByRole("spinbutton", { name: "精确开始（秒）" })).toBeTruthy();
    expect(screen.getByRole("spinbutton", { name: "精确结束（秒）" })).toBeTruthy();
    expect(screen.getByText("0.5 秒")).toBeTruthy();
    expect(screen.getByText("2.5 秒")).toBeTruthy();
  });
});

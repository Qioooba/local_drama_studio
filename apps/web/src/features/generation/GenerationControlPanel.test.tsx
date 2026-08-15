import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GenerationControlPanel } from "./GenerationControlPanel";

describe("GenerationControlPanel multimodal contracts", () => {
  it("exposes immutable driving/reference bindings with semantic role and weight", () => {
    const onReferenceBindingsChange = vi.fn();
    render(<GenerationControlPanel timedDirections="[]" performanceBindings="[]" referenceBindings="[]" motionMasks="[]" onTimedDirectionsChange={vi.fn()} onPerformanceBindingsChange={vi.fn()} onReferenceBindingsChange={onReferenceBindingsChange} onMotionMasksChange={vi.fn()} />);
    const field = screen.getAllByRole("textbox")[2];
    fireEvent.change(field, { target: { value: '[{"role":"DRIVING_VIDEO","media_version_id":"local-video","ordinal":0,"weight":0.7}]' } });
    expect(onReferenceBindingsChange).toHaveBeenCalledWith('[{"role":"DRIVING_VIDEO","media_version_id":"local-video","ordinal":0,"weight":0.7}]');
    expect(screen.getByText(/数量、顺序、媒体类型和 weight/)).toBeTruthy();
  });
});

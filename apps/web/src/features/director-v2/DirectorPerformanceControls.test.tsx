import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { EmotionPicker, EyeLineControl, MicroExpressionSelect } from "./DirectorPerformanceControls";

function Harness() {
  const [emotion, setEmotion] = useState<string | null>(null);
  const [intensity, setIntensity] = useState(.5);
  const [face, setFace] = useState<string | null>(null);
  const [eye, setEye] = useState<string | null>(null);
  return <>
    <EmotionPicker value={emotion} intensity={intensity} onChange={(next) => { if ("emotion" in next) setEmotion(next.emotion ?? null); if (typeof next.intensity === "number") setIntensity(next.intensity); }} />
    <MicroExpressionSelect value={face} onChange={setFace} />
    <EyeLineControl value={eye} onChange={setEye} />
    <output data-testid="performance-value">{JSON.stringify({ emotion, intensity, face, eye })}</output>
  </>;
}

describe("Director performance controls", () => {
  it("keeps common performance directions structured while retaining custom text", () => {
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "决绝" }));
    fireEvent.change(screen.getByRole("slider", { name: "表演强度" }), { target: { value: "0.8" } });
    fireEvent.click(screen.getByRole("button", { name: "挑眉" }));
    fireEvent.click(screen.getByRole("button", { name: "肌肉紧绷" }));
    fireEvent.click(screen.getByRole("button", { name: "看向画左" }));

    expect(screen.getByTestId("performance-value").textContent).toBe(JSON.stringify({
      emotion: "决绝", intensity: .8, face: "挑眉；肌肉紧绷", eye: "LOOK_LEFT",
    }));

    fireEvent.change(screen.getByLabelText("自定义情绪"), { target: { value: "强装镇定" } });
    fireEvent.change(screen.getByLabelText("自定义微表情"), { target: { value: "鼻翼轻颤" } });
    fireEvent.change(screen.getByLabelText("自定义视线"), { target: { value: "越过肩膀看向门外" } });
    expect(screen.getByTestId("performance-value").textContent).toContain("强装镇定");
    expect(screen.getByTestId("performance-value").textContent).toContain("挑眉；肌肉紧绷；鼻翼轻颤");
    expect(screen.getByTestId("performance-value").textContent).toContain("越过肩膀看向门外");
  });
});

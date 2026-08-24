import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StagingBoard, suggestEyeLineFromStaging, type StagingBoardValue } from "./StagingBoard";

describe("StagingBoard creator controls", () => {
  it("selects shot-bound character assets and keeps coordinates in advanced disclosure", () => {
    const onChange = vi.fn();
    render(<StagingBoard participantOptions={[
      { id: "character-ning", label: "阿宁 · CHARACTER_ANING" },
      { id: "character-zhou", label: "周野 · CHARACTER_ZHOUYE" },
    ]} onChange={onChange} />);

    expect(screen.queryByLabelText("角色名")).toBeNull();
    fireEvent.change(screen.getByLabelText("角色 A 资产"), { target: { value: "character-ning" } });
    const next = onChange.mock.calls.at(-1)?.[0] as StagingBoardValue;
    expect(next.participants[0]).toMatchObject({ asset_id: "character-ning", label: "阿宁 · CHARACTER_ANING" });
    expect(screen.getByRole("button", { name: /阿宁.*运动终点/ })).toBeTruthy();

    fireEvent.click(screen.getByText("高级坐标"));
    expect(screen.getByLabelText("运动终点 X")).toBeTruthy();
    expect(screen.getByLabelText("运动终点 Y")).toBeTruthy();
  });

  it("explains the recovery path when the shot has no bound characters", () => {
    render(<StagingBoard />);
    expect(screen.getByText(/请先在“资产”标签绑定出场角色/)).toBeTruthy();
  });

  it("derives an opt-in eye-line suggestion from camera-relative blocking", () => {
    const staging: StagingBoardValue = {
      schema_version: "staging-board.v1",
      scene: { label: "室内", width_m: 8, depth_m: 5 },
      participants: [
        { id: "A", label: "阿宁", position: { x: 34, y: 31 }, facing_degrees: 12, movement_target: { x: 34, y: 31 } },
        { id: "B", label: "周野", position: { x: 65, y: 34 }, facing_degrees: 192, movement_target: { x: 65, y: 34 } },
      ],
      camera: { position: { x: 49, y: 55 }, target: { x: 34, y: 31 }, movement_target: { x: 49, y: 55 }, movement: "STATIC", intensity: 0 },
      axis: { start: { x: 20, y: 32 }, end: { x: 80, y: 32 } },
    };
    expect(suggestEyeLineFromStaging(staging)).toMatchObject({
      value: "LOOK_RIGHT",
      subject_label: "阿宁",
      target_label: "周野",
    });
    staging.participants[0].facing_degrees = 180;
    expect(suggestEyeLineFromStaging(staging)).toBeNull();
  });
});

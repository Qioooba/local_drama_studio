import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GenerationExperimentPanel } from "./GenerationExperimentPanel";

vi.mock("../../generated/api", () => ({
  cancelRemainingGenerationExperiment: vi.fn(),
  confirmGenerationExperiment: vi.fn(),
  createGenerationExperiment: vi.fn(),
  estimateGenerationExperiment: vi.fn(),
  expandGenerationExperiment: vi.fn(),
  getGenerationExperiment: vi.fn(),
}));

describe("GenerationExperimentPanel", () => {
  it("explains and disables experiment creation until an intent is locked", () => {
    render(<GenerationExperimentPanel intentId={null} />);
    expect(screen.getByText(/锁定生成意图后才能保存实验计划/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存实验计划" })).toHaveProperty("disabled", true);
    expect(screen.queryByText(/候选值（逗号分隔）/)).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Seed 42" })).toBeTruthy();
  });
});

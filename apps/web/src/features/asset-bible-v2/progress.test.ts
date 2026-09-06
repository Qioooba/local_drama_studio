import { describe, expect, it } from "vitest";
import { normalizeGenerationPercent } from "./progress";

describe("normalizeGenerationPercent", () => {
  it("renders fractional worker progress as an honest percentage", () => {
    expect(normalizeGenerationPercent(0.8)).toBe(80);
    expect(normalizeGenerationPercent(80)).toBe(80);
  });

  it("clamps invalid or out-of-range values", () => {
    expect(normalizeGenerationPercent("invalid")).toBe(0);
    expect(normalizeGenerationPercent(-3)).toBe(0);
    expect(normalizeGenerationPercent(120)).toBe(100);
  });
});

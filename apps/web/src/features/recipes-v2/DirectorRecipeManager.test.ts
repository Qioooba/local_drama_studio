import { describe, expect, it } from "vitest";
import { findForbiddenRecipePath, validateRecipeDuration } from "./DirectorRecipeManager";

describe("Director Recipe declarative safety guard", () => {
  it("accepts declarative capability and policy references", () => {
    expect(findForbiddenRecipePath({ generation: { video: { capability: "VIDEO_I2V" } }, qc_policy_ref: { policy_version_id: "v1" } })).toBeNull();
  });

  it("rejects executable keys at any nesting level", () => {
    expect(findForbiddenRecipePath({ generation: { video: { executor: "python" } } })).toBe("recipe.generation.video.executor");
    expect(findForbiddenRecipePath({ steps: [{ command: "rm" }] })).toBe("recipe.steps[0].command");
  });
});

describe("Director Recipe duration validation", () => {
  it("explains the 250ms step instead of relying on silent native validation", () => {
    expect(validateRecipeDuration(3200)).toContain("250 毫秒为步长");
    expect(validateRecipeDuration(3250)).toBeNull();
    expect(validateRecipeDuration(0)).toContain("250–120000");
  });
});

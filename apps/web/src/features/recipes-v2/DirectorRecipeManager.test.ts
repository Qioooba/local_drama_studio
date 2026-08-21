import { describe, expect, it } from "vitest";
import { findForbiddenRecipePath } from "./DirectorRecipeManager";

describe("Director Recipe declarative safety guard", () => {
  it("accepts declarative capability and policy references", () => {
    expect(findForbiddenRecipePath({ generation: { video: { capability: "VIDEO_I2V" } }, qc_policy_ref: { policy_version_id: "v1" } })).toBeNull();
  });

  it("rejects executable keys at any nesting level", () => {
    expect(findForbiddenRecipePath({ generation: { video: { executor: "python" } } })).toBe("recipe.generation.video.executor");
    expect(findForbiddenRecipePath({ steps: [{ command: "rm" }] })).toBe("recipe.steps[0].command");
  });
});

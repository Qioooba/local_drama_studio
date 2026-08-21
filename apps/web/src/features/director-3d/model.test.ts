import { describe, expect, it } from "vitest";
import { buildDirector3DOutput, cloneDirector3DValue, DEFAULT_DIRECTOR_3D_VALUE } from "./model";

describe("Director 3D structured output", () => {
  it("produces a V3-compatible patch with FOV and two-person blocking", () => {
    const output = buildDirector3DOutput(DEFAULT_DIRECTOR_3D_VALUE);
    expect(output.blocking_summary).toContain("角色 A");
    expect(output.blocking_summary).toContain("角色 B");
    expect(output.prompt_context).toContain("48 degree field of view");
    expect(output.director_intent_patch.camera_plan.prompt_text).toBe(output.prompt_context);
    expect(output.staging_3d.schema_version).toBe("director-staging-3d.v1");
  });

  it("returns a detached staging snapshot suitable for persistence", () => {
    const value = cloneDirector3DValue(DEFAULT_DIRECTOR_3D_VALUE);
    const output = buildDirector3DOutput(value);
    value.participants[0].position.x = 99;
    expect(output.staging_3d.participants[0].position.x).not.toBe(99);
  });
});

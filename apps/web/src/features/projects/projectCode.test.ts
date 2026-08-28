import { describe, expect, it } from "vitest";
import { generateProjectCode } from "./projectCode";

describe("generateProjectCode", () => {
  it("romanizes Chinese titles instead of hiding them behind a hash", () => {
    expect(generateProjectCode("逆袭神豪")).toBe("ni_xi_shen_hao");
    expect(generateProjectCode("重返十八岁")).toBe("chong_fan_shi_ba_sui");
  });

  it("preserves English words and supports mixed titles", () => {
    expect(generateProjectCode("The Last Take")).toBe("the_last_take");
    expect(generateProjectCode("The Last 逆袭 2049")).toBe("the_last_ni_xi_2049");
  });

  it("always produces a valid project-code shape", () => {
    expect(generateProjectCode("2049：归来")).toBe("project_2049_gui_lai");
    expect(generateProjectCode("🎬")).toMatch(/^project_[a-z0-9]+$/);
    expect(generateProjectCode("   ")).toBe("");
    expect(generateProjectCode("长".repeat(100))).toMatch(/^[a-z][a-z0-9_]{1,63}$/);
  });
});

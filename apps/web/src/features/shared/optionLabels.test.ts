import { describe, expect, it } from "vitest";
import { ACCELERATION_LABELS, JOB_TYPE_LABELS, MEDIA_KIND_LABELS, MEDIA_STAGE_LABELS, PRODUCTION_TIER_LABELS, STATUS_LABELS, optionLabel, statusLabel, userFacingLabel } from "./optionLabels";

describe("下拉框中文标签", () => {
  it("将后台媒体码值转换为中文显示文本", () => {
    expect(optionLabel(MEDIA_KIND_LABELS, "VIDEO")).toBe("视频");
    expect(optionLabel(MEDIA_STAGE_LABELS, "KEYFRAME")).toBe("关键帧");
  });

  it("覆盖常见后台状态且保留未知扩展值", () => {
    expect(optionLabel(STATUS_LABELS, "PUBLISHED")).toBe("已发布");
    expect(optionLabel(STATUS_LABELS, "FUTURE_STATUS")).toBe("FUTURE_STATUS");
  });

  it("运行参数显示中文但仍使用后台枚举码", () => {
    expect(optionLabel(PRODUCTION_TIER_LABELS, "BALANCED")).toBe("质量与速度平衡");
    expect(optionLabel(ACCELERATION_LABELS, "TURBO_LORA")).toBe("启用 Turbo LoRA 加速");
  });

  it("普通界面不泄露未知后台枚举", () => {
    expect(statusLabel("FUTURE_STATUS")).toBe("状态未知");
    expect(userFacingLabel(JOB_TYPE_LABELS, "GENERATION_VARIANT")).toBe("生成镜头候选");
    expect(userFacingLabel(JOB_TYPE_LABELS, "FUTURE_JOB")).toBe("未识别类型");
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RecommendationFacts } from "./RecommendationFacts";
import type { GenerationResolution } from "./types";

function resolution(status: "AVAILABLE" | "UNKNOWN"): GenerationResolution {
  return {
    capability: "VIDEO_I2V", profile_version_id: "uuid-must-not-be-rendered", source: "AUTO",
    native_support: true, fallback_support: false, warnings: [], estimated_resources: { vram_gb: 12 },
    blocked_reason: null, preference: null,
    profile: { code: "wan-i2v", title: "Wan Image to Video", version_no: 3, capability: "VIDEO_I2V", status: "PUBLISHED", resources: { vram_gb: 12 } },
    recommendation: {
      selection_reason: "AUTO_NEWEST_PUBLISHED_EXACT_CAPABILITY",
      facts: { capability_exact_match: true, published: true, native_support: true, resources: { vram_gb: 12 } },
      local_success_rate: {
        status, reason: status === "UNKNOWN" ? "INSUFFICIENT_SAME_DIMENSION_SAMPLES" : null,
        value: status === "AVAILABLE" ? 0.75 : null, successful_sample_count: status === "AVAILABLE" ? 3 : 1,
        terminal_sample_count: status === "AVAILABLE" ? 4 : 1, minimum_sample_count: 3,
        dimensions: { width: 1280, height: 720, duration_seconds: 4, steps: 20, gpu_class: "GPU_H3_HEAVY" },
        evidence: { source: "LOCAL_TERMINAL_JOB_ATTEMPTS", candidate_count: 4, candidate_limit: 100, gpu_hardware_model_known: false },
      },
    },
  };
}

describe("RecommendationFacts", () => {
  it("shows semantic profile identity and authoritative same-dimension rate without UUID", () => {
    const { container } = render(<RecommendationFacts resolution={resolution("AVAILABLE")} />);
    expect(screen.getByText("Wan Image to Video · 第 3 版")).toBeTruthy();
    expect(screen.getByText(/图片生成视频.*用途匹配/)).toBeTruthy();
    expect(screen.getByText("最近同维度成功率 75%")).toBeTruthy();
    expect(screen.getByText("成功 3 / 终态 4")).toBeTruthy();
    expect(container.textContent).not.toContain("uuid-must-not-be-rendered");
  });

  it("states unknown instead of inventing a rate when the cohort is too small", () => {
    render(<RecommendationFacts resolution={resolution("UNKNOWN")} />);
    expect(screen.getByText("最近成功率未知")).toBeTruthy();
    expect(screen.getByText(/同维度终态样本不足.*1\/3/)).toBeTruthy();
  });
});

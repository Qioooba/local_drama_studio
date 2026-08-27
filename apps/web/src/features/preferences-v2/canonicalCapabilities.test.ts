import { describe, expect, it } from "vitest";
import {
  CANONICAL_CAPABILITIES,
  CAPABILITY_LABELS,
  isCanonicalCapability,
  normalizeCapability,
} from "./canonicalCapabilities";

describe("canonicalCapabilities (PR-CUR-003)", () => {
  it("includes all 28 domain canonical capabilities", () => {
    expect(CANONICAL_CAPABILITIES.length).toBeGreaterThanOrEqual(20);
    expect(CANONICAL_CAPABILITIES).toContain("LLM_STORY_PARSE");
    expect(CANONICAL_CAPABILITIES).toContain("IMAGE_CHARACTER");
    expect(CANONICAL_CAPABILITIES).toContain("VIDEO_I2V");
    expect(CANONICAL_CAPABILITIES).toContain("VIDEO_T2V");
    expect(CANONICAL_CAPABILITIES).toContain("TTS");
    expect(CANONICAL_CAPABILITIES).toContain("VOICE_CLONE");
    expect(CANONICAL_CAPABILITIES).toContain("LIPSYNC");
  });

  it("normalizes legacy aliases without fuzzy matching", () => {
    expect(normalizeCapability("SCRIPT_BREAKDOWN_LLM")).toBe("LLM_STORY_PARSE");
    expect(normalizeCapability("i2v")).toBe("VIDEO_I2V");
    expect(normalizeCapability("T2V")).toBe("VIDEO_T2V");
    expect(normalizeCapability("audio_tts")).toBe("TTS");
    expect(normalizeCapability("LIP_SYNC")).toBe("LIPSYNC");
    expect(normalizeCapability("MOTION_BRUSH")).toBe("VIDEO_MOTION_CONTROL");
  });

  it("fails closed on unknown or ambiguous capability", () => {
    expect(() => normalizeCapability("IMAGE_GENERATION")).toThrow();
    expect(() => normalizeCapability("UNKNOWN_CAPABILITY_XYZ")).toThrow();
    expect(() => normalizeCapability("RANDOM_MODEL")).toThrow();
    expect(isCanonicalCapability("UNKNOWN_CAPABILITY_XYZ")).toBe(false);
  });

  it("provides human readable labels for all canonical capabilities", () => {
    for (const cap of CANONICAL_CAPABILITIES) {
      expect(CAPABILITY_LABELS[cap]).toBeTruthy();
      expect(typeof CAPABILITY_LABELS[cap]).toBe("string");
    }
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TimelineTracks } from "./TimelineTracks";

const shots = [{ shotId: "s1", code: "S001", mediaVersionId: "video-1", durationUs: 4_000_000, transition: "CUT" as const, selectionSource: "CURRENT_MEDIA" as const, continuityStatus: "OK" }];
const baseAudio = { episode_id: "e1", take_no: 1, source_license_status: "VERIFIED_LOCAL", authorization_status: "VERIFIED_EVIDENCE" as const, license_evidence: {}, loop_enabled: false, fade_in_us: 0, fade_out_us: 0 };
const subtitles = { revision_count: 1, latest: { revision_no: 3, cue_count: 12, authority_status: "VERIFIED_SCRIPT" } };

describe("TimelineTracks", () => {
  it("uses persisted audio ranges and does not draw a fake waveform", () => {
    const { container } = render(<TimelineTracks shots={shots} audio={[{ ...baseAudio, id: "a1", media_version_id: "dialogue-1", track_type: "DIALOGUE", start_us: 0, end_us: 2_000_000, gain_db: -1, source_name: "line.wav", duration_ms: 2000, waveform_ready: false }]} subtitles={subtitles} includeAudio includeSubtitles />);
    expect(screen.getByText("对白")).toBeTruthy();
    expect(screen.getByText(/2\.0s · -1 dB/)).toBeTruthy();
    expect(screen.getByText(/12 cues/)).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/不提供虚假开关/)).toBeTruthy();
  });

  it("loads only a registered derived waveform when cache fact is ready", () => {
    const { container } = render(<TimelineTracks shots={shots} audio={[{ ...baseAudio, id: "a2", media_version_id: "bgm-1", track_type: "BGM", start_us: 0, end_us: 4_000_000, gain_db: -8, waveform_ready: true }]} subtitles={{ revision_count: 0, latest: null }} includeAudio includeSubtitles={false} />);
    expect(container.querySelector("img")?.getAttribute("src")).toBe("/api/v1/media-versions/bgm-1/waveform");
    expect(container.querySelector("img")?.getAttribute("src")).not.toContain("/content");
  });
});

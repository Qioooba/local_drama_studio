import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AudioTrackPanel } from "./AudioTrackPanel";

describe("AudioTrackPanel", () => {
  it("renders a non-preloading local player and truthful legacy authorization", () => {
    const { container } = render(<AudioTrackPanel bindings={[{ id: "binding", episode_id: "episode", media_version_id: "audio/version", track_type: "MUSIC", start_us: 0, end_us: 2_000_000, gain_db: -3, source_license_status: "VERIFIED_LOCAL", authorization_status: "LEGACY_INCOMPLETE", license_evidence: {}, loop_enabled: true, fade_in_us: 100_000, fade_out_us: 200_000 }]} />);
    expect(screen.getByText("遗留授权证据不完整")).toBeTruthy();
    const audio = container.querySelector("audio");
    expect(audio?.getAttribute("preload")).toBe("none");
    expect(audio?.hasAttribute("loop")).toBe(true);
    expect(audio?.getAttribute("src")).toContain("audio%2Fversion/content");
  });
});

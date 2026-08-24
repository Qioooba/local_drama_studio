import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { unbindEpisodeAudio } from "../../generated/api";
import { AudioTrackPanel } from "./AudioTrackPanel";

vi.mock("../../generated/api", () => ({
  unbindEpisodeAudio: vi.fn(),
}));

describe("AudioTrackPanel", () => {
  it("renders a non-preloading local player and truthful legacy authorization", () => {
    const { container } = render(<AudioTrackPanel projectId="project" episodeId="episode" onBound={() => undefined} bindings={[{ id: "binding", episode_id: "episode", media_version_id: "audio/version", track_type: "MUSIC", start_us: 0, end_us: 2_000_000, gain_db: -3, source_license_status: "VERIFIED_LOCAL", authorization_status: "LEGACY_INCOMPLETE", license_evidence: {}, loop_enabled: true, fade_in_us: 100_000, fade_out_us: 200_000 }]} />);
    expect(screen.getByText("遗留授权证据不完整")).toBeTruthy();
    const audio = container.querySelector("audio");
    expect(audio?.getAttribute("preload")).toBe("none");
    expect(audio?.hasAttribute("loop")).toBe(true);
    expect(audio?.getAttribute("src")).toContain("audio%2Fversion/content");
  });

  it("opens confirmation dialog on unbind click, allows cancel, and calls unbind API on confirm", async () => {
    vi.mocked(unbindEpisodeAudio).mockResolvedValue({ result: { id: "binding-1", status: "UNBOUND", media_version_id: "m-123" } });
    const onBound = vi.fn();
    render(<AudioTrackPanel projectId="project" episodeId="episode" onBound={onBound} bindings={[{ id: "binding-1", episode_id: "episode", media_version_id: "m-123456789012", track_type: "BGM", start_us: 0, end_us: 5_000_000, gain_db: -6, source_license_status: "VERIFIED_LOCAL", authorization_status: "VERIFIED_EVIDENCE", license_evidence: {}, loop_enabled: true, fade_in_us: 500_000, fade_out_us: 800_000 }]} />);

    // Click "解绑" button
    fireEvent.click(screen.getByRole("button", { name: "解绑" }));
    expect(screen.getByText(/确认解绑 BGM\/音乐？/)).toBeTruthy();
    expect(screen.getByText(/只移除本集时间线与媒体版本 m-1234567890… 的绑定。/)).toBeTruthy();

    // Click "取消"
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByText(/确认解绑 BGM\/音乐？/)).toBeNull();
    expect(unbindEpisodeAudio).not.toHaveBeenCalled();

    // Click "解绑" again and click "确认解绑"
    fireEvent.click(screen.getByRole("button", { name: "解绑" }));
    fireEvent.click(screen.getByRole("button", { name: "确认解绑" }));
    await waitFor(() => expect(unbindEpisodeAudio).toHaveBeenCalledWith("binding-1"));
    expect(onBound).toHaveBeenCalled();
  });
});


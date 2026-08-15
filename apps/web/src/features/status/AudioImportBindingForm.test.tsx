import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { bindEpisodeAudio, importMedia } from "../../generated/api";
import { AudioImportBindingForm } from "./AudioImportBindingForm";

vi.mock("../../generated/api", () => ({
  importMedia: vi.fn(),
  bindEpisodeAudio: vi.fn(),
}));

describe("AudioImportBindingForm", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("imports real local audio then binds explicit authorization and loop policy", async () => {
    vi.mocked(importMedia).mockResolvedValue({ media: { media_version_id: "media-v1", media_asset_id: "asset", sha256: "a".repeat(64), rel_path: "audio.wav" } });
    vi.mocked(bindEpisodeAudio).mockResolvedValue({ audio_binding: { id: "binding-123456789", episode_id: "episode", media_version_id: "media-v1", track_type: "MUSIC", start_us: 0, end_us: 5_000_000, gain_db: -2, source_license_status: "USER_OWNED", authorization_status: "VERIFIED_EVIDENCE", license_evidence: {}, loop_enabled: true, fade_in_us: 100_000, fade_out_us: 200_000 } });
    const onBound = vi.fn();
    render(<AudioImportBindingForm projectId="project" episodeId="episode" onBound={onBound} />);
    fireEvent.click(screen.getByRole("button", { name: "导入并绑定本地音频" }));
    fireEvent.change(screen.getByLabelText("本地音频绝对路径"), { target: { value: "F:\\audio\\music.wav" } });
    fireEvent.change(screen.getByLabelText("项目内授权证据相对路径"), { target: { value: "00_admin/licenses/music.json" } });
    fireEvent.change(screen.getByLabelText("轨道"), { target: { value: "MUSIC" } });
    fireEvent.change(screen.getByLabelText("授权类型"), { target: { value: "USER_OWNED" } });
    fireEvent.change(screen.getByLabelText("结束（秒）"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Gain（dB）"), { target: { value: "-2" } });
    fireEvent.change(screen.getByLabelText("淡入（ms）"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("淡出（ms）"), { target: { value: "200" } });
    fireEvent.click(screen.getByLabelText("循环源音频以覆盖绑定范围"));
    fireEvent.click(screen.getByRole("button", { name: "校验、导入并绑定" }));
    await waitFor(() => expect(onBound).toHaveBeenCalledTimes(1));
    expect(importMedia).toHaveBeenCalledWith(expect.objectContaining({ project_id: "project", source_path: "F:\\audio\\music.wav", media_kind: "AUDIO" }));
    expect(bindEpisodeAudio).toHaveBeenCalledWith("episode", expect.objectContaining({ media_version_id: "media-v1", track_type: "MUSIC", end_us: 5_000_000, loop_enabled: true, fade_in_us: 100_000, fade_out_us: 200_000, license_evidence_path_rel: "00_admin/licenses/music.json" }));
    expect(screen.getByRole("status").textContent).toContain("授权证据已验证");
  });

  it("blocks incomplete input before any API mutation", () => {
    render(<AudioImportBindingForm projectId="project" episodeId="episode" onBound={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "导入并绑定本地音频" }));
    fireEvent.click(screen.getByRole("button", { name: "校验、导入并绑定" }));
    expect(importMedia).not.toHaveBeenCalled();
  });
});

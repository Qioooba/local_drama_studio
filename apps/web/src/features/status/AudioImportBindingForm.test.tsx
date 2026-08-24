import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { bindEpisodeAudio, listProjectLocalResources } from "../../generated/api";
import { uploadProjectMediaFile } from "../media-picker/mediaPickerClient";
import { AudioImportBindingForm } from "./AudioImportBindingForm";

vi.mock("../../generated/api", () => ({
  bindEpisodeAudio: vi.fn(),
  listProjectLocalResources: vi.fn(),
}));
vi.mock("../media-picker/mediaPickerClient", () => ({ uploadProjectMediaFile: vi.fn() }));

describe("AudioImportBindingForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProjectLocalResources).mockResolvedValue({ project_id: "project", kind: "LICENSE_EVIDENCE", items: [{ path_rel: "00_admin/licenses/music.json", name: "music.json", suffix: ".json", byte_size: 96 }], truncated: false, limit: 200, read_only: true, runtime_contacted: false, network_contacted: false, mutated: false });
  });

  const renderForm = (onBound: () => void) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}><AudioImportBindingForm projectId="project" episodeId="episode" onBound={onBound} /></QueryClientProvider>);
  };

  it("imports real local audio then binds explicit authorization and loop policy", async () => {
    vi.mocked(uploadProjectMediaFile).mockResolvedValue("media-v1");
    vi.mocked(bindEpisodeAudio).mockResolvedValue({ audio_binding: { id: "binding-123456789", episode_id: "episode", media_version_id: "media-v1", track_type: "MUSIC", start_us: 0, end_us: 5_000_000, gain_db: -2, source_license_status: "USER_OWNED", authorization_status: "VERIFIED_EVIDENCE", license_evidence: {}, loop_enabled: true, fade_in_us: 100_000, fade_out_us: 200_000 } });
    const onBound = vi.fn();
    renderForm(onBound);
    fireEvent.click(screen.getByRole("button", { name: "导入并绑定本地音频" }));
    const file = new File(["audio"], "music.wav", { type: "audio/wav" });
    fireEvent.change(screen.getByLabelText("本地音频文件"), { target: { files: [file] } });
    await screen.findByRole("option", { name: /music\.json/ });
    fireEvent.change(screen.getByLabelText("项目内授权证据"), { target: { value: "00_admin/licenses/music.json" } });
    fireEvent.change(screen.getByLabelText("轨道"), { target: { value: "MUSIC" } });
    fireEvent.change(screen.getByLabelText("授权类型"), { target: { value: "USER_OWNED" } });
    fireEvent.change(screen.getByLabelText("结束（秒）"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Gain（dB）"), { target: { value: "-2" } });
    fireEvent.change(screen.getByLabelText("淡入（ms）"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("淡出（ms）"), { target: { value: "200" } });
    fireEvent.click(screen.getByLabelText("循环源音频以覆盖绑定范围"));
    fireEvent.click(screen.getByRole("button", { name: "校验、导入并绑定" }));
    await waitFor(() => expect(onBound).toHaveBeenCalledTimes(1));
    expect(uploadProjectMediaFile).toHaveBeenCalledWith("project", file);
    expect(bindEpisodeAudio).toHaveBeenCalledWith("episode", expect.objectContaining({ media_version_id: "media-v1", track_type: "MUSIC", end_us: 5_000_000, loop_enabled: true, fade_in_us: 100_000, fade_out_us: 200_000, license_evidence_path_rel: "00_admin/licenses/music.json" }));
    expect(screen.getByRole("status").textContent).toContain("授权证据已验证");
  });

  it("blocks incomplete input before any API mutation", () => {
    renderForm(() => undefined);
    fireEvent.click(screen.getByRole("button", { name: "导入并绑定本地音频" }));
    fireEvent.click(screen.getByRole("button", { name: "校验、导入并绑定" }));
    expect(uploadProjectMediaFile).not.toHaveBeenCalled();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listCharacterVoiceBindings, listEpisodeAudioBindings } from "../../generated/api";
import { DirectorSoundInspector } from "./DirectorSoundInspector";

vi.mock("../../generated/api", () => ({ listCharacterVoiceBindings: vi.fn(), listEpisodeAudioBindings: vi.fn() }));

function renderInspector(assets: Array<Record<string, unknown>> = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><DirectorSoundInspector projectId="project-1" episodeId="episode-1" shotCode="S012" dialogue="阿宁：快走。" assets={assets} /></MemoryRouter></QueryClientProvider>);
}

describe("DirectorSoundInspector", () => {
  beforeEach(() => {
    vi.mocked(listCharacterVoiceBindings).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(listEpisodeAudioBindings).mockReset().mockResolvedValue({ items: [] });
  });

  it("maps shot characters to real project voice bindings and episode audio facts", async () => {
    vi.mocked(listCharacterVoiceBindings).mockResolvedValue({ items: [{ id: "voice-binding-uuid", project_id: "project-1", character_asset_id: "character-uuid", voice_profile_version_id: "voice-version-uuid", created_at: "now", created_by: "test", character: { id: "character-uuid", code: "CHAR_ANING", name: "阿宁", kind: "CHARACTER", status: "ACTIVE" }, voice: { id: "voice-version-uuid", code: "VOICE_ANING", title: "阿宁青年声线", voice_ref: "local", status: "PUBLISHED" } }] });
    vi.mocked(listEpisodeAudioBindings).mockResolvedValue({ items: [{ id: "audio-binding-uuid", episode_id: "episode-1", media_version_id: "media-version-uuid", track_type: "BGM", start_us: 1_000_000, end_us: 5_000_000, gain_db: -6, source_license_status: "USER_OWNED", authorization_status: "VERIFIED_EVIDENCE", license_evidence: {}, loop_enabled: true, fade_in_us: 100_000, fade_out_us: 200_000 }] });
    const { container } = renderInspector([{ id: "character-uuid", kind: "CHARACTER", code: "CHAR_ANING", name: "阿宁", role_in_shot: "主角" }]);

    expect(await screen.findByText("阿宁青年声线 · VOICE_ANING")).not.toBeNull();
    expect(screen.getByText("BGM / 音乐")).not.toBeNull();
    expect(screen.getByText("1.00s–5.00s")).not.toBeNull();
    expect(screen.getByText("授权证据已验证")).not.toBeNull();
    expect(screen.getByRole("link", { name: "打开声音工作区" }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-1/audio");
    expect(container.querySelector("audio,video,img")).toBeNull();
    expect(container.textContent).not.toContain("media-version-uuid");
  });

  it("states empty authority without inventing shot-scoped audio actions", async () => {
    renderInspector();
    expect(await screen.findByText(/本镜尚未绑定角色/)).not.toBeNull();
    expect(screen.getByText(/本集还没有持久化/)).not.toBeNull();
    expect(screen.getByText(/当前没有镜头级声音写入命令/)).not.toBeNull();
  });
});

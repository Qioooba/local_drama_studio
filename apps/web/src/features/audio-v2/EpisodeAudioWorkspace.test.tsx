import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getEpisodeAudioWorkspaceV2, removeEpisodeAudioTrackV2, updateEpisodeAudioTrackV2 } from "../../generated/api";
import { EpisodeAudioWorkspace } from "./EpisodeAudioWorkspace";

vi.mock("../../generated/api", () => ({
  createEpisodeAudioTrackV2: vi.fn(),
  getEpisodeAudioWorkspaceV2: vi.fn(),
  removeEpisodeAudioTrackV2: vi.fn(),
  updateEpisodeAudioTrackV2: vi.fn(),
}));
vi.mock("../media-picker/mediaPickerClient", () => ({ uploadProjectMediaFile: vi.fn() }));
vi.mock("../shared/ProjectLocalResourceSelect", () => ({ ProjectLocalResourceSelect: () => <div>授权证据选择</div> }));

const workspace = {
  episode_id: "episode-1", project_id: "project-1", episode_code: "EP01", episode_title: "第一集",
  mix_revision: 4, mix_status: "DRAFT" as const,
  dialogue_references: [{ line_id: "line-1", line_code: "DL001", shot_id: "shot-1", shot_code: "S001", speaker: "阿宁", text: "我们走。", text_revision_id: "text-1", text_revision_no: 2, selected_tts_candidate_id: "tts-1", selected_media_version_id: "audio-dialogue", selected_media_duration_ms: 800, selected_candidate_kind: "FORMAL", selection_stale: false }],
  tracks: [{ id: "track-1", episode_id: "episode-1", media_version_id: "audio-bgm", source_name: "theme.wav", track_kind: "BGM", start_us: 0, end_us: 10_000_000, gain_db: -3, loop_enabled: true, fade_in_us: 500_000, fade_out_us: 500_000, source_duration_ms: 1000, license_status: "USER_OWNED", authorization_status: "VERIFIED_EVIDENCE" as const, machine_status: "PASS", latest_review_decision: null, revision: 2, allowed_actions: ["UPDATE_MIX_TRACK", "REMOVE_MIX_TRACK"] }],
  gaps: [], summary: { dialogue_count: 1, adopted_dialogue_count: 1, bgm_count: 1, sfx_count: 0, gap_count: 0 }, allowed_actions: ["ADD_BGM", "ADD_SFX", "EDIT_MIX_DRAFT"],
};

function mount(path = "/") {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter initialEntries={[path]}><EpisodeAudioWorkspace projectId="project-1" episodeId="episode-1" /></MemoryRouter></QueryClientProvider>);
}

describe("EpisodeAudioWorkspace v2", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getEpisodeAudioWorkspaceV2).mockResolvedValue({ workspace, read_only: true, request_shape: "episode_audio_workspace_v2" });
    vi.mocked(updateEpisodeAudioTrackV2).mockResolvedValue({ track: { id: "track-1", episode_id: "episode-1", media_version_id: "audio-bgm", track_kind: "BGM", revision: 3, mix_revision: 5, outcome: "UPDATED", idempotent_replay: false } });
    vi.mocked(removeEpisodeAudioTrackV2).mockResolvedValue({ track: { id: "track-1", episode_id: "episode-1", media_version_id: "audio-bgm", track_kind: "BGM", revision: 2, mix_revision: 5, outcome: "REMOVED", idempotent_replay: false } });
  });

  it("uses one aggregate for dialogue references and restores focus", async () => {
    mount();
    expect(await screen.findByText("DL001 · 阿宁")).toBeTruthy();
    expect(screen.getByText("我们走。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "音乐与音效" }));
    expect(await screen.findByRole("button", { name: /背景音乐/ })).toBeTruthy();
    expect(screen.getByText("revision 4")).toBeTruthy();
  });

  it("updates the selected mix track with exact revisions", async () => {
    mount("/?focus=music-sfx");
    expect(await screen.findByDisplayValue("-3")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("音量（dB）"), { target: { value: "-5" } });
    fireEvent.click(screen.getByRole("button", { name: "保存混音调整" }));
    await waitFor(() => expect(updateEpisodeAudioTrackV2).toHaveBeenCalledWith("track-1", expect.objectContaining({ gain_db: -5, expected_revision: 2, expected_mix_revision: 4, idempotency_key: expect.stringMatching(/^audio-update:/) })));
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { bindCharacterVoice, listCharacterVoiceBindings, listStoryAssets, selectTTSCandidate, submitEpisodeTTSBatch, unbindCharacterVoice, type CharacterVoiceBinding, type DialogueLine, type StoryAsset, type VoiceProfileVersion } from "../../generated/api";
import { DialogueTTSPanel } from "./DialogueTTSPanel";

vi.mock("../../generated/api", () => ({
  selectTTSCandidate: vi.fn(),
  createDialogueLine: vi.fn(),
  createDialogueTextRevision: vi.fn(),
  createVoiceProfileVersion: vi.fn(),
  discoverLocalSapiVoices: vi.fn(),
  finalizeTTSJob: vi.fn(),
  registerTTSCandidate: vi.fn(),
  submitTTSJob: vi.fn(),
  listStoryAssets: vi.fn(),
  listCharacterVoiceBindings: vi.fn(),
  bindCharacterVoice: vi.fn(),
  unbindCharacterVoice: vi.fn(),
  submitEpisodeTTSBatch: vi.fn(),
}));

const character: StoryAsset = {
  id: "char-1", project_id: "project-1", kind: "CHARACTER", code: "CHAR-001", name: "周桂兰",
  description: "", canonical_media_version_id: null, extra: {}, status: "ACTIVE",
  revision: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  created_by: "test", schema_version: "v2",
};
const voice: VoiceProfileVersion = {
  id: "voice-1", project_id: "project-1", code: "voice-a", version_no: 1, title: "Voice A",
  voice_ref: "sapi:TestVoice", license_status: "USER_OWNED",
  license_evidence: { path_rel: "00_admin/voice-license.json", sha256: "a".repeat(64) },
  provider_profile_version_id: "tts-profile-v1", status: "ACTIVE",
};
const binding: CharacterVoiceBinding = {
  id: "binding-1", project_id: "project-1", character_asset_id: "char-1", voice_profile_version_id: "voice-1",
  created_at: "2026-01-01T00:00:00Z", created_by: "test",
  character: { id: "char-1", code: "CHAR-001", name: "周桂兰", kind: "CHARACTER", status: "ACTIVE" },
  voice: { id: "voice-1", code: "voice-a", title: "Voice A", voice_ref: "sapi:TestVoice", status: "ACTIVE" },
};

function renderPanel(props: { lines?: DialogueLine[]; voices?: VoiceProfileVersion[]; projectId?: string; episodeId?: string; onChanged?: () => void } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}><DialogueTTSPanel lines={props.lines ?? []} voices={props.voices ?? []} projectId={props.projectId} episodeId={props.episodeId} onChanged={props.onChanged} /></QueryClientProvider></MemoryRouter>);
}

describe("DialogueTTSPanel", () => {
  beforeEach(() => {
    vi.mocked(listStoryAssets).mockReset().mockResolvedValue({ items: [character] });
    vi.mocked(listCharacterVoiceBindings).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(bindCharacterVoice).mockReset();
    vi.mocked(unbindCharacterVoice).mockReset();
    vi.mocked(submitEpisodeTTSBatch).mockReset();
    vi.mocked(selectTTSCandidate).mockReset().mockResolvedValue({ selection: {} as never });
  });

  it("shows an honest empty blocked state without mock candidates", () => {
    renderPanel();
    expect(screen.getByText("配音配置缺失")).toBeTruthy();
    expect(screen.getByText("当前集没有对白文本 revision；未创建 Mock 候选。")).toBeTruthy();
  });

  it("projects immutable candidate provenance counts", () => {
    renderPanel({ lines: [{ id: "line", episode_id: "episode", shot_id: null, code: "DLG-001", speaker: "A", text_revisions: [{ id: "text", revision_no: 2, text: "line", text_hash: "h", pronunciation: {} }], candidates: [{ id: "candidate", dialogue_text_revision_id: "text", voice_profile_version_id: "voice", media_version_id: "media", emotion: "neutral", speech_rate: 1, seed: 42, model_ref: "local", candidate_kind: "PREVIEW", status: "READY", provenance: {} }], selection: null }] });
    expect(screen.getByText("DLG-001")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
    expect(screen.getByText("真实 TTS 生成保持阻塞")).toBeTruthy();
  });

  it("offers the next review step after selecting a current candidate", async () => {
    renderPanel({ projectId: "project-1", episodeId: "episode-1", lines: [{ id: "line", episode_id: "episode-1", shot_id: null, code: "DLG-001", speaker: "A", text_revisions: [{ id: "text", revision_no: 1, text: "line", text_hash: "h", pronunciation: {} }], candidates: [{ id: "candidate", dialogue_text_revision_id: "text", voice_profile_version_id: "voice", media_version_id: "media", emotion: "neutral", speech_rate: 1, seed: 42, model_ref: "local", candidate_kind: "FORMAL", status: "READY", provenance: {} }], selection: null }] });
    fireEvent.click(screen.getByRole("button", { name: "选择此候选" }));
    const next = await screen.findByRole("link", { name: /前往本集审核/ });
    expect(next.getAttribute("href")).toBe("/projects/project-1/episodes/episode-1/review");
    expect(screen.getByRole("link", { name: /生成可审阅字幕草稿/ }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-1/timeline?view=subtitles&derive=tts");
  });

  it("marks candidates and selections from an older text revision as stale", () => {
    renderPanel({ lines: [{
      id: "line", episode_id: "episode", shot_id: null, code: "DLG-001", speaker: "A",
      text_revisions: [
        { id: "text-1", revision_no: 1, text: "旧文本", text_hash: "h1", pronunciation: {} },
        { id: "text-2", revision_no: 2, text: "新文本", text_hash: "h2", pronunciation: {} },
      ],
      candidates: [{ id: "candidate", dialogue_text_revision_id: "text-1", voice_profile_version_id: "voice", media_version_id: "media", emotion: "neutral", speech_rate: 1, seed: 42, model_ref: "local", candidate_kind: "FORMAL", status: "READY", provenance: {} }],
      selection: { tts_candidate_id: "candidate" },
    }] });
    expect(screen.getByText("已失效")).toBeTruthy();
    expect(screen.getByText("旧文本候选 · 已失效")).toBeTruthy();
    expect(screen.getByRole("button", { name: "候选已失效" }).getAttribute("disabled")).not.toBeNull();
  });

  it("binds a character to an active voice profile", async () => {
    vi.mocked(bindCharacterVoice).mockResolvedValue({ binding });
    const onChanged = vi.fn();
    renderPanel({ projectId: "project-1", episodeId: "episode-1", onChanged, voices: [voice] });
    await screen.findByText("周桂兰（CHAR-001）");
    fireEvent.change(screen.getByLabelText("角色资产"), { target: { value: "char-1" } });
    fireEvent.change(screen.getByLabelText("音色版本"), { target: { value: "voice-1" } });
    fireEvent.click(screen.getByRole("button", { name: "绑定角色音色" }));
    await waitFor(() => expect(bindCharacterVoice).toHaveBeenCalledWith("project-1", { character_asset_id: "char-1", voice_profile_version_id: "voice-1" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("lists and unbinds existing character voice bindings", async () => {
    vi.mocked(listCharacterVoiceBindings).mockResolvedValue({ items: [binding] });
    vi.mocked(unbindCharacterVoice).mockResolvedValue({ result: { id: "binding-1", status: "UNBOUND" } });
    renderPanel({ projectId: "project-1", episodeId: "episode-1" });
    await screen.findByText("周桂兰");
    expect(screen.getByText("Voice A")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "解绑" }));
    expect(unbindCharacterVoice).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog").textContent).toContain("确认解绑 周桂兰 的音色");
    fireEvent.click(screen.getByRole("button", { name: "确认解绑" }));
    await waitFor(() => expect(unbindCharacterVoice).toHaveBeenCalledWith("binding-1"));
  });

  it("submits an episode TTS batch and summarizes counts and skipped reasons", async () => {
    vi.mocked(submitEpisodeTTSBatch).mockResolvedValue({
      batch: {
        episode_id: "episode-1",
        submitted: [{ line_id: "l1", code: "DLG-001", speaker: "周桂兰", character_asset_id: "char-1", voice_profile_version_id: "voice-1", job_id: "job-1", text_revision_id: "t1" }],
        skipped: [{ line_id: "l2", code: "DLG-002", speaker: "神秘人", reason: "VOICE_UNRESOLVED" }],
        failed: [],
        counts: { submitted: 1, skipped: 1, failed: 0 },
      },
    });
    renderPanel({ projectId: "project-1", episodeId: "episode-1" });
    const submit = await screen.findByRole("button", { name: "整集批量 TTS" });
    fireEvent.change(screen.getByLabelText("整集情绪"), { target: { value: "TENSE" } });
    fireEvent.change(screen.getByLabelText("整集语速"), { target: { value: "0.9" } });
    fireEvent.click(submit);
    await waitFor(() => expect(submitEpisodeTTSBatch).toHaveBeenCalled());
    const [calledEpisodeId, payload] = vi.mocked(submitEpisodeTTSBatch).mock.calls[0];
    expect(calledEpisodeId).toBe("episode-1");
    expect(payload.emotion).toBe("TENSE");
    expect(payload.speech_rate).toBe(0.9);
    expect(payload.idempotency_key_prefix).toMatch(/^ep-/);
    expect(await screen.findByText("批量结果：已提交 1 · 跳过 1 · 失败 0")).toBeTruthy();
    expect(screen.getByText(/未解析到角色音色/)).toBeTruthy();
  });
});

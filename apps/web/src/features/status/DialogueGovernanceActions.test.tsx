import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../generated/api";
import { DialogueGovernanceActions } from "./DialogueGovernanceActions";

vi.mock("../../generated/api", async () => {
  const actual = await vi.importActual<typeof import("../../generated/api")>("../../generated/api");
  return { ...actual, createDialogueLine: vi.fn(), createDialogueTextRevision: vi.fn(), createVoiceProfileVersion: vi.fn(), discoverLocalSapiVoices: vi.fn(), publishLocalSapiTTSProfile: vi.fn(), registerTTSCandidate: vi.fn(), selectTTSCandidate: vi.fn(), submitTTSJob: vi.fn(), finalizeTTSJob: vi.fn(), listJobs: vi.fn(), listProjectLocalResources: vi.fn() };
});
vi.mock("../media-picker/MediaPicker", () => ({ MediaPicker: ({ onChange, label }: { onChange: (value: string) => void; label: string }) => <button type="button" aria-label={label} onClick={() => onChange("media-1")}>选择 dialogue-preview.wav · v2</button> }));

const lines: api.DialogueLine[] = [{ id: "line-1", episode_id: "episode-1", shot_id: null, code: "DLG-001", speaker: "A", text_revisions: [{ id: "text-1", revision_no: 1, text: "你好", text_hash: "hash", pronunciation: {} }], candidates: [], selection: null }];
const voices: api.VoiceProfileVersion[] = [{ id: "voice-1", project_id: "project-1", code: "VOICE-A", version_no: 1, title: "A", voice_ref: "local:a", license_status: "USER_OWNED", license_evidence: { path_rel: "00_admin/voice.txt", sha256: "hash" }, provider_profile_version_id: null, status: "ACTIVE" }];

describe("DialogueGovernanceActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listJobs).mockResolvedValue({ items: [] });
    vi.mocked(api.listProjectLocalResources).mockResolvedValue({ project_id: "project-1", kind: "LICENSE_EVIDENCE", items: [{ path_rel: "00_admin/licenses/voice-license.json", name: "voice-license.json", suffix: ".json", byte_size: 128 }], truncated: false, limit: 200, read_only: true, runtime_contacted: false, network_contacted: false, mutated: false });
  });

  it("creates a real immutable dialogue line from explicit fields", async () => {
    vi.mocked(api.createDialogueLine).mockResolvedValue({ dialogue: { ...lines[0], code: "DLG-002" } });
    const changed = vi.fn();
    render(<DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} onChanged={changed} />);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    fireEvent.change(screen.getByLabelText("操作类型"), { target: { value: "LINE" } });
    expect(screen.getByText("对白编号").parentElement?.textContent).toContain("DLG-002");
    fireEvent.change(screen.getByLabelText("说话人"), { target: { value: "B" } });
    fireEvent.change(screen.getByLabelText("剧本文本"), { target: { value: "请进。" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并创建不可变记录" }));
    await waitFor(() => expect(api.createDialogueLine).toHaveBeenCalledWith("episode-1", { code: "DLG-002", speaker: "B", text: "请进。" }));
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("registers preview provenance without inventing a provider", async () => {
    vi.mocked(api.registerTTSCandidate).mockResolvedValue({ candidate: { id: "candidate-1", dialogue_text_revision_id: "text-1", voice_profile_version_id: "voice-1", media_version_id: "media-1", emotion: "警觉", speech_rate: 0.95, seed: 42, model_ref: "IMPORTED_LOCAL_AUDIO", candidate_kind: "PREVIEW", status: "READY", provenance: {} } });
    render(<DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} onChanged={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    fireEvent.change(screen.getByLabelText("操作类型"), { target: { value: "CANDIDATE" } });
    fireEvent.change(screen.getByLabelText("对白文本版本"), { target: { value: "text-1" } });
    fireEvent.change(screen.getByLabelText("音色版本"), { target: { value: "voice-1" } });
    fireEvent.click(screen.getByRole("button", { name: "候选音频选择器" }));
    fireEvent.change(screen.getByLabelText("情绪"), { target: { value: "TENSE" } });
    fireEvent.change(screen.getByLabelText("语速"), { target: { value: "0.95" } });
    fireEvent.change(screen.getByLabelText("Seed（可空）"), { target: { value: "42" } });
    fireEvent.change(screen.getByLabelText("模型来源"), { target: { value: "IMPORTED_LOCAL_AUDIO" } });
    fireEvent.change(screen.getByLabelText("候选类型"), { target: { value: "PREVIEW" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并创建不可变记录" }));
    await waitFor(() => expect(api.registerTTSCandidate).toHaveBeenCalledWith("text-1", { voice_profile_version_id: "voice-1", media_version_id: "media-1", emotion: "TENSE", speech_rate: 0.95, seed: 42, model_ref: "IMPORTED_LOCAL_AUDIO", candidate_kind: "PREVIEW" }));
  });

  it("creates a new text and pronunciation revision with optimistic concurrency", async () => {
    vi.mocked(api.createDialogueTextRevision).mockResolvedValue({ dialogue: { ...lines[0], text_revisions: [...lines[0].text_revisions, { id: "text-2", revision_no: 2, text: "您好", text_hash: "hash-2", pronunciation: { "您": "nin2" } }] } });
    render(<DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} onChanged={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    fireEvent.change(screen.getByLabelText("操作类型"), { target: { value: "REVISION" } });
    fireEvent.change(screen.getByLabelText("已有对白"), { target: { value: "line-1" } });
    fireEvent.change(screen.getByLabelText("新文本"), { target: { value: "您好" } });
    fireEvent.click(screen.getByRole("button", { name: "添加发音映射" }));
    fireEvent.change(screen.getByLabelText("发音映射 1 原词"), { target: { value: "您" } });
    fireEvent.change(screen.getByLabelText("发音映射 1 读音"), { target: { value: "nin2" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并创建不可变记录" }));
    await waitFor(() => expect(api.createDialogueTextRevision).toHaveBeenCalledWith("line-1", { expected_revision_no: 1, text: "您好", pronunciation: { "您": "nin2" } }));
  });

  it("keeps the action disabled until an operation is explicitly selected", () => {
    render(<DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} onChanged={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    expect((screen.getByRole("button", { name: "校验并创建不可变记录" }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.createDialogueLine).not.toHaveBeenCalled();
  });

  it("discovers local SAPI voices before the user explicitly saves a voice profile", async () => {
    vi.mocked(api.discoverLocalSapiVoices).mockResolvedValue({ status: "AVAILABLE", items: [{ name: "Microsoft Huihui Desktop", culture: "zh-CN", gender: "Female", age: "Adult", voice_ref: "sapi:Microsoft Huihui Desktop" }], message: null, runtime_contacted: true, network_contacted: false, mutated: false });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} onChanged={() => undefined} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    fireEvent.change(screen.getByLabelText("操作类型"), { target: { value: "VOICE" } });
    fireEvent.click(screen.getByRole("button", { name: "扫描 Windows 系统音色" }));
    await waitFor(() => expect(api.discoverLocalSapiVoices).toHaveBeenCalledTimes(1));
    fireEvent.change(await screen.findByLabelText("Windows 系统音色"), { target: { value: "sapi:Microsoft Huihui Desktop" } });
    expect((screen.getByLabelText("Windows 系统音色") as HTMLSelectElement).value).toBe("sapi:Microsoft Huihui Desktop");
  });

  it("publishes a local SAPI profile only after an explicit scanned voice and smoke phrase", async () => {
    vi.mocked(api.discoverLocalSapiVoices).mockResolvedValue({ status: "AVAILABLE", items: [{ name: "Microsoft Huihui Desktop", culture: "zh-CN", gender: "Female", age: "Adult", voice_ref: "sapi:Microsoft Huihui Desktop" }], message: null, runtime_contacted: true, network_contacted: false, mutated: false });
    vi.mocked(api.publishLocalSapiTTSProfile).mockResolvedValue({ profile: { id: "tts-profile-v1", code: "local-tts-windows-sapi", version_no: 1, capability: "TTS", status: "PUBLISHED", evidence: { network_contacted: false } } });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} onChanged={() => undefined} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: /新增对白/ }));
    fireEvent.change(screen.getByLabelText("操作类型"), { target: { value: "SAPI_PROFILE" } });
    fireEvent.click(screen.getByRole("button", { name: "扫描 Windows 系统音色" }));
    fireEvent.change(await screen.findByLabelText("待发布的 Windows 系统音色"), { target: { value: "sapi:Microsoft Huihui Desktop" } });
    fireEvent.change(screen.getByLabelText("真实冒烟短句"), { target: { value: "本机语音验收" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并创建不可变记录" }));
    await waitFor(() => expect(api.publishLocalSapiTTSProfile).toHaveBeenCalledWith({ voice_ref: "sapi:Microsoft Huihui Desktop", smoke_text: "本机语音验收" }));
  });

  it("queues a formal local TTS Job only through a provider-bound voice", async () => {
    const providerVoice = { ...voices[0], provider_profile_version_id: "sapi-profile-v1" };
    vi.mocked(api.submitTTSJob).mockResolvedValue({ job: { id: "job-1", type: "TTS_GENERATION", project_id: "project-1", state: "QUEUED", channel: "CPU", priority: 100, max_attempts: 1, revision: 1 } });
    render(<DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={[providerVoice]} onChanged={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    fireEvent.change(screen.getByLabelText("操作类型"), { target: { value: "TTS_JOB" } });
    fireEvent.change(screen.getByLabelText("最新文本版本"), { target: { value: "text-1" } });
    fireEvent.change(screen.getByLabelText("已发布的正式语音音色"), { target: { value: "voice-1" } });
    fireEvent.change(screen.getByLabelText("情绪"), { target: { value: "NEUTRAL" } });
    fireEvent.change(screen.getByLabelText("语速"), { target: { value: "1.1" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并创建不可变记录" }));
    await waitFor(() => expect(api.submitTTSJob).toHaveBeenCalled());
    expect(vi.mocked(api.submitTTSJob).mock.calls[0][0]).toBe("text-1");
    expect(vi.mocked(api.submitTTSJob).mock.calls[0][1]).toEqual({ voice_profile_version_id: "voice-1", emotion: "NEUTRAL", speech_rate: 1.1 });
    expect(vi.mocked(api.submitTTSJob).mock.calls[0][2]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("binds a selected Published TTS Profile when creating a voice", async () => {
    vi.mocked(api.discoverLocalSapiVoices).mockResolvedValue({ status: "AVAILABLE", items: [{ name: "Microsoft Huihui Desktop", culture: "zh-CN", gender: "Female", age: "Adult", voice_ref: "sapi:Microsoft Huihui Desktop" }], message: null, runtime_contacted: true, network_contacted: false, mutated: false });
    vi.mocked(api.createVoiceProfileVersion).mockResolvedValue({ voice_profile: { ...voices[0], id: "voice-tts", provider_profile_version_id: "tts-profile-v1" } });
    const profiles: api.Profile[] = [{ id: "profile", code: "sapi-tts", title: "Windows SAPI", version_id: "tts-profile-v1", version_no: 1, capability: "TTS_SAPI_LOCAL", status: "PUBLISHED" }];
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} profiles={profiles} onChanged={() => undefined} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    fireEvent.change(screen.getByLabelText("操作类型"), { target: { value: "VOICE" } });
    fireEvent.change(screen.getByLabelText("音色名称"), { target: { value: "Windows SAPI" } });
    fireEvent.click(screen.getByRole("button", { name: "扫描 Windows 系统音色" }));
    fireEvent.change(await screen.findByLabelText("Windows 系统音色"), { target: { value: "sapi:Microsoft Huihui Desktop" } });
    await screen.findByRole("option", { name: /voice-license\.json/ });
    fireEvent.change(screen.getByLabelText("项目内音色授权证据"), { target: { value: "00_admin/licenses/voice-license.json" } });
    fireEvent.change(screen.getByLabelText("授权状态"), { target: { value: "USER_OWNED" } });
    fireEvent.change(screen.getByLabelText("已发布语音合成配置"), { target: { value: "tts-profile-v1" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并创建不可变记录" }));
    await waitFor(() => expect(api.createVoiceProfileVersion).toHaveBeenCalledWith("project-1", { code: "VOICE_WINDOWS_SAPI", title: "Windows SAPI", voice_ref: "sapi:Microsoft Huihui Desktop", license_status: "USER_OWNED", license_evidence_path_rel: "00_admin/licenses/voice-license.json", provider_profile_version_id: "tts-profile-v1" }));
  });

  it("finalizes only an explicitly supplied successful TTS Job", async () => {
    vi.mocked(api.listJobs).mockResolvedValue({ items: [{ id: "job-1", type: "TTS_GENERATION", project_id: "project-1", state: "SUCCEEDED", channel: "CPU", priority: 100, max_attempts: 1, revision: 1, subject_id: "text-1" }] });
    vi.mocked(api.finalizeTTSJob).mockResolvedValue({ result: { job_id: "job-1", artifact_id: "artifact-1", media: { id: "media-1" }, candidate: { id: "candidate-1", dialogue_text_revision_id: "text-1", voice_profile_version_id: "voice-1", media_version_id: "media-1", emotion: "neutral", speech_rate: 1, seed: null, model_ref: "WINDOWS_SAPI_LOCAL", candidate_kind: "FORMAL", status: "READY", provenance: {} }, idempotent_replay: false } });
    render(<DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} onChanged={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    fireEvent.change(screen.getByLabelText("操作类型"), { target: { value: "FINALIZE_TTS_JOB" } });
    fireEvent.change(await screen.findByLabelText("语音合成任务"), { target: { value: "job-1" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并创建不可变记录" }));
    await waitFor(() => expect(api.finalizeTTSJob).toHaveBeenCalledWith("job-1"));
  });
});

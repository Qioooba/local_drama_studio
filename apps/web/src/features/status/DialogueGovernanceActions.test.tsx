import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../generated/api";
import { DialogueGovernanceActions } from "./DialogueGovernanceActions";

vi.mock("../../generated/api", async () => {
  const actual = await vi.importActual<typeof import("../../generated/api")>("../../generated/api");
  return { ...actual, createDialogueLine: vi.fn(), createVoiceProfileVersion: vi.fn(), registerTTSCandidate: vi.fn(), selectTTSCandidate: vi.fn() };
});

const lines: api.DialogueLine[] = [{ id: "line-1", episode_id: "episode-1", shot_id: null, code: "DLG-001", speaker: "A", text_revisions: [{ id: "text-1", revision_no: 1, text: "你好", text_hash: "hash", pronunciation: {} }], candidates: [], selection: null }];
const voices: api.VoiceProfileVersion[] = [{ id: "voice-1", project_id: "project-1", code: "VOICE-A", version_no: 1, title: "A", voice_ref: "local:a", license_status: "USER_OWNED", license_evidence: { path_rel: "00_admin/voice.txt", sha256: "hash" }, provider_profile_version_id: null, status: "ACTIVE" }];

describe("DialogueGovernanceActions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("creates a real immutable dialogue line from explicit fields", async () => {
    vi.mocked(api.createDialogueLine).mockResolvedValue({ dialogue: { ...lines[0], code: "DLG-002" } });
    const changed = vi.fn();
    render(<DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} onChanged={changed} />);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    fireEvent.change(screen.getByLabelText("操作类型"), { target: { value: "LINE" } });
    fireEvent.change(screen.getByLabelText("对白编号"), { target: { value: "DLG-002" } });
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
    fireEvent.change(screen.getByLabelText("对白文本 revision"), { target: { value: "text-1" } });
    fireEvent.change(screen.getByLabelText("音色版本"), { target: { value: "voice-1" } });
    fireEvent.change(screen.getByLabelText("AUDIO MediaVersion ID"), { target: { value: "media-1" } });
    fireEvent.change(screen.getByLabelText("情绪"), { target: { value: "警觉" } });
    fireEvent.change(screen.getByLabelText("语速"), { target: { value: "0.95" } });
    fireEvent.change(screen.getByLabelText("Seed（可空）"), { target: { value: "42" } });
    fireEvent.change(screen.getByLabelText("模型来源"), { target: { value: "IMPORTED_LOCAL_AUDIO" } });
    fireEvent.change(screen.getByLabelText("候选类型"), { target: { value: "PREVIEW" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并创建不可变记录" }));
    await waitFor(() => expect(api.registerTTSCandidate).toHaveBeenCalledWith("text-1", { voice_profile_version_id: "voice-1", media_version_id: "media-1", emotion: "警觉", speech_rate: 0.95, seed: 42, model_ref: "IMPORTED_LOCAL_AUDIO", candidate_kind: "PREVIEW" }));
  });

  it("keeps the action disabled until an operation is explicitly selected", () => {
    render(<DialogueGovernanceActions projectId="project-1" episodeId="episode-1" lines={lines} voices={voices} onChanged={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "新增对白、音色或候选" }));
    expect((screen.getByRole("button", { name: "校验并创建不可变记录" }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.createDialogueLine).not.toHaveBeenCalled();
  });
});

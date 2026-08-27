import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  adoptDialogueWorkingAudioV2,
  putShotDialogueDraftV2,
  submitDialogueTtsGenerationV2,
  type ShotDialogueProjection,
} from "../../generated/api";
import { DirectorSoundInspector } from "./DirectorSoundInspector";

vi.mock("../../generated/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../generated/api")>();
  return { ...actual, adoptDialogueWorkingAudioV2: vi.fn(), putShotDialogueDraftV2: vi.fn(), submitDialogueTtsGenerationV2: vi.fn() };
});

const dialogue: ShotDialogueProjection = {
  total: 1,
  lines: [{
    id: "line-1",
    code: "DLG-012",
    speaker: "阿宁",
    revision: 1,
    current_text: { id: "text-1", revision_no: 1, text: "快走。", pronunciation: {}, text_hash: "hash", created_at: "now" },
    voice_binding: { character_asset_id: "char-1", character_code: "ANING", character_name: "阿宁", voice_profile_version_id: "voice-1", voice_code: "VOICE_ANING", voice_title: "阿宁青年声线", voice_ref: "sapi:test", provider_profile_version_id: "profile-1" },
    candidates: [{ id: "candidate-1", dialogue_text_revision_id: "text-1", voice_profile_version_id: "voice-1", media_version_id: "audio-1", emotion: "neutral", speech_rate: 1, seed: null, model_ref: "WINDOWS_SAPI_LOCAL", candidate_kind: "FORMAL", status: "READY", created_at: "now", is_stale: false, selected: false }],
    working_selection: null,
  }],
};

function renderInspector(value: ShotDialogueProjection = dialogue) {
  const onChanged = vi.fn().mockResolvedValue(undefined);
  const rendered = render(<MemoryRouter><DirectorSoundInspector projectId="project-1" shotId="shot-1" shotCode="S012" shotRevision={3} dialogue={value} canEdit reviewHref="/review" onChanged={onChanged} /></MemoryRouter>);
  return { onChanged, container: rendered.container };
}

describe("DirectorSoundInspector", () => {
  beforeEach(() => {
    vi.mocked(putShotDialogueDraftV2).mockReset().mockResolvedValue({ dialogue: { shot_id: "shot-1", line_id: "line-1", code: "DLG-012", speaker: "阿宁", line_revision: 2, text_revision: dialogue.lines[0].current_text, idempotent_replay: false } });
    vi.mocked(submitDialogueTtsGenerationV2).mockReset().mockResolvedValue({ line_id: "line-1", text_revision_id: "text-1", text_revision_no: 1, job: { id: "job-1", state: "QUEUED", subject_kind: "DIALOGUE_TEXT_REVISION", scope_project_id: "project-1", scope_episode_id: "episode-1", scope_shot_id: "shot-1", stage_code: "AUDIO_SUBTITLE", idempotent_replay: false } });
    vi.mocked(adoptDialogueWorkingAudioV2).mockReset().mockResolvedValue({ adoption: { id: "selection-1", dialogue_line_id: "line-1", tts_candidate_id: "candidate-1", media_version_id: "audio-1", source_text_revision_id: "text-1", status: "ADOPTED", idempotent_replay: false } });
  });

  it("shows only shot dialogue production facts and real lazy audio", () => {
    const { container } = renderInspector();
    expect(screen.getByText("DLG-012 · 阿宁")).not.toBeNull();
    expect(screen.getByText("阿宁青年声线 · VOICE_ANING")).not.toBeNull();
    expect(screen.getByLabelText("DLG-012 TTS 候选试听").getAttribute("src")).toBe("/api/v1/media-versions/audio-1/content");
    expect(screen.getByText(/BGM、环境声和音效属于后期音频时间线/)).not.toBeNull();
    expect(container.textContent).not.toContain("分集音频轨道");
  });

  it("submits typed TTS and working-adoption commands", async () => {
    const { onChanged } = renderInspector();
    fireEvent.click(screen.getByRole("button", { name: "生成 TTS 候选" }));
    await waitFor(() => expect(submitDialogueTtsGenerationV2).toHaveBeenCalledTimes(1));
    expect(vi.mocked(submitDialogueTtsGenerationV2).mock.calls[0][1]).toMatchObject({ expected_text_revision_no: 1, voice_profile_version_id: "voice-1", emotion: "neutral", speech_rate: 1 });
    fireEvent.click(screen.getByRole("button", { name: "采用为工作声音" }));
    await waitFor(() => expect(adoptDialogueWorkingAudioV2).toHaveBeenCalledWith("audio-1", expect.objectContaining({ expected_text_revision_no: 1, idempotency_key: expect.any(String) })));
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("creates structured dialogue instead of editing a free-form shot field", async () => {
    renderInspector({ lines: [], total: 0 });
    fireEvent.change(screen.getByLabelText("对白编号"), { target: { value: "DLG-001" } });
    fireEvent.change(screen.getByLabelText("说话人"), { target: { value: "阿宁" } });
    fireEvent.change(screen.getByLabelText("对白文本"), { target: { value: "快走。" } });
    fireEvent.click(screen.getByRole("button", { name: "创建对白" }));
    await waitFor(() => expect(putShotDialogueDraftV2).toHaveBeenCalledWith("shot-1", expect.objectContaining({ line_id: null, expected_shot_revision: 3, code: "DLG-001", speaker: "阿宁", text: "快走。" })));
  });
});

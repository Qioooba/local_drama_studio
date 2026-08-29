import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRequestError,
  adoptDialogueWorkingAudioV2,
  createShotLipsyncJob,
  finalizeLipsyncJob,
  listShotLipsyncJobs,
  putShotDialogueDraftV2,
  submitDialogueTtsGenerationV2,
  type DialogueTtsCandidateFact,
  type ShotDialogueLineFact,
  type ShotDialogueProjection,
} from "../../generated/api";
import { mediaContentUrl } from "../shared/mediaPlaybackPolicy";

type DirectorSoundInspectorProps = {
  projectId: string;
  shotId: string;
  shotCode: string;
  shotRevision: number;
  dialogue: ShotDialogueProjection;
  videoOptions: Array<{ id: string; label: string }>;
  canEdit: boolean;
  reviewHref: string;
  onChanged: () => Promise<unknown>;
};

type Draft = { lineId: string | null; code: string; speaker: string; text: string; pronunciation: string };
const emptyDraft = (): Draft => ({ lineId: null, code: "", speaker: "", text: "", pronunciation: "{}" });

function errorMessage(error: unknown) {
  if (error instanceof ApiRequestError) return `${error.message}（${error.code}）`;
  return error instanceof Error ? error.message : String(error);
}

function candidateLabel(candidate: DialogueTtsCandidateFact) {
  return `${candidate.emotion} · ${candidate.speech_rate}× · ${candidate.model_ref}`;
}

export function DirectorSoundInspector({ projectId, shotId, shotCode, shotRevision, dialogue, videoOptions, canEdit, reviewHref, onChanged }: DirectorSoundInspectorProps) {
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [saving, setSaving] = useState(false);
  const [pendingTtsLineId, setPendingTtsLineId] = useState<string | null>(null);
  const [lipsyncVideoId, setLipsyncVideoId] = useState("");
  const [lipsyncAudioId, setLipsyncAudioId] = useState("");
  const queryClient = useQueryClient();
  const lipsyncJobs = useQuery({
    queryKey: ["shot-lipsync-jobs", shotId],
    queryFn: () => listShotLipsyncJobs(shotId),
    enabled: Boolean(shotId),
    refetchInterval: (query) => (query.state.data?.items ?? []).some((item) => ["QUEUED", "CLAIMED", "RUNNING"].includes(item.state)) ? 4000 : false,
  });
  const pendingFinalizeRef = useRef(new Set<string>());
  const jobs = lipsyncJobs.data?.items ?? [];
  useEffect(() => {
    for (const entry of jobs) {
      if (entry.state === "SUCCEEDED" && !entry.output_media_version_id && !pendingFinalizeRef.current.has(entry.id)) {
        pendingFinalizeRef.current.add(entry.id);
        void finalizeLipsyncJob(entry.id)
          .then(() => queryClient.invalidateQueries({ queryKey: ["shot-lipsync-jobs", shotId] }))
          .catch(() => pendingFinalizeRef.current.delete(entry.id));
      }
    }
  }, [jobs, queryClient, shotId]);
  const audioOptions = dialogue.lines
    .map((line) => line.working_selection)
    .filter((selection): selection is NonNullable<typeof selection> => Boolean(selection));
  const lipsyncCreate = useMutation({
    mutationFn: async () => {
      return createShotLipsyncJob(shotId, {
        video_media_version_id: lipsyncVideoId,
        audio_media_version_id: lipsyncAudioId,
        idempotency_key: `lipsync:${shotId}:${crypto.randomUUID()}`,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["shot-lipsync-jobs", shotId] });
    },
  });
  const [pendingAdoptionId, setPendingAdoptionId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const [failure, setFailure] = useState("");
  const commandKeys = useRef(new Map<string, string>());

  const commandKey = (identity: string) => {
    const existing = commandKeys.current.get(identity);
    if (existing) return existing;
    const next = `shot-audio:${crypto.randomUUID()}`;
    commandKeys.current.set(identity, next);
    return next;
  };

  useEffect(() => {
    if (draft.lineId && !dialogue.lines.some((line) => line.id === draft.lineId)) setDraft(emptyDraft());
  }, [dialogue.lines, draft.lineId]);

  const editLine = (line: ShotDialogueLineFact) => {
    setDraft({ lineId: line.id, code: line.code, speaker: line.speaker, text: line.current_text.text, pronunciation: JSON.stringify(line.current_text.pronunciation, null, 2) });
    setFailure("");
    setFeedback("");
  };

  const saveDraft = async () => {
    const line = draft.lineId ? dialogue.lines.find((item) => item.id === draft.lineId) : undefined;
    let pronunciation: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(draft.pronunciation || "{}");
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error();
      pronunciation = parsed as Record<string, unknown>;
    } catch {
      setFailure("发音词典必须是 JSON 对象。");
      return;
    }
    setSaving(true);
    setFailure("");
    const identity = `dialogue-draft:${draft.lineId ?? shotId}:${line?.current_text.revision_no ?? shotRevision}:${draft.code}:${draft.speaker}:${draft.text}:${draft.pronunciation}`;
    try {
      await putShotDialogueDraftV2(shotId, {
        line_id: draft.lineId,
        code: draft.code.trim(),
        speaker: draft.speaker.trim(),
        text: draft.text.trim(),
        pronunciation,
        expected_shot_revision: shotRevision,
        expected_text_revision_no: line?.current_text.revision_no,
        idempotency_key: commandKey(identity),
      });
      commandKeys.current.delete(identity);
      setDraft(emptyDraft());
      setFeedback(line ? "对白新版本已保存；旧 TTS 候选会明确标记为失效。" : "对白已创建，可继续生成声音候选。");
      await onChanged();
    } catch (error) {
      setFailure(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  const submitTts = async (line: ShotDialogueLineFact) => {
    if (!line.voice_binding) return;
    setPendingTtsLineId(line.id);
    setFailure("");
    const identity = `tts:${line.id}:${line.current_text.revision_no}:${line.voice_binding.voice_profile_version_id}`;
    try {
      await submitDialogueTtsGenerationV2(line.id, {
        expected_text_revision_no: line.current_text.revision_no,
        voice_profile_version_id: line.voice_binding.voice_profile_version_id,
        emotion: "neutral",
        speech_rate: 1,
        idempotency_key: commandKey(identity),
      });
      commandKeys.current.delete(identity);
      setFeedback(`${line.code} 的 TTS 任务已进入队列。`);
      await onChanged();
    } catch (error) {
      setFailure(errorMessage(error));
    } finally {
      setPendingTtsLineId(null);
    }
  };

  const adoptCandidate = async (line: ShotDialogueLineFact, candidate: DialogueTtsCandidateFact) => {
    setPendingAdoptionId(candidate.id);
    setFailure("");
    const identity = `tts-adopt:${candidate.id}:${line.current_text.revision_no}`;
    try {
      await adoptDialogueWorkingAudioV2(candidate.media_version_id, {
        expected_text_revision_no: line.current_text.revision_no,
        idempotency_key: commandKey(identity),
      });
      commandKeys.current.delete(identity);
      setFeedback(`${line.code} 已采用该工作声音；正式批准仍在审核工作区完成。`);
      await onChanged();
    } catch (error) {
      setFailure(errorMessage(error));
    } finally {
      setPendingAdoptionId(null);
    }
  };

  return <section className="director-sound-inspector" aria-labelledby="director-sound-title">
    <div className="director-section-head"><strong id="director-sound-title">{shotCode} · 对白与声音候选</strong><Link to={`/projects/${projectId}/assets`}>管理角色音色</Link></div>
    <p className="director-help">这里维护镜头对白、生成 TTS 候选并采用工作声音。BGM、环境声和音效属于后期音频时间线，不在镜头检查器重复编辑。</p>

    <div className="director-dialogue-editor">
      <div className="director-sound-subhead"><strong>{draft.lineId ? "编辑对白新版本" : "新增对白"}</strong>{draft.lineId && <button type="button" className="director-text-button" onClick={() => setDraft(emptyDraft())}>取消编辑</button>}</div>
      <label>对白编号<input value={draft.code} onChange={(event) => setDraft((value) => ({ ...value, code: event.target.value }))} placeholder="例如 DLG-012" disabled={!canEdit || saving} /></label>
      <label>说话人<input value={draft.speaker} onChange={(event) => setDraft((value) => ({ ...value, speaker: event.target.value }))} placeholder="须与本镜角色名称或编码一致" disabled={!canEdit || saving} /></label>
      <label>对白文本<textarea value={draft.text} onChange={(event) => setDraft((value) => ({ ...value, text: event.target.value }))} rows={3} disabled={!canEdit || saving} /></label>
      <details><summary>发音词典（JSON）</summary><textarea aria-label="发音词典 JSON" value={draft.pronunciation} onChange={(event) => setDraft((value) => ({ ...value, pronunciation: event.target.value }))} rows={3} disabled={!canEdit || saving} /></details>
      <button type="button" className="director-button primary wide" disabled={!canEdit || saving || !draft.code.trim() || !draft.speaker.trim() || !draft.text.trim()} onClick={() => void saveDraft()}>{saving ? "保存中…" : draft.lineId ? "保存为新版本" : "创建对白"}</button>
    </div>

    {dialogue.lines.length === 0 ? <p className="director-sound-state">本镜还没有结构化对白。上方创建后，文本版本、音色和候选都会成为可追溯事实。</p> : <div className="director-dialogue-list">
      {dialogue.lines.map((line) => <article key={line.id} className="director-dialogue-card">
        <header><div><strong>{line.code} · {line.speaker}</strong><small>文本 v{line.current_text.revision_no}</small></div><button type="button" className="director-text-button" disabled={!canEdit} onClick={() => editLine(line)}>编辑</button></header>
        <p>{line.current_text.text}</p>
        <div className="director-dialogue-voice"><span>{line.voice_binding ? `${line.voice_binding.voice_title} · ${line.voice_binding.voice_code}` : "未匹配角色音色"}</span><button type="button" className="director-button secondary" disabled={!canEdit || pendingTtsLineId !== null || !line.voice_binding?.provider_profile_version_id} onClick={() => void submitTts(line)}>{pendingTtsLineId === line.id ? "提交中…" : "生成 TTS 候选"}</button></div>
        {!line.voice_binding && <small className="director-sound-warning">说话人须与本镜已绑定角色的名称或编码一致，并先绑定音色。</small>}
        {line.voice_binding && !line.voice_binding.provider_profile_version_id && <small className="director-sound-warning">该音色尚未绑定 Published 本地 TTS Profile。</small>}
        {line.candidates.length === 0 ? <p className="director-sound-state">尚无声音候选。</p> : <ul className="director-tts-candidates">
          {line.candidates.map((candidate) => <li key={candidate.id} className={candidate.selected ? "selected" : undefined}>
            <div><strong>{candidateLabel(candidate)}</strong><span>{candidate.selected ? "当前工作声音" : candidate.is_stale ? "基于旧文本" : candidate.status}</span></div>
            <audio controls preload="none" src={mediaContentUrl(candidate.media_version_id)} aria-label={`${line.code} TTS 候选试听`} />
            <button type="button" className="director-button secondary" disabled={!canEdit || candidate.is_stale || candidate.selected || candidate.status !== "READY" || pendingAdoptionId !== null} onClick={() => void adoptCandidate(line, candidate)}>{pendingAdoptionId === candidate.id ? "采用中…" : candidate.selected ? "已采用" : candidate.is_stale ? "候选已失效" : "采用为工作声音"}</button>
          </li>)}
        </ul>}
      </article>)}
    </div>}
    <details className="director-lipsync">
      <summary>一键唇形对齐（LatentSync）</summary>
      <p className="director-help">把本镜视频与已采用的对白音频交给本机 LatentSync，生成口型匹配的新视频版本；任务在本机 GPU 队列执行。</p>
      <label>视频<select value={lipsyncVideoId} onChange={(event) => setLipsyncVideoId(event.target.value)} disabled={!canEdit}><option value="">选择本镜视频候选</option>{videoOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
      <label>对白音频<select value={lipsyncAudioId} onChange={(event) => setLipsyncAudioId(event.target.value)} disabled={!canEdit}><option value="">选择已采用的对白音频</option>{audioOptions.map((selection) => <option key={selection.media_version_id} value={selection.media_version_id}>{selection.media_version_id.slice(0, 8)}（已采用）</option>)}</select></label>
      <button type="button" className="director-button primary wide" disabled={!canEdit || !lipsyncVideoId || !lipsyncAudioId || lipsyncCreate.isPending} onClick={() => lipsyncCreate.mutate()}>{lipsyncCreate.isPending ? "排队中…" : "生成口型对齐视频"}</button>
      {lipsyncCreate.error && <p className="director-sound-state error" role="alert">{errorMessage(lipsyncCreate.error)}</p>}
      {jobs.length > 0 && <ul className="director-lipsync-jobs">
        {jobs.map((entry) => <li key={entry.id}>
          <div><strong>{entry.id.slice(0, 8)}</strong><span>{entry.state}</span></div>
          {entry.output_media_version_id && <video controls preload="none" src={mediaContentUrl(entry.output_media_version_id)} aria-label="唇形对齐输出视频" />}
        </li>)}
      </ul>}
    </details>
    {failure && <p className="director-sound-state error" role="alert">{failure}</p>}
    {feedback && <p className="director-sound-state" role="status">{feedback}</p>}
    <p className="director-help">“采用”只确定制作中的工作声音，不等于正式批准。需要批准或退回时，<Link to={reviewHref}>前往审核工作区</Link>。</p>
  </section>;
}

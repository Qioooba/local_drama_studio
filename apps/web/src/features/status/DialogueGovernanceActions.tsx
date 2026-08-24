import { useEffect, useMemo, useState } from "react";
import { createDialogueLine, createDialogueTextRevision, createVoiceProfileVersion, discoverLocalSapiVoices, finalizeTTSJob, listJobs, publishLocalSapiTTSProfile, registerTTSCandidate, selectTTSCandidate, submitTTSJob, type DialogueLine, type Job, type LocalSapiVoice, type Profile, type VoiceProfileVersion } from "../../generated/api";
import { MediaPicker } from "../media-picker/MediaPicker";
import { ProjectLocalResourceSelect } from "../shared/ProjectLocalResourceSelect";
import { TTS_CANDIDATE_KIND_LABELS, TTS_EMOTION_LABELS, TTS_EMOTION_OPTIONS, TTS_MODEL_REF_OPTIONS } from "./ttsOptions";
import { generateMachineCode, nextOrdinalCode } from "../shared/autoCode";
import { ProfileExecutionDetailButton } from "../model-config/ProfileExecutionDetailButton";

type PronunciationEntry = { source: string; reading: string };

function parsePronunciationEntries(value: string | undefined): PronunciationEntry[] {
  if (!value?.trim()) return [];
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    return Object.entries(parsed).map(([source, reading]) => ({ source, reading: String(reading) }));
  } catch {
    return [];
  }
}

export function DialogueGovernanceActions({ projectId, episodeId, lines, voices, profiles = [], onChanged }: { projectId: string; episodeId: string; lines: DialogueLine[]; voices: VoiceProfileVersion[]; profiles?: Profile[]; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [mode, setMode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [sapiVoices, setSapiVoices] = useState<LocalSapiVoice[]>([]);
  const [sapiStatus, setSapiStatus] = useState<string | null>(null);
  const [discoveringSapi, setDiscoveringSapi] = useState(false);
  const [successfulTtsJobs, setSuccessfulTtsJobs] = useState<Job[]>([]);
  const [jobsStatus, setJobsStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [ttsJobKey, setTtsJobKey] = useState(() => crypto.randomUUID());
  const set = (key: string, value: string) => setValues((current) => ({ ...current, [key]: value }));
  const pronunciationEntries = parsePronunciationEntries(values.pronunciation);
  const setPronunciationEntries = (entries: PronunciationEntry[]) => set("pronunciation", JSON.stringify(Object.fromEntries(entries.map((entry) => [entry.source, entry.reading]))));
  const latestRevisions = lines.flatMap((line) => line.text_revisions.at(-1) ? [{ line, revision: line.text_revisions.at(-1)! }] : []);
  const candidates = lines.flatMap((line) => line.candidates.map((candidate) => ({ line, candidate })));
  const nextDialogueCode = nextOrdinalCode("DLG", lines.map((line) => line.code));
  const voiceCode = generateMachineCode("VOICE", values.title ?? "");
  const episodeTextRevisionIds = useMemo(() => new Set(lines.flatMap((line) => line.text_revisions.map((revision) => revision.id))), [lines]);

  useEffect(() => {
    if (mode !== "FINALIZE_TTS_JOB") return;
    let cancelled = false;
    setJobsStatus("loading");
    void listJobs(projectId).then(({ items }) => {
      if (cancelled) return;
      setSuccessfulTtsJobs(items.filter((job) => job.type === "TTS_GENERATION" && job.state === "SUCCEEDED" && episodeTextRevisionIds.has(String(job.subject_id ?? ""))));
      setJobsStatus("ready");
    }).catch(() => {
      if (!cancelled) setJobsStatus("error");
    });
    return () => { cancelled = true; };
  }, [episodeTextRevisionIds, mode, projectId]);

  const discoverSapiVoices = async () => {
    setDiscoveringSapi(true); setSapiStatus(null);
    try {
      const result = await discoverLocalSapiVoices();
      setSapiVoices(result.items);
      setSapiStatus(result.status === "AVAILABLE" ? `已读取 ${result.items.length} 个本机音色；未复制或上传。` : (result.message ?? "本机没有可用 SAPI 音色。"));
    } catch (caught) {
      setSapiStatus(`扫描失败：${String(caught)}`);
    } finally { setDiscoveringSapi(false); }
  };

  const submit = async () => {
    setError(null); setSuccess(null); setPending(true);
    try {
      if (mode === "LINE") {
        if (!values.speaker?.trim() || !values.text?.trim()) throw new Error("说话人和文本均为必填。");
        const result = await createDialogueLine(episodeId, { code: nextDialogueCode, speaker: values.speaker.trim(), text: values.text.trim() });
        setSuccess(`已创建不可变对白 v1：${result.dialogue.code}`);
      } else if (mode === "REVISION") {
        const line = lines.find((item) => item.id === values.lineId);
        const latest = line?.text_revisions.at(-1);
        if (!line || !latest || !values.revisedText?.trim()) throw new Error("必须选择已有对白并填写新文本。");
        let pronunciation: Record<string, unknown> = {};
        if (values.pronunciation?.trim()) {
          const parsed: unknown = JSON.parse(values.pronunciation);
          if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("发音映射必须是 JSON object。");
          pronunciation = Object.fromEntries(Object.entries(parsed as Record<string, unknown>).filter(([source]) => source.trim()));
        }
        const result = await createDialogueTextRevision(line.id, { expected_revision_no: latest.revision_no, text: values.revisedText.trim(), pronunciation });
        setSuccess(`已创建 ${result.dialogue.code} 的不可变文本 v${result.dialogue.text_revisions.at(-1)?.revision_no ?? latest.revision_no + 1}`);
      } else if (mode === "VOICE") {
        if (!voiceCode || !values.title?.trim() || !values.voiceRef?.trim() || !values.licensePath?.trim() || !values.licenseStatus) throw new Error("音色名称、引用、授权证据与状态均为必填。");
        const providerProfileVersionId = values.providerProfileVersionId?.trim() || null;
        const result = await createVoiceProfileVersion(projectId, { code: voiceCode, title: values.title.trim(), voice_ref: values.voiceRef.trim(), license_status: values.licenseStatus as "USER_OWNED" | "VERIFIED_LOCAL", license_evidence_path_rel: values.licensePath.trim(), provider_profile_version_id: providerProfileVersionId });
        setSuccess(`已冻结音色授权版本：${result.voice_profile.code} v${result.voice_profile.version_no}`);
      } else if (mode === "SAPI_PROFILE") {
        if (!values.voiceRef?.trim()) throw new Error("必须先扫描并请选择Windows 系统音色。");
        const result = await publishLocalSapiTTSProfile({ voice_ref: values.voiceRef.trim(), smoke_text: values.smokeText?.trim() || undefined });
        setSuccess(`真实 WAV 冒烟通过，已发布 ${result.profile.code} v${result.profile.version_no}。`);
      } else if (mode === "CANDIDATE") {
        if (!values.textRevisionId || !values.voiceId || !values.mediaVersionId?.trim() || !values.emotion?.trim() || !values.modelRef?.trim() || !values.candidateKind) throw new Error("候选的文本、音色、媒体、情绪、模型来源和类型均须显式填写。");
        const speechRate = Number(values.speechRate ?? "1"), seed = values.seed?.trim() ? Number(values.seed) : null;
        if (!Number.isFinite(speechRate) || speechRate < 0.5 || speechRate > 2 || (seed !== null && !Number.isInteger(seed))) throw new Error("语速必须在 0.5—2.0，seed 必须为空或整数。");
        const result = await registerTTSCandidate(values.textRevisionId, { voice_profile_version_id: values.voiceId, media_version_id: values.mediaVersionId.trim(), emotion: values.emotion.trim(), speech_rate: speechRate, seed, model_ref: values.modelRef.trim(), candidate_kind: values.candidateKind as "PREVIEW" | "FORMAL" });
        setSuccess(`候选已登记：${result.candidate.id.slice(0, 12)} · ${result.candidate.candidate_kind}`);
      } else if (mode === "SELECT") {
        if (!values.candidateId) throw new Error("必须请选择候选。");
        await selectTTSCandidate(values.candidateId);
        setSuccess("候选选择已记录；FORMAL 候选仍由机器 QC 与人工批准硬门禁约束。");
      } else if (mode === "TTS_JOB") {
        if (!values.textRevisionId || !values.voiceId || !values.emotion?.trim()) throw new Error("正式 TTS Job 必须请选择文本、Published Profile 音色并填写情绪。");
        const speechRate = Number(values.speechRate ?? "1");
        if (!Number.isFinite(speechRate) || speechRate < 0.5 || speechRate > 2) throw new Error("语速必须在 0.5—2.0。");
        const result = await submitTTSJob(values.textRevisionId, { voice_profile_version_id: values.voiceId, emotion: values.emotion.trim(), speech_rate: speechRate }, ttsJobKey);
        setSuccess(`真实本地 TTS Job 已排队：${result.job.id}。请由 CPU worker 执行，成功后再结束登记。`);
        setTtsJobKey(crypto.randomUUID());
      } else if (mode === "FINALIZE_TTS_JOB") {
        if (!values.ttsJobId?.trim()) throw new Error("必须填写已成功的 TTS 任务 ID。");
        const result = await finalizeTTSJob(values.ttsJobId.trim());
        setSuccess(`TTS Job 已结束登记：FORMAL candidate ${result.result.candidate.id.slice(0, 12)}${result.result.idempotent_replay ? "（幂等复用）" : ""}`);
      } else throw new Error("请先请选择操作类型。");
      onChanged();
    } catch (caught) {
      setError(`操作失败：${String(caught)}`);
    } finally { setPending(false); }
  };

  return <div className="dialogue-governance-actions">
    <button className="secondary" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "收起对白治理操作" : "新增对白、音色或候选"}</button>
    {expanded && <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label>操作类型<select value={mode} onChange={(event) => { setMode(event.target.value); setError(null); setSuccess(null); }} required><option value="">请选择</option><option value="LINE">创建对白文本 v1</option><option value="REVISION">创建文本 / 发音新版本</option><option value="SAPI_PROFILE">发布本机系统语音合成配置</option><option value="VOICE">创建授权音色版本</option><option value="CANDIDATE">登记真实音频候选</option><option value="TTS_JOB">提交正式本地语音合成任务</option><option value="FINALIZE_TTS_JOB">完成已成功的语音合成任务</option><option value="SELECT">选择候选</option></select></label>
      {mode === "LINE" && <div className="field-grid"><div className="field-fact"><span>对白编号</span><strong>{nextDialogueCode}</strong><small>按本集现有对白自动递增</small></div><label>说话人<input value={values.speaker ?? ""} onChange={(event) => set("speaker", event.target.value)} placeholder="例如：阿宁" required /></label><label>剧本文本<textarea value={values.text ?? ""} onChange={(event) => set("text", event.target.value)} placeholder="输入角色实际说出的台词或旁白" required /></label></div>}
      {mode === "REVISION" && <><div className="field-grid"><label>已有对白<select value={values.lineId ?? ""} onChange={(event) => set("lineId", event.target.value)} required><option value="">请选择</option>{lines.map((line) => <option key={line.id} value={line.id}>{line.code} · 当前第 {line.text_revisions.at(-1)?.revision_no ?? 0} 版</option>)}</select></label><label>新文本<input value={values.revisedText ?? ""} onChange={(event) => set("revisedText", event.target.value)} required /></label></div><fieldset><legend>发音映射（可选）</legend>{pronunciationEntries.map((entry, index) => <div className="field-grid" key={index}><label>原词<input aria-label={`发音映射 ${index + 1} 原词`} value={entry.source} onChange={(event) => setPronunciationEntries(pronunciationEntries.map((item, itemIndex) => itemIndex === index ? { ...item, source: event.target.value } : item))} /></label><label>读音<input aria-label={`发音映射 ${index + 1} 读音`} value={entry.reading} onChange={(event) => setPronunciationEntries(pronunciationEntries.map((item, itemIndex) => itemIndex === index ? { ...item, reading: event.target.value } : item))} /></label><button type="button" className="secondary" onClick={() => setPronunciationEntries(pronunciationEntries.filter((_, itemIndex) => itemIndex !== index))}>删除</button></div>)}<button type="button" className="secondary" onClick={() => setPronunciationEntries([...pronunciationEntries, { source: "", reading: "" }])}>添加发音映射</button></fieldset></>}
      {mode === "VOICE" && <><div className="field-grid"><div className="field-fact"><span>音色技术标识</span><strong>{voiceCode || "填写名称后自动生成"}</strong></div><label>音色名称<input value={values.title ?? ""} onChange={(event) => set("title", event.target.value)} placeholder="例如：阿宁青年声线" required /></label><ProjectLocalResourceSelect projectId={projectId} kind="LICENSE_EVIDENCE" value={values.licensePath ?? ""} onChange={(value) => set("licensePath", value)} label="项目内音色授权证据" required emptyLabel="请选择授权证据" /><label>授权状态<select value={values.licenseStatus ?? ""} onChange={(event) => set("licenseStatus", event.target.value)} required><option value="">请选择</option><option value="USER_OWNED">用户拥有</option><option value="VERIFIED_LOCAL">本地授权已核验</option></select></label><label>已发布语音合成配置（可选）<select aria-label="已发布语音合成配置" value={values.providerProfileVersionId ?? ""} onChange={(event) => set("providerProfileVersionId", event.target.value)}><option value="">仅导入试听，不绑定生成服务</option>{profiles.filter((profile) => profile.status === "PUBLISHED" && String(profile.capability).toUpperCase().includes("TTS")).map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.code} · 第 {String(profile.version_no ?? "?")} 版</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={values.providerProfileVersionId} /></div><div className="post-process-actions"><button type="button" className="secondary" onClick={() => void discoverSapiVoices()} disabled={discoveringSapi}>{discoveringSapi ? "扫描本机音色中…" : "扫描 Windows 系统音色"}</button>{sapiVoices.length > 0 && <label>Windows 系统音色<select aria-label="Windows 系统音色" value={values.voiceRef?.startsWith("sapi:") ? values.voiceRef : ""} onChange={(event) => { const selected = sapiVoices.find((voice) => voice.voice_ref === event.target.value); set("voiceRef", event.target.value); if (selected && !values.title?.trim()) set("title", selected.name); }} required><option value="">请选择音色</option>{sapiVoices.map((voice) => <option key={voice.voice_ref} value={voice.voice_ref}>{voice.name}</option>)}</select></label>}{sapiStatus && <span className="muted" role="status">{sapiStatus}</span>}</div><p className="muted">音色引用只能来自 Windows 本机音色扫描；绑定已发布的语音合成配置后，才可排队正式本地语音合成任务。</p></>}
      {mode === "CANDIDATE" && <div className="field-grid"><label>对白文本版本<select value={values.textRevisionId ?? ""} onChange={(event) => set("textRevisionId", event.target.value)} required><option value="">请选择</option>{latestRevisions.map(({ line, revision }) => <option key={revision.id} value={revision.id}>{line.code} · 第 {revision.revision_no} 版</option>)}</select></label><label>音色版本<select value={values.voiceId ?? ""} onChange={(event) => set("voiceId", event.target.value)} required><option value="">请选择</option>{voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.code} · 第 {voice.version_no} 版</option>)}</select></label><div className="wide"><MediaPicker projectId={projectId} mediaKind="AUDIO" allowUpload={false} value={values.mediaVersionId ?? ""} onChange={(mediaVersionId) => set("mediaVersionId", mediaVersionId)} disabled={pending} label="候选音频选择器" /><p className="muted">仅列出当前项目已登记的真实音频版本；选择后仍由服务端校验时长、探测结果与用途。</p></div><label>情绪<select value={values.emotion ?? ""} onChange={(event) => set("emotion", event.target.value)} required><option value="">请选择</option>{TTS_EMOTION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><label>语速<input type="number" min="0.5" max="2" step="0.05" value={values.speechRate ?? "1"} onChange={(event) => set("speechRate", event.target.value)} required /></label><label>Seed（可空）<input type="number" step="1" value={values.seed ?? ""} onChange={(event) => set("seed", event.target.value)} /></label><label>模型来源<select value={values.modelRef ?? ""} onChange={(event) => set("modelRef", event.target.value)} required><option value="">请选择</option>{TTS_MODEL_REF_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><label>候选类型<select value={values.candidateKind ?? ""} onChange={(event) => set("candidateKind", event.target.value)} required><option value="">请选择</option><option value="PREVIEW">试听候选（仅供试听）</option><option value="FORMAL">正式候选（可用于成片）</option></select></label></div>}
      {mode === "TTS_JOB" && <div className="field-grid"><label>最新文本版本<select value={values.textRevisionId ?? ""} onChange={(event) => set("textRevisionId", event.target.value)} required><option value="">请选择</option>{latestRevisions.map(({ line, revision }) => <option key={revision.id} value={revision.id}>{line.code} · 第 {revision.revision_no} 版</option>)}</select></label><label>已发布的正式语音音色<select value={values.voiceId ?? ""} onChange={(event) => set("voiceId", event.target.value)} required><option value="">请选择</option>{voices.filter((voice) => voice.provider_profile_version_id).map((voice) => <option key={voice.id} value={voice.id}>{voice.code} · 第 {voice.version_no} 版</option>)}</select></label><label>情绪<select value={values.emotion ?? ""} onChange={(event) => set("emotion", event.target.value)} required><option value="">请选择</option>{TTS_EMOTION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><label>语速<input type="number" min="0.5" max="2" step="0.05" value={values.speechRate ?? "1"} onChange={(event) => set("speechRate", event.target.value)} required /></label></div>}
      {mode === "FINALIZE_TTS_JOB" && <label>语音合成任务<select value={values.ttsJobId ?? ""} onChange={(event) => set("ttsJobId", event.target.value)} disabled={jobsStatus === "loading" || successfulTtsJobs.length === 0} required><option value="">{jobsStatus === "loading" ? "正在读取成功任务…" : successfulTtsJobs.length ? "选择本集成功任务" : "本集暂无可结束的成功任务"}</option>{successfulTtsJobs.map((job) => <option key={job.id} value={job.id}>{job.id.slice(0, 12)} · {job.finished_at ? new Date(job.finished_at).toLocaleString() : "已成功"}</option>)}</select>{jobsStatus === "error" && <span className="inline-error" role="alert">任务列表读取失败，请稍后重试。</span>}</label>}
      {mode === "SELECT" && <label>候选<select value={values.candidateId ?? ""} onChange={(event) => set("candidateId", event.target.value)} required><option value="">请选择</option>{candidates.map(({ line, candidate }) => <option key={candidate.id} value={candidate.id}>{line.code} · {TTS_CANDIDATE_KIND_LABELS[candidate.candidate_kind] ?? "音频候选"} · {TTS_EMOTION_LABELS[candidate.emotion] ?? "未标注情绪"}</option>)}</select></label>}
      {mode === "SAPI_PROFILE" && <><div className="post-process-actions"><button type="button" className="secondary" onClick={() => void discoverSapiVoices()} disabled={discoveringSapi}>{discoveringSapi ? "扫描本机音色中…" : "扫描 Windows 系统音色"}</button>{sapiVoices.length > 0 && <label>Windows 系统音色<select aria-label="待发布的 Windows 系统音色" value={values.voiceRef ?? ""} onChange={(event) => set("voiceRef", event.target.value)} required><option value="">请选择音色</option>{sapiVoices.map((voice) => <option key={voice.voice_ref} value={voice.voice_ref}>{voice.name}</option>)}</select></label>}{sapiStatus && <span className="muted" role="status">{sapiStatus}</span>}</div><label>真实冒烟短句<input value={values.smokeText ?? "本机语音合成验收通过"} onChange={(event) => set("smokeText", event.target.value)} maxLength={120} required /></label><p className="muted">仅调用 Windows 本机系统语音；生成短音频并通过媒体探测、文件大小和内容校验后才发布配置，全程不连接网络。</p></>}
      <p className="muted">这里仅登记真实本地数据。试听候选必须是服务端已探测的 3—10 秒音频；正式候选要求已发布语音合成配置；最终选择还要求机器质检通过并完成人工批准，界面不能绕过这些规则。</p>
      <button className="primary-action" type="submit" disabled={pending || !mode}>{pending ? "正在校验…" : "校验并创建不可变记录"}</button>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {success && <p className="review-success" role="status">{success}</p>}
    </form>}
  </div>;
}

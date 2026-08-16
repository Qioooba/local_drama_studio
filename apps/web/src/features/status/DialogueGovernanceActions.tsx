import { useState } from "react";
import { createDialogueLine, createDialogueTextRevision, createVoiceProfileVersion, discoverLocalSapiVoices, finalizeTTSJob, registerTTSCandidate, selectTTSCandidate, submitTTSJob, type DialogueLine, type LocalSapiVoice, type Profile, type VoiceProfileVersion } from "../../generated/api";

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
  const [ttsJobKey, setTtsJobKey] = useState(() => crypto.randomUUID());
  const set = (key: string, value: string) => setValues((current) => ({ ...current, [key]: value }));
  const latestRevisions = lines.flatMap((line) => line.text_revisions.at(-1) ? [{ line, revision: line.text_revisions.at(-1)! }] : []);
  const candidates = lines.flatMap((line) => line.candidates.map((candidate) => ({ line, candidate })));

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
        if (!values.code?.trim() || !values.speaker?.trim() || !values.text?.trim()) throw new Error("对白编号、说话人和文本均为必填。");
        const result = await createDialogueLine(episodeId, { code: values.code.trim(), speaker: values.speaker.trim(), text: values.text.trim() });
        setSuccess(`已创建不可变对白 v1：${result.dialogue.code}`);
      } else if (mode === "REVISION") {
        const line = lines.find((item) => item.id === values.lineId);
        const latest = line?.text_revisions.at(-1);
        if (!line || !latest || !values.revisedText?.trim()) throw new Error("必须选择已有对白并填写新文本。");
        let pronunciation: Record<string, unknown> = {};
        if (values.pronunciation?.trim()) {
          const parsed: unknown = JSON.parse(values.pronunciation);
          if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("发音映射必须是 JSON object。");
          pronunciation = parsed as Record<string, unknown>;
        }
        const result = await createDialogueTextRevision(line.id, { expected_revision_no: latest.revision_no, text: values.revisedText.trim(), pronunciation });
        setSuccess(`已创建 ${result.dialogue.code} 的不可变文本 v${result.dialogue.text_revisions.at(-1)?.revision_no ?? latest.revision_no + 1}`);
      } else if (mode === "VOICE") {
        if (!values.voiceCode?.trim() || !values.title?.trim() || !values.voiceRef?.trim() || !values.licensePath?.trim() || !values.licenseStatus) throw new Error("音色标识、名称、引用、授权证据与状态均为必填。");
        const providerProfileVersionId = values.providerProfileVersionId?.trim() || null;
        const result = await createVoiceProfileVersion(projectId, { code: values.voiceCode.trim(), title: values.title.trim(), voice_ref: values.voiceRef.trim(), license_status: values.licenseStatus as "USER_OWNED" | "VERIFIED_LOCAL", license_evidence_path_rel: values.licensePath.trim(), provider_profile_version_id: providerProfileVersionId });
        setSuccess(`已冻结音色授权版本：${result.voice_profile.code} v${result.voice_profile.version_no}`);
      } else if (mode === "CANDIDATE") {
        if (!values.textRevisionId || !values.voiceId || !values.mediaVersionId?.trim() || !values.emotion?.trim() || !values.modelRef?.trim() || !values.candidateKind) throw new Error("候选的文本、音色、媒体、情绪、模型来源和类型均须显式填写。");
        const speechRate = Number(values.speechRate ?? "1"), seed = values.seed?.trim() ? Number(values.seed) : null;
        if (!Number.isFinite(speechRate) || speechRate < 0.5 || speechRate > 2 || (seed !== null && !Number.isInteger(seed))) throw new Error("语速必须在 0.5—2.0，seed 必须为空或整数。");
        const result = await registerTTSCandidate(values.textRevisionId, { voice_profile_version_id: values.voiceId, media_version_id: values.mediaVersionId.trim(), emotion: values.emotion.trim(), speech_rate: speechRate, seed, model_ref: values.modelRef.trim(), candidate_kind: values.candidateKind as "PREVIEW" | "FORMAL" });
        setSuccess(`候选已登记：${result.candidate.id.slice(0, 12)} · ${result.candidate.candidate_kind}`);
      } else if (mode === "SELECT") {
        if (!values.candidateId) throw new Error("必须显式选择候选。");
        await selectTTSCandidate(values.candidateId);
        setSuccess("候选选择已记录；FORMAL 候选仍由机器 QC 与人工批准硬门禁约束。");
      } else if (mode === "TTS_JOB") {
        if (!values.textRevisionId || !values.voiceId || !values.emotion?.trim()) throw new Error("正式 TTS Job 必须显式选择文本、Published Profile 音色并填写情绪。");
        const speechRate = Number(values.speechRate ?? "1");
        if (!Number.isFinite(speechRate) || speechRate < 0.5 || speechRate > 2) throw new Error("语速必须在 0.5—2.0。");
        const result = await submitTTSJob(values.textRevisionId, { voice_profile_version_id: values.voiceId, emotion: values.emotion.trim(), speech_rate: speechRate }, ttsJobKey);
        setSuccess(`真实本地 TTS Job 已排队：${result.job.id}。请由 CPU worker 执行，成功后再结束登记。`);
        setTtsJobKey(crypto.randomUUID());
      } else if (mode === "FINALIZE_TTS_JOB") {
        if (!values.ttsJobId?.trim()) throw new Error("必须填写已成功的 TTS Job ID。");
        const result = await finalizeTTSJob(values.ttsJobId.trim());
        setSuccess(`TTS Job 已结束登记：FORMAL candidate ${result.result.candidate.id.slice(0, 12)}${result.result.idempotent_replay ? "（幂等复用）" : ""}`);
      } else throw new Error("请先显式选择操作类型。");
      onChanged();
    } catch (caught) {
      setError(`操作失败：${String(caught)}`);
    } finally { setPending(false); }
  };

  return <div className="dialogue-governance-actions">
    <button className="secondary" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "收起对白治理操作" : "新增对白、音色或候选"}</button>
    {expanded && <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label>操作类型<select value={mode} onChange={(event) => { setMode(event.target.value); setError(null); setSuccess(null); }} required><option value="">显式选择</option><option value="LINE">创建对白文本 v1</option><option value="REVISION">创建文本/发音新 revision</option><option value="VOICE">创建授权音色版本</option><option value="CANDIDATE">登记真实音频候选</option><option value="TTS_JOB">提交正式本地 TTS Job</option><option value="FINALIZE_TTS_JOB">结束登记成功的 TTS Job</option><option value="SELECT">选择候选</option></select></label>
      {mode === "LINE" && <div className="field-grid"><label>对白编号<input value={values.code ?? ""} onChange={(event) => set("code", event.target.value)} required /></label><label>说话人<input value={values.speaker ?? ""} onChange={(event) => set("speaker", event.target.value)} required /></label><label>剧本文本<input value={values.text ?? ""} onChange={(event) => set("text", event.target.value)} required /></label></div>}
      {mode === "REVISION" && <div className="field-grid"><label>已有对白<select value={values.lineId ?? ""} onChange={(event) => set("lineId", event.target.value)} required><option value="">显式选择</option>{lines.map((line) => <option key={line.id} value={line.id}>{line.code} · 当前 v{line.text_revisions.at(-1)?.revision_no ?? 0}</option>)}</select></label><label>新文本<input value={values.revisedText ?? ""} onChange={(event) => set("revisedText", event.target.value)} required /></label><label>发音映射 JSON（可空）<input value={values.pronunciation ?? ""} onChange={(event) => set("pronunciation", event.target.value)} placeholder={'{"词":"拼音"}'} /></label></div>}
      {mode === "VOICE" && <><div className="field-grid"><label>音色标识<input value={values.voiceCode ?? ""} onChange={(event) => set("voiceCode", event.target.value)} required /></label><label>音色名称<input value={values.title ?? ""} onChange={(event) => set("title", event.target.value)} required /></label><label>本地音色引用<input value={values.voiceRef ?? ""} onChange={(event) => set("voiceRef", event.target.value)} placeholder="扫描后选择，或填 sapi:音色名" required /></label><label>项目内授权证据路径<input value={values.licensePath ?? ""} onChange={(event) => set("licensePath", event.target.value)} required /></label><label>授权状态<select value={values.licenseStatus ?? ""} onChange={(event) => set("licenseStatus", event.target.value)} required><option value="">显式选择</option><option value="USER_OWNED">用户拥有</option><option value="VERIFIED_LOCAL">本地授权已核验</option></select></label><label>Published TTS Profile（可选）<select aria-label="Published TTS Profile" value={values.providerProfileVersionId ?? ""} onChange={(event) => set("providerProfileVersionId", event.target.value)}><option value="">仅导入试听，不绑定 Provider</option>{profiles.filter((profile) => profile.status === "PUBLISHED" && String(profile.capability).toUpperCase().includes("TTS")).map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.code} · v{String(profile.version_no ?? "?")}</option>)}</select></label></div><div className="post-process-actions"><button type="button" className="secondary" onClick={() => void discoverSapiVoices()} disabled={discoveringSapi}>{discoveringSapi ? "扫描本机音色中…" : "扫描本机 SAPI 音色"}</button>{sapiVoices.length > 0 && <label>扫描结果<select aria-label="本机 SAPI 音色" value={values.voiceRef?.startsWith("sapi:") ? values.voiceRef : ""} onChange={(event) => { const selected = sapiVoices.find((voice) => voice.voice_ref === event.target.value); set("voiceRef", event.target.value); if (selected && !values.title?.trim()) set("title", selected.name); }}><option value="">显式选择音色</option>{sapiVoices.map((voice) => <option key={voice.voice_ref} value={voice.voice_ref}>{voice.name} · {voice.culture || "未知区域"}</option>)}</select></label>}{sapiStatus && <span className="muted" role="status">{sapiStatus}</span>}</div><p className="muted">绑定 Published TTS Profile 后才可排队正式本地 TTS Job；留空则只允许导入真实 AUDIO 作为试听候选。</p></>}
      {mode === "CANDIDATE" && <div className="field-grid"><label>对白文本 revision<select value={values.textRevisionId ?? ""} onChange={(event) => set("textRevisionId", event.target.value)} required><option value="">显式选择</option>{latestRevisions.map(({ line, revision }) => <option key={revision.id} value={revision.id}>{line.code} · v{revision.revision_no}</option>)}</select></label><label>音色版本<select value={values.voiceId ?? ""} onChange={(event) => set("voiceId", event.target.value)} required><option value="">显式选择</option>{voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.code} · v{voice.version_no}</option>)}</select></label><label>AUDIO MediaVersion ID<input value={values.mediaVersionId ?? ""} onChange={(event) => set("mediaVersionId", event.target.value)} required /></label><label>情绪<input value={values.emotion ?? ""} onChange={(event) => set("emotion", event.target.value)} required /></label><label>语速<input type="number" min="0.5" max="2" step="0.05" value={values.speechRate ?? "1"} onChange={(event) => set("speechRate", event.target.value)} required /></label><label>Seed（可空）<input type="number" step="1" value={values.seed ?? ""} onChange={(event) => set("seed", event.target.value)} /></label><label>模型来源<input value={values.modelRef ?? ""} onChange={(event) => set("modelRef", event.target.value)} placeholder="IMPORTED_LOCAL_AUDIO" required /></label><label>候选类型<select value={values.candidateKind ?? ""} onChange={(event) => set("candidateKind", event.target.value)} required><option value="">显式选择</option><option value="PREVIEW">试听 PREVIEW</option><option value="FORMAL">正式 FORMAL</option></select></label></div>}
      {mode === "TTS_JOB" && <div className="field-grid"><label>最新文本 revision<select value={values.textRevisionId ?? ""} onChange={(event) => set("textRevisionId", event.target.value)} required><option value="">显式选择</option>{latestRevisions.map(({ line, revision }) => <option key={revision.id} value={revision.id}>{line.code} · v{revision.revision_no}</option>)}</select></label><label>Published TTS 音色<select value={values.voiceId ?? ""} onChange={(event) => set("voiceId", event.target.value)} required><option value="">显式选择</option>{voices.filter((voice) => voice.provider_profile_version_id).map((voice) => <option key={voice.id} value={voice.id}>{voice.code} · v{voice.version_no}</option>)}</select></label><label>情绪<input value={values.emotion ?? ""} onChange={(event) => set("emotion", event.target.value)} required /></label><label>语速<input type="number" min="0.5" max="2" step="0.05" value={values.speechRate ?? "1"} onChange={(event) => set("speechRate", event.target.value)} required /></label></div>}
      {mode === "FINALIZE_TTS_JOB" && <label>TTS Job ID<input value={values.ttsJobId ?? ""} onChange={(event) => set("ttsJobId", event.target.value)} required /></label>}
      {mode === "SELECT" && <label>候选<select value={values.candidateId ?? ""} onChange={(event) => set("candidateId", event.target.value)} required><option value="">显式选择</option>{candidates.map(({ line, candidate }) => <option key={candidate.id} value={candidate.id}>{line.code} · {candidate.candidate_kind} · {candidate.emotion}</option>)}</select></label>}
      <p className="muted">这里仅登记真实本地数据。PREVIEW 必须是服务端已探测的 3—10 秒 AUDIO；FORMAL 候选要求 Published TTS Profile；正式选择还要求最新机器 QC PASS 和人工 APPROVED，UI 不能绕过。</p>
      <button className="primary-action" type="submit" disabled={pending || !mode}>{pending ? "正在校验…" : "校验并创建不可变记录"}</button>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {success && <p className="review-success" role="status">{success}</p>}
    </form>}
  </div>;
}

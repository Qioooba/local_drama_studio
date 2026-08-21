import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bindCharacterVoice, listCharacterVoiceBindings, listStoryAssets, selectTTSCandidate, submitEpisodeTTSBatch, unbindCharacterVoice, type DialogueLine, type EpisodeTTSBatchResult, type Profile, type VoiceProfileVersion } from "../../generated/api";
import { DialogueGovernanceActions } from "./DialogueGovernanceActions";

export function DialogueTTSPanel({ lines, voices, profiles = [], projectId, episodeId, onChanged }: { lines: DialogueLine[]; voices: VoiceProfileVersion[]; profiles?: Profile[]; projectId?: string; episodeId?: string; onChanged?: () => void }) {
  const candidates = lines.reduce((total, line) => total + line.candidates.length, 0);
  const publishedProviders = voices.filter((voice) => Boolean(voice.provider_profile_version_id));
  const [selectingId, setSelectingId] = useState<string | null>(null);
  const [selectionMessage, setSelectionMessage] = useState<string | null>(null);
  const chooseCandidate = async (candidateId: string) => {
    setSelectingId(candidateId); setSelectionMessage(null);
    try { await selectTTSCandidate(candidateId); setSelectionMessage(`已选择候选 ${candidateId.slice(0, 12)}；正式候选仍须机器 QC 与人工批准。`); onChanged?.(); }
    catch (error) { setSelectionMessage(`选择失败：${String(error)}`); }
    finally { setSelectingId(null); }
  };
  return <section className="panel dialogue-tts-panel" aria-labelledby="dialogue-tts-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-AUD-001 · 对白 / TTS</p><h3 id="dialogue-tts-title">对白候选与音色授权</h3></div><span className={`status-pill${publishedProviders.length ? "" : " neutral"}`}>{publishedProviders.length ? "TTS 配置就绪" : "TTS 配置缺失"}</span></div>
    <p className="muted">文本、发音、音色授权、情绪、语速、seed、model 和媒体 hash 均按不可变 revision 追溯。未绑定已发布 TTS Profile 的本地音频只能登记为导入试听候选，不能冒充正式 TTS 生成。</p>
    <div className="configuration-grid capacity-grid">
      <div className="configuration-card"><small>对白文本</small><strong>{lines.length}</strong><span>每次校对创建新 revision</span></div>
      <div className="configuration-card"><small>授权音色版本</small><strong>{voices.length}</strong><span>授权证据必须位于项目内并冻结 SHA-256</span></div>
      <div className="configuration-card"><small>试听候选</small><strong>{candidates}</strong><span>文本变化后旧候选不可再次选择</span></div>
      <div className="configuration-card"><small>已发布 TTS Profile</small><strong>{publishedProviders.length}</strong><span>{publishedProviders.length ? "可进入真实 Provider 生成验收" : "真实 TTS 生成保持阻塞"}</span></div>
    </div>
    {lines.length === 0 ? <p className="empty-state">当前集没有对白文本 revision；未创建 Mock 候选。</p> : <><div className="configuration-table" role="table" aria-label="对白 TTS 候选"><div className="configuration-row header" role="row"><span>对白</span><span>文本 revision</span><span>候选</span><span>选择</span></div>{lines.map((line) => <div className="configuration-row" role="row" key={line.id}><span>{line.code}<small>{line.speaker}</small></span><span>v{line.text_revisions.at(-1)?.revision_no ?? 0}</span><span>{line.candidates.length}</span><span>{line.selection ? "已选择" : "未选择"}</span></div>)}</div><div className="tts-candidate-grid" aria-label="TTS 音频试听候选">{lines.flatMap((line) => line.candidates.map((candidate) => <article className="tts-candidate-card" key={candidate.id}><div><strong>{line.code} · {candidate.candidate_kind}</strong><small>{candidate.emotion} · {candidate.speech_rate}× · seed {candidate.seed ?? "随机"}</small></div><img src={`/api/v1/media-versions/${encodeURIComponent(candidate.media_version_id)}/waveform`} alt="TTS 候选派生波形" width="320" height="64" loading="lazy" decoding="async" /><audio controls preload="none" src={`/api/v1/media-versions/${encodeURIComponent(candidate.media_version_id)}/content`} /><small>模型：{candidate.model_ref}</small><button type="button" className="secondary" onClick={() => void chooseCandidate(candidate.id)} disabled={selectingId !== null}>{selectingId === candidate.id ? "选择中…" : "选择此候选"}</button></article>))}</div></>}
    {selectionMessage && <p className="review-success" role="status">{selectionMessage}</p>}
    <p className="muted">只读投影不会启动 Provider、提交 Job 或读取原音频。</p>
    {projectId && episodeId && onChanged && <DialogueGovernanceActions projectId={projectId} episodeId={episodeId} lines={lines} voices={voices} profiles={profiles} onChanged={onChanged} />}
    {projectId && episodeId && <CharacterVoiceOrchestration projectId={projectId} episodeId={episodeId} voices={voices} onChanged={onChanged} />}
  </section>;
}

function CharacterVoiceOrchestration({ projectId, episodeId, voices, onChanged }: { projectId: string; episodeId: string; voices: VoiceProfileVersion[]; onChanged?: () => void }) {
  const queryClient = useQueryClient();
  const characters = useQuery({ queryKey: ["story-assets", projectId, "CHARACTER"], queryFn: () => listStoryAssets(projectId, "CHARACTER"), enabled: Boolean(projectId) });
  const bindings = useQuery({ queryKey: ["character-voice-bindings", projectId], queryFn: () => listCharacterVoiceBindings(projectId), enabled: Boolean(projectId) });
  const [characterId, setCharacterId] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [emotion, setEmotion] = useState("NEUTRAL");
  const [speechRate, setSpeechRate] = useState("1");
  const [batchSummary, setBatchSummary] = useState<EpisodeTTSBatchResult | null>(null);
  const refreshBindings = () => { void queryClient.invalidateQueries({ queryKey: ["character-voice-bindings", projectId] }); onChanged?.(); };
  const bind = useMutation({
    mutationFn: () => bindCharacterVoice(projectId, { character_asset_id: characterId, voice_profile_version_id: voiceId }),
    onSuccess: () => { setCharacterId(""); setVoiceId(""); refreshBindings(); },
  });
  const unbind = useMutation({ mutationFn: (bindingId: string) => unbindCharacterVoice(bindingId), onSuccess: refreshBindings });
  const batch = useMutation({
    mutationFn: () => submitEpisodeTTSBatch(episodeId, { idempotency_key_prefix: `ep-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`, emotion: emotion.trim() || "NEUTRAL", speech_rate: Number(speechRate) }),
    onSuccess: (result) => { setBatchSummary(result.batch); onChanged?.(); },
  });
  const rate = Number(speechRate);
  const batchValid = Number.isFinite(rate) && rate >= 0.5 && rate <= 2;
  const characterOptions = (characters.data?.items ?? []).filter((asset) => asset.status === "ACTIVE");
  const activeVoices = voices.filter((voice) => voice.status === "ACTIVE");
  const items = bindings.data?.items ?? [];
  return <div className="character-voice-orchestration">
    <div className="panel-heading"><div><p className="eyebrow">G11 · 多音色编排</p><h4>角色音色绑定与整集批量 TTS</h4></div><span className="status-pill neutral">{items.length} 个绑定</span></div>
    <p className="muted">角色卡（故事资产 CHARACTER）绑定 ACTIVE 音色版本；整集批量按镜头角色绑定优先、说话人名称/编号精确匹配兜底，逐行提交真实本地 TTS Job。</p>
    <div className="field-grid">
      <label>角色资产<select aria-label="角色资产" value={characterId} onChange={(event) => setCharacterId(event.target.value)} disabled={bind.isPending}><option value="">选择角色</option>{characterOptions.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}（{asset.code}）</option>)}</select></label>
      <label>音色版本<select aria-label="音色版本" value={voiceId} onChange={(event) => setVoiceId(event.target.value)} disabled={bind.isPending}><option value="">选择音色</option>{activeVoices.map((voice) => <option key={voice.id} value={voice.id}>{voice.title} · v{voice.version_no}</option>)}</select></label>
      <button type="button" className="secondary" disabled={!characterId || !voiceId || bind.isPending} onClick={() => bind.mutate()}>{bind.isPending ? "绑定中…" : "绑定角色音色"}</button>
    </div>
    {bind.error && <p className="inline-error" role="alert">绑定失败：{String(bind.error)}</p>}
    {unbind.error && <p className="inline-error" role="alert">解绑失败：{String(unbind.error)}</p>}
    {items.length === 0 ? <p className="empty-state">尚未绑定任何角色音色。</p> : <div className="configuration-table" role="table" aria-label="角色音色绑定"><div className="configuration-row header" role="row"><span>角色</span><span>音色</span><span>操作</span></div>{items.map((binding) => <div className="configuration-row" role="row" key={binding.id}><span>{binding.character.name}<small>{binding.character.code}</small></span><span>{binding.voice.title}<small>{binding.voice.voice_ref}</small></span><span><button type="button" className="secondary" disabled={unbind.isPending} onClick={() => unbind.mutate(binding.id)}>解绑</button></span></div>)}</div>}
    <div className="field-grid episode-tts-batch-fields">
      <label>整集情绪<input aria-label="整集情绪" value={emotion} onChange={(event) => setEmotion(event.target.value)} /></label>
      <label>整集语速<input aria-label="整集语速" type="number" min="0.5" max="2" step="0.05" value={speechRate} onChange={(event) => setSpeechRate(event.target.value)} /></label>
      <button type="button" className="primary-action" disabled={!batchValid || batch.isPending} onClick={() => batch.mutate()}>{batch.isPending ? "批量提交中…" : "整集批量 TTS"}</button>
    </div>
    {batch.error && <p className="inline-error" role="alert">批量提交失败：{String(batch.error)}</p>}
    {batchSummary && <div className="batch-summary" role="status"><p>批量结果：已提交 {batchSummary.counts.submitted} · 跳过 {batchSummary.counts.skipped} · 失败 {batchSummary.counts.failed}</p>{batchSummary.skipped.length > 0 && <ul>{batchSummary.skipped.map((item) => <li key={item.line_id}>{item.code} · {item.speaker}：{item.reason === "VOICE_UNRESOLVED" ? "未解析到角色音色" : item.reason === "VOICE_NOT_JOB_ELIGIBLE" ? "音色不可提交正式 TTS Job" : item.reason}</li>)}</ul>}</div>}
  </div>;
}

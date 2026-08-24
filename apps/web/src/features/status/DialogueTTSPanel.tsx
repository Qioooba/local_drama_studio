import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { bindCharacterVoice, listCharacterVoiceBindings, listStoryAssets, selectTTSCandidate, submitEpisodeTTSBatch, unbindCharacterVoice, type DialogueLine, type EpisodeTTSBatchResult, type Profile, type VoiceProfileVersion } from "../../generated/api";
import { DialogueGovernanceActions } from "./DialogueGovernanceActions";
import { Dialog } from "../../components/ui/primitives";
import { TTS_EMOTION_OPTIONS } from "./ttsOptions";

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
    <div className="panel-heading"><div><p className="eyebrow">对白配音</p><h3 id="dialogue-tts-title">对白候选与音色授权</h3></div><span className={`status-pill${publishedProviders.length ? "" : " neutral"}`}>{publishedProviders.length ? "配音配置就绪" : "配音配置缺失"}</span></div>
    <p className="muted">文本、发音、音色授权、情绪、语速、seed、model 和媒体 hash 均按不可变 revision 追溯。未绑定已发布 TTS 配置的本地音频只能登记为导入试听候选，不能冒充正式 TTS 生成。</p>
    <div className="configuration-grid capacity-grid">
      <div className="configuration-card"><small>对白文本</small><strong>{lines.length}</strong><span>每次校对创建新 revision</span></div>
      <div className="configuration-card"><small>授权音色版本</small><strong>{voices.length}</strong><span>授权证据必须位于项目内并冻结 SHA-256</span></div>
      <div className="configuration-card"><small>试听候选</small><strong>{candidates}</strong><span>文本变化后旧候选不可再次选择</span></div>
      <div className="configuration-card"><small>已发布 TTS 配置</small><strong>{publishedProviders.length}</strong><span>{publishedProviders.length ? "可进入真实 Provider 生成验收" : "真实 TTS 生成保持阻塞"}</span></div>
    </div>
    {lines.length === 0 ? <p className="empty-state">当前集没有对白文本 revision；未创建 Mock 候选。</p> : <><div className="configuration-table" role="table" aria-label="对白 TTS 候选"><div className="configuration-row header" role="row"><span>对白</span><span>文本 revision</span><span>候选</span><span>选择</span></div>{lines.map((line) => {
      const latestRevisionId = line.text_revisions.at(-1)?.id;
      const selectedCandidateId = String(line.selection?.["tts_candidate_id"] ?? "");
      const selectedCandidate = line.candidates.find((candidate) => candidate.id === selectedCandidateId);
      const selectionCurrent = Boolean(latestRevisionId && selectedCandidate?.dialogue_text_revision_id === latestRevisionId);
      return <div className="configuration-row" role="row" key={line.id}><span>{line.code}<small>{line.speaker}</small></span><span>v{line.text_revisions.at(-1)?.revision_no ?? 0}</span><span>{line.candidates.filter((candidate) => candidate.dialogue_text_revision_id === latestRevisionId).length} 当前 / {line.candidates.length} 总计</span><span>{selectionCurrent ? "已选择" : line.selection ? "已失效" : "未选择"}</span></div>;
    })}</div><div className="tts-candidate-grid" aria-label="TTS 音频试听候选">{lines.flatMap((line) => {
      const latestRevisionId = line.text_revisions.at(-1)?.id;
      return line.candidates.map((candidate) => {
        const stale = !latestRevisionId || candidate.dialogue_text_revision_id !== latestRevisionId;
        return <article className="tts-candidate-card" key={candidate.id}><div><strong>{line.code} · {candidate.candidate_kind}</strong><small>{candidate.emotion} · {candidate.speech_rate}× · seed {candidate.seed ?? "随机"}</small></div>{stale && <span className="status-pill neutral">旧文本候选 · 已失效</span>}<img src={`/api/v1/media-versions/${encodeURIComponent(candidate.media_version_id)}/waveform`} alt="TTS 候选派生波形" width="320" height="64" loading="lazy" decoding="async" /><audio controls preload="none" src={`/api/v1/media-versions/${encodeURIComponent(candidate.media_version_id)}/content`} /><small>模型：{candidate.model_ref}</small><button type="button" className="secondary" onClick={() => void chooseCandidate(candidate.id)} disabled={selectingId !== null || stale}>{stale ? "候选已失效" : selectingId === candidate.id ? "选择中…" : "选择此候选"}</button></article>;
      });
    })}</div></>}
    {selectionMessage && <div className="review-success" role="status"><span>{selectionMessage}</span>{projectId && episodeId && !selectionMessage.startsWith("选择失败") && <><Link className="v2-inline-link" to={`/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}/review`}>前往本集审核，继续机器 QC 与人工批准</Link><Link className="v2-inline-link" to={`/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}/timeline?view=subtitles&derive=tts`}>用当前采用结果生成可审阅字幕草稿</Link></>}</div>}
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
  const [batchNotice, setBatchNotice] = useState("");
  const [pendingUnbind, setPendingUnbind] = useState<{ id: string; characterName: string; characterCode: string; voiceTitle: string } | null>(null);
  const refreshBindings = () => { void queryClient.invalidateQueries({ queryKey: ["character-voice-bindings", projectId] }); onChanged?.(); };
  const bind = useMutation({
    mutationFn: () => bindCharacterVoice(projectId, { character_asset_id: characterId, voice_profile_version_id: voiceId }),
    onSuccess: () => { setCharacterId(""); setVoiceId(""); refreshBindings(); },
  });
  const unbind = useMutation({ mutationFn: (bindingId: string) => unbindCharacterVoice(bindingId), onSuccess: () => { setPendingUnbind(null); refreshBindings(); } });
  const batch = useMutation({
    mutationFn: () => submitEpisodeTTSBatch(episodeId, { idempotency_key_prefix: `ep-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`, emotion: emotion.trim() || "NEUTRAL", speech_rate: Number(speechRate) }),
    onMutate: () => { setBatchSummary(null); setBatchNotice("正在提交整集 TTS 任务…"); },
    onSuccess: (result) => { setBatchSummary(result.batch); setBatchNotice(`整集 TTS 已处理：提交 ${result.batch.counts.submitted}，跳过 ${result.batch.counts.skipped}，失败 ${result.batch.counts.failed}`); onChanged?.(); },
    onError: (error) => setBatchNotice(`整集 TTS 提交失败：${String(error)}`),
  });
  const rate = Number(speechRate);
  const batchValid = Number.isFinite(rate) && rate >= 0.5 && rate <= 2;
  const characterOptions = (characters.data?.items ?? []).filter((asset) => asset.status === "ACTIVE");
  const activeVoices = voices.filter((voice) => voice.status === "ACTIVE");
  const items = bindings.data?.items ?? [];
  return <div className="character-voice-orchestration">
    <div className="panel-heading"><div><p className="eyebrow">多音色编排</p><h4>角色音色绑定与整集批量配音</h4></div><span className="status-pill neutral">{items.length} 个绑定</span></div>
    <p className="muted">角色卡（故事资产 CHARACTER）绑定 ACTIVE 音色版本；整集批量按镜头角色绑定优先、说话人名称/编号精确匹配兜底，逐行提交真实本地 TTS Job。</p>
    <div className="field-grid">
      <label>角色资产<select aria-label="角色资产" value={characterId} onChange={(event) => setCharacterId(event.target.value)} disabled={bind.isPending || characterOptions.length === 0}><option value="">{characterOptions.length ? "选择角色" : "暂无角色资产，请先在资产圣经中创建"}</option>{characterOptions.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}（{asset.code}）</option>)}</select></label>
      <label>音色版本<select aria-label="音色版本" value={voiceId} onChange={(event) => setVoiceId(event.target.value)} disabled={bind.isPending || activeVoices.length === 0}><option value="">{activeVoices.length ? "选择音色" : "暂无可用音色，请先在资产圣经中创建音色"}</option>{activeVoices.map((voice) => <option key={voice.id} value={voice.id}>{voice.title} · 第 {voice.version_no} 版</option>)}</select></label>
      <button type="button" className="secondary" disabled={!characterId || !voiceId || bind.isPending} title={!characterId || !voiceId ? "请先选择角色与音色" : undefined} onClick={() => bind.mutate()}>{bind.isPending ? "绑定中…" : "绑定角色音色"}</button>
    </div>
    {(!characterId || !voiceId) && <small className="muted">{characterOptions.length === 0 || activeVoices.length === 0 ? "当前缺少可绑定的角色或音色；请先在资产圣经中补齐资产。" : "提示：请先选择角色资产与音色版本后再进行绑定。"}</small>}
    {bind.error && <p className="inline-error" role="alert">绑定失败：{String(bind.error)}</p>}
    {unbind.error && <p className="inline-error" role="alert">解绑失败：{String(unbind.error)}</p>}
    {items.length === 0 ? <p className="empty-state">尚未绑定任何角色音色。</p> : <div className="configuration-table" role="table" aria-label="角色音色绑定"><div className="configuration-row header" role="row"><span>角色</span><span>音色</span><span>操作</span></div>{items.map((binding) => <div className="configuration-row" role="row" key={binding.id}><span>{binding.character.name}<small>{binding.character.code}</small></span><span>{binding.voice.title}<small>{binding.voice.voice_ref}</small></span><span><button type="button" className="secondary" disabled={unbind.isPending} onClick={() => setPendingUnbind({ id: binding.id, characterName: binding.character.name, characterCode: binding.character.code, voiceTitle: binding.voice.title })}>解绑</button></span></div>)}</div>}
    <Dialog open={Boolean(pendingUnbind)} title={pendingUnbind ? `确认解绑 ${pendingUnbind.characterName} 的音色？` : "确认解绑角色音色"} onClose={() => setPendingUnbind(null)} footer={pendingUnbind ? <><button type="button" onClick={() => setPendingUnbind(null)}>取消</button><button type="button" className="secondary" disabled={unbind.isPending} onClick={() => unbind.mutate(pendingUnbind.id)}>{unbind.isPending ? "解绑中…" : "确认解绑"}</button></> : undefined}>
      {pendingUnbind && <><p>{pendingUnbind.characterCode} · {pendingUnbind.voiceTitle}</p><p>这会移除项目角色与该音色版本的绑定并写入审计；不会删除音色、授权证据或已生成候选，之后仍可重新绑定。</p></>}
    </Dialog>
    <div className="field-grid episode-tts-batch-fields">
      <label>整集情绪<select aria-label="整集情绪" value={emotion} onChange={(event) => setEmotion(event.target.value)}>{TTS_EMOTION_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      <label>整集语速<input aria-label="整集语速" type="number" min="0.5" max="2" step="0.05" value={speechRate} onChange={(event) => setSpeechRate(event.target.value)} /></label>
      <button type="button" className="primary-action" disabled={!batchValid || batch.isPending} onClick={() => batch.mutate()}>{batch.isPending ? "批量提交中…" : "整集批量 TTS"}</button>
    </div>
    {batchNotice && <p className={batch.error ? "inline-error" : "review-success"} role="status" aria-live="polite">{batchNotice}</p>}
    {batch.error && <p className="inline-error" role="alert">批量提交失败：{String(batch.error)}</p>}
    {batchSummary && <div className="batch-summary" role="status"><p>批量结果：已提交 {batchSummary.counts.submitted} · 跳过 {batchSummary.counts.skipped} · 失败 {batchSummary.counts.failed}</p>{batchSummary.skipped.length > 0 && <ul>{batchSummary.skipped.map((item) => <li key={item.line_id}>{item.code} · {item.speaker}：{item.reason === "VOICE_UNRESOLVED" ? "未解析到角色音色" : item.reason === "VOICE_NOT_JOB_ELIGIBLE" ? "音色不可提交正式 TTS Job" : item.reason}</li>)}</ul>}</div>}
  </div>;
}

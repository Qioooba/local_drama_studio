import type { AudioBinding, DialogueLine, ReviewInboxItem, VoiceProfileVersion } from "../../generated/api";

const selectedCandidateId = (line: DialogueLine) => String(line.selection?.["tts_candidate_id"] ?? "");

export function AudioEpisodeOverview({
  lines,
  voices,
  bindings,
  reviewItems,
}: {
  lines: DialogueLine[];
  voices: VoiceProfileVersion[];
  bindings: AudioBinding[];
  reviewItems: ReviewInboxItem[];
}) {
  const missingText = lines.filter((line) => !line.text_revisions.length || !line.text_revisions.at(-1)?.text.trim()).length;
  const missingTakes = lines.filter((line) => line.candidates.length === 0).length;
  const missingSelection = lines.filter((line) => !line.selection).length;
  const selectedFormal = lines.filter((line) => {
    const candidateId = selectedCandidateId(line);
    return line.candidates.some((candidate) => candidate.id === candidateId && candidate.candidate_kind === "FORMAL");
  }).length;
  const activeVoices = voices.filter((voice) => voice.status === "ACTIVE");
  const jobReadyVoices = activeVoices.filter((voice) => Boolean(voice.provider_profile_version_id));
  const incompleteVoiceLicenses = activeVoices.filter((voice) => !voice.license_evidence?.path_rel || !voice.license_evidence?.sha256).length;
  const unauthorizedBindings = bindings.filter((binding) => binding.authorization_status !== "VERIFIED_EVIDENCE").length;
  const bgm = bindings.filter((binding) => ["BGM", "MUSIC"].includes(binding.track_type)).length;
  const sfx = bindings.filter((binding) => ["SFX", "ENVIRONMENT"].includes(binding.track_type)).length;
  const qcPassed = reviewItems.filter((item) => item.machine_status === "PASS").length;
  const qcBlocked = reviewItems.filter((item) => item.machine_status && item.machine_status !== "PASS").length;
  const qcPending = reviewItems.filter((item) => !item.machine_status).length;

  return <>
    <div className="card-grid v2-summary-grid" aria-label="本集声音生产概况">
      <div className="status-card"><span>台词</span><strong>{lines.length}</strong><small>{missingText ? `${missingText} 条缺少有效文本 revision` : "文本 revision 齐全"}</small></div>
      <div className="status-card"><span>Speaker / Voice</span><strong>{new Set(lines.map((line) => line.speaker.trim()).filter(Boolean)).size} / {activeVoices.length}</strong><small>{jobReadyVoices.length} 个音色可提交正式 TTS</small></div>
      <div className="status-card"><span>TTS Takes</span><strong>{lines.reduce((count, line) => count + line.candidates.length, 0)}</strong><small>{missingTakes} 条无候选 · {missingSelection} 条未选音频</small></div>
      <div className="status-card"><span>Selected Audio</span><strong>{lines.length - missingSelection}</strong><small>{selectedFormal} 条已选 FORMAL；选择由 QC / 人审门禁保护</small></div>
      <div className="status-card"><span>授权缺口</span><strong>{incompleteVoiceLicenses + unauthorizedBindings}</strong><small>音色 {incompleteVoiceLicenses} · 音轨 {unauthorizedBindings}</small></div>
      <div className="status-card"><span>音量 / QC</span><strong>{qcPassed} PASS</strong><small>{qcBlocked} 阻塞 · {qcPending} 待检查（审核投影）</small></div>
      <div className="status-card"><span>BGM / SFX</span><strong>{bgm} / {sfx}</strong><small>共 {bindings.length} 条持久化音频绑定</small></div>
    </div>
    {(missingText > 0 || missingTakes > 0 || missingSelection > 0 || incompleteVoiceLicenses > 0 || unauthorizedBindings > 0 || qcBlocked > 0) && <section className="panel" aria-labelledby="audio-attention-title">
      <div className="panel-heading"><div><p className="eyebrow">生产缺口</p><h3 id="audio-attention-title">进入时间线前需要关注</h3></div><span className="status-pill neutral">事实汇总</span></div>
      <div className="review-meta">
        {missingText > 0 && <span>{missingText} 条台词缺少有效文本</span>}
        {missingTakes > 0 && <span>{missingTakes} 条台词尚无 TTS take</span>}
        {missingSelection > 0 && <span>{missingSelection} 条台词尚未选择音频</span>}
        {incompleteVoiceLicenses > 0 && <span>{incompleteVoiceLicenses} 个 ACTIVE 音色缺少完整授权证据</span>}
        {unauthorizedBindings > 0 && <span>{unauthorizedBindings} 条 BGM/SFX 绑定授权证据不完整</span>}
        {qcBlocked > 0 && <span>{qcBlocked} 个本集音频媒体最新 QC 未通过</span>}
      </div>
      <p className="muted">这里仅汇总现有对白、音色、绑定与审核读模型；不会创建候选、替代正式审核或自动覆盖选择。</p>
    </section>}
  </>;
}

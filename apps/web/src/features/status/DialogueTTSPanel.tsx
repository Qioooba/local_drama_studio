import type { DialogueLine, VoiceProfileVersion } from "../../generated/api";

export function DialogueTTSPanel({ lines, voices }: { lines: DialogueLine[]; voices: VoiceProfileVersion[] }) {
  const candidates = lines.reduce((total, line) => total + line.candidates.length, 0);
  const publishedProviders = voices.filter((voice) => Boolean(voice.provider_profile_version_id));
  return <section className="panel dialogue-tts-panel" aria-labelledby="dialogue-tts-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-AUD-001 · DIALOGUE / TTS</p><h3 id="dialogue-tts-title">对白候选与音色授权</h3></div><span className={`status-pill${publishedProviders.length ? "" : " neutral"}`}>{publishedProviders.length ? "TTS PROFILE READY" : "TTS PROFILE MISSING"}</span></div>
    <p className="muted">文本、发音、音色授权、情绪、语速、seed、model 和媒体 hash 均按不可变 revision 追溯。未绑定 Published TTS Profile 的本地音频只能登记为导入试听候选，不能冒充正式 TTS 生成。</p>
    <div className="configuration-grid capacity-grid">
      <div className="configuration-card"><small>对白文本</small><strong>{lines.length}</strong><span>每次校对创建新 revision</span></div>
      <div className="configuration-card"><small>授权音色版本</small><strong>{voices.length}</strong><span>授权证据必须位于项目内并冻结 SHA-256</span></div>
      <div className="configuration-card"><small>试听候选</small><strong>{candidates}</strong><span>文本变化后旧候选不可再次选择</span></div>
      <div className="configuration-card"><small>Published TTS Profile</small><strong>{publishedProviders.length}</strong><span>{publishedProviders.length ? "可进入真实 Provider 生成验收" : "真实 TTS 生成保持阻塞"}</span></div>
    </div>
    {lines.length === 0 ? <p className="empty-state">当前集没有对白文本 revision；未创建 Mock 候选。</p> : <div className="configuration-table" role="table" aria-label="对白 TTS 候选"><div className="configuration-row header" role="row"><span>对白</span><span>文本 revision</span><span>候选</span><span>选择</span></div>{lines.map((line) => <div className="configuration-row" role="row" key={line.id}><span>{line.code}<small>{line.speaker}</small></span><span>v{line.text_revisions.at(-1)?.revision_no ?? 0}</span><span>{line.candidates.length}</span><span>{line.selection ? "已选择" : "未选择"}</span></div>)}</div>}
    <p className="muted">只读投影不会启动 Provider、提交 Job 或读取原音频。</p>
  </section>;
}

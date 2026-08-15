import type { AudioBinding } from "../../generated/api";

const trackLabels: Record<string, string> = { DIALOGUE: "对白", ENVIRONMENT: "环境", SFX: "音效", MUSIC: "音乐" };

export function AudioTrackPanel({ bindings }: { bindings: AudioBinding[] }) {
  return <section className="panel audio-track-panel" aria-labelledby="audio-track-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-AUD-002 · AUDIO TRACKS</p><h3 id="audio-track-title">音效、环境与音乐绑定</h3></div><span className="status-pill neutral">按需试听</span></div>
    <p className="muted">播放器默认不预加载；只有用户明确播放时才读取本地音频。loop、淡入淡出、gain、范围与授权证据均来自持久化绑定。</p>
    {bindings.length === 0 ? <p className="empty-state">当前集没有真实音频绑定。</p> : <div className="configuration-table" role="table" aria-label="分集音频轨道">
      <div className="configuration-row header" role="row"><span>轨道</span><span>范围 / Gain</span><span>播放策略</span><span>授权</span></div>
      {bindings.map((binding) => <div className="configuration-row audio-binding-row" role="row" key={binding.id}>
        <span><strong>{trackLabels[binding.track_type] ?? binding.track_type}</strong><small>{binding.media_version_id.slice(0, 12)}…</small></span>
        <span>{(binding.start_us / 1_000_000).toFixed(2)}s–{(binding.end_us / 1_000_000).toFixed(2)}s<small>{binding.gain_db.toFixed(1)} dB · fade {binding.fade_in_us / 1000}/{binding.fade_out_us / 1000} ms</small></span>
        <span><audio controls preload="none" loop={binding.loop_enabled} aria-label={`${trackLabels[binding.track_type] ?? binding.track_type}本地试听`} src={`/api/v1/media-versions/${encodeURIComponent(binding.media_version_id)}/content`} /><small>{binding.loop_enabled ? "循环" : "单次"}</small></span>
        <span className={binding.authorization_status === "VERIFIED_EVIDENCE" ? "status-pill" : "status-pill neutral"}>{binding.authorization_status === "VERIFIED_EVIDENCE" ? "授权证据已验证" : "遗留授权证据不完整"}</span>
      </div>)}
    </div>}
  </section>;
}

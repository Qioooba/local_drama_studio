import { useState } from "react";
import { Dialog } from "../../components/ui/primitives";
import { unbindEpisodeAudio, type AudioBinding } from "../../generated/api";
import { AudioImportBindingForm } from "./AudioImportBindingForm";

const trackLabels: Record<string, string> = { DIALOGUE: "对白", BGM: "BGM/音乐", SFX: "音效", MUSIC: "音乐（旧）", ENVIRONMENT: "环境（旧）" };

export function AudioTrackPanel({ bindings, projectId, episodeId, onBound }: { bindings: AudioBinding[]; projectId: string; episodeId: string; onBound: () => void }) {
  const [pendingUnbind, setPendingUnbind] = useState<AudioBinding | null>(null);
  const [unbinding, setUnbinding] = useState(false);
  const [unbindError, setUnbindError] = useState<string | null>(null);
  const confirmUnbind = async () => {
    if (!pendingUnbind) return;
    setUnbinding(true);
    setUnbindError(null);
    try {
      await unbindEpisodeAudio(pendingUnbind.id);
      setPendingUnbind(null);
      onBound();
    } catch (error) {
      setUnbindError(String(error));
    } finally {
      setUnbinding(false);
    }
  };
  return <section className="panel audio-track-panel" aria-labelledby="audio-track-title">
    <div className="panel-heading"><div><p className="eyebrow">音频轨道</p><h3 id="audio-track-title">音效、环境与音乐绑定</h3></div><span className="status-pill neutral">按需试听</span></div>
    <p className="muted">播放器默认不预加载；只有用户明确播放时才读取本地音频。loop、淡入淡出、gain、范围与授权证据均来自持久化绑定。</p>
    {bindings.length === 0 ? <p className="empty-state">当前集没有真实音频绑定。</p> : <div className="configuration-table" role="table" aria-label="分集音频轨道">
      <div className="configuration-row header" role="row"><span>轨道</span><span>范围 / Gain</span><span>播放策略</span><span>授权 / 操作</span></div>
      {bindings.map((binding) => <div className="configuration-row audio-binding-row" role="row" key={binding.id}>
        <span><strong>{trackLabels[binding.track_type] ?? binding.track_type}</strong><small>{binding.media_version_id.slice(0, 12)}…</small></span>
        <span>{(binding.start_us / 1_000_000).toFixed(2)}s–{(binding.end_us / 1_000_000).toFixed(2)}s<small>{binding.gain_db.toFixed(1)} dB · fade {binding.fade_in_us / 1000}/{binding.fade_out_us / 1000} ms</small></span>
        <span><audio controls preload="none" loop={binding.loop_enabled} aria-label={`${trackLabels[binding.track_type] ?? binding.track_type}本地试听`} src={`/api/v1/media-versions/${encodeURIComponent(binding.media_version_id)}/content`} /><small>{binding.loop_enabled ? "循环" : "单次"}</small></span>
        <span><span className={binding.authorization_status === "VERIFIED_EVIDENCE" ? "status-pill" : "status-pill neutral"}>{binding.authorization_status === "VERIFIED_EVIDENCE" ? "授权证据已验证" : "遗留授权证据不完整"}</span><button type="button" className="secondary" onClick={() => { setUnbindError(null); setPendingUnbind(binding); }}>解绑</button></span>
      </div>)}
    </div>}
    <Dialog open={Boolean(pendingUnbind)} title={pendingUnbind ? `确认解绑 ${trackLabels[pendingUnbind.track_type] ?? pendingUnbind.track_type}？` : "确认解绑音频轨道"} onClose={() => !unbinding && setPendingUnbind(null)} footer={pendingUnbind ? <><button type="button" disabled={unbinding} onClick={() => setPendingUnbind(null)}>取消</button><button type="button" className="secondary" disabled={unbinding} onClick={() => void confirmUnbind()}>{unbinding ? "解绑中…" : "确认解绑"}</button></> : undefined}>
      {pendingUnbind && <><p>只移除本集时间线与媒体版本 {pendingUnbind.media_version_id.slice(0, 12)}… 的绑定。</p><p>原音频、授权证据、机器 QC、人工审核和审计历史均保留；已冻结的旧时间线 revision 不会被改写。</p>{unbindError && <p className="inline-error" role="alert">解绑失败：{unbindError}</p>}</>}
    </Dialog>
    <AudioImportBindingForm projectId={projectId} episodeId={episodeId} onBound={onBound} />
  </section>;
}

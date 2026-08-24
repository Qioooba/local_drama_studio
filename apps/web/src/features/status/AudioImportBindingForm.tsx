import { useState } from "react";
import { bindEpisodeAudio } from "../../generated/api";
import { uploadProjectMediaFile } from "../media-picker/mediaPickerClient";
import { ProjectLocalResourceSelect } from "../shared/ProjectLocalResourceSelect";

export function AudioImportBindingForm({ projectId, episodeId, onBound }: { projectId: string; episodeId: string; onBound: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [licensePath, setLicensePath] = useState("");
  const [trackType, setTrackType] = useState("");
  const [licenseStatus, setLicenseStatus] = useState("");
  const [startSeconds, setStartSeconds] = useState("0");
  const [endSeconds, setEndSeconds] = useState("");
  const [gainDb, setGainDb] = useState("0");
  const [fadeInMs, setFadeInMs] = useState("0");
  const [fadeOutMs, setFadeOutMs] = useState("0");
  const [loopEnabled, setLoopEnabled] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const submit = async () => {
    const start = Number(startSeconds), end = Number(endSeconds), gain = Number(gainDb), fadeIn = Number(fadeInMs), fadeOut = Number(fadeOutMs);
    if (!sourceFile || !licensePath.trim() || !trackType || !licenseStatus) return setError("请选择本地音频，并完整填写授权证据、轨道和授权类型。");
    if (![start, end, gain, fadeIn, fadeOut].every(Number.isFinite) || start < 0 || end <= start || fadeIn < 0 || fadeOut < 0) return setError("时间范围与 gain/fade 必须是有效数字，且结束时间大于开始时间。");
    setPending(true); setError(null); setSuccess(null);
    try {
      const mediaVersionId = await uploadProjectMediaFile(projectId, sourceFile);
      const result = await bindEpisodeAudio(episodeId, { media_version_id: mediaVersionId, track_type: trackType, start_us: Math.round(start * 1_000_000), end_us: Math.round(end * 1_000_000), gain_db: gain, source_license_status: licenseStatus as "VERIFIED_LOCAL" | "USER_OWNED" | "PUBLIC_DOMAIN", license_evidence_path_rel: licensePath.trim(), loop_enabled: loopEnabled, fade_in_us: Math.round(fadeIn * 1000), fade_out_us: Math.round(fadeOut * 1000) });
      setSuccess(`绑定已创建：${result.audio_binding.id.slice(0, 12)} · 授权证据已验证`);
      onBound();
    } catch (caught) {
      setError(`导入或绑定失败：${String(caught)}。若媒体已成功导入，它仍保留为未绑定版本，不会冒充已授权轨道。`);
    } finally { setPending(false); }
  };

  return <div className="audio-import-binding">
    <button className="secondary" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "收起本地音频绑定" : "导入并绑定本地音频"}</button>
    {expanded && <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div className="field-grid">
        <label>本地音频文件<input type="file" accept="audio/*" onChange={(event) => setSourceFile(event.target.files?.[0] ?? null)} /></label>
        <ProjectLocalResourceSelect projectId={projectId} kind="LICENSE_EVIDENCE" value={licensePath} onChange={setLicensePath} label="项目内授权证据" required emptyLabel="请选择授权证据" />
        <label>轨道<select value={trackType} onChange={(event) => setTrackType(event.target.value)} required><option value="">显式选择</option><option value="DIALOGUE">对白</option><option value="BGM">背景音乐</option><option value="SFX">音效</option><option value="ENVIRONMENT">环境（旧兼容）</option><option value="MUSIC">音乐（旧兼容）</option></select></label>
        <label>授权类型<select value={licenseStatus} onChange={(event) => setLicenseStatus(event.target.value)} required><option value="">显式选择</option><option value="USER_OWNED">用户拥有</option><option value="VERIFIED_LOCAL">本地授权已核验</option><option value="PUBLIC_DOMAIN">公有领域</option></select></label>
        <label>开始（秒）<input type="number" min="0" step="0.001" value={startSeconds} onChange={(event) => setStartSeconds(event.target.value)} required /></label>
        <label>结束（秒）<input type="number" min="0.001" step="0.001" value={endSeconds} onChange={(event) => setEndSeconds(event.target.value)} required /></label>
        <label>Gain（dB）<input type="number" step="0.1" value={gainDb} onChange={(event) => setGainDb(event.target.value)} required /></label>
        <label>淡入（ms）<input type="number" min="0" step="1" value={fadeInMs} onChange={(event) => setFadeInMs(event.target.value)} required /></label>
        <label>淡出（ms）<input type="number" min="0" step="1" value={fadeOutMs} onChange={(event) => setFadeOutMs(event.target.value)} required /></label>
        <label className="checkbox-label"><input type="checkbox" checked={loopEnabled} onChange={(event) => setLoopEnabled(event.target.checked)} />循环源音频以覆盖绑定范围</label>
      </div>
      <p className="muted">浏览器会把所选音频上传到当前项目并登记为不可变 AUDIO MediaVersion，再以项目内授权文件 SHA 创建绑定；不会调用公网或自动选择 Provider。</p>
      <button className="primary-action" type="submit" disabled={pending}>{pending ? "正在校验并绑定…" : "校验、导入并绑定"}</button>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {success && <p className="review-success" role="status">{success}</p>}
    </form>}
  </div>;
}

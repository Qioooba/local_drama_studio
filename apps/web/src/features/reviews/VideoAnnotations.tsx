import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createVideoAnnotation, listVideoAnnotations } from "../../generated/api";

const CATEGORIES = ["IDENTITY", "MOTION", "ARTIFACT", "FLICKER", "AUDIO_SYNC", "SUBTITLE", "CONTINUITY", "OTHER"];

export function VideoAnnotations({ mediaVersionId, durationMs, currentTimeMs, getCurrentTimeMs, onSeek }: { mediaVersionId: string; durationMs: number; currentTimeMs?: number; getCurrentTimeMs?: () => number; onSeek?: (timecodeMs: number) => void }) {
  const queryClient = useQueryClient();
  const [timecodeMs, setTimecodeMs] = useState(0);
  const [category, setCategory] = useState("ARTIFACT");
  const [comment, setComment] = useState("");
  const [snapshotId, setSnapshotId] = useState("");
  const [reworkJobId, setReworkJobId] = useState("");
  const annotations = useQuery({ queryKey: ["video-annotations", mediaVersionId], queryFn: () => listVideoAnnotations(mediaVersionId) });
  const create = useMutation({
    mutationFn: () => createVideoAnnotation(mediaVersionId, {
      timecode_ms: timecodeMs,
      category,
      comment,
      ...(snapshotId.trim() ? { snapshot_media_version_id: snapshotId.trim() } : {}),
      ...(reworkJobId.trim() ? { rework_job_id: reworkJobId.trim() } : {}),
    }),
    onSuccess: () => {
      setComment("");
      void queryClient.invalidateQueries({ queryKey: ["video-annotations", mediaVersionId] });
    },
  });
  const formatTimecode = (value: number) => `${String(Math.floor(value / 60_000)).padStart(2, "0")}:${String(Math.floor(value / 1_000) % 60).padStart(2, "0")}.${String(value % 1_000).padStart(3, "0")}`;
  return <section className="video-annotations" aria-labelledby="video-annotations-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-VID-008</p><h4 id="video-annotations-title">时间码问题标记</h4></div><span className="status-pill">{annotations.data?.items.length ?? 0} 条</span></div>
    <p className="muted">标记不可变；截图必须是当前视频真实派生帧，返工任务必须属于同一项目。</p>
    <div className="annotation-form">
      <label>时间码（毫秒）<input aria-label="时间码（毫秒）" type="number" min={0} max={Math.max(0, durationMs - 1)} value={timecodeMs} onChange={(event) => { if (event.target.value === "") return; setTimecodeMs(Number(event.target.value.replace(/^(-?)0+(?=\d)/, "$1"))); }} /></label>
      <button className="secondary" type="button" onClick={() => setTimecodeMs(Math.min(Math.max(0, Math.round(getCurrentTimeMs?.() ?? currentTimeMs ?? 0)), Math.max(0, durationMs - 1)))} disabled={currentTimeMs === undefined && !getCurrentTimeMs}>使用播放器当前时间</button>
      <label>问题分类<select aria-label="问题分类" value={category} onChange={(event) => setCategory(event.target.value)}>{CATEGORIES.map((item) => <option key={item}>{item}</option>)}</select></label>
      <label className="annotation-comment">备注<textarea aria-label="问题备注" value={comment} maxLength={4000} onChange={(event) => setComment(event.target.value)} placeholder="描述可复核的问题" /></label>
      <label>截图 MediaVersion（可选）<input aria-label="截图 MediaVersion（可选）" value={snapshotId} onChange={(event) => setSnapshotId(event.target.value)} /></label>
      <label>返工 Job（可选）<input aria-label="返工 Job（可选）" value={reworkJobId} onChange={(event) => setReworkJobId(event.target.value)} /></label>
      <button type="button" className="secondary" disabled={!comment.trim() || timecodeMs < 0 || timecodeMs >= durationMs || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "保存中…" : "保存标记"}</button>
    </div>
    {create.error && <p className="inline-error" role="alert">标记失败：{String(create.error)}</p>}
    {annotations.error && <p className="inline-error" role="alert">读取标记失败：{String(annotations.error)}</p>}
    <div className="annotation-list">{annotations.data?.items.map((item) => <article key={item.id}><button type="button" className="annotation-jump" onClick={() => onSeek?.(item.timecode_ms)} disabled={!onSeek}><strong>{formatTimecode(item.timecode_ms)} · {item.category}</strong></button><p>{item.comment}</p><small>{item.snapshot_media_version_id ? `截图 ${item.snapshot_media_version_id.slice(0, 12)}` : "无截图"} · {item.rework_job_id ? `返工 ${item.rework_job_id.slice(0, 12)}` : "未关联返工"}</small></article>)}</div>
  </section>;
}

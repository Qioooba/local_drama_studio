import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createReviewAnnotationV2, listReviewAnnotationsV2, type ReviewAnnotationCategory } from "../../generated/api";

const CATEGORIES = [
  ["IDENTITY", "人物身份或造型"],
  ["MOTION", "动作或运镜"],
  ["ARTIFACT", "画面瑕疵"],
  ["FLICKER", "画面闪烁"],
  ["AUDIO_SYNC", "音画不同步"],
  ["SUBTITLE", "字幕问题"],
  ["CONTINUITY", "镜头连续性"],
  ["OTHER", "其他问题"],
] as const;

const CATEGORY_LABELS = Object.fromEntries(CATEGORIES) as Record<string, string>;

function clampTimecode(value: number, durationMs: number) {
  return Math.min(Math.max(0, Math.round(value)), Math.max(0, durationMs - 1));
}

function formatTimecode(value: number) {
  return `${String(Math.floor(value / 60_000)).padStart(2, "0")}:${String(Math.floor(value / 1_000) % 60).padStart(2, "0")}.${String(value % 1_000).padStart(3, "0")}`;
}

export function VideoAnnotations({
  mediaVersionId,
  expectedRevision,
  durationMs,
  currentTimeMs,
  getCurrentTimeMs,
  onSeek,
}: {
  mediaVersionId: string;
  expectedRevision: number;
  durationMs: number;
  currentTimeMs?: number;
  getCurrentTimeMs?: () => number;
  onSeek?: (timecodeMs: number) => void;
}) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState("ARTIFACT");
  const [comment, setComment] = useState("");
  const annotations = useQuery({
    queryKey: ["review-v2", "annotations", mediaVersionId],
    queryFn: () => listReviewAnnotationsV2("MEDIA_VERSION", mediaVersionId, { limit: 100 }),
  });
  const currentTimecode = () => clampTimecode(getCurrentTimeMs?.() ?? currentTimeMs ?? 0, durationMs);
  const create = useMutation({
    mutationFn: (commandKey: string) => createReviewAnnotationV2("MEDIA_VERSION", mediaVersionId, {
      expected_revision: expectedRevision,
      timecode_ms: currentTimecode(),
      category: category as ReviewAnnotationCategory,
      comment: comment.trim(),
      idempotency_key: commandKey,
    }),
    onSuccess: () => {
      setComment("");
      void queryClient.invalidateQueries({ queryKey: ["review-v2", "annotations", mediaVersionId] });
    },
  });

  return (
    <section className="video-annotations" aria-labelledby="video-annotations-title">
      <div className="panel-heading">
        <div><p className="eyebrow">逐帧批注</p><h4 id="video-annotations-title">标记当前画面的问题</h4></div>
        <span className="status-pill">{annotations.data?.items.length ?? 0} 条</span>
      </div>
      <p className="muted">暂停到问题画面后直接保存，系统会自动记录当前时间并绑定真实派生画面；不需要填写时间码或任务标识。</p>
      <div className="annotation-form">
        <label>问题分类
          <select aria-label="问题分类" value={category} onChange={(event) => setCategory(event.target.value)}>
            {CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="annotation-comment">问题描述
          <textarea aria-label="问题描述" value={comment} maxLength={4000} onChange={(event) => setComment(event.target.value)} placeholder="描述画面中需要修改的地方" />
        </label>
        <button type="button" className="secondary" disabled={!comment.trim() || durationMs <= 0 || create.isPending} onClick={() => create.mutate(`review-annotation:${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`)}>
          {create.isPending ? "保存中…" : "标记播放器当前画面"}
        </button>
      </div>
      {create.error && <p className="inline-error" role="alert">标记失败：{String(create.error)}</p>}
      {annotations.error && <p className="inline-error" role="alert">读取标记失败：{String(annotations.error)}</p>}
      <div className="annotation-list">
        {annotations.data?.items.map((item) => (
          <article key={item.id}>
            <button type="button" className="annotation-jump" onClick={() => onSeek?.(item.timecode_ms)} disabled={!onSeek}>
              <strong>{formatTimecode(item.timecode_ms)} · {CATEGORY_LABELS[item.category] ?? "其他问题"}</strong>
            </button>
            <p>{item.comment}</p>
            <small>{item.snapshot_media_version_id ? "已保存对应画面" : "画面正在由系统派生"}{item.rework_job_id ? " · 已关联返工" : ""}</small>
          </article>
        ))}
      </div>
    </section>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import type { DirectorDeskCandidate } from "./types";
import { fallbackToOriginalVideo, mediaContentUrl, mediaProxyUrl } from "../shared/mediaPlaybackPolicy";
import "./candidate-compare.css";

function thumbnailUrl(mediaVersionId: string) {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=medium&frame=poster`;
}

function durationLabel(durationMs: number | null) {
  if (durationMs == null) return "时长未探测";
  return `${(durationMs / 1000).toFixed(2)}s`;
}

export function CandidateCompareDialog({ candidates, initialCandidateId, onClose }: { candidates: DirectorDeskCandidate[]; initialCandidateId?: string | null; onClose: () => void }) {
  const initial = useMemo(() => {
    const preferred = initialCandidateId ? candidates.find((candidate) => candidate.media_version_id === initialCandidateId) : undefined;
    return [preferred, ...candidates.filter((candidate) => candidate !== preferred)].filter(Boolean).slice(0, Math.min(2, candidates.length)).map((candidate) => candidate!.media_version_id);
  }, [candidates, initialCandidateId]);
  const [selectedIds, setSelectedIds] = useState<string[]>(initial);
  const [playing, setPlaying] = useState(false);
  const videoRefs = useRef(new Map<string, HTMLVideoElement>());
  const closeRef = useRef<HTMLButtonElement>(null);
  const selected = candidates.filter((candidate) => selectedIds.includes(candidate.media_version_id));

  useEffect(() => { closeRef.current?.focus(); }, []);
  useEffect(() => () => { for (const video of videoRefs.current.values()) video.pause(); }, []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
      if (event.key === " ") { event.preventDefault(); void togglePlayback(); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const toggleCandidate = (id: string) => {
    setSelectedIds((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length < 4 ? [...current, id] : current);
  };
  const togglePlayback = async () => {
    const videos = [...videoRefs.current.values()];
    if (playing) {
      videos.forEach((video) => video.pause());
      setPlaying(false);
    } else {
      await Promise.allSettled(videos.map((video) => video.play()));
      setPlaying(true);
    }
  };
  const restart = () => {
    for (const video of videoRefs.current.values()) { video.pause(); video.currentTime = 0; }
    setPlaying(false);
  };

  return <div className="compare-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className="compare-dialog" role="dialog" aria-modal="true" aria-labelledby="candidate-compare-title">
      <header><div><span className="director-kicker">2-up / 4-up Compare</span><h2 id="candidate-compare-title">并排比较候选</h2><p>同一组播放控制只影响这里的视频；采用与批准仍回到 Takes 区分别执行。</p></div><button ref={closeRef} type="button" className="compare-close" aria-label="关闭候选比较" onClick={onClose}>关闭</button></header>
      <div className="compare-picker" aria-label="选择比较候选">
        {candidates.map((candidate, index) => <label key={candidate.media_version_id} className={selectedIds.includes(candidate.media_version_id) ? "selected" : ""}><input type="checkbox" checked={selectedIds.includes(candidate.media_version_id)} disabled={!selectedIds.includes(candidate.media_version_id) && selectedIds.length >= 4} onChange={() => toggleCandidate(candidate.media_version_id)} /><span>Take {candidate.take_no ?? index + 1}</span><small>{candidate.approved ? "已批准" : candidate.selected ? "已采用" : candidate.is_stale ? "已失效" : "待比较"}</small></label>)}
      </div>
      <div className="compare-controls"><button type="button" onClick={() => void togglePlayback()} disabled={!selected.some((candidate) => candidate.media_kind === "VIDEO")}>{playing ? "全部暂停" : "全部播放"} <kbd>Space</kbd></button><button type="button" onClick={restart} disabled={!selected.some((candidate) => candidate.media_kind === "VIDEO")}>回到开头</button><span>已选择 {selected.length} / 4</span></div>
      <div className={`compare-grid compare-count-${selected.length}`}>
        {selected.map((candidate, index) => <figure key={candidate.media_version_id}>
          <div className="compare-media">{candidate.media_kind === "VIDEO" ? <video ref={(node) => { if (node) videoRefs.current.set(candidate.media_version_id, node); else videoRefs.current.delete(candidate.media_version_id); }} src={mediaProxyUrl(candidate.media_version_id)} data-original-src={mediaContentUrl(candidate.media_version_id)} poster={thumbnailUrl(candidate.media_version_id)} preload="none" muted playsInline onError={fallbackToOriginalVideo} onPlay={() => setPlaying(true)} onPause={() => { if ([...videoRefs.current.values()].every((video) => video.paused)) setPlaying(false); }} /> : <img src={thumbnailUrl(candidate.media_version_id)} alt={`候选 ${index + 1}`} />}</div>
          <figcaption><strong>Take {candidate.take_no ?? index + 1}</strong><span>{candidate.stage ?? "未知阶段"} · {durationLabel(candidate.duration_ms)}</span><span>{candidate.branch_reason || "原始候选"}</span>{candidate.is_stale && <em>输入已变化：{candidate.stale_reason ?? "需要重新生成"}</em>}</figcaption>
        </figure>)}
        {selected.length === 0 && <div className="compare-empty">至少选择一个候选。最多可同时比较四个。</div>}
      </div>
    </section>
  </div>;
}

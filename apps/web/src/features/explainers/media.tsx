/**
 * Real media players and a manifest-driven review timeline.
 *
 * The audit found that the review page rendered ``MediaPlaceholder`` (which always
 * says "尚未生成媒体") even when a verified render existed, that the frame buttons
 * only moved a local integer, and that the time was computed as ``frame * 40`` —
 * a hard-coded 25 fps with no upper bound.  The narration page had no ``<audio>``
 * at all.  These components take the read model's media facts and drive a real
 * ``HTMLMediaElement``; when there is nothing playable they say exactly why.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { InlineError, InlineOk } from "./components";
import { formatMs } from "./viewModels";

export type RenderMedia = {
  id: string;
  media_version_id: string | null;
  mime_type: string | null;
  byte_size: number | null;
  duration_ms: number | null;
  frame_count: number | null;
  fps_num: number | null;
  fps_den: number | null;
  playback_url: string | null;
  thumbnail_url?: string | null;
  availability: string;
  playable: boolean;
  sha256: string | null;
  integrity_status?: string | null;
  status?: string | null;
};

type MediaState = "NOT_GENERATED" | "LOADING" | "PLAYABLE" | "MEDIA_MISSING" | "READ_ERROR";

const AVAILABILITY_COPY: Record<string, { state: MediaState; text: string }> = {
  PLAYABLE: { state: "PLAYABLE", text: "可播放" },
  NOT_READY: { state: "LOADING", text: "渲染尚未完成，暂时不能播放" },
  INTEGRITY_FAILED: { state: "READ_ERROR", text: "媒体完整性校验未通过，拒绝播放" },
  MEDIA_REFERENCE_MISSING: { state: "MEDIA_MISSING", text: "渲染记录存在，但已找不到对应的媒体版本" },
};

function availabilityFor(media: RenderMedia | null | undefined): { state: MediaState; text: string } {
  if (!media) return { state: "NOT_GENERATED", text: "尚未生成成片" };
  if (!media.playable) {
    return AVAILABILITY_COPY[String(media.availability)] ?? {
      state: "READ_ERROR",
      text: `媒体状态不可播放（${String(media.availability)}）`,
    };
  }
  if (!media.playback_url) return { state: "MEDIA_MISSING", text: "缺少受控播放地址" };
  return { state: "LOADING", text: "正在载入媒体…" };
}

/**
 * One real ``<video>`` for a verified render.
 *
 * Frame stepping converts a frame index to seconds with the **rational** frame
 * rate (``frame * fps_den / fps_num``) and clamps to ``0 … frame_count - 1``.  The
 * displayed position is updated from the element's own events, never from the
 * requested target, so the number on screen is where the decoder actually is.
 */
export function RenderPlayer({
  media,
  seekToFrame,
  onFrameChange,
  onDurationChange,
}: {
  media: RenderMedia | null | undefined;
  seekToFrame?: number | null;
  onFrameChange?: (frame: number) => void;
  onDurationChange?: (seconds: number) => void;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [mediaState, setMediaState] = useState<MediaState>(() => availabilityFor(media).state);
  const [detail, setDetail] = useState<string>(() => availabilityFor(media).text);
  const [currentTime, setCurrentTime] = useState(0);
  const [attempt, setAttempt] = useState(0);

  const fpsNum = Number(media?.fps_num ?? 0);
  const fpsDen = Number(media?.fps_den ?? 0);
  const totalFrames = media?.frame_count === null || media?.frame_count === undefined ? null : Number(media.frame_count);
  const hasRationalFps = fpsNum > 0 && fpsDen > 0;
  const frameSeconds = hasRationalFps ? fpsDen / fpsNum : null;

  // Reset whenever the bound media changes: switching editions must not keep the
  // previous film's state, selection or frame counter.
  useEffect(() => {
    const next = availabilityFor(media);
    setMediaState(next.state);
    setDetail(next.text);
    setCurrentTime(0);
  }, [media?.id, media?.availability, media?.playback_url, attempt]);

  const handleLoadedMetadata = useCallback(() => {
    const element = videoRef.current;
    setMediaState("PLAYABLE");
    setDetail("可播放");
    if (element && onDurationChange) onDurationChange(element.duration || 0);
  }, [onDurationChange]);

  const handleError = useCallback(() => {
    setMediaState("READ_ERROR");
    setDetail("媒体读取失败：文件可能已移动、损坏或编码不被当前浏览器支持。");
  }, []);

  const handleTimeUpdate = useCallback(() => {
    const element = videoRef.current;
    if (!element) return;
    setCurrentTime(element.currentTime);
    if (frameSeconds && onFrameChange) {
      onFrameChange(Math.max(0, Math.min(Math.floor(element.currentTime / frameSeconds), (totalFrames ?? 1) - 1)));
    }
  }, [frameSeconds, onFrameChange, totalFrames]);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || seekToFrame === null || seekToFrame === undefined || !frameSeconds) return;
    if (mediaState !== "PLAYABLE") return;
    const clamped = Math.max(0, Math.min(Number(seekToFrame), (totalFrames ?? Number(seekToFrame) + 1) - 1));
    const target = clamped * frameSeconds;
    if (Math.abs(element.currentTime - target) < frameSeconds / 4) return;
    // Browsers seek to the nearest decodable frame; the displayed frame is updated
    // from the element's own ``timeupdate``/``seeked`` events afterwards.
    element.currentTime = target;
  }, [seekToFrame, frameSeconds, mediaState, totalFrames]);

  const currentFrame = frameSeconds ? Math.max(0, Math.floor(currentTime / frameSeconds)) : null;

  return (
    <div className="explainer-player">
      {mediaState === "PLAYABLE" || mediaState === "LOADING" ? (
        <video
          ref={videoRef}
          className="explainer-player-media"
          controls
          // The repository's media URL policy keeps a full render from loading until
          // the operator asks for it, and requires the poster to come from the
          // thumbnail endpoint rather than from the video body.
          preload="none"
          poster={media?.thumbnail_url ?? undefined}
          src={media?.playback_url ?? undefined}
          onLoadedMetadata={handleLoadedMetadata}
          onError={handleError}
          onTimeUpdate={handleTimeUpdate}
          onSeeked={handleTimeUpdate}
        />
      ) : (
        <div className="explainer-placeholder" role="status" aria-label={`成片预览（${detail}）`}>
          <div>
            <strong>成片预览</strong>
            <div>{detail}</div>
          </div>
        </div>
      )}
      <div className="explainer-player-meta">
        <span className={`badge ${mediaState === "PLAYABLE" ? "green" : mediaState === "READ_ERROR" ? "danger" : "warn"}`}>
          {mediaState === "PLAYABLE"
            ? "可播放"
            : mediaState === "LOADING"
              ? "载入中"
              : mediaState === "NOT_GENERATED"
                ? "尚未生成"
                : mediaState === "MEDIA_MISSING"
                  ? "媒体缺失"
                  : "读取失败"}
        </span>
        {hasRationalFps ? (
          <span className="badge">
            {fpsNum}/{fpsDen} fps · {totalFrames ?? "?"} 帧
          </span>
        ) : (
          <span className="badge warn">该版本未记录帧率，不能按帧定位</span>
        )}
        <span className="badge">
          {currentFrame === null ? "位置未知" : `当前帧 ${currentFrame}`} · {formatMs(currentTime * 1000)}
        </span>
        {media?.mime_type ? <span className="badge">{media.mime_type}</span> : null}
        {media?.byte_size ? <span className="badge">{Math.round(media.byte_size / 1024)} KB</span> : null}
      </div>
      {mediaState === "READ_ERROR" || mediaState === "MEDIA_MISSING" ? (
        <div className="explainer-actions">
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>重新载入媒体</button>
          {media?.media_version_id ? (
            <a className="explainer-source-link" href={media.playback_url ?? "#"} target="_blank" rel="noreferrer">
              在新窗口打开受控媒体地址 →
            </a>
          ) : null}
          <span className="muted">仍失败时请检查渲染产物与媒体登记，不会退回占位画面假装可审。</span>
        </div>
      ) : null}
      <InlineError message={mediaState === "READ_ERROR" ? detail : null} />
    </div>
  );
}

/** One real ``<audio>`` for a narration take. */
export function NarrationPlayer({
  src,
  label,
  durationMs,
}: {
  src: string | null | undefined;
  label: string;
  durationMs?: number | null;
}) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    setFailed(false);
  }, [src, attempt]);
  if (!src) {
    return <p className="muted" role="status">这一段还没有可试听的配音音频（不会显示伪波形）。</p>;
  }
  return (
    <div className="explainer-audio">
      {/* key forces a fresh element on retry so a failed load is really retried */}
      <audio key={`${src}-${attempt}`} controls preload="metadata" src={src} onError={() => setFailed(true)} />
      <span className="muted">
        {label}
        {durationMs ? ` · 实测 ${formatMs(durationMs)}` : ""}
      </span>
      {failed ? (
        <span className="explainer-actions">
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>重新载入音频</button>
        </span>
      ) : null}
      {failed ? <InlineError message="音频读取失败；请检查 take 的媒体版本是否仍然可用。" /> : null}
    </div>
  );
}

export type TimelineLane = {
  track: string;
  label: string;
  totalMs: number;
  segments: Array<{ id: string; itemKind: string; startMs: number; endMs: number; label: string }>;
};

/**
 * A read-only timeline drawn from the frozen manifest's own clip ranges.
 *
 * The previous lane view was three fixed boxes labelled 起始/主体/收束 plus a few
 * fake cues.  If the manifest is unavailable the timeline says so instead of
 * inventing structure.
 */
export function ReviewTimeline({ lanes, busy }: { lanes: TimelineLane[]; busy: boolean }) {
  const [hover, setHover] = useState<string | null>(null);
  if (busy) return <p className="muted">正在载入冻结 manifest 时间线…</p>;
  if (lanes.length === 0) {
    return <p className="muted">尚无可绘制的 manifest 区间；时间线不会用占位结构代替真实数据。</p>;
  }
  return (
    <div className="explainer-timeline">
      {lanes.map((lane) => (
        <div className="explainer-timeline-lane" key={lane.track}>
          <span>{lane.label}</span>
          <div className="explainer-timeline-clips" title={`${lane.track} · 共 ${lane.segments.length} 个区间`}>
            {lane.segments.map((segment) => (
              <b
                key={segment.id}
                className={`explainer-timeline-seg ${segment.itemKind === "AUDIO_CLIP" ? "audio" : ""}`}
                style={{ flexGrow: Math.max(1, segment.endMs - segment.startMs) }}
                title={`${segment.label} · ${formatMs(segment.startMs)}–${formatMs(segment.endMs)}`}
                onMouseEnter={() => setHover(`${segment.label} · ${formatMs(segment.startMs)}–${formatMs(segment.endMs)}`)}
                onMouseLeave={() => setHover(null)}
              >
                {segment.label}
              </b>
            ))}
          </div>
        </div>
      ))}
      <p className="explainer-note">
        {hover ? `悬停区间：${hover}` : "区间来自冻结 manifest；点击问题会把播放器定位到它的证据区间。"}
      </p>
    </div>
  );
}

/** Build lanes from the manifest composition items the read model exposes. */
export function lanesFromManifest(
  items: Array<Record<string, unknown>>,
  {
    fpsNum,
    fpsDen,
    videoLabel = "画面",
  }: { fpsNum: number | null | undefined; fpsDen: number | null | undefined; videoLabel?: string },
): TimelineLane[] {
  const num = Number(fpsNum ?? 0);
  const den = Number(fpsDen ?? 0);
  if (!(num > 0 && den > 0) || items.length === 0) return [];
  const toMs = (frame: unknown) => (Number(frame) * den * 1000) / num;
  const byTrack = new Map<string, TimelineLane>();
  for (const item of items) {
    const track = String(item.track ?? "UNKNOWN").toUpperCase();
    const lane =
      byTrack.get(track) ??
      {
        track,
        label: track === "VIDEO" ? videoLabel : track === "NARRATION" ? "旁白" : track === "BGM" ? "音乐" : track === "SUBTITLE" ? "字幕" : track,
        totalMs: 0,
        segments: [],
      };
    const startMs = toMs(item.start_frame);
    const endMs = toMs(item.end_frame_exclusive);
    lane.segments.push({
      id: String(item.id ?? `${track}-${lane.segments.length}`),
      itemKind: String(item.item_kind ?? ""),
      startMs,
      endMs,
      label: `${track === "SUBTITLE" ? "cue" : track.toLowerCase()} ${lane.segments.length + 1}`,
    });
    lane.totalMs = Math.max(lane.totalMs, endMs);
    byTrack.set(track, lane);
  }
  return [...byTrack.values()];
}

/** Pure helper: the frame a millisecond offset falls on, using the rational rate. */
export function frameForMs(ms: number, fpsNum: number | null | undefined, fpsDen: number | null | undefined): number | null {
  const num = Number(fpsNum ?? 0);
  const den = Number(fpsDen ?? 0);
  if (!(num > 0 && den > 0)) return null;
  return Math.max(0, Math.floor((ms / 1000) * (num / den)));
}

/** Pure helper: clamp a requested frame into the render's real range. */
export function clampFrame(frame: number, totalFrames: number | null | undefined): number {
  if (!totalFrames || totalFrames <= 0) return Math.max(0, Math.floor(frame));
  return Math.max(0, Math.min(Math.floor(frame), totalFrames - 1));
}

export function useSortedIssues(
  issues: Array<Record<string, unknown>>,
  options: { pageSize?: number } = {},
) {
  const pageSize = options.pageSize ?? 8;
  const [visible, setVisible] = useState(pageSize);
  const severityRank = useMemo(() => ({ BLOCKER: 0, MAJOR: 1, MINOR: 2, UNKNOWN: 3 }) as Record<string, number>, []);
  const sorted = useMemo(() => {
    return [...issues].sort((left, right) => {
      const rank = (severityRank[String(left.severity)] ?? 9) - (severityRank[String(right.severity)] ?? 9);
      if (rank !== 0) return rank;
      const start = Number(left.start_ms ?? -1) - Number(right.start_ms ?? -1);
      if (start !== 0) return start;
      // Stable final tiebreaker so the list does not jump between refetches.
      return String(left.id).localeCompare(String(right.id));
    });
  }, [issues, severityRank]);
  useEffect(() => {
    setVisible(pageSize);
  }, [issues.length, pageSize]);
  return {
    sorted,
    visible: sorted.slice(0, visible),
    hiddenCount: Math.max(0, sorted.length - visible),
    showMore: () => setVisible((value) => value + pageSize),
    showAll: () => setVisible(sorted.length),
    expanded: visible >= sorted.length,
  };
}

export { InlineOk };

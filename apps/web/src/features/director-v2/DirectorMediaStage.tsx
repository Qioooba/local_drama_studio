import { useCallback, useEffect, useRef, useState } from "react";
import { fallbackToOriginalVideo, mediaContentUrl, mediaProxyUrl } from "../shared/mediaPlaybackPolicy";
import { MediaThumbnail } from "../shared/MediaThumbnail";
import "./director-media-stage.css";

export type DirectorStageMedia = {
  mediaVersionId: string;
  mediaKind: string | null;
  mimeType?: string | null;
  durationMs?: number | null;
  thumbnailReady?: boolean;
};

export type DirectorMediaStageProps = {
  media: DirectorStageMedia | null;
  comparisonMedia?: DirectorStageMedia | null;
  label: string;
  badges?: string[];
  emptyTitle?: string;
  emptyDescription?: string;
  emptyActionLabel?: string;
  onEmptyAction?: () => void;
  keyboardShortcutsEnabled?: boolean;
};

export function directorThumbnailUrl(mediaVersionId: string) {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=medium&frame=poster`;
}

export function directorContentUrl(mediaVersionId: string) {
  return mediaContentUrl(mediaVersionId);
}

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function StageIcon({ name }: { name: "frame" | "play" | "pause" | "restart" | "volume" | "muted" }) {
  const paths = {
    frame: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m6 16 4-4 3 3 3-3 3 4" /></>,
    play: <path d="m9 7 9 5-9 5Z" />,
    pause: <><path d="M9 7v10M15 7v10" /></>,
    restart: <><path d="M6.5 8H3V4.5" /><path d="M4 8a9 9 0 1 1-.5 7" /></>,
    volume: <><path d="M5 10h3l4-3v10l-4-3H5Z" /><path d="M15 9a4 4 0 0 1 0 6" /></>,
    muted: <><path d="M5 10h3l4-3v10l-4-3H5Z" /><path d="m16 10 4 4m0-4-4 4" /></>,
  };
  return <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">{paths[name]}</svg>;
}

export function DirectorMediaStage({ media, comparisonMedia = null, label, badges = [], emptyTitle = "等待首个画面", emptyDescription = "当前镜头还没有已选中或已批准的媒体。", emptyActionLabel = "生成候选", onEmptyAction, keyboardShortcutsEnabled = true }: DirectorMediaStageProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [muted, setMuted] = useState(true);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState((media?.durationMs ?? 0) / 1000);
  const [viewMode, setViewMode] = useState<"fit" | "actual">("fit");
  const [gridVisible, setGridVisible] = useState(false);
  const [safeFrameVisible, setSafeFrameVisible] = useState(false);
  const [detailZoom, setDetailZoom] = useState(false);
  const [showComparison, setShowComparison] = useState(false);
  const isVideo = media?.mediaKind?.toUpperCase() === "VIDEO" || media?.mimeType?.toLowerCase().startsWith("video/");
  const canFlicker = !isVideo && comparisonMedia && comparisonMedia.mediaVersionId !== media?.mediaVersionId;

  useEffect(() => { setPlaying(false); setLoading(false); setError(null); setCurrentTime(0); setDuration((media?.durationMs ?? 0) / 1000); }, [media?.mediaVersionId, media?.durationMs]);

  const togglePlayback = useCallback(async () => {
    const video = videoRef.current;
    if (!video || error) return;
    if (!video.paused) { video.pause(); return; }
    setLoading(true);
    try { await video.play(); } catch (playError) { setLoading(false); setError(playError instanceof Error ? playError.message : "视频无法播放"); }
  }, [error]);

  useEffect(() => {
    if (!isVideo || !keyboardShortcutsEnabled) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.code !== "Space") return;
      const target = event.target;
      if (target instanceof Element && target.closest("input, textarea, select, button, a, [contenteditable='true'], [role='button'], [role='dialog']")) return;
      event.preventDefault();
      void togglePlayback();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isVideo, keyboardShortcutsEnabled, togglePlayback]);

  const restart = async () => { const video = videoRef.current; if (!video || error) return; video.currentTime = 0; setCurrentTime(0); setLoading(true); try { await video.play(); } catch (playError) { setLoading(false); setError(playError instanceof Error ? playError.message : "视频无法播放"); } };

  return <div className={`director-media-stage${isVideo ? " is-video" : " is-image"} view-${viewMode}${detailZoom ? " detail-zoom" : ""}`}>
    {!media ? <div className="director-stage-empty"><StageIcon name="frame" /><strong>{emptyTitle}</strong><span>{emptyDescription}</span>{onEmptyAction && <button type="button" className="director-button primary" onClick={onEmptyAction}>{emptyActionLabel}</button>}</div>
      : isVideo ? <>
        <video ref={videoRef} src={mediaProxyUrl(media.mediaVersionId)} data-original-src={directorContentUrl(media.mediaVersionId)} poster={directorThumbnailUrl(media.mediaVersionId)} preload="none" playsInline muted={muted} aria-label={`${label} 视频预览`} onClick={() => void togglePlayback()} onWaiting={() => setLoading(true)} onCanPlay={() => setLoading(false)} onPlaying={() => { setPlaying(true); setLoading(false); }} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)} onLoadedMetadata={(event) => { if (Number.isFinite(event.currentTarget.duration)) setDuration(event.currentTarget.duration); }} onError={(event) => { if (fallbackToOriginalVideo(event)) return; setLoading(false); setPlaying(false); setError("视频读取失败；请检查媒体完整性或任务产物。"); }} />
        {!playing && !loading && !error && <button type="button" className="director-media-stage__center-play" aria-label="播放视频（Space）" onClick={() => void togglePlayback()}><StageIcon name="play" /></button>}
        {loading && <div className="director-media-stage__loading" role="status"><span aria-hidden="true" />正在读取视频 Range…</div>}
        {error && <div className="director-media-stage__error" role="alert"><strong>无法播放当前视频</strong><span>{error}</span><button type="button" onClick={() => { setError(null); videoRef.current?.load(); }}>重新加载</button></div>}
        <div className="director-media-stage__controls" aria-label="视频播放控制"><button type="button" aria-label={playing ? "暂停（Space）" : "播放（Space）"} onClick={() => void togglePlayback()} disabled={Boolean(error)}><StageIcon name={playing ? "pause" : "play"} /></button><button type="button" aria-label="从头播放" onClick={() => void restart()} disabled={Boolean(error)}><StageIcon name="restart" /></button><span aria-label={`播放时间 ${formatTime(currentTime)}，总时长 ${formatTime(duration)}`}>{formatTime(currentTime)} / {formatTime(duration)}</span><input aria-label="视频进度" type="range" min="0" max={Math.max(duration, .01)} step="0.05" value={Math.min(currentTime, duration || 0)} disabled={!duration || Boolean(error)} onChange={(event) => { const next = Number(event.target.value); if (videoRef.current) videoRef.current.currentTime = next; setCurrentTime(next); }} /><button type="button" aria-label={muted ? "取消静音" : "静音"} aria-pressed={muted} onClick={() => setMuted((value) => !value)}><StageIcon name={muted ? "muted" : "volume"} /></button></div>
      </> : media.thumbnailReady === false
        ? <span className="media-thumbnail-fallback" role="img" aria-label={`${label}缩略图待生成`}><span aria-hidden="true">◫</span><small>缩略图待生成</small></span>
        : <MediaThumbnail src={directorThumbnailUrl(showComparison && comparisonMedia ? comparisonMedia.mediaVersionId : media.mediaVersionId)} alt={`${label}${showComparison ? " 对照" : ""}图片缩略图`} fallbackLabel={`${label}缩略图待生成`} loading="eager" decoding="async" />}
    {media && <div className="director-media-stage__view-tools" aria-label="画面检查工具">
      <button type="button" aria-pressed={viewMode === "fit"} onClick={() => { setViewMode("fit"); setDetailZoom(false); }}>适合</button>
      <button type="button" aria-pressed={viewMode === "actual"} onClick={() => { setViewMode("actual"); setDetailZoom(false); }}>100%</button>
      <button type="button" aria-pressed={detailZoom} onClick={() => setDetailZoom((value) => !value)}>细节 ×2</button>
      <button type="button" aria-pressed={gridVisible} onClick={() => setGridVisible((value) => !value)}>九宫格</button>
      <button type="button" aria-pressed={safeFrameVisible} onClick={() => setSafeFrameVisible((value) => !value)}>安全框</button>
      {canFlicker && <button type="button" aria-pressed={showComparison} onPointerDown={() => setShowComparison(true)} onPointerUp={() => setShowComparison(false)} onPointerCancel={() => setShowComparison(false)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") setShowComparison(true); }} onKeyUp={() => setShowComparison(false)}>按住闪切</button>}
    </div>}
    {gridVisible && <div className="director-media-stage__grid" aria-hidden="true" />}
    {safeFrameVisible && <div className="director-media-stage__safe-frame" aria-hidden="true"><span>标题安全区</span></div>}
    {badges.length > 0 && <div className="director-media-badges">{badges.map((badge) => <span key={badge}>{badge}</span>)}</div>}
  </div>;
}

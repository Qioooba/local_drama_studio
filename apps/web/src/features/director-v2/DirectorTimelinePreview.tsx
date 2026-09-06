import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { getEpisodeEditWorkspaceV2 } from "../../generated/api";
import { TimelineLanes, timelineTimeLabel } from "../edit-v2/TimelineLanes";
import { fallbackToOriginalVideo, mediaContentUrl, mediaProxyUrl } from "../shared/mediaPlaybackPolicy";

export function DirectorTimelinePreview({
  projectId,
  episodeId,
  currentShotId,
  onSelectShot,
}: {
  projectId: string;
  episodeId: string;
  currentShotId: string;
  onSelectShot: (shotId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [playheadUs, setPlayheadUs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(1);
  const videoRef = useRef<HTMLVideoElement>(null);
  const query = useQuery({
    queryKey: ["post-edit-v2", episodeId],
    queryFn: () => getEpisodeEditWorkspaceV2(episodeId),
    enabled: expanded,
  });
  const data = query.data?.workspace;
  const durationUs = Math.max(data?.duration_us ?? 0, data?.video_clips.at(-1)?.end_us ?? 0);

  const clips = useMemo(() => {
    if (!data) return [];
    let cursor = 0;
    return data.video_clips.map((clip) => {
      const duration = clip.end_us - clip.start_us;
      const timed = { ...clip, start_us: cursor, end_us: cursor + duration } as typeof clip & { start_us: number; end_us: number };
      cursor += duration;
      return timed;
    });
  }, [data]);

  // map playhead to clip
  const activeClip = useMemo(() => {
    if (!clips.length) return null;
    return clips.find((clip) => playheadUs >= clip.start_us && playheadUs < clip.end_us) ?? clips.at(-1) ?? null;
  }, [clips, playheadUs]);

  const activeOffsetSec = useMemo(() => {
    if (!activeClip) return 0;
    return Math.max(0, (playheadUs - activeClip.start_us + (activeClip.source_start_us ?? 0)) / 1_000_000);
  }, [activeClip, playheadUs]);

  useEffect(() => {
    if (!data) return;
    const current = clips.find((clip) => clip.shot_id === currentShotId);
    if (current) setPlayheadUs(current.start_us);
  }, [currentShotId, data?.upstream_fingerprint, data?.latest_revision?.id, clips]);

  // sync video currentTime when playhead changes while paused or when clip changes
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !activeClip || !activeClip.media_version_id) return;
    // only seek when difference is significant or clip changed
    const expected = activeOffsetSec;
    if (Math.abs(video.currentTime - expected) > 0.3) {
      video.currentTime = expected;
    }
    const expectedSrc = mediaProxyUrl(String(activeClip.media_version_id));
    if (video.src !== expectedSrc) {
      // src will be updated via render; let effect handle
    }
  }, [activeClip, activeOffsetSec, activeClip?.media_version_id]);

  // playback loop: advance playhead via rAF when playing
  useEffect(() => {
    if (!playing || !durationUs) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const deltaMs = now - last;
      last = now;
      setPlayheadUs((prev) => {
        const next = prev + deltaMs * 1000;
        if (next >= durationUs) {
          setPlaying(false);
          return durationUs;
        }
        // also keep video in sync every ~200ms
        const clip = clips.find((c) => next >= c.start_us && next < c.end_us) ?? clips.at(-1);
        if (clip && videoRef.current && clip.media_version_id !== activeClip?.media_version_id) {
          // clip change will be handled by activeClip memo and video src re-render
        }
        return Math.round(next);
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, durationUs, clips, activeClip]);

  // when video time updates (if user plays native video), sync playhead
  const handleVideoTimeUpdate = (event: React.SyntheticEvent<HTMLVideoElement>) => {
    if (!activeClip) return;
    if (!playing) return; // only sync when our playback is active; native video play should drive playhead as fallback
    const video = event.currentTarget;
    const videoElapsedUs = Math.max(0, (video.currentTime - activeClip.source_start_us / 1_000_000) * 1_000_000);
    // ensure within clip
    const global = activeClip.start_us + videoElapsedUs;
    // clamp
    setPlayheadUs(Math.max(0, Math.min(durationUs, Math.round(global))));
  };

  const togglePlay = async () => {
    const video = videoRef.current;
    if (!clips.length) return;
    if (playing) {
      setPlaying(false);
      video?.pause();
      return;
    }
    // if at end, restart
    if (playheadUs >= durationUs - 20000) setPlayheadUs(0);
    setPlaying(true);
    if (video) {
      try {
        video.currentTime = activeOffsetSec;
        await video.play();
      } catch {
        // fallback to synthetic playback even if video fails
      }
    }
  };

  const handleScrub = (value: number) => {
    setPlayheadUs(value);
    // keep video seeked
    if (videoRef.current && activeClip) {
      const offset = Math.max(0, (value - activeClip.start_us) / 1_000_000 + activeClip.source_start_us / 1_000_000);
      // defer seek after clip update if clip changed
      requestAnimationFrame(() => {
        if (videoRef.current) videoRef.current.currentTime = offset;
      });
    }
  };

  return (
    <details className="director-timeline-preview" onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>
        <span>
          <strong>本集同步预览</strong>
          <small>读取后期采用视频、对白、BGM/SFX 与字幕；播放头可拖动，支持播放</small>
        </span>
        <span aria-hidden="true">⌄</span>
      </summary>
      {expanded && query.isPending ? <p className="director-timeline-preview-state" role="status">正在读取本集时间线…</p> : null}
      {expanded && query.isError ? (
        <p className="director-timeline-preview-state error" role="alert">
          时间线读取失败。
          <button type="button" onClick={() => void query.refetch()}>
            重试
          </button>
        </p>
      ) : null}
      {expanded && data ? (
        <div className="director-timeline-preview-body">
          <header>
            <div>
              <strong>
                {data.episode_code} · {clips.length} 镜
              </strong>
              <small>
                {data.freshness === "STALE"
                  ? "上游已变化，预览显示最新聚合事实"
                  : data.latest_revision
                    ? `时间线 v${data.latest_revision.revision_no} · ${data.latest_revision.status}`
                    : "尚未保存时间线版本"}
              </small>
            </div>
            <Link to={routes.postEdit(projectId, episodeId)}>进入后期编辑</Link>
          </header>
          {data.issues.length ? (
            <p className="director-timeline-preview-issue">
              {data.issues[0].message}
              {data.issues.length > 1 ? `，另有 ${data.issues.length - 1} 项` : ""}
            </p>
          ) : null}

          {/* inline preview player for current playhead clip */}
          {activeClip && activeClip.media_version_id ? (
            <div className="director-timeline-preview-player">
              <div className="director-timeline-preview-video">
                <video
                  ref={videoRef}
                  key={String(activeClip.media_version_id)}
                  poster={`/api/v1/media-versions/${encodeURIComponent(String(activeClip.media_version_id))}/thumbnail?size=medium&frame=poster`}
                  src={mediaProxyUrl(String(activeClip.media_version_id))}
                  data-original-src={mediaContentUrl(String(activeClip.media_version_id))}
                  preload="none"
                  playsInline
                  muted
                  controls={false}
                  onLoadedMetadata={(event) => {
                    event.currentTarget.currentTime = activeOffsetSec;
                  }}
                  onTimeUpdate={handleVideoTimeUpdate}
                  onError={fallbackToOriginalVideo}
                  aria-label={`${activeClip.shot_code} 预览视频`}
                  onClick={() => void togglePlay()}
                />
                <button
                  type="button"
                  className="director-timeline-preview-play"
                  aria-label={playing ? "暂停" : "播放"}
                  onClick={() => void togglePlay()}
                >
                  {playing ? "❚❚" : "▶"}
                </button>
                <span className="director-timeline-preview-badge">
                  {activeClip.shot_code} · {timelineTimeLabel(playheadUs - activeClip.start_us)} / {timelineTimeLabel(activeClip.end_us - activeClip.start_us)}
                </span>
              </div>
              <div className="director-timeline-preview-transport">
                <button
                  type="button"
                  className="director-button ghost"
                  onClick={() => void togglePlay()}
                  aria-label={playing ? "暂停播放" : "播放时间线"}
                >
                  {playing ? "暂停" : "播放"}
                </button>
                <button type="button" className="director-button ghost" onClick={() => handleScrub(0)}>
                  回到开头
                </button>
                <span className="director-timeline-preview-time">
                  {timelineTimeLabel(playheadUs)} / {timelineTimeLabel(durationUs)}
                </span>
                <label>
                  缩放
                  <input
                    aria-label="时间线缩放"
                    type="range"
                    min={1}
                    max={3}
                    step={0.25}
                    value={zoom}
                    onChange={(event) => setZoom(Number(event.target.value))}
                  />
                </label>
                <button type="button" className="director-button ghost" onClick={() => setPlayheadUs((value) => Math.max(0, value - 500_000))}>
                  ◀ 0.5s
                </button>
                <button type="button" className="director-button ghost" onClick={() => setPlayheadUs((value) => Math.min(durationUs, value + 500_000))}>
                  0.5s ▶
                </button>
              </div>
            </div>
          ) : null}

          <label className="director-timeline-scrubber">
            播放头
            <input
              aria-label="导演台时间线播放头"
              type="range"
              min={0}
              max={Math.max(0, durationUs)}
              step={10000}
              value={Math.min(playheadUs, durationUs)}
              onChange={(event) => handleScrub(Number(event.target.value))}
            />
            <output>
              {timelineTimeLabel(playheadUs)} / {timelineTimeLabel(durationUs)}
            </output>
          </label>

          {clips.length ? (
            <TimelineLanes
              ariaLabel="导演台只读同步时间线"
              clips={clips}
              audio={data.audio_clips}
              subtitle={data.subtitle}
              durationUs={durationUs}
              zoom={zoom}
              playheadUs={playheadUs}
              selectedShotId={currentShotId}
              includeDialogue
              includeMusic
              includeSubtitles
              onSelect={(shotId, startUs) => {
                setPlayheadUs(startUs);
                onSelectShot(shotId);
              }}
              onPlayheadChange={(value) => handleScrub(value)}
            />
          ) : (
            <p className="director-timeline-preview-state">当前还没有已采用的视频，时间线预览为空。</p>
          )}
        </div>
      ) : null}
    </details>
  );
}

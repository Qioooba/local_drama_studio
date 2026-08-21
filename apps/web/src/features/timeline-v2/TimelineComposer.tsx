import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { createTimelineRevision, getTimelineRevision, listEpisodeAudioBindings, type TimelineStatus } from "../../generated/api";
import { loadDirectorDesk } from "../director-v2/DirectorDeskClient";
import type { DirectorDeskResponse, DirectorDeskShotNavItem } from "../director-v2/types";
import { getShotGroupWorkspace } from "../episode-plan-v2/shotGroupsApi";
import { buildTimelineItems, revisionUpstreamFingerprint, type TimelineShotDraft, type TimelineUpstream, upstreamFingerprint } from "./types";
import { MediaPicker } from "../media-picker/MediaPicker";
import { TimelineTracks } from "./TimelineTracks";

async function loadTimelineSelections(projectId: string, episodeId: string) {
  const pages: DirectorDeskResponse[] = [];
  const seen = new Set<string>();
  let shotId: string | undefined;
  for (let page = 0; page < 10; page += 1) {
    const result = await loadDirectorDesk(projectId, episodeId, shotId);
    pages.push(result);
    result.shot_nav.items.forEach((item) => seen.add(item.id));
    if (!result.shot_nav.has_next) break;
    const nextAnchor = result.shot_nav.items.at(-1)?.id;
    if (!nextAnchor || nextAnchor === shotId) break;
    shotId = nextAnchor;
  }
  const nav = new Map<string, DirectorDeskShotNavItem>();
  pages.flatMap((page) => page.shot_nav.items).forEach((item) => nav.set(item.id, item));
  const exactMedia = new Map<string, string>();
  pages.forEach((page) => {
    const mediaId = page.current_shot.current_media?.media_version_id;
    if (mediaId) exactMedia.set(page.current_shot.shot.id, mediaId);
  });
  return { nav, exactMedia, total: pages[0]?.shot_nav.total ?? seen.size };
}

function secondsLabel(durationUs: number) {
  return `${(durationUs / 1_000_000).toFixed(durationUs % 1_000_000 ? 1 : 0)}s`;
}

export function TimelineComposer({ projectId, episodeId, status, onCreated }: { projectId: string; episodeId: string; status: TimelineStatus; onCreated: () => void }) {
  const production = useQuery({ queryKey: ["episode", episodeId, "shot-groups", "timeline-v2"], queryFn: () => getShotGroupWorkspace(episodeId) });
  const selections = useQuery({ queryKey: ["episode", episodeId, "timeline-selections-v2"], queryFn: () => loadTimelineSelections(projectId, episodeId) });
  const audio = useQuery({ queryKey: ["episode", episodeId, "audio-bindings"], queryFn: () => listEpisodeAudioBindings(episodeId) });
  const latestId = status.timeline.latest?.id ? String(status.timeline.latest.id) : null;
  const latest = useQuery({ queryKey: ["timeline-revision", latestId], queryFn: () => getTimelineRevision(latestId as string), enabled: Boolean(latestId) });
  const upstream = useMemo<TimelineUpstream | null>(() => {
    if (!production.data || !selections.data || !audio.data) return null;
    const shots = production.data.shots.map((item) => {
      const shotId = String(item.id);
      const nav = selections.data.nav.get(shotId);
      const exact = selections.data.exactMedia.get(shotId);
      return {
        shotId,
        code: String(item.code ?? shotId.slice(0, 8)),
        mediaVersionId: exact ?? nav?.thumbnail_media_version_id ?? "",
        durationUs: Math.max(100_000, Number(item.target_duration_ms ?? 1000) * 1000),
        transition: "CUT" as const,
        selectionSource: exact ? "CURRENT_MEDIA" as const : nav?.thumbnail_media_version_id ? "SELECTION_READ_MODEL" as const : "MISSING" as const,
        continuityStatus: nav?.continuity_status ?? "MISSING",
      };
    });
    return { shots, audio: audio.data.items, subtitleRevisionId: status.subtitles.latest?.id ? String(status.subtitles.latest.id) : null };
  }, [audio.data, production.data, selections.data, status.subtitles.latest]);
  const currentFingerprint = upstream ? upstreamFingerprint(upstream) : "";
  const initialized = useRef(false);
  const loadedFingerprint = useRef("");
  const [shots, setShots] = useState<TimelineShotDraft[]>([]);
  const [includeAudio, setIncludeAudio] = useState(true);
  const [includeSubtitles, setIncludeSubtitles] = useState(true);
  const [freezeConfirmed, setFreezeConfirmed] = useState(false);
  const [upstreamChanged, setUpstreamChanged] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [pickingShotIndex, setPickingShotIndex] = useState<number | null>(null);

  useEffect(() => {
    if (!upstream) return;
    if (!initialized.current) {
      setShots(upstream.shots);
      loadedFingerprint.current = currentFingerprint;
      initialized.current = true;
      return;
    }
    if (loadedFingerprint.current !== currentFingerprint) setUpstreamChanged(true);
  }, [currentFingerprint, upstream]);

  const loadLatestUpstream = () => {
    if (!upstream) return;
    setShots(upstream.shots);
    loadedFingerprint.current = currentFingerprint;
    setUpstreamChanged(false);
    setFreezeConfirmed(false);
    setFeedback("已把最新采用结果载入为未保存草稿；历史 revision 未被修改。");
  };
  const missingVideo = shots.filter((shot) => !shot.mediaVersionId);
  const totalUs = shots.reduce((sum, shot) => sum + shot.durationUs, 0);
  const persistedFingerprint = revisionUpstreamFingerprint(latest.data?.timeline);
  const persistedStale = Boolean(persistedFingerprint && persistedFingerprint !== currentFingerprint);
  const legacyUnknown = Boolean(latest.data?.timeline && !persistedFingerprint);
  const save = useMutation({
    mutationFn: async (statusValue: "DRAFT" | "FROZEN") => {
      if (!upstream) throw new Error("上游事实仍在读取");
      if (missingVideo.length) throw new Error(`${missingVideo.map((shot) => shot.code).join("、")} 尚未选择视频`);
      if (statusValue === "FROZEN" && !freezeConfirmed) throw new Error("冻结前必须确认本次上游快照");
      const selectedAudio = includeAudio ? upstream.audio : [];
      const subtitleId = includeSubtitles ? upstream.subtitleRevisionId : null;
      return createTimelineRevision(episodeId, {
        status: statusValue,
        items: buildTimelineItems(shots, selectedAudio),
        input_snapshot: {
          schema_version: "localdrama.timeline-editor.v2",
          source: "P10_TIMELINE_COMPOSER",
          stale_policy: "MARK_STALE_REQUIRE_EXPLICIT_RELOAD",
          upstream_selection_fingerprint: currentFingerprint,
          selected_videos: shots.map((shot) => ({ shot_id: shot.shotId, media_version_id: shot.mediaVersionId })),
          audio_binding_ids: selectedAudio.map((item) => item.id),
          subtitle_revision_id: subtitleId,
        },
      });
    },
    onSuccess: (result) => {
      setFeedback(`已创建不可变 TimelineRevision v${result.timeline.revision_no} · ${result.timeline.status}`);
      setFreezeConfirmed(false);
      onCreated();
    },
    onError: (error) => setFeedback(`未保存：${error instanceof Error ? error.message : String(error)}`),
  });

  if (production.isPending || selections.isPending || audio.isPending) return <section className="timeline-v2-shell" aria-busy="true"><p className="empty-state">正在读取镜头采用结果、音轨与字幕…</p></section>;
  if (production.isError || selections.isError || audio.isError) return <section className="timeline-v2-shell"><p className="inline-error" role="alert">时间线生产事实读取失败：{String(production.error ?? selections.error ?? audio.error)}</p></section>;

  return <section className="timeline-v2-shell" aria-labelledby="timeline-compose-title">
    <div className="timeline-v2-heading"><div><p className="eyebrow">P10 · TIMELINE / COMPOSE</p><h3 id="timeline-compose-title">从已采用镜头编排本集</h3><p>画面、声音、字幕和转场共同写入新的不可变快照。</p></div><div className="timeline-v2-runtime"><strong>{secondsLabel(totalUs)}</strong><span>{shots.length} 镜 · {upstream?.audio.length ?? 0} 音轨</span></div></div>
    {(upstreamChanged || persistedStale) && <div className="timeline-stale-banner" role="status"><div><strong>上游采用结果已变化</strong><span>{upstreamChanged ? "当前编辑草稿保持原样，没有被自动替换。" : "最新持久化 revision 与当前采用结果不同，已标记为 stale。"}</span></div><button type="button" className="secondary" onClick={loadLatestUpstream}>载入最新上游为新草稿</button></div>}
    {legacyUnknown && <p className="timeline-policy-note">最新历史 revision 没有 v2 上游指纹，无法自动判定 stale；仍可查看或导出，但建议显式载入当前采用结果创建新 revision。</p>}
    <div className="timeline-track-toolbar"><div><strong>V1 画面</strong><span>{missingVideo.length ? `${missingVideo.length} 镜缺少选择` : "镜头选择齐全"}</span></div><label><input type="checkbox" checked={includeAudio} onChange={(event) => setIncludeAudio(event.target.checked)} />包含已授权音轨</label><label><input type="checkbox" checked={includeSubtitles} disabled={!upstream?.subtitleRevisionId} onChange={(event) => setIncludeSubtitles(event.target.checked)} />包含最新字幕 revision</label></div>
    {pickingShotIndex !== null && shots[pickingShotIndex] && <div className="timeline-video-picker"><div className="panel-heading"><div><p className="eyebrow">V1 · 项目视频目录</p><h4>为 {shots[pickingShotIndex].code} 选择不可变视频版本</h4></div><button type="button" className="secondary" onClick={() => setPickingShotIndex(null)}>关闭</button></div><MediaPicker projectId={projectId} mediaKind="VIDEO" allowUpload={false} value={shots[pickingShotIndex].mediaVersionId} label={`${shots[pickingShotIndex].code} 视频选择器`} onChange={(mediaVersionId) => { setShots((items) => items.map((item, itemIndex) => itemIndex === pickingShotIndex ? { ...item, mediaVersionId, selectionSource: "CURRENT_MEDIA" } : item)); setPickingShotIndex(null); }} /></div>}
    <div className="timeline-filmstrip" role="list" aria-label="本集视频时间线">
      {shots.map((shot, index) => <article className={`timeline-clip${shot.mediaVersionId ? "" : " missing"}`} role="listitem" key={shot.shotId}>
        <div className="timeline-clip-preview">{shot.mediaVersionId ? <img src={`/api/v1/media-versions/${encodeURIComponent(shot.mediaVersionId)}/thumbnail?size=small&frame=poster`} alt={`${shot.code} 已采用视频缩略图`} loading="lazy" decoding="async" /> : <span>缺少视频</span>}<strong>{shot.code}</strong></div>
        <button type="button" className="secondary timeline-choose-video" onClick={() => setPickingShotIndex(index)}>{shot.mediaVersionId ? "更换项目视频" : "选择项目视频"}</button>
        <div className="timeline-clip-controls"><label>时长（秒）<input type="number" min="0.1" step="0.1" value={shot.durationUs / 1_000_000} onChange={(event) => setShots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, durationUs: Math.max(100_000, Math.round(Number(event.target.value || 0.1) * 1_000_000)) } : item))} /></label><label>入场转场<select value={shot.transition} disabled={index === 0} onChange={(event) => setShots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, transition: event.target.value as TimelineShotDraft["transition"] } : item))}><option value="CUT">硬切</option><option value="DISSOLVE">叠化</option><option value="FADE">淡入</option></select></label></div>
        <footer><span title={shot.mediaVersionId}>{shot.selectionSource === "MISSING" ? "待选择" : shot.selectionSource === "CURRENT_MEDIA" ? "精确采用" : "采用指针"}</span><span className={shot.continuityStatus === "STALE" ? "is-stale" : ""}>{shot.continuityStatus}</span><Link to={`/projects/${projectId}/episodes/${episodeId}/direct/${shot.shotId}`}>打开镜头</Link></footer>
      </article>)}
    </div>
    <TimelineTracks shots={shots} audio={upstream?.audio ?? []} subtitles={status.subtitles} includeAudio={includeAudio} includeSubtitles={includeSubtitles} />
    <div className="timeline-track-summary"><div><strong>A1–A3 声音</strong><span>{includeAudio ? `${upstream?.audio.length ?? 0} 条活动 binding 将随 revision 记录；渲染仍以服务端授权 binding 为准。` : "本 revision 不记录音频引用。"}</span><Link to={`/projects/${projectId}/episodes/${episodeId}/audio`}>编辑 gain / fade / loop binding</Link></div><div><strong>CC 字幕</strong><span>{includeSubtitles && upstream?.subtitleRevisionId ? `revision ${upstream.subtitleRevisionId.slice(0, 12)}…` : "未包含字幕"}</span></div></div>
    <div className="timeline-savebar"><label className="timeline-freeze-confirm"><input type="checkbox" checked={freezeConfirmed} onChange={(event) => setFreezeConfirmed(event.target.checked)} />我已核对画面、时长、音频和字幕，允许冻结这次快照</label><div><button type="button" className="secondary" disabled={save.isPending || missingVideo.length > 0} onClick={() => save.mutate("DRAFT")}>保存新草稿 revision</button><button type="button" className="primary-action" disabled={save.isPending || missingVideo.length > 0 || !freezeConfirmed} onClick={() => save.mutate("FROZEN")}>{save.isPending ? "正在保存…" : "冻结新 revision"}</button></div></div>
    {feedback && <p className={feedback.startsWith("未保存") ? "inline-error" : "review-success"} role="status">{feedback}</p>}
  </section>;
}

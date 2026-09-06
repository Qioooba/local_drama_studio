import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { Dialog, Drawer } from "../../components/ui";
import { createEpisodeTimelineDraftV2, freezeEpisodeTimelineV2, getEpisodeEditWorkspaceV2, type EditVideoClipV2 } from "../../generated/api";
import { fallbackToOriginalVideo, mediaContentUrl, mediaProxyUrl } from "../shared/mediaPlaybackPolicy";
import { SubtitleRevisionPanel } from "../production/SubtitleRevisionPanel";
import { TimelineExportPanel } from "../timeline-v2/TimelineExportPanel";
import { TimelineLanes, timelineTimeLabel } from "./TimelineLanes";

type EditableClip = EditVideoClipV2 & { duration_us: number };
const commandKey = (prefix: string) => `${prefix}:${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
const editable = (item: EditVideoClipV2): EditableClip => ({ ...item, duration_us: item.end_us - item.start_us });
const signature = (items: EditableClip[]) => JSON.stringify(items.map((item) => [item.shot_id, item.media_version_id, item.duration_us, item.source_start_us, item.transition_in]));
const EDIT_STATUS_LABELS: Record<string, string> = { MISSING: "缺少媒体", OK: "连续性正常", CONFLICT: "连续性冲突", STALE: "连续性已过期", DRAFT: "草稿", FROZEN: "已冻结" };

export function EpisodeEditWorkspace({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["post-edit-v2", episodeId], queryFn: () => getEpisodeEditWorkspaceV2(episodeId) });
  const data = query.data?.workspace;
  const [clips, setClips] = useState<EditableClip[]>([]);
  const [baseline, setBaseline] = useState("");
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);
  const [playheadUs, setPlayheadUs] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [includeDialogue, setIncludeDialogue] = useState(true);
  const [includeMusic, setIncludeMusic] = useState(true);
  const [includeSubtitles, setIncludeSubtitles] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [subtitleOpen, setSubtitleOpen] = useState(false);
  const [composeOpen, setComposeOpen] = useState(false);
  const [freezeOpen, setFreezeOpen] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!data) return;
    const next = data.video_clips.map(editable);
    setClips(next); setBaseline(signature(next));
    setSelectedShotId((current) => current && next.some((item) => item.shot_id === current) ? current : next[0]?.shot_id ?? null);
    setPlayheadUs(0);
  }, [data?.upstream_fingerprint, data?.latest_revision?.id]);

  const selected = clips.find((item) => item.shot_id === selectedShotId) ?? null;
  const timedClips = useMemo(() => { let cursor = 0; return clips.map((item) => { const result = { ...item, start_us: cursor, end_us: cursor + item.duration_us }; cursor += item.duration_us; return result; }); }, [clips]);
  const durationUs = timedClips.at(-1)?.end_us ?? 0;
  const dirty = signature(clips) !== baseline;
  const frozenId = data?.latest_revision?.status === "FROZEN" && data.freshness === "CURRENT" ? data.latest_revision.id : null;
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["post-edit-v2", episodeId] });
  const save = useMutation({
    mutationFn: (key: string) => createEpisodeTimelineDraftV2(episodeId, { clips: clips.map((item) => ({ shot_id: item.shot_id, media_version_id: item.media_version_id as string, duration_us: item.duration_us, source_start_us: item.source_start_us, transition_in: item.transition_in })), include_dialogue: includeDialogue, include_music_and_sfx: includeMusic, include_subtitles: includeSubtitles, expected_latest_revision_id: data?.latest_revision?.id ?? null, expected_upstream_fingerprint: data?.upstream_fingerprint ?? "", idempotency_key: key }),
    onSuccess: async (result) => { setFeedback(`已创建不可变草稿 v${result.timeline.revision_no}`); await refresh(); },
  });
  const freeze = useMutation({
    mutationFn: (key: string) => freezeEpisodeTimelineV2(data?.latest_revision?.id ?? "", { expected_latest_revision_id: data?.latest_revision?.id ?? "", expected_upstream_fingerprint: data?.upstream_fingerprint ?? "", idempotency_key: key }),
    onSuccess: async (result) => { setFreezeOpen(false); setFeedback(`已冻结时间线 v${result.timeline.revision_no}；后续改动会创建新版本`); await refresh(); },
  });
  const updateClip = (patch: Partial<EditableClip>) => { if (selected) setClips((items) => items.map((item) => item.shot_id === selected.shot_id ? { ...item, ...patch } : item)); };
  const moveClip = (direction: -1 | 1) => { if (!selected) return; setClips((items) => { const index = items.findIndex((item) => item.shot_id === selected.shot_id); const target = index + direction; if (index < 0 || target < 0 || target >= items.length) return items; const next = [...items]; [next[index], next[target]] = [next[target], next[index]]; if (next[0].transition_in !== "CUT") next[0] = { ...next[0], transition_in: "CUT" }; return next; }); };

  if (query.isPending) return <p className="empty-state" role="status">正在读取时间线工作区…</p>;
  if (query.isError || !data) return <p className="inline-error" role="alert">编辑工作区读取失败：{String(query.error)} <button type="button" className="secondary" onClick={() => void query.refetch()}>重试</button></p>;
  return <div className="edit-workspace-v2">
    <header className="edit-command-header"><div><p className="eyebrow">NLE-lite · 不可变版本</p><h3>{data.episode_code} 时间线</h3><span>{timelineTimeLabel(durationUs)} · {clips.length} 镜 · v{data.latest_revision?.revision_no ?? 0} {data.freshness === "STALE" ? "· 需要更新" : ""}</span></div><div className="action-row"><button type="button" className="secondary" onClick={() => setHistoryOpen(true)}>版本历史</button><button type="button" className="secondary" onClick={() => setSubtitleOpen(true)}>字幕</button><button type="button" className="secondary" onClick={() => setComposeOpen(true)}>合成与导出</button></div></header>
    {data.issues.length > 0 ? <section className={`edit-issue-strip${data.issues.some((item) => item.severity === "BLOCKER") ? " is-blocked" : ""}`} aria-label="时间线问题"><strong>{data.issues.some((item) => item.severity === "BLOCKER") ? "当前不能保存" : data.freshness === "STALE" ? "已载入最新上游" : "需要留意"}</strong><span>{data.issues[0].message}{data.issues.length > 1 ? `，另有 ${data.issues.length - 1} 项` : ""}</span><Link to={routes.shotStudio(projectId, episodeId)}>检查镜头</Link></section> : null}
    <section className="edit-stage-grid"><div className="edit-player-panel"><div className="edit-player">{selected?.media_version_id ? <video key={selected.media_version_id} ref={videoRef} controls playsInline preload="none" poster={`/api/v1/media-versions/${encodeURIComponent(selected.media_version_id)}/thumbnail?size=medium&frame=poster`} src={mediaProxyUrl(selected.media_version_id)} data-original-src={mediaContentUrl(selected.media_version_id)} onError={fallbackToOriginalVideo} onLoadedMetadata={(event) => { event.currentTarget.currentTime = selected.source_start_us / 1_000_000; }} onTimeUpdate={(event) => { const clip = timedClips.find((item) => item.shot_id === selected.shot_id); if (clip) setPlayheadUs(Math.min(clip.end_us, clip.start_us + Math.max(0, event.currentTarget.currentTime * 1_000_000 - selected.source_start_us))); }} aria-label={`${selected.shot_code} 视频预览`} /> : <p className="empty-state">当前镜头没有可播放视频。</p>}</div><div className="edit-transport"><button type="button" onClick={() => { const video = videoRef.current; if (!video) return; video.paused ? void video.play() : video.pause(); }}>播放 / 暂停</button><span>{timelineTimeLabel(playheadUs)} / {timelineTimeLabel(durationUs)}</span><label>缩放<input aria-label="时间线缩放" type="range" min="1" max="4" step="0.25" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /></label><button type="button" onClick={() => setPlayheadUs(0)}>回到开头</button></div></div>
      <aside className="edit-clip-inspector" aria-label="镜头 Inspector">{selected ? <><header><div><p className="eyebrow">镜头 Inspector</p><h3>{selected.shot_code}</h3></div><span className={`status-pill ${selected.continuity_status === "CONFLICT" ? "danger" : ""}`}>{EDIT_STATUS_LABELS[selected.continuity_status] ?? "状态待确认"}</span></header><p title={selected.source_name ?? undefined}>{selected.source_name ?? "尚未采用视频"}</p><label>成片时长（秒）<input type="number" min="0.1" max="3600" step="0.1" value={(selected.duration_us / 1_000_000).toFixed(2)} onChange={(event) => updateClip({ duration_us: Math.max(100_000, Math.round(Number(event.target.value || 0.1) * 1_000_000)) })} /></label><label>源视频入点（秒）<input type="number" min="0" step="0.1" value={(selected.source_start_us / 1_000_000).toFixed(2)} onChange={(event) => updateClip({ source_start_us: Math.max(0, Math.round(Number(event.target.value || 0) * 1_000_000)) })} /></label><label>入场转场<select value={selected.transition_in} disabled={timedClips[0]?.shot_id === selected.shot_id} onChange={(event) => updateClip({ transition_in: event.target.value as EditableClip["transition_in"] })}><option value="CUT">硬切</option><option value="DISSOLVE">叠化</option><option value="FADE">淡入</option></select></label><div className="edit-reorder"><button type="button" className="secondary" onClick={() => moveClip(-1)}>前移</button><button type="button" className="secondary" onClick={() => moveClip(1)}>后移</button></div><small>调整只会进入新的 TimelineRevision；不会覆盖镜头采用、源媒体或旧冻结版本。</small></> : <p className="empty-state">选择时间线中的一个镜头进行调整。</p>}</aside></section>
    <TimelineLanes clips={timedClips} audio={data.audio_clips} subtitle={data.subtitle} durationUs={durationUs} zoom={zoom} playheadUs={playheadUs} selectedShotId={selectedShotId} includeDialogue={includeDialogue} includeMusic={includeMusic} includeSubtitles={includeSubtitles} onSelect={(shotId, startUs) => { setSelectedShotId(shotId); setPlayheadUs(startUs); }} onPlayheadChange={setPlayheadUs} />
    <footer className="edit-savebar"><div className="edit-track-toggles"><label><input type="checkbox" checked={includeDialogue} onChange={(event) => setIncludeDialogue(event.target.checked)} />对白</label><label><input type="checkbox" checked={includeMusic} onChange={(event) => setIncludeMusic(event.target.checked)} />BGM / SFX</label><label><input type="checkbox" checked={includeSubtitles} onChange={(event) => setIncludeSubtitles(event.target.checked)} />字幕</label></div><span>{feedback ?? (dirty ? "存在未保存编排" : data.latest_revision ? `当前 v${data.latest_revision.revision_no} · ${data.latest_revision.status}` : "尚未创建时间线")}</span><div className="action-row"><button type="button" className="secondary" disabled={!dirty} onClick={() => { const next = data.video_clips.map(editable); setClips(next); setBaseline(signature(next)); }}>放弃调整</button><button type="button" className="secondary" disabled={!data.allowed_actions.includes("CREATE_DRAFT") || save.isPending} onClick={() => save.mutate(commandKey("timeline-draft"))}>{save.isPending ? "保存中…" : "保存新草稿"}</button><button type="button" className="primary-action" disabled={!data.allowed_actions.includes("FREEZE_LATEST_DRAFT") || dirty || freeze.isPending} onClick={() => setFreezeOpen(true)}>冻结当前草稿</button></div>{save.error || freeze.error ? <p className="inline-error" role="alert">操作失败：{String(save.error ?? freeze.error)}</p> : null}</footer>
    <Drawer open={historyOpen} onClose={() => setHistoryOpen(false)} title="时间线版本历史" width={560}><div className="edit-history-list">{data.history.length === 0 ? <p className="empty-state">还没有时间线版本。补齐采用视频后保存第一份草稿，版本会显示在这里。</p> : data.history.map((item) => <article key={item.id}><div><strong>v{item.revision_no} · {EDIT_STATUS_LABELS[item.status] ?? "状态待确认"}</strong><span>{timelineTimeLabel(item.duration_us)} · {item.video_count} 镜 · {item.audio_count} 音频</span></div><small>{new Date(item.created_at).toLocaleString()} · {item.revision_hash.slice(0, 12)}</small></article>)}{data.history_has_more ? <p className="muted">这里只显示最近 20 个版本；完整历史通过分页 API 读取。</p> : null}</div></Drawer>
    <Drawer open={subtitleOpen} onClose={() => setSubtitleOpen(false)} title="字幕版本" width={720}><SubtitleRevisionPanel episodeId={episodeId} projectId={projectId} defaultSourceDocumentVersionId="" onCreated={() => { void refresh(); }} /></Drawer>
    <Drawer open={composeOpen} onClose={() => setComposeOpen(false)} title="合成与专业导出" width={720}><div className="edit-compose-drawer"><p>合成和导出只接受当前、已冻结且输入未失效的 TimelineRevision。</p><TimelineExportPanel timelineRevisionId={frozenId} subtitleRevisionId={data.subtitle?.revision_id ?? null} />{frozenId ? <Link className="primary-action" to={routes.delivery(projectId, episodeId)}>进入交付工作区</Link> : <p className="inline-error" role="note">请先保存草稿并冻结；上游变化后需重新创建版本。</p>}</div></Drawer>
    <Dialog open={freezeOpen} title="冻结当前时间线草稿？" onClose={() => !freeze.isPending && setFreezeOpen(false)} footer={<><button type="button" onClick={() => setFreezeOpen(false)}>取消</button><button type="button" className="primary-action" disabled={freeze.isPending} onClick={() => freeze.mutate(commandKey("timeline-freeze"))}>{freeze.isPending ? "正在冻结…" : "确认冻结新版本"}</button></>}>冻结会复制当前草稿为新的不可变 revision，并锁定镜头、对白、BGM/SFX 与字幕输入；不会把机器检查当成人工批准。</Dialog>
  </div>;
}

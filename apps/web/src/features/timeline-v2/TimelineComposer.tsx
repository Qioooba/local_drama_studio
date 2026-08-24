import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { createTimelineRevision, getEpisodeTimelineSelections, getTimelineRevision, listEpisodeAudioBindings, type TimelineRevision, type TimelineStatus } from "../../generated/api";
import { getShotGroupWorkspace } from "../episode-plan-v2/shotGroupsApi";
import { buildTimelineItems, revisionUpstreamFingerprint, type TimelineShotDraft, type TimelineUpstream, upstreamFingerprint } from "./types";
import { MediaPicker } from "../media-picker/MediaPicker";
import { TimelineTracks } from "./TimelineTracks";

async function loadTimelineSelections(projectId: string, episodeId: string) {
  const result = await getEpisodeTimelineSelections(projectId, episodeId);
  if (result.has_more) throw new Error(`本集有 ${result.total} 个镜头，超过单次安全读取上限 ${result.limit}；请先拆分分集。`);
  const nav = new Map(result.items.map((item) => [item.id, item]));
  const exactMedia = new Map(result.items.flatMap((item) => item.current_video_media_version_id ? [[item.id, item.current_video_media_version_id] as const] : []));
  return { nav, exactMedia, total: result.total };
}

type TimelineSessionDraft = {
  baseFingerprint: string;
  shots: TimelineShotDraft[];
  includeAudio: boolean;
  includeSubtitles: boolean;
  savedAt: string;
};

type TimelineEditorBaseline = {
  shots: TimelineShotDraft[];
  includeAudio: boolean;
  includeSubtitles: boolean;
};

function editorSignature(value: TimelineEditorBaseline) {
  return JSON.stringify(value);
}

function editorBaselineFromRevision(revision: TimelineRevision | null | undefined, upstream: TimelineUpstream): TimelineEditorBaseline | null {
  if (!revision) return null;
  const videoByShot = new Map<string, Record<string, unknown>>();
  revision.items.forEach((rawItem) => {
    const item = rawItem as Record<string, unknown>;
    if (String(item.track_type ?? "") !== "VIDEO") return;
    const parameters = item.parameters && typeof item.parameters === "object" ? item.parameters as Record<string, unknown> : {};
    const shotId = String(parameters.shot_id ?? "");
    if (shotId) videoByShot.set(shotId, item);
  });
  if (upstream.shots.some((shot) => !videoByShot.has(shot.shotId))) return null;
  const allowedTransitions = new Set<TimelineShotDraft["transition"]>(["CUT", "DISSOLVE", "FADE"]);
  const shots = upstream.shots.map((shot, index) => {
    const item = videoByShot.get(shot.shotId) as Record<string, unknown>;
    const parameters = item.parameters && typeof item.parameters === "object" ? item.parameters as Record<string, unknown> : {};
    const transitionCandidate = String(parameters.transition_in ?? "CUT") as TimelineShotDraft["transition"];
    return {
      ...shot,
      mediaVersionId: String(item.media_version_id ?? ""),
      durationUs: Math.max(100_000, Number(item.end_us ?? 0) - Number(item.start_us ?? 0)),
      transition: index === 0 || !allowedTransitions.has(transitionCandidate) ? "CUT" : transitionCandidate,
      selectionSource: "MANUAL" as const,
    };
  });
  if (shots.some((shot) => !shot.mediaVersionId)) return null;
  const snapshot = revision.input_snapshot ?? {};
  const audioBindingIds = snapshot.audio_binding_ids;
  const subtitleRevisionId = snapshot.subtitle_revision_id;
  return {
    shots,
    includeAudio: Array.isArray(audioBindingIds) ? audioBindingIds.length > 0 : revision.items.some((item) => String(item.track_type ?? "") !== "VIDEO"),
    includeSubtitles: typeof subtitleRevisionId === "string" && Boolean(subtitleRevisionId),
  };
}

const timelineDraftKey = (projectId: string, episodeId: string) => `localdrama:timeline-draft:v2:${projectId}:${episodeId}`;

function readTimelineDraft(key: string): TimelineSessionDraft | null {
  try {
    const persisted = window.localStorage.getItem(key);
    const legacy = persisted ? null : window.sessionStorage.getItem(key);
    const value = persisted ?? legacy;
    if (!value) return null;
    const parsed = JSON.parse(value) as Partial<TimelineSessionDraft>;
    if (!Array.isArray(parsed.shots) || typeof parsed.baseFingerprint !== "string") return null;
    if (legacy) {
      window.localStorage.setItem(key, value);
      window.sessionStorage.removeItem(key);
    }
    return {
      baseFingerprint: parsed.baseFingerprint,
      shots: parsed.shots as TimelineShotDraft[],
      includeAudio: parsed.includeAudio !== false,
      includeSubtitles: parsed.includeSubtitles !== false,
      savedAt: typeof parsed.savedAt === "string" ? parsed.savedAt : "",
    };
  } catch {
    return null;
  }
}

function removeTimelineDraft(key: string) {
  try { window.localStorage.removeItem(key); } catch { /* best-effort browser draft cleanup */ }
  try { window.sessionStorage.removeItem(key); } catch { /* remove a pre-migration session draft too */ }
}

function secondsLabel(durationUs: number) {
  return `${(durationUs / 1_000_000).toFixed(durationUs % 1_000_000 ? 1 : 0)}s`;
}

export function TimelineComposer({ projectId, episodeId, status, onCreated }: { projectId: string; episodeId: string; status: TimelineStatus; onCreated: () => void }) {
  const draftKey = timelineDraftKey(projectId, episodeId);
  const production = useQuery({ queryKey: ["episode", episodeId, "shot-groups", "timeline-v2"], queryFn: () => getShotGroupWorkspace(episodeId) });
  const activeShots = production.data?.shots.filter((shot) => !shot.archived_at) ?? [];
  const hasShots = activeShots.length > 0;
  const initialShotId = activeShots[0]?.id ? String(activeShots[0].id) : "";
  const selections = useQuery({
    queryKey: ["episode", episodeId, "timeline-selections-v2", initialShotId],
    queryFn: () => loadTimelineSelections(projectId, episodeId),
    enabled: hasShots && Boolean(initialShotId),
  });
  const audio = useQuery({ queryKey: ["episode", episodeId, "audio-bindings"], queryFn: () => listEpisodeAudioBindings(episodeId) });
  const latestId = status.timeline.latest?.id ? String(status.timeline.latest.id) : null;
  const latest = useQuery({ queryKey: ["timeline-revision", latestId], queryFn: () => getTimelineRevision(latestId as string), enabled: Boolean(latestId) });
  const upstream = useMemo<TimelineUpstream | null>(() => {
    if (!production.data || !selections.data || !audio.data) return null;
    const shots = production.data.shots.filter((item) => !item.archived_at).map((item) => {
      const shotId = String(item.id);
      const nav = selections.data.nav.get(shotId);
      const exact = selections.data.exactMedia.get(shotId);
      return {
        shotId,
        code: String(item.code ?? shotId.slice(0, 8)),
        mediaVersionId: exact ?? nav?.current_video_media_version_id ?? "",
        durationUs: Math.max(100_000, Number(item.target_duration_ms ?? 1000) * 1000),
        transition: "CUT" as const,
        selectionSource: exact || nav?.current_video_media_version_id ? "CURRENT_MEDIA" as const : "MISSING" as const,
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
  const [editorBaseline, setEditorBaseline] = useState<TimelineEditorBaseline | null>(null);
  const sessionDraftDirty = Boolean(editorBaseline && editorSignature({ shots, includeAudio, includeSubtitles }) !== editorSignature(editorBaseline));

  useEffect(() => {
    if (!upstream) return;
    if (!initialized.current) {
      if (latestId && latest.isPending) return;
      const restored = readTimelineDraft(draftKey);
      const upstreamShotIds = new Set(upstream.shots.map((shot) => shot.shotId));
      const validRestored = restored?.shots.length === upstream.shots.length
        && restored.shots.every((shot) => upstreamShotIds.has(shot.shotId));
      const persistedBaseline = editorBaselineFromRevision(latest.data?.timeline, upstream);
      const baseline: TimelineEditorBaseline = persistedBaseline ?? { shots: upstream.shots, includeAudio: true, includeSubtitles: true };
      setEditorBaseline(baseline);
      if (restored && validRestored) {
        setShots(restored.shots);
        setIncludeAudio(restored.includeAudio);
        setIncludeSubtitles(restored.includeSubtitles);
        loadedFingerprint.current = restored.baseFingerprint;
        setUpstreamChanged(restored.baseFingerprint !== currentFingerprint);
        setFeedback("已恢复本浏览器中尚未保存的时间线草稿。");
      } else {
        removeTimelineDraft(draftKey);
        setShots(baseline.shots);
        setIncludeAudio(baseline.includeAudio);
        setIncludeSubtitles(baseline.includeSubtitles);
        loadedFingerprint.current = revisionUpstreamFingerprint(latest.data?.timeline) ?? currentFingerprint;
        setUpstreamChanged(loadedFingerprint.current !== currentFingerprint);
      }
      initialized.current = true;
      return;
    }
    if (loadedFingerprint.current !== currentFingerprint) setUpstreamChanged(true);
  }, [currentFingerprint, draftKey, latest.data?.timeline, latest.isPending, latestId, upstream]);

  useEffect(() => {
    if (!initialized.current || shots.length === 0) return;
    if (!sessionDraftDirty) {
      removeTimelineDraft(draftKey);
      return;
    }
    const draft: TimelineSessionDraft = {
      baseFingerprint: loadedFingerprint.current || currentFingerprint,
      shots,
      includeAudio,
      includeSubtitles,
      savedAt: new Date().toISOString(),
    };
    try {
      window.localStorage.setItem(draftKey, JSON.stringify(draft));
    } catch {
      setFeedback("未能把未保存草稿写入浏览器持久存储；请尽快保存为正式 revision。");
    }
  }, [currentFingerprint, draftKey, includeAudio, includeSubtitles, sessionDraftDirty, shots]);

  const loadLatestUpstream = () => {
    if (!upstream) return;
    const currentByShot = new Map(shots.map((shot) => [shot.shotId, shot]));
    setShots(upstream.shots.map((shot) => {
      const current = currentByShot.get(shot.shotId);
      if (!current) return shot;
      return {
        ...shot,
        mediaVersionId: shot.mediaVersionId || current.mediaVersionId || "",
        durationUs: current.durationUs,
        transition: current.transition,
        selectionSource: shot.mediaVersionId
          ? shot.selectionSource
          : current.selectionSource === "MISSING" ? "MANUAL" as const : current.selectionSource,
      };
    }));
    setIncludeAudio(true);
    setIncludeSubtitles(Boolean(upstream.subtitleRevisionId));
    loadedFingerprint.current = currentFingerprint;
    setUpstreamChanged(false);
    setFreezeConfirmed(false);
    setFeedback("已载入最新采用、音轨与字幕；同镜头保留当前时长与转场，没有上游视频的镜头保留人工选择。历史 revision 未被修改。");
  };
  const discardSessionDraft = () => {
    if (!upstream || !editorBaseline) return;
    removeTimelineDraft(draftKey);
    setShots(editorBaseline.shots);
    setIncludeAudio(editorBaseline.includeAudio);
    setIncludeSubtitles(editorBaseline.includeSubtitles);
    loadedFingerprint.current = revisionUpstreamFingerprint(latest.data?.timeline) ?? currentFingerprint;
    setUpstreamChanged(loadedFingerprint.current !== currentFingerprint);
    setFreezeConfirmed(false);
    setPickingShotIndex(null);
    setFeedback("已放弃本浏览器中的未保存草稿，并恢复最近一次已保存的时间线基线。");
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
      removeTimelineDraft(draftKey);
      setEditorBaseline({ shots, includeAudio, includeSubtitles });
      loadedFingerprint.current = currentFingerprint;
      setUpstreamChanged(false);
      setFeedback(`已创建不可变 TimelineRevision v${result.timeline.revision_no} · ${result.timeline.status}`);
      setFreezeConfirmed(false);
      onCreated();
    },
    onError: (error) => setFeedback(`未保存：${error instanceof Error ? error.message : String(error)}`),
  });
  const autoRoughCut = useMutation({
    mutationFn: async () => {
      if (!upstream) throw new Error("上游事实仍在读取");
      const missing = upstream.shots.filter((shot) => !shot.mediaVersionId);
      if (missing.length) throw new Error(`${missing.map((shot) => shot.code).join("、")} 尚未选择视频`);
      return createTimelineRevision(episodeId, {
        status: "DRAFT",
        items: buildTimelineItems(upstream.shots, upstream.audio),
        input_snapshot: {
          schema_version: "localdrama.timeline-editor.v2",
          source: "P10_AUTO_ROUGH_CUT",
          assembly_policy: "CURRENT_VIDEO_IN_SHOT_ORDER_WITH_PARALLEL_AUTHORIZED_AUDIO",
          stale_policy: "MARK_STALE_REQUIRE_EXPLICIT_RELOAD",
          upstream_selection_fingerprint: currentFingerprint,
          selected_videos: upstream.shots.map((shot) => ({ shot_id: shot.shotId, media_version_id: shot.mediaVersionId })),
          audio_binding_ids: upstream.audio.map((item) => item.id),
          subtitle_revision_id: upstream.subtitleRevisionId,
          auto_rough_cut: true,
          requires_human_freeze_confirmation: true,
        },
      });
    },
    onSuccess: (result) => {
      if (!upstream) return;
      removeTimelineDraft(draftKey);
      setShots(upstream.shots);
      setIncludeAudio(true);
      setIncludeSubtitles(Boolean(upstream.subtitleRevisionId));
      setEditorBaseline({ shots: upstream.shots, includeAudio: true, includeSubtitles: Boolean(upstream.subtitleRevisionId) });
      loadedFingerprint.current = currentFingerprint;
      setUpstreamChanged(false);
      setFreezeConfirmed(false);
      setFeedback(`已创建自动初剪 TimelineRevision v${result.timeline.revision_no} · DRAFT；仍需人工核对后才能冻结。`);
      onCreated();
    },
    onError: (error) => setFeedback(`未创建自动初剪：${error instanceof Error ? error.message : String(error)}`),
  });

  if (production.isPending || audio.isPending || (hasShots && selections.isPending) || (latestId && latest.isPending)) return <section className="timeline-v2-shell" aria-busy="true"><p className="empty-state">正在读取镜头采用结果、音轨与字幕…</p></section>;
  if (production.isError || audio.isError || (hasShots && selections.isError)) return <section className="timeline-v2-shell"><p className="inline-error" role="alert">时间线生产事实读取失败：{String(production.error ?? selections.error ?? audio.error)}</p></section>;
  if (!hasShots) return <section className="timeline-v2-shell" aria-labelledby="timeline-compose-title"><div className="timeline-v2-heading"><div><p className="eyebrow">P10 · TIMELINE / COMPOSE</p><h3 id="timeline-compose-title">从已采用镜头编排本集</h3><p>画面、声音、字幕和转场共同写入新的不可变快照。</p></div></div><div className="empty-state" role="status"><strong>本集还没有镜头</strong><span>先在分集策划中生成或创建镜头，再回到这里选择正式视频并冻结时间线。</span><Link className="secondary" to={`/projects/${projectId}/episodes/${episodeId}/plan`}>前往分集策划</Link></div></section>;

  return <section className="timeline-v2-shell" aria-labelledby="timeline-compose-title">
    <div className="timeline-v2-heading"><div><p className="eyebrow">P10 · TIMELINE / COMPOSE</p><h3 id="timeline-compose-title">从已采用镜头编排本集</h3><p>画面、声音、字幕和转场共同写入新的不可变快照。</p></div><div className="timeline-v2-runtime"><strong>{secondsLabel(totalUs)}</strong><span>{shots.length} 镜 · {upstream?.audio.length ?? 0} 音轨</span></div></div>
    {(upstreamChanged || persistedStale) && <div className="timeline-stale-banner" role="status"><div><strong>上游采用结果已变化</strong><span>{upstreamChanged ? "当前编辑草稿保持原样，没有被自动替换。" : "最新持久化 revision 与当前采用结果不同，已标记为 stale。"}</span></div><button type="button" className="secondary" onClick={loadLatestUpstream}>载入最新上游为新草稿</button></div>}
    <div className="timeline-session-draft" role="note"><span>{sessionDraftDirty ? "有未保存修改，已暂存在此浏览器；关闭并重新打开后仍可恢复，不会写入正式 revision。" : "未保存修改会暂存在此浏览器；关闭并重新打开后仍可恢复，不会写入正式 revision。"}</span><div><button type="button" className="secondary" disabled={autoRoughCut.isPending || Boolean(upstream?.shots.some((shot) => !shot.mediaVersionId))} title={upstream?.shots.some((shot) => !shot.mediaVersionId) ? "所有镜头必须先有采用视频" : "按镜头顺序、目标时长、活动音轨与最新字幕创建 DRAFT；不会冻结"} onClick={() => autoRoughCut.mutate()}>{autoRoughCut.isPending ? "正在创建初剪…" : "生成自动初剪草稿 revision"}</button><button type="button" className="secondary" disabled={!sessionDraftDirty} onClick={discardSessionDraft}>放弃未保存草稿</button></div></div>
    {legacyUnknown && <p className="timeline-policy-note">最新历史 revision 没有 v2 上游指纹，无法自动判定 stale；仍可查看或导出，但建议显式载入当前采用结果创建新 revision。</p>}
    <div className="timeline-track-toolbar"><div><strong>V1 画面</strong><span>{missingVideo.length ? `${missingVideo.length} 镜缺少选择` : "镜头选择齐全"}</span></div><label><input type="checkbox" checked={includeAudio} onChange={(event) => setIncludeAudio(event.target.checked)} />包含已授权音轨</label><label><input type="checkbox" checked={includeSubtitles} disabled={!upstream?.subtitleRevisionId} onChange={(event) => setIncludeSubtitles(event.target.checked)} />包含最新字幕 revision</label></div>
    {pickingShotIndex !== null && shots[pickingShotIndex] && <div className="timeline-video-picker"><div className="panel-heading"><div><p className="eyebrow">V1 · 项目视频目录</p><h4>为 {shots[pickingShotIndex].code} 选择不可变视频版本</h4></div><button type="button" className="secondary" onClick={() => setPickingShotIndex(null)}>关闭</button></div><MediaPicker projectId={projectId} mediaKind="VIDEO" allowUpload={false} value={shots[pickingShotIndex].mediaVersionId} label={`${shots[pickingShotIndex].code} 视频选择器`} onChange={(mediaVersionId) => { setShots((items) => items.map((item, itemIndex) => itemIndex === pickingShotIndex ? { ...item, mediaVersionId, selectionSource: "MANUAL" } : item)); setPickingShotIndex(null); }} /></div>}
    <div className="timeline-filmstrip" role="list" aria-label="本集视频时间线">
      {shots.map((shot, index) => <article className={`timeline-clip${shot.mediaVersionId ? "" : " missing"}`} role="listitem" key={shot.shotId}>
        <div className="timeline-clip-preview">{shot.mediaVersionId ? <img src={`/api/v1/media-versions/${encodeURIComponent(shot.mediaVersionId)}/thumbnail?size=small&frame=poster`} alt={`${shot.code} 已采用视频缩略图`} loading="lazy" decoding="async" /> : <span>缺少视频</span>}<strong>{shot.code}</strong></div>
        <button type="button" className="secondary timeline-choose-video" onClick={() => setPickingShotIndex(index)}>{shot.mediaVersionId ? "更换项目视频" : "选择项目视频"}</button>
        <div className="timeline-clip-controls"><label>时长（秒）<input type="number" min="0.1" step="0.1" value={shot.durationUs / 1_000_000} onChange={(event) => setShots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, durationUs: Math.max(100_000, Math.round(Number(event.target.value || 0.1) * 1_000_000)) } : item))} /></label><label>入场转场<select value={shot.transition} disabled={index === 0} onChange={(event) => setShots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, transition: event.target.value as TimelineShotDraft["transition"] } : item))}><option value="CUT">硬切</option><option value="DISSOLVE">叠化</option><option value="FADE">淡入</option></select></label></div>
        <footer><span title={shot.mediaVersionId}>{shot.selectionSource === "MISSING" ? "待选择" : shot.selectionSource === "CURRENT_MEDIA" ? "精确采用" : shot.selectionSource === "MANUAL" ? "手工选片" : "采用指针"}</span><span className={shot.continuityStatus === "STALE" ? "is-stale" : ""}>{!shot.mediaVersionId ? "缺少/待选择" : shot.continuityStatus === "STALE" ? "上游已失效" : shot.continuityStatus === "READY" ? "已就绪" : shot.continuityStatus === "MISSING" ? "视频已选择" : shot.continuityStatus}</span><Link to={`/projects/${projectId}/episodes/${episodeId}/direct/${shot.shotId}`}>打开镜头</Link></footer>
      </article>)}
    </div>
    <TimelineTracks shots={shots} audio={upstream?.audio ?? []} subtitles={status.subtitles} includeAudio={includeAudio} includeSubtitles={includeSubtitles} />
    <div className="timeline-track-summary"><div><strong>A1–A3 声音</strong><span>{includeAudio ? `${upstream?.audio.length ?? 0} 条活动 binding 将随 revision 记录；对白、BGM、SFX 按各自 start/end 与 V1 并行混合，不会串接到画面尾部。` : "本 revision 不记录音频引用。"}</span><Link to={`/projects/${projectId}/episodes/${episodeId}/audio`}>编辑 gain / fade / loop binding</Link></div><div><strong>CC 字幕</strong><span>{includeSubtitles && upstream?.subtitleRevisionId ? `revision ${upstream.subtitleRevisionId.slice(0, 12)}…；作为并行文字层随时间码覆盖 V1` : "未包含字幕；字幕是并行文字层，不会改变镜头时长"}</span></div></div>
    <div className="timeline-savebar"><label className="timeline-freeze-confirm"><input type="checkbox" checked={freezeConfirmed} onChange={(event) => setFreezeConfirmed(event.target.checked)} />我已核对画面、时长、音频和字幕，允许冻结这次快照</label><div><button type="button" className="secondary" disabled={save.isPending || missingVideo.length > 0} onClick={() => save.mutate("DRAFT")}>保存新草稿 revision</button><button type="button" className="primary-action" disabled={save.isPending || missingVideo.length > 0 || !freezeConfirmed} title={save.isPending ? "正在保存…" : missingVideo.length > 0 ? "有镜头缺少视频，无法冻结" : !freezeConfirmed ? "请先勾选左侧确认复选框" : undefined} onClick={() => save.mutate("FROZEN")}>{save.isPending ? "正在保存…" : "冻结新 revision"}</button></div></div>
    {feedback && <p className={feedback.startsWith("未保存") || feedback.startsWith("未创建") ? "inline-error" : "review-success"} role="status">{feedback}</p>}
  </section>;
}

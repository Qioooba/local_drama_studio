import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { Dialog, Drawer } from "../../components/ui";
import { createEpisodeTimelineDraftV2, freezeEpisodeTimelineV2, getEpisodeEditWorkspaceV2, type EditTimelineRevisionV2, type EditVideoClipV2 } from "../../generated/api";
import { draftRegistry, type DraftDiscardResult, type DraftHandle, type DraftSaveResult } from "../drafts/draftRegistry";
import { fallbackToOriginalVideo, mediaContentUrl, mediaProxyUrl } from "../shared/mediaPlaybackPolicy";
import { SubtitleRevisionPanel } from "../production/SubtitleRevisionPanel";
import { TimelineExportPanel } from "../timeline-v2/TimelineExportPanel";
import { TimelineLanes, timelineTimeLabel } from "./TimelineLanes";

type EditableClip = EditVideoClipV2 & { duration_us: number };

/**
 * FE-07: the audio / model-voice / subtitle switches are part of the frozen
 * timeline contract, so they must be part of the dirty signature as well.
 * `null` means "the server did not report this field" (unknown / not loaded):
 * it is hashed as null, never silently replaced by a default, so any explicit
 * user choice becomes a real change. The backend defaults are used for display
 * only and are sent as "not specified" so the server keeps authority.
 */
type TimelineAudioOptions = {
  includeDialogue: boolean | null;
  includeMusicAndSfx: boolean | null;
  includeSourceAudio: boolean | null;
  includeSubtitles: boolean | null;
};

const AUDIO_OPTION_DEFAULTS = {
  includeDialogue: true,
  includeMusicAndSfx: true,
  includeSourceAudio: false,
  includeSubtitles: true,
} as const;

const UNKNOWN_AUDIO_OPTIONS: TimelineAudioOptions = {
  includeDialogue: null,
  includeMusicAndSfx: null,
  includeSourceAudio: null,
  includeSubtitles: null,
};

type TimelineEditorSnapshot = TimelineAudioOptions & { clips: EditableClip[] };

const commandKey = (prefix: string) => `${prefix}:${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
const editable = (item: EditVideoClipV2): EditableClip => ({ ...item, duration_us: item.end_us - item.start_us });
const EDIT_STATUS_LABELS: Record<string, string> = { MISSING: "缺少媒体", OK: "连续性正常", CONFLICT: "连续性冲突", STALE: "连续性已过期", DRAFT: "草稿", FROZEN: "已冻结" };

function snapshotSignature(snapshot: TimelineEditorSnapshot): string {
  return JSON.stringify([
    snapshot.clips.map((item) => [item.shot_id, item.media_version_id, item.duration_us, item.source_start_us, item.transition_in]),
    snapshot.includeDialogue,
    snapshot.includeMusicAndSfx,
    snapshot.includeSourceAudio,
    snapshot.includeSubtitles,
  ]);
}

function optionsOf(snapshot: TimelineEditorSnapshot): TimelineAudioOptions {
  return {
    includeDialogue: snapshot.includeDialogue,
    includeMusicAndSfx: snapshot.includeMusicAndSfx,
    includeSourceAudio: snapshot.includeSourceAudio,
    includeSubtitles: snapshot.includeSubtitles,
  };
}

/**
 * Read contract need (backend, owned by another agent): the workspace read
 * response does not expose the switches that `TimelineDraftCreateCommand`
 * accepts. Until `EditTimelineRevisionFact` returns them, a missing value stays
 * "unknown" instead of being reported as the hardcoded UI default.
 */
function readRevisionAudioOptions(revision: EditTimelineRevisionV2 | null | undefined): TimelineAudioOptions | null {
  if (!revision) return null;
  const record = revision as unknown as Record<string, unknown>;
  const pick = (key: string): boolean | null => (typeof record[key] === "boolean" ? (record[key] as boolean) : null);
  const options: TimelineAudioOptions = {
    includeDialogue: pick("include_dialogue"),
    includeMusicAndSfx: pick("include_music_and_sfx"),
    includeSourceAudio: pick("include_source_audio"),
    includeSubtitles: pick("include_subtitles"),
  };
  return Object.values(options).some((value) => value !== null) ? options : null;
}

export function EpisodeEditWorkspace({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["post-edit-v2", episodeId], queryFn: () => getEpisodeEditWorkspaceV2(episodeId) });
  const data = query.data?.workspace;
  const [clips, setClips] = useState<EditableClip[]>([]);
  const [audioOptions, setAudioOptions] = useState<TimelineAudioOptions>(UNKNOWN_AUDIO_OPTIONS);
  const [audioOptionsKnown, setAudioOptionsKnown] = useState(false);
  const [baseline, setBaseline] = useState("");
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null);
  const [playheadUs, setPlayheadUs] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [subtitleOpen, setSubtitleOpen] = useState(false);
  const [subtitleDirty, setSubtitleDirty] = useState(false);
  const [composeOpen, setComposeOpen] = useState(false);
  const [freezeOpen, setFreezeOpen] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [syncNotice, setSyncNotice] = useState<string | null>(null);
  const [timelineVersion, setTimelineVersion] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const pendingSeekUsRef = useRef<number | null>(null);
  // Registry mirrors: save/discard always freeze the latest payload.
  const handleRef = useRef<DraftHandle | null>(null);
  const versionRef = useRef(0);
  const snapshotRef = useRef<TimelineEditorSnapshot>({ clips: [], ...UNKNOWN_AUDIO_OPTIONS });
  const loadedSnapshotRef = useRef<TimelineEditorSnapshot>({ clips: [], ...UNKNOWN_AUDIO_OPTIONS });
  const baselineRef = useRef("");
  const loadedRevisionIdRef = useRef<string | null>(null);
  // The *upstream fingerprint* of the last snapshot this editor accepted, and the
  // freshness the server reported with it.  A refresh can change the fingerprint
  // without changing the revision id (upstream video/subtitle/audio moved), and a
  // revision can already be stale.  Both must participate in "may I overwrite the
  // local edits?" and in "must I create a new revision?".
  const loadedUpstreamFingerprintRef = useRef<string | null>(null);
  const loadedFreshnessRef = useRef<string>("");
  const dataRef = useRef<typeof data>(data);
  const entityKeyRef = useRef("本集时间线");
  const saveTimelineRef = useRef<(expectedVersion: number) => Promise<DraftSaveResult>>(async () => ({ status: "blocked", reason: "时间线草稿尚未就绪。" }));
  const discardTimelineRef = useRef<(expectedVersion: number) => Promise<DraftDiscardResult>>(async () => ({ status: "blocked", reason: "时间线草稿尚未就绪。" }));

  const entityKey = `第 ${data?.episode_code ?? "当前"} 集时间线`;
  dataRef.current = data;
  entityKeyRef.current = entityKey;
  snapshotRef.current = { clips, ...audioOptions };
  baselineRef.current = baseline;

  const publishTimeline = useCallback((version: number, dirty: boolean) => {
    const handle = handleRef.current;
    if (!handle) return;
    draftRegistry.update(handle, { version, dirty, entityKey: entityKeyRef.current });
  }, []);

  useEffect(() => {
    if (!data) return;
    const nextClips = data.video_clips.map(editable);
    const reported = readRevisionAudioOptions(data.latest_revision);
    const loaded: TimelineEditorSnapshot = { clips: nextClips, ...(reported ?? UNKNOWN_AUDIO_OPTIONS) };
    const loadedSignature = snapshotSignature(loaded);
    const revisionId = data.latest_revision?.id ?? null;
    const previousRevisionId = loadedRevisionIdRef.current;
    const fingerprint = data.upstream_fingerprint ?? "";
    const previousFingerprint = loadedUpstreamFingerprintRef.current;
    setAudioOptionsKnown(Boolean(reported));
    loadedRevisionIdRef.current = revisionId;
    loadedUpstreamFingerprintRef.current = fingerprint;
    loadedFreshnessRef.current = data.freshness ?? "";
    loadedSnapshotRef.current = loaded;
    // A background refresh that moved the server forward must not throw away the
    // user's unsaved edits; the decision stays explicit.  This previously compared
    // only the *revision id*, so an upstream change that kept the same revision id
    // (video/subtitle/audio replaced under it) went straight through and silently
    // reset the local edits to the server snapshot.
    const editing = snapshotSignature(snapshotRef.current) !== baselineRef.current;
    const isExternalAdvance =
      editing &&
      previousRevisionId !== null &&
      (revisionId !== previousRevisionId || (previousFingerprint !== null && fingerprint !== previousFingerprint));
    if (isExternalAdvance) {
      setSyncNotice(
        `服务器已有新的输入或时间线版本${revisionId !== previousRevisionId ? ` v${data.latest_revision?.revision_no ?? 0}` : ""}；` +
          "页面中的未保存编排已保留。请先保存或放弃，再载入服务器版本。",
      );
      return;
    }
    setClips(nextClips);
    setAudioOptions(optionsOf(loaded));
    setBaseline(loadedSignature);
    setSelectedShotId((current) => current && nextClips.some((item) => item.shot_id === current) ? current : nextClips[0]?.shot_id ?? null);
    setPlayheadUs(0);
    setSyncNotice(null);
    publishTimeline(versionRef.current, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.upstream_fingerprint, data?.latest_revision?.id, data?.freshness]);

  useEffect(() => {
    publishTimeline(timelineVersion, snapshotSignature(snapshotRef.current) !== baselineRef.current);
  }, [baseline, publishTimeline, timelineVersion]);

  const selected = clips.find((item) => item.shot_id === selectedShotId) ?? null;
  const timedClips = useMemo(() => { let cursor = 0; return clips.map((item) => { const result = { ...item, start_us: cursor, end_us: cursor + item.duration_us }; cursor += item.duration_us; return result; }); }, [clips]);
  const durationUs = timedClips.at(-1)?.end_us ?? 0;
  const currentSignature = snapshotSignature({ clips, ...audioOptions });
  const dirty = currentSignature !== baseline;
  const frozenId = data?.latest_revision?.status === "FROZEN" && data.freshness === "CURRENT" ? data.latest_revision.id : null;
  const includeDialogue = audioOptions.includeDialogue ?? AUDIO_OPTION_DEFAULTS.includeDialogue;
  const includeMusic = audioOptions.includeMusicAndSfx ?? AUDIO_OPTION_DEFAULTS.includeMusicAndSfx;
  const includeSourceAudio = audioOptions.includeSourceAudio ?? AUDIO_OPTION_DEFAULTS.includeSourceAudio;
  const includeSubtitles = audioOptions.includeSubtitles ?? AUDIO_OPTION_DEFAULTS.includeSubtitles;
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["post-edit-v2", episodeId] });

  /** Replaces the editable payload, bumps the local version and publishes dirty state. */
  const applySnapshot = useCallback((nextClips: EditableClip[], nextOptions: TimelineAudioOptions) => {
    const nextVersion = versionRef.current + 1;
    versionRef.current = nextVersion;
    setTimelineVersion(nextVersion);
    setClips(nextClips);
    setAudioOptions(nextOptions);
    const next: TimelineEditorSnapshot = { clips: nextClips, ...nextOptions };
    snapshotRef.current = next;
    publishTimeline(nextVersion, snapshotSignature(next) !== baselineRef.current);
  }, [publishTimeline]);

  const adoptSnapshot = useCallback((snapshot: TimelineEditorSnapshot, version: number) => {
    setClips(snapshot.clips);
    setAudioOptions(optionsOf(snapshot));
    snapshotRef.current = snapshot;
    loadedSnapshotRef.current = snapshot;
    const signature = snapshotSignature(snapshot);
    baselineRef.current = signature;
    setBaseline(signature);
    setSyncNotice(null);
    setSelectedShotId((current) => current && snapshot.clips.some((item) => item.shot_id === current) ? current : snapshot.clips[0]?.shot_id ?? null);
    publishTimeline(version, false);
  }, [publishTimeline]);

  const draftCommandFor = useCallback((snapshot: TimelineEditorSnapshot, key: string) => ({
    clips: snapshot.clips.map((item) => ({
      shot_id: item.shot_id,
      media_version_id: item.media_version_id as string,
      duration_us: item.duration_us,
      source_start_us: item.source_start_us,
      transition_in: item.transition_in,
    })),
    // Unknown values are omitted so the server defaults stay authoritative.
    include_dialogue: snapshot.includeDialogue ?? undefined,
    include_music_and_sfx: snapshot.includeMusicAndSfx ?? undefined,
    include_source_audio: snapshot.includeSourceAudio ?? undefined,
    include_subtitles: snapshot.includeSubtitles ?? undefined,
    expected_latest_revision_id: dataRef.current?.latest_revision?.id ?? null,
    expected_upstream_fingerprint: dataRef.current?.upstream_fingerprint ?? "",
    idempotency_key: key,
  }), []);

  const saveTimelineDraft = useCallback(async (expectedVersion: number, key?: string): Promise<DraftSaveResult> => {
    if (versionRef.current !== expectedVersion) {
      return { status: "blocked", reason: `“${entityKeyRef.current}”产生了新修改，请重新确认。` };
    }
    const submitted = snapshotRef.current;
    const submittedSignature = snapshotSignature(submitted);
    // "The editor holds the same values the server last sent" is NOT the same as
    // "a persisted timeline revision exists".  A freshly entered episode whose
    // upstream suggestion was loaded as the baseline has no revision at all, so the
    // old short-circuit answered "saved" without calling the API and left the
    // episode without a freezable v1.  A stale revision must also be replaced.
    const latestRevisionId = dataRef.current?.latest_revision?.id ?? null;
    const freshness = dataRef.current?.freshness ?? "";
    const mustCreateRevision = !latestRevisionId || freshness !== "CURRENT" || !dataRef.current?.allowed_actions.includes("FREEZE_TIMELINE");
    if (submittedSignature === baselineRef.current && !mustCreateRevision) {
      publishTimeline(expectedVersion, false);
      return { status: "saved", savedVersion: expectedVersion };
    }
    if (!dataRef.current?.allowed_actions.includes("CREATE_DRAFT")) {
      return { status: "blocked", reason: "当前状态不允许保存新的时间线草稿，请先处理时间线问题。" };
    }
    try {
      const result = await createEpisodeTimelineDraftV2(episodeId, draftCommandFor(submitted, key ?? commandKey("timeline-draft")));
      setFeedback(`已创建不可变草稿 v${result.timeline.revision_no}`);
      const stillCurrent = versionRef.current === expectedVersion && snapshotSignature(snapshotRef.current) === submittedSignature;
      if (stillCurrent) {
        baselineRef.current = submittedSignature;
        setBaseline(submittedSignature);
        loadedSnapshotRef.current = submitted;
        publishTimeline(expectedVersion, false);
      }
      try {
        await refresh();
      } catch (error) {
        setSyncNotice(`草稿已保存，但页面同步失败：${error instanceof Error ? error.message : String(error)}。请刷新后确认，不要重复提交。`);
      }
      return { status: "saved", savedVersion: expectedVersion };
    } catch (error) {
      const reason = `保存时间线草稿失败：${error instanceof Error ? error.message : String(error)}`;
      setFeedback(reason);
      return { status: "blocked", reason };
    }
  }, [draftCommandFor, episodeId, publishTimeline]);

  const discardTimelineDraft = useCallback(async (expectedVersion: number): Promise<DraftDiscardResult> => {
    if (versionRef.current !== expectedVersion) {
      return { status: "blocked", reason: `“${entityKeyRef.current}”产生了新修改，请重新确认。` };
    }
    adoptSnapshot(loadedSnapshotRef.current, expectedVersion);
    return { status: "discarded", discardedVersion: expectedVersion };
  }, [adoptSnapshot]);

  saveTimelineRef.current = saveTimelineDraft;
  discardTimelineRef.current = discardTimelineDraft;

  // FE-02: register this editor so AppShell can protect navigation/tab close.
  useEffect(() => {
    const handle = draftRegistry.register({
      ownerId: `timeline-edit:${episodeId}`,
      entityKey: entityKeyRef.current,
      version: versionRef.current,
      dirty: snapshotSignature(snapshotRef.current) !== baselineRef.current,
      save: (expectedVersion: number) => saveTimelineRef.current(expectedVersion),
      discard: (expectedVersion: number) => discardTimelineRef.current(expectedVersion),
    });
    handleRef.current = handle;
    publishTimeline(versionRef.current, snapshotSignature(snapshotRef.current) !== baselineRef.current);
    return () => {
      const live = handleRef.current;
      if (live && live.token === handle.token) {
        draftRegistry.unregister(handle);
        handleRef.current = null;
      }
    };
  }, [episodeId, publishTimeline]);

  const save = useMutation({
    // The button and the shared draft registry use the exact same write path,
    // so "保存并切换" can never issue a second, slightly different request.
    mutationFn: async (key: string) => {
      const result = await saveTimelineDraft(versionRef.current, key);
      if (result.status === "blocked") throw new Error(result.reason);
      return result;
    },
  });
  const freeze = useMutation({
    mutationFn: (key: string) => freezeEpisodeTimelineV2(data?.latest_revision?.id ?? "", { expected_latest_revision_id: data?.latest_revision?.id ?? "", expected_upstream_fingerprint: data?.upstream_fingerprint ?? "", idempotency_key: key }),
    onSuccess: async (result) => { setFreezeOpen(false); setFeedback(`已冻结时间线 v${result.timeline.revision_no}；后续改动会创建新版本`); await refresh(); },
  });

  const updateClip = (patch: Partial<EditableClip>) => {
    if (!selected) return;
    applySnapshot(clips.map((item) => item.shot_id === selected.shot_id ? { ...item, ...patch } : item), audioOptions);
  };
  const moveClip = (direction: -1 | 1) => {
    if (!selected) return;
    const index = clips.findIndex((item) => item.shot_id === selected.shot_id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= clips.length) return;
    const next = [...clips];
    [next[index], next[target]] = [next[target], next[index]];
    if (next[0].transition_in !== "CUT") next[0] = { ...next[0], transition_in: "CUT" };
    applySnapshot(next, audioOptions);
  };
  const setAudioOption = (key: keyof TimelineAudioOptions, value: boolean) => {
    applySnapshot(clips, { ...audioOptions, [key]: value });
  };

  /* FE-08: one seek entry point shared by the transport, the ruler and clip
   * clicks. The player always previews a single selected clip, so a seek
   * resolves the clip that owns the timeline time and converts it into that
   * clip's source time (`source_start_us + offset`). */
  const applyVideoSeek = (sourceTimeUs: number) => {
    const video = videoRef.current;
    const seconds = Math.max(0, sourceTimeUs) / 1_000_000;
    if (!video) {
      pendingSeekUsRef.current = sourceTimeUs;
      return;
    }
    // Browsers ignore currentTime before metadata is available, so the value is
    // remembered and re-applied in onLoadedMetadata (and after a clip switch).
    pendingSeekUsRef.current = video.readyState >= 1 ? null : sourceTimeUs;
    try {
      video.currentTime = seconds;
    } catch {
      pendingSeekUsRef.current = sourceTimeUs;
    }
  };

  const seekToTimelineTime = (timeUs: number) => {
    const clamped = Math.max(0, Math.min(timeUs, durationUs));
    setPlayheadUs(clamped);
    const clip = timedClips.find((item) => clamped >= item.start_us && clamped < item.end_us) ?? timedClips.at(-1);
    if (!clip) {
      pendingSeekUsRef.current = null;
      return;
    }
    const sourceTimeUs = clip.source_start_us + Math.max(0, clamped - clip.start_us);
    if (clip.shot_id !== selectedShotId) {
      pendingSeekUsRef.current = sourceTimeUs;
      setSelectedShotId(clip.shot_id);
      return;
    }
    applyVideoSeek(sourceTimeUs);
  };

  useEffect(() => {
    const pending = pendingSeekUsRef.current;
    const video = videoRef.current;
    if (pending === null || !video || video.readyState < 1) return;
    pendingSeekUsRef.current = null;
    video.currentTime = Math.max(0, pending) / 1_000_000;
  }, [selectedShotId]);

  if (query.isPending) return <p className="empty-state" role="status">正在读取时间线工作区…</p>;
  if (query.isError || !data) return <p className="inline-error" role="alert">编辑工作区读取失败：{String(query.error)} <button type="button" className="secondary" onClick={() => void query.refetch()}>重试</button></p>;
  return <div className="edit-workspace-v2">
    <header className="edit-command-header"><div><p className="eyebrow">NLE-lite · 不可变版本</p><h3>{data.episode_code} 时间线</h3><span>{timelineTimeLabel(durationUs)} · {clips.length} 镜 · v{data.latest_revision?.revision_no ?? 0} {data.freshness === "STALE" ? "· 需要更新" : ""}</span></div><div className="action-row"><button type="button" className="secondary" onClick={() => setHistoryOpen(true)}>版本历史</button><button type="button" className="secondary" onClick={() => setSubtitleOpen(true)}>字幕</button><button type="button" className="secondary" onClick={() => setComposeOpen(true)}>合成与导出</button></div></header>
    {syncNotice ? <section className="inline-error" role="alert"><span>{syncNotice}</span><div className="action-row"><button type="button" className="secondary" onClick={() => adoptSnapshot(loadedSnapshotRef.current, versionRef.current)}>载入服务器版本</button><button type="button" className="secondary" onClick={() => setSyncNotice(null)}>保留当前编排</button></div></section> : null}
    {data.issues.length > 0 ? <section className={`edit-issue-strip${data.issues.some((item) => item.severity === "BLOCKER") ? " is-blocked" : ""}`} aria-label="时间线问题"><strong>{data.issues.some((item) => item.severity === "BLOCKER") ? "当前不能保存" : data.freshness === "STALE" ? "已载入最新上游" : "需要留意"}</strong><span>{data.issues[0].message}{data.issues.length > 1 ? `，另有 ${data.issues.length - 1} 项` : ""}</span><Link to={routes.shotStudio(projectId, episodeId)}>检查镜头</Link></section> : null}
    <section className="edit-stage-grid"><div className="edit-player-panel"><div className="edit-player">{selected?.media_version_id ? <video key={selected.media_version_id} ref={videoRef} controls playsInline preload="none" poster={`/api/v1/media-versions/${encodeURIComponent(selected.media_version_id)}/thumbnail?size=medium&frame=poster`} src={mediaProxyUrl(selected.media_version_id)} data-original-src={mediaContentUrl(selected.media_version_id)} onError={fallbackToOriginalVideo} onLoadedMetadata={(event) => { const pending = pendingSeekUsRef.current; pendingSeekUsRef.current = null; event.currentTarget.currentTime = (pending ?? selected.source_start_us) / 1_000_000; }} onTimeUpdate={(event) => { const clip = timedClips.find((item) => item.shot_id === selected.shot_id); if (clip) setPlayheadUs(Math.min(clip.end_us, clip.start_us + Math.max(0, event.currentTarget.currentTime * 1_000_000 - selected.source_start_us))); }} aria-label={`${selected.shot_code} 视频预览`} /> : <p className="empty-state">当前镜头没有可播放视频。</p>}</div><div className="edit-transport"><button type="button" onClick={() => { const video = videoRef.current; if (!video) return; video.paused ? void video.play() : video.pause(); }}>播放 / 暂停</button><span>{timelineTimeLabel(playheadUs)} / {timelineTimeLabel(durationUs)}</span><label>缩放<input aria-label="时间线缩放" type="range" min="1" max="4" step="0.25" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /></label><button type="button" title="回到时间线开头，并把当前镜头预览定位到它的源入点" onClick={() => seekToTimelineTime(0)}>回到开头</button><small>单镜预览：播放与定位只作用于当前选中的镜头，跨镜头请在时间线上选择。</small></div></div>
      <aside className="edit-clip-inspector" aria-label="镜头 Inspector">{selected ? <><header><div><p className="eyebrow">镜头 Inspector</p><h3>{selected.shot_code}</h3></div><span className={`status-pill ${selected.continuity_status === "CONFLICT" ? "danger" : ""}`}>{EDIT_STATUS_LABELS[selected.continuity_status] ?? "状态待确认"}</span></header><p title={selected.source_name ?? undefined}>{selected.source_name ?? "尚未采用视频"}</p><label>成片时长（秒）<input type="number" min="0.1" max="3600" step="0.1" value={(selected.duration_us / 1_000_000).toFixed(2)} onChange={(event) => updateClip({ duration_us: Math.max(100_000, Math.round(Number(event.target.value || 0.1) * 1_000_000)) })} /></label><label>源视频入点（秒）<input type="number" min="0" step="0.1" value={(selected.source_start_us / 1_000_000).toFixed(2)} onChange={(event) => updateClip({ source_start_us: Math.max(0, Math.round(Number(event.target.value || 0) * 1_000_000)) })} /></label><label>入场转场<select value={selected.transition_in} disabled={timedClips[0]?.shot_id === selected.shot_id} onChange={(event) => updateClip({ transition_in: event.target.value as EditableClip["transition_in"] })}><option value="CUT">硬切</option><option value="DISSOLVE">叠化</option><option value="FADE">淡入</option></select></label><div className="edit-reorder"><button type="button" className="secondary" onClick={() => moveClip(-1)}>前移</button><button type="button" className="secondary" onClick={() => moveClip(1)}>后移</button></div><small>调整只会进入新的 TimelineRevision；不会覆盖镜头采用、源媒体或旧冻结版本。</small></> : <p className="empty-state">选择时间线中的一个镜头进行调整。</p>}</aside></section>
    <TimelineLanes clips={timedClips} audio={data.audio_clips} subtitle={data.subtitle} durationUs={durationUs} zoom={zoom} playheadUs={playheadUs} selectedShotId={selectedShotId} includeDialogue={includeDialogue} includeMusic={includeMusic} includeSubtitles={includeSubtitles} onSelect={(_shotId, startUs) => seekToTimelineTime(startUs)} onPlayheadChange={seekToTimelineTime} />
    <footer className="edit-savebar"><div className="edit-track-toggles"><label><input type="checkbox" checked={includeDialogue} onChange={(event) => setAudioOption("includeDialogue", event.target.checked)} />对白</label><label><input type="checkbox" checked={includeMusic} onChange={(event) => setAudioOption("includeMusicAndSfx", event.target.checked)} />BGM / SFX</label><label title="模型原生音轨可能包含未经脚本授权的人声；使用后期 TTS 时建议关闭。"><input type="checkbox" checked={includeSourceAudio} onChange={(event) => setAudioOption("includeSourceAudio", event.target.checked)} />模型原声</label><label><input type="checkbox" checked={includeSubtitles} onChange={(event) => setAudioOption("includeSubtitles", event.target.checked)} />字幕</label></div>{!audioOptionsKnown ? <p className="inline-error" role="note">当前服务器版本没有返回对白 / 音轨 / 字幕开关：这里按后端默认值显示，未改动的开关不会被写成显式设置。</p> : null}{includeSourceAudio && includeDialogue ? <p className="inline-error" role="note">模型原声会与后期对白同时混入；请先试听并确认没有重复或未经脚本授权的人声。</p> : null}<span>{feedback ?? (dirty ? "存在未保存编排（含对白 / 音轨 / 字幕开关）" : data.latest_revision ? `当前 v${data.latest_revision.revision_no} · ${data.latest_revision.status}` : "尚未创建时间线")}</span><div className="action-row"><button type="button" className="secondary" disabled={!dirty} onClick={() => adoptSnapshot(loadedSnapshotRef.current, versionRef.current)}>放弃调整</button><button type="button" className="secondary" disabled={!data.allowed_actions.includes("CREATE_DRAFT") || save.isPending} onClick={() => save.mutate(commandKey("timeline-draft"))}>{save.isPending ? "保存中…" : "保存新草稿"}</button><button type="button" className="primary-action" title={dirty ? "存在未保存编排：请先保存新草稿，冻结只接受已保存且未修改的版本" : undefined} disabled={!data.allowed_actions.includes("FREEZE_LATEST_DRAFT") || dirty || freeze.isPending} onClick={() => setFreezeOpen(true)}>冻结当前草稿</button></div>{dirty ? <small>冻结会锁定音轨与字幕开关，因此必须先保存新草稿。</small> : null}{save.error || freeze.error ? <p className="inline-error" role="alert">操作失败：{String(save.error ?? freeze.error)}</p> : null}</footer>
    <Drawer open={historyOpen} onClose={() => setHistoryOpen(false)} title="时间线版本历史" width={560}><div className="edit-history-list">{data.history.length === 0 ? <p className="empty-state">还没有时间线版本。补齐采用视频后保存第一份草稿，版本会显示在这里。</p> : data.history.map((item) => <article key={item.id}><div><strong>v{item.revision_no} · {EDIT_STATUS_LABELS[item.status] ?? "状态待确认"}</strong><span>{timelineTimeLabel(item.duration_us)} · {item.video_count} 镜 · {item.audio_count} 音频</span></div><small>{new Date(item.created_at).toLocaleString()} · {item.revision_hash.slice(0, 12)}</small></article>)}{data.history_has_more ? <p className="muted">这里只显示最近 20 个版本；完整历史通过分页 API 读取。</p> : null}</div></Drawer>
    <Drawer open={subtitleOpen} onClose={() => setSubtitleOpen(false)} dirtyGuard={subtitleDirty} title="字幕版本" width={720}><SubtitleRevisionPanel episodeId={episodeId} projectId={projectId} defaultSourceDocumentVersionId="" onDirtyChange={setSubtitleDirty} onCreated={() => { void refresh(); }} /></Drawer>
    <Drawer open={composeOpen} onClose={() => setComposeOpen(false)} title="合成与专业导出" width={720}><div className="edit-compose-drawer"><p>合成和导出只接受当前、已冻结且输入未失效的 TimelineRevision。</p><TimelineExportPanel timelineRevisionId={frozenId} subtitleRevisionId={data.subtitle?.revision_id ?? null} />{frozenId ? <Link className="primary-action" to={routes.delivery(projectId, episodeId)}>进入交付工作区</Link> : <p className="inline-error" role="note">请先保存草稿并冻结；上游变化后需重新创建版本。</p>}</div></Drawer>
    <Dialog open={freezeOpen} title="冻结当前时间线草稿？" onClose={() => !freeze.isPending && setFreezeOpen(false)} footer={<><button type="button" onClick={() => setFreezeOpen(false)}>取消</button><button type="button" className="primary-action" disabled={freeze.isPending} onClick={() => freeze.mutate(commandKey("timeline-freeze"))}>{freeze.isPending ? "正在冻结…" : "确认冻结新版本"}</button></>}>冻结会复制当前已保存草稿为新的不可变 revision，并锁定镜头、对白、BGM/SFX、模型原声与字幕输入；不会把机器检查当成人工批准。</Dialog>
  </div>;
}

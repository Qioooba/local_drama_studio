import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { routes } from "../app/routeRegistry";
import {
  commitEpisodeRenderReviewBatch,
  commitVideoUpscaleCleanup,
  commitEpisodeDeliverySelections,
  controlVideoUpscaleBatch,
  createVideoUpscaleBatch,
  createVideoUpscalePreview,
  createVideoUpscalePlan,
  getProjectConfiguration,
  getVideoUpscaleOptions,
  getVideoUpscalePlan,
  getVideoUpscaleRun,
  listEpisodeDeliveryVersions,
  listReviewTemplates,
  listProjectDeliveryEpisodes,
  listVideoUpscaleDeliveryBuildBatches,
  listVideoUpscaleBatches,
  planEpisodeDeliverySelections,
  planEpisodeRenderReviewBatch,
  planVideoUpscaleCleanup,
  planVideoUpscaleDeliveryBuildBatch,
  resolveVideoUpscaleSelection,
  retryFailedVideoUpscaleDeliveryBuildBatch,
  submitVideoUpscaleDeliveryBuildBatch,
  type DeliveryEpisode,
  type EpisodeDeliveryVersions,
  type EpisodeRenderBatchReviewItem,
  type EpisodeRenderBatchReviewPlan,
  type ProjectConfiguration,
  type VideoUpscaleBatch,
  type VideoUpscaleCleanupPlan,
  type VideoUpscaleDeliveryBatch,
  type VideoUpscalePlan,
} from "../generated/api";
import "./project-delivery.css";

type WorkspaceView = "episodes" | "queue" | "versions";
const VIEWS = new Set<WorkspaceView>(["episodes", "queue", "versions"]);

function selectionStorageKey(projectId: string) {
  return `localdrama.project-delivery.selection.${projectId}`;
}

function readStoredSelection(projectId: string) {
  if (!projectId || typeof window === "undefined") return new Set<string>();
  try {
    const value = JSON.parse(window.sessionStorage.getItem(selectionStorageKey(projectId)) ?? "[]");
    return new Set(Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set<string>();
  }
}

type DeliveryRender = EpisodeDeliveryVersions["items"][number];
type DeliveryTarget = ProjectConfiguration["delivery_targets"][number];

function renderPixels(render: DeliveryRender) {
  const streams = Array.isArray(render.probe.streams) ? render.probe.streams : [];
  const video = streams.find((stream) => typeof stream === "object" && stream !== null && (stream as Record<string, unknown>).codec_type === "video") as Record<string, unknown> | undefined;
  return { width: Number(video?.width ?? 0), height: Number(video?.height ?? 0) };
}

function targetMatchesRender(target: DeliveryTarget, render: DeliveryRender) {
  const pixels = renderPixels(render);
  return Number(target.spec.width ?? 0) === pixels.width && Number(target.spec.height ?? 0) === pixels.height;
}

function matchingDeliveryTarget(configuration: ProjectConfiguration | undefined, render: DeliveryRender) {
  return configuration?.delivery_targets
    .filter((target) => targetMatchesRender(target, render))
    .sort((left, right) => Number(right.version_status === "ACTIVE") - Number(left.version_status === "ACTIVE") || right.version_no - left.version_no)[0];
}

function tileFallbackLabel(tileSize: number) {
  const ladder = tileSize === 0 ? [256, 128, 64] : [512, 256, 128, 64].filter((value) => value < tileSize).slice(0, 3);
  return ladder.length ? `${tileSize === 0 ? "自动" : tileSize} → ${ladder.join(" → ")}` : "不回退";
}

function formatDuration(value: number | null | undefined) {
  if (!value) return "—";
  const seconds = Math.round(value / 1000);
  return `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}

function stateLabel(state: string | null | undefined) {
  return ({ QUEUED: "等待中", CLAIMED: "已领取", RUNNING: "处理中", SUCCEEDED: "超分完成 · 待审核", FAILED: "失败", PAUSED: "已暂停", PAUSED_FOR_BATCH: "本批已暂停", CANCELLED: "已取消", CANCELLED_FOR_BATCH: "本批已取消" } as Record<string, string>)[String(state)] ?? String(state || "未开始");
}

function deliveryStateLabel(state: string | null | undefined) {
  return ({ QUEUED: "等待打包", CLAIMED: "已领取", RUNNING: "正在打包", SUCCEEDED: "交付完成", FAILED: "打包失败", NEEDS_ATTENTION: "需要处理", ORPHANED: "等待恢复", CANCELLED: "已取消" } as Record<string, string>)[String(state)] ?? String(state || "未开始");
}

function EpisodeTable({ items, selected, onToggle, onSelectPage, onOpenVersions }: { items: DeliveryEpisode[]; selected: Set<string>; onToggle: (id: string) => void; onSelectPage: () => void; onOpenVersions: (id: string) => void }) {
  const selectable = items.filter((item) => item.selectable);
  const selectPageRef = useRef<HTMLInputElement>(null);
  const selectedOnPage = selectable.filter((item) => selected.has(item.episode.id)).length;
  const pageSelected = selectable.length > 0 && selectable.every((item) => selected.has(item.episode.id));
  useEffect(() => {
    if (selectPageRef.current) selectPageRef.current.indeterminate = selectedOnPage > 0 && !pageSelected;
  }, [pageSelected, selectedOnPage]);
  return <section className="upscale-episode-table" aria-label="分集成片列表">
    <div className="upscale-table-head">
      <label><input ref={selectPageRef} type="checkbox" checked={pageSelected} aria-checked={selectedOnPage > 0 && !pageSelected ? "mixed" : pageSelected} onChange={onSelectPage} disabled={!selectable.length} /> 选择本页可处理项</label>
      <span>来源成片</span><span>输出目标</span><span>状态</span><span className="sr-only">操作</span>
    </div>
    {items.map((item) => {
      const episode = item.episode;
      const compose = item.compose;
      const target = item.recommended_source?.geometry as { target?: { width?: number; height?: number } } | undefined;
      return <article className={`upscale-episode-row${item.selectable ? "" : " is-blocked"}`} key={episode.id}>
        <div className="upscale-episode-name">
          <input aria-label={`选择 ${episode.code} ${episode.title}`} type="checkbox" checked={selected.has(episode.id)} disabled={!item.selectable} onChange={() => onToggle(episode.id)} />
          <div><strong>{episode.code} · {episode.title || `第 ${episode.number} 集`}</strong><small>{episode.season_title || `第 ${episode.season_number} 季`} · {formatDuration(compose?.duration_ms)}</small></div>
        </div>
        <div><strong>{compose?.width && compose?.height ? `${compose.width}×${compose.height}` : "尚无成片"}</strong><small>{item.recommended_source?.kind === "DELIVERY_FILE" ? "已确认交付文件" : "已批准合成成片"}</small></div>
        <div><strong>{target?.target ? `${target.target.width}×${target.target.height}` : "—"}</strong><small>跟随横竖 · 保持帧率</small></div>
        <div>{item.selectable ? <span className="status-pill success">可处理</span> : <span className="status-pill warning">需处理</span>}<small>{item.blockers[0]?.message ?? (item.derived_version_count ? `已有 ${item.derived_version_count} 个超分候选` : "尚未超分")}</small></div>
        <button type="button" className="secondary" onClick={() => onOpenVersions(episode.id)}>版本</button>
      </article>;
    })}
  </section>;
}

function BatchCard({ batch, onControl, pending }: { batch: VideoUpscaleBatch; onControl: (batch: VideoUpscaleBatch, action: "PAUSE_PENDING" | "PAUSE_ALL" | "RESUME" | "RETRY_FAILED" | "CANCEL_UNFINISHED") => void; pending: boolean }) {
  const states = batch.aggregate.states;
  return <article className="upscale-batch-card">
    <header><div><p className="eyebrow">{new Date(batch.created_at).toLocaleString()}</p><h3>{batch.title}</h3></div><span className="status-pill neutral">{batch.control_state}</span></header>
    <dl className="upscale-batch-metrics"><div><dt>总集数</dt><dd>{batch.aggregate.total}</dd></div><div><dt>完成</dt><dd>{states.SUCCEEDED ?? 0}</dd></div><div><dt>运行</dt><dd>{(states.RUNNING ?? 0) + (states.CLAIMED ?? 0)}</dd></div><div><dt>失败</dt><dd>{states.FAILED ?? 0}</dd></div></dl>
    <div className="upscale-batch-actions">
      <button type="button" className="secondary" disabled={pending} onClick={() => onControl(batch, "PAUSE_PENDING")}>暂停待处理</button>
      <button type="button" className="secondary" disabled={pending} onClick={() => onControl(batch, "PAUSE_ALL")}>暂停全部</button>
      <button type="button" className="secondary" disabled={pending} onClick={() => onControl(batch, "RESUME")}>恢复</button>
      <button type="button" className="secondary" disabled={pending || !(states.FAILED || states.NEEDS_ATTENTION || states.ORPHANED)} onClick={() => onControl(batch, "RETRY_FAILED")}>重试失败项</button>
      <button type="button" className="danger" disabled={pending} onClick={() => onControl(batch, "CANCEL_UNFINISHED")}>取消未完成</button>
    </div>
    <div className="upscale-batch-items">{batch.items.map((item) => <div key={item.id}><span>第 {item.ordinal} 项</span><span>{stateLabel(item.effective_state)}</span><progress max={100} value={Number(item.progress.percent ?? (item.job_state === "SUCCEEDED" ? 100 : 0))} /><small>{item.progress.completed_frames ? `${item.progress.completed_frames}/${item.progress.total_frames} 帧` : item.job_id ?? "复用运行"}</small></div>)}</div>
  </article>;
}

function DeliveryBuildCard({ batch, onRetry, pending }: { batch: VideoUpscaleDeliveryBatch; onRetry: (batch: VideoUpscaleDeliveryBatch) => void; pending: boolean }) {
  const states = batch.aggregate.states;
  return <article className="upscale-batch-card delivery-build-card">
    <header><div><p className="eyebrow">{new Date(batch.created_at).toLocaleString()}</p><h3>{batch.title}</h3></div><span className="status-pill neutral">正式交付</span></header>
    <dl className="upscale-batch-metrics"><div><dt>总集数</dt><dd>{batch.aggregate.total}</dd></div><div><dt>完成</dt><dd>{states.SUCCEEDED ?? 0}</dd></div><div><dt>处理中</dt><dd>{(states.RUNNING ?? 0) + (states.QUEUED ?? 0)}</dd></div><div><dt>失败</dt><dd>{states.FAILED ?? 0}</dd></div></dl>
    <div className="upscale-batch-actions"><button type="button" className="secondary" disabled={pending || !(states.FAILED || states.NEEDS_ATTENTION || states.ORPHANED)} onClick={() => onRetry(batch)}>只重试失败项</button></div>
    <div className="upscale-batch-items">{batch.items.map((item) => <div key={item.id}><span>第 {item.ordinal} 项</span><span>{deliveryStateLabel(item.job_state)}</span><progress max={100} value={Number(item.progress.percent ?? (item.job_state === "SUCCEEDED" ? 100 : 0))} /><small>{item.package_id ? `交付包 ${item.package_id.slice(0, 8)}` : item.job_id ?? "等待创建"}</small></div>)}</div>
  </article>;
}

function PreviewCompare({ sourceUrl, resultUrl, startMs }: { sourceUrl: string; resultUrl: string; startMs: number }) {
  const sourceRef = useRef<HTMLVideoElement>(null);
  const [pixelInspect, setPixelInspect] = useState(false);
  const [cropX, setCropX] = useState(50);
  const [cropY, setCropY] = useState(50);
  const [sourceSize, setSourceSize] = useState({ width: 0, height: 0 });
  const [resultSize, setResultSize] = useState({ width: 0, height: 0 });
  const offset = startMs / 1000;
  const alignSource = (result: HTMLVideoElement) => {
    if (sourceRef.current && Math.abs(sourceRef.current.currentTime - (offset + result.currentTime)) > 0.08) sourceRef.current.currentTime = offset + result.currentTime;
  };
  const videoStyle = (size: { width: number; height: number }) => pixelInspect && size.width
    ? { width: `${size.width}px`, height: `${size.height}px`, left: "50%", top: "50%", transform: `translate(-${cropX}%, -${cropY}%)`, maxWidth: "none" }
    : undefined;
  return <section className="upscale-preview-inspector" aria-label="样片画质对比">
    <div className="upscale-preview-tools">
      <button type="button" className="secondary" aria-pressed={pixelInspect} onClick={() => setPixelInspect((value) => !value)}>{pixelInspect ? "退出 100% 像素裁切" : "进入 100% 像素裁切"}</button>
      <span role="status">{pixelInspect ? `同步查看原始像素区域：横向 ${cropX}% · 纵向 ${cropY}%` : "适合窗口同步对比"}</span>
    </div>
    {pixelInspect && <fieldset className="upscale-crop-controls">
      <legend>两侧同步裁切位置</legend>
      <label>横向<input type="range" min={0} max={100} value={cropX} onChange={(event) => setCropX(Number(event.target.value))} /><output>{cropX}%</output></label>
      <label>纵向<input type="range" min={0} max={100} value={cropY} onChange={(event) => setCropY(Number(event.target.value))} /><output>{cropY}%</output></label>
    </fieldset>}
    <div className={`upscale-preview-compare${pixelInspect ? " is-pixel-inspect" : ""}`}>
      <figure><figcaption>原始成片 · 静音同步 · 原文件</figcaption><div className="upscale-video-viewport"><video ref={sourceRef} muted playsInline preload="none" src={`${sourceUrl}#t=${offset}`} aria-label="原始成片同步对比" style={videoStyle(sourceSize)} onLoadedMetadata={(event) => setSourceSize({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight })} /></div></figure>
      <figure><figcaption>AI 超分样片 · 此侧声音 · 非交付成片</figcaption><div className="upscale-video-viewport"><video controls playsInline preload="none" src={resultUrl} aria-label="AI 超分五秒样片" style={videoStyle(resultSize)} onLoadedMetadata={(event) => setResultSize({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight })} onPlay={(event) => { alignSource(event.currentTarget); void sourceRef.current?.play(); }} onPause={() => sourceRef.current?.pause()} onSeeking={(event) => alignSource(event.currentTarget)} onTimeUpdate={(event) => alignSource(event.currentTarget)} /></div></figure>
    </div>
    <p className="muted upscale-preview-evidence">当前读取原始成片与 5 秒样片文件，不使用低码率预览代理；样片只用于质检，不能采用或交付。</p>
  </section>;
}

export function ProjectDeliveryPage() {
  const { projectId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const requestedView = params.get("view") as WorkspaceView | null;
  const view: WorkspaceView = requestedView && VIEWS.has(requestedView) ? requestedView : "episodes";
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [selected, setSelected] = useState<Set<string>>(() => readStoredSelection(projectId));
  const [presetVersionId, setPresetVersionId] = useState("");
  const [profileVersionId, setProfileVersionId] = useState("");
  const [tileSize, setTileSize] = useState(0);
  const [chunkFrames, setChunkFrames] = useState(240);
  const [crf, setCrf] = useState(18);
  const [planId, setPlanId] = useState<string | null>(null);
  const [previewRunId, setPreviewRunId] = useState<string | null>(null);
  const [deliveryBuildPlan, setDeliveryBuildPlan] = useState<{ plan_hash: string; items: Array<Record<string, unknown>>; requestItems: Array<{ episode_id: string; target_version_id: string }> } | null>(null);
  const [cleanupPlan, setCleanupPlan] = useState<VideoUpscaleCleanupPlan | null>(null);
  const [cleanupNotice, setCleanupNotice] = useState("");
  const [reviewChecks, setReviewChecks] = useState<Record<string, Record<string, boolean>>>({});
  const [reviewComments, setReviewComments] = useState<Record<string, string>>({});
  const [reviewBatchPlan, setReviewBatchPlan] = useState<EpisodeRenderBatchReviewPlan | null>(null);
  const [reviewNotice, setReviewNotice] = useState("");
  const [acknowledgeWarnings, setAcknowledgeWarnings] = useState(false);
  const queryClient = useQueryClient();
  const episodes = useQuery({ queryKey: ["project-delivery", projectId, "episodes", deferredSearch], queryFn: () => listProjectDeliveryEpisodes(projectId, { search: deferredSearch || undefined, limit: 50 }), enabled: Boolean(projectId), placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === projectId ? previous : undefined });
  const options = useQuery({ queryKey: ["project-delivery", projectId, "options"], queryFn: () => getVideoUpscaleOptions(projectId), enabled: Boolean(projectId) });
  const batches = useQuery({ queryKey: ["project-delivery", projectId, "batches"], queryFn: () => listVideoUpscaleBatches(projectId), enabled: Boolean(projectId) && view === "queue", refetchInterval: (query) => query.state.data?.items.some((batch) => batch.items.some((item) => ["QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"].includes(String(item.job_state)))) ? 2_000 : false });
  const configuration = useQuery({ queryKey: ["project", projectId, "configuration"], queryFn: () => getProjectConfiguration(projectId), enabled: Boolean(projectId) && view === "versions" });
  const versionEpisodeId = params.get("episodeId") ?? episodes.data?.items[0]?.episode.id ?? "";
  const versions = useQuery({ queryKey: ["project-delivery", versionEpisodeId, "versions"], queryFn: () => listEpisodeDeliveryVersions(versionEpisodeId), enabled: view === "versions" && Boolean(versionEpisodeId) });
  const reviewEpisodeIds = [...(selected.size ? selected : new Set([versionEpisodeId].filter(Boolean)))].sort();
  const reviewVersions = useQuery({
    queryKey: ["project-delivery", projectId, "batch-review-versions", reviewEpisodeIds],
    queryFn: () => Promise.all(reviewEpisodeIds.map((episodeId) => listEpisodeDeliveryVersions(episodeId))),
    enabled: view === "versions" && reviewEpisodeIds.length > 0,
  });
  const reviewTemplates = useQuery({ queryKey: ["review-templates", "episode_upscale"], queryFn: () => listReviewTemplates(), enabled: view === "versions" });
  const plan = useQuery({ queryKey: ["project-delivery", "plan", planId], queryFn: () => getVideoUpscalePlan(planId as string), enabled: Boolean(planId), refetchInterval: (query) => query.state.data?.plan.status === "CHECKING" ? 1_500 : false });
  const previewRun = useQuery({ queryKey: ["project-delivery", "preview", previewRunId], queryFn: () => getVideoUpscaleRun(previewRunId as string), enabled: Boolean(previewRunId), refetchInterval: (query) => ["QUEUED", "CLAIMED", "RUNNING"].includes(String(query.state.data?.run.job_state)) ? 2_000 : false });
  const deliveryBatches = useQuery({ queryKey: ["project-delivery", projectId, "delivery-builds"], queryFn: () => listVideoUpscaleDeliveryBuildBatches(projectId), enabled: Boolean(projectId) && view === "versions", refetchInterval: (query) => query.state.data?.items.some((batch) => batch.items.some((item) => ["QUEUED", "CLAIMED", "RUNNING"].includes(String(item.job_state)))) ? 2_000 : false });

  useEffect(() => {
    if (!options.data || presetVersionId) return;
    const configured = String(options.data.settings.preset_version_id ?? "");
    const preset = options.data.presets.find((item) => item.version_id === configured) ?? options.data.presets[0];
    if (preset) {
      setPresetVersionId(preset.version_id);
      setTileSize(Number(preset.model_options.tile_size ?? 0));
      setChunkFrames(Number(preset.pipeline_options.chunk_frames ?? 240));
      setCrf(Number(preset.pipeline_options.crf ?? 18));
      if (preset.profile_version_id) setProfileVersionId(preset.profile_version_id);
    }
    const ready = options.data.profiles.find((item) => item.ready);
    if (ready) setProfileVersionId((current) => current || ready.profile_version_id);
  }, [options.data, presetVersionId]);

  useEffect(() => {
    setSelected(readStoredSelection(projectId));
    setPlanId(null);
    setPreviewRunId(null);
    setDeliveryBuildPlan(null);
    setReviewBatchPlan(null);
    setReviewChecks({});
    setReviewComments({});
    setReviewNotice("");
  }, [projectId]);

  const updateSelected = (updater: (current: Set<string>) => Set<string>) => setSelected((current) => {
    const next = updater(current);
    if (typeof window !== "undefined") window.sessionStorage.setItem(selectionStorageKey(projectId), JSON.stringify([...next]));
    return next;
  });

  const readyProfiles = options.data?.profiles.filter((profile) => profile.ready) ?? [];
  const activePlan: VideoUpscalePlan | undefined = plan.data?.plan;
  const warningIds = activePlan?.items.flatMap((item) => item.warnings.map((warning) => `${item.episode_id}:${warning.code}`)) ?? [];
  const selectedPreset = options.data?.presets.find((item) => item.version_id === presetVersionId);
  const configuredPipelineOverrides = options.data?.settings.preset_version_id === presetVersionId
    ? ((options.data.settings.overrides as { pipeline?: Record<string, unknown> } | undefined)?.pipeline ?? {})
    : {};
  const sourcePolicy = String(configuredPipelineOverrides.source_policy ?? selectedPreset?.pipeline_options.source_policy ?? "PREFER_FINAL_DELIVERY") as "PREFER_FINAL_DELIVERY" | "APPROVED_COMPOSE";
  const reviewTemplate = reviewTemplates.data?.items
    .filter((item) => item.code === "episode_upscale" && item.subject_type === "EPISODE_RENDER_VERSION")
    .sort((left, right) => right.version_no - left.version_no)[0];
  const reviewCandidates = (reviewVersions.data ?? []).flatMap(({ versions: page }) => {
    const render = page.items.find((item) => item.render_kind === "SUPER_RESOLUTION" && !item.approved);
    return render ? [{ page, render }] : [];
  });
  const allReviewChecksComplete = reviewTemplate
    ? reviewCandidates.length === reviewEpisodeIds.length
      && reviewCandidates.every(({ render }) => reviewTemplate.items.filter((item) => item.required).every((item) => reviewChecks[render.id]?.[item.id]))
    : false;

  const prepare = useMutation({
    mutationFn: async (mode: "EXPLICIT" | "ALL_ELIGIBLE") => {
      const resolved = await resolveVideoUpscaleSelection(projectId, { mode, episode_ids: mode === "EXPLICIT" ? [...selected] : [], search: mode === "ALL_ELIGIBLE" ? deferredSearch || undefined : undefined, source_policy: sourcePolicy });
      if (!resolved.selection.count) throw new Error("当前没有可处理的分集");
      if (mode === "ALL_ELIGIBLE") updateSelected(() => new Set(resolved.selection.items.map((item) => item.episode_id)));
      return createVideoUpscalePlan(projectId, { schema_version: "localdrama.video-upscale-request.v1", selection_hash: resolved.selection.selection_hash, episode_ids: resolved.selection.items.map((item) => item.episode_id), preset_version_id: presetVersionId, execution_profile_version_id: profileVersionId || null, batch_pipeline_overrides: { chunk_frames: chunkFrames, crf }, batch_model_overrides: { tile_size: tileSize }, item_overrides: [], existing_result_policy: "REUSE_EQUIVALENT" });
    },
    onSuccess: (data) => { setPlanId(data.plan.id); setAcknowledgeWarnings(false); },
  });
  const submit = useMutation({
    mutationFn: () => createVideoUpscaleBatch(projectId, { plan_id: activePlan?.id, plan_hash: activePlan?.plan_hash, title: `整剧 1080p 超分 · ${new Date().toLocaleDateString()}`, acknowledged_warning_ids: warningIds }, crypto.randomUUID()),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["project-delivery", projectId, "batches"] }); setParams({ view: "queue" }); },
  });
  const preview = useMutation({
    mutationFn: () => {
      const previewEpisodeId = activePlan?.items.find((item) => !item.blockers.length)?.episode_id;
      if (!activePlan?.plan_hash || !previewEpisodeId) throw new Error("预检通过后才能创建样片");
      return createVideoUpscalePreview(projectId, { plan_id: activePlan.id, plan_hash: activePlan.plan_hash, episode_id: previewEpisodeId, start_ms: 0, duration_ms: 5_000, acknowledged_warning_ids: warningIds }, crypto.randomUUID());
    },
    onSuccess: (data) => setPreviewRunId(data.run.id),
  });
  const control = useMutation({ mutationFn: ({ batch, action }: { batch: VideoUpscaleBatch; action: "PAUSE_PENDING" | "PAUSE_ALL" | "RESUME" | "RETRY_FAILED" | "CANCEL_UNFINISHED" }) => controlVideoUpscaleBatch(batch.id, action, batch.revision), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["project-delivery", projectId, "batches"] }) });
  const adopt = useMutation({
    mutationFn: async (renderId: string) => {
      const render = versions.data?.versions.items.find((item) => item.id === renderId);
      const targetVersionId = render ? matchingDeliveryTarget(configuration.data?.configuration, render)?.version_id : undefined;
      if (!targetVersionId) throw new Error("没有与该成片横竖方向和像素精确匹配的交付目标版本");
      const current = versions.data?.versions.selections.find((item) => item.target_slot === targetVersionId);
      const items = [{ episode_id: versionEpisodeId, target_slot: targetVersionId, selected_render_id: renderId, expected_selection_revision: current?.revision ?? 0 }];
      const prepared = await planEpisodeDeliverySelections(projectId, items);
      return commitEpisodeDeliverySelections(projectId, items, prepared.plan.plan_hash);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["project-delivery", versionEpisodeId, "versions"] }),
  });
  const adoptSelected = useMutation({
    mutationFn: async () => {
      const targetVersionId = String(configuration.data?.configuration?.selected_delivery_target_version_id ?? "");
      if (!targetVersionId) throw new Error("项目尚未选择交付目标版本");
      const episodeIds = selected.size ? [...selected] : [versionEpisodeId].filter(Boolean);
      const pages = await Promise.all(episodeIds.map((episodeId) => listEpisodeDeliveryVersions(episodeId)));
      const items = pages.flatMap(({ versions: page }) => {
        const render = page.items.find((item) => item.render_kind === "SUPER_RESOLUTION" && item.adoptable);
        if (!render) return [];
        const targetVersionId = matchingDeliveryTarget(configuration.data?.configuration, render)?.version_id;
        if (!targetVersionId) return [];
        const current = page.selections.find((item) => item.target_slot === targetVersionId);
        if (current?.selected_render_id === render.id) return [];
        return [{ episode_id: page.episode_id, target_slot: targetVersionId, selected_render_id: render.id, expected_selection_revision: current?.revision ?? 0 }];
      });
      if (!items.length) throw new Error("所选分集没有可批量采用的已批准超分版本");
      const prepared = await planEpisodeDeliverySelections(projectId, items);
      return commitEpisodeDeliverySelections(projectId, items, prepared.plan.plan_hash);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["project-delivery"] });
      setDeliveryBuildPlan(null);
    },
  });
  const prepareDelivery = useMutation({
    mutationFn: async () => {
      const episodeIds = selected.size ? [...selected] : [versionEpisodeId].filter(Boolean);
      const pages = await Promise.all(episodeIds.map((episodeId) => listEpisodeDeliveryVersions(episodeId)));
      const requestItems = pages.flatMap(({ versions: page }) => {
        for (const selection of page.selections) {
          const render = page.items.find((item) => item.id === selection.selected_render_id && item.render_kind === "SUPER_RESOLUTION");
          const target = configuration.data?.configuration?.delivery_targets.find((item) => item.version_id === selection.target_slot);
          if (render && target && targetMatchesRender(target, render)) {
            return [{ episode_id: page.episode_id, target_version_id: target.version_id }];
          }
        }
        return [];
      });
      if (!requestItems.length) throw new Error("请先选择要交付的分集");
      if (requestItems.length !== episodeIds.length) throw new Error("部分分集尚未采用到与其横竖方向和像素匹配的交付目标");
      const prepared = await planVideoUpscaleDeliveryBuildBatch(projectId, { items: requestItems });
      return { ...prepared.plan, requestItems };
    },
    onSuccess: (data) => setDeliveryBuildPlan(data),
  });
  const submitDelivery = useMutation({
    mutationFn: () => {
      if (!deliveryBuildPlan) throw new Error("请先检查批量交付计划");
      return submitVideoUpscaleDeliveryBuildBatch(projectId, { items: deliveryBuildPlan.requestItems, plan_hash: deliveryBuildPlan.plan_hash, title: `整剧 1080p 正式交付 · ${new Date().toLocaleDateString()}` }, crypto.randomUUID());
    },
    onSuccess: async () => {
      setDeliveryBuildPlan(null);
      await queryClient.invalidateQueries({ queryKey: ["project-delivery", projectId, "delivery-builds"] });
    },
  });
  const retryDelivery = useMutation({
    mutationFn: (batch: VideoUpscaleDeliveryBatch) => retryFailedVideoUpscaleDeliveryBuildBatch(batch.id, batch.revision),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["project-delivery", projectId, "delivery-builds"] }),
  });
  const prepareReviewBatch = useMutation({
    mutationFn: () => {
      if (!reviewTemplate) throw new Error("当前没有可用的 episode_upscale 审核模板");
      if (reviewCandidates.length !== reviewEpisodeIds.length) throw new Error("所选分集中有分集没有待审核的最新超分版本");
      const items: EpisodeRenderBatchReviewItem[] = reviewCandidates.map(({ render }) => ({
        render_id: render.id,
        template_version_id: reviewTemplate.id,
        decision: "APPROVED",
        expected_subject_revision: render.revision,
        checks: reviewTemplate.items
          .filter((item) => reviewChecks[render.id]?.[item.id])
          .map((item) => ({ item_id: item.id, result: "PASS", comment: `人工逐集确认：${item.label}` })),
        comment: reviewComments[render.id]?.trim() || null,
      }));
      return planEpisodeRenderReviewBatch(projectId, items);
    },
    onSuccess: ({ plan: nextPlan }) => {
      setReviewBatchPlan(nextPlan);
      setReviewNotice("");
    },
  });
  const commitReviewBatch = useMutation({
    mutationFn: () => {
      if (!reviewBatchPlan) throw new Error("请先生成批量审核计划");
      return commitEpisodeRenderReviewBatch(reviewBatchPlan.plan_token, reviewBatchPlan.plan_hash);
    },
    onSuccess: async ({ commit }) => {
      setReviewNotice(`已原子批准 ${commit.review_count} 集超分成片；现在可以批量采用。`);
      setReviewBatchPlan(null);
      setReviewChecks({});
      setReviewComments({});
      await queryClient.invalidateQueries({ queryKey: ["project-delivery"] });
    },
  });
  const previewCleanup = useMutation({
    mutationFn: () => planVideoUpscaleCleanup(projectId, 7),
    onSuccess: ({ plan: nextPlan }) => {
      setCleanupPlan(nextPlan);
      setCleanupNotice("");
    },
  });
  const cleanup = useMutation({
    mutationFn: () => {
      if (!cleanupPlan) throw new Error("请先预览可清理内容");
      return commitVideoUpscaleCleanup(projectId, cleanupPlan);
    },
    onSuccess: ({ result }) => {
      setCleanupNotice(`已安全清理 ${result.deleted_count} 个运行目录，释放 ${formatBytes(result.released_bytes)}。正式成片和交付包未改动。`);
      setCleanupPlan(null);
    },
  });

  const changeView = (next: WorkspaceView, episodeId?: string) => setParams((current) => { const value = new URLSearchParams(current); if (next === "episodes") value.delete("view"); else value.set("view", next); if (episodeId) value.set("episodeId", episodeId); return value; });
  const toggle = (id: string) => updateSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); setPlanId(null); return next; });
  const selectPage = () => updateSelected((current) => { const next = new Set(current); const selectable = episodes.data?.items.filter((item) => item.selectable) ?? []; const all = selectable.every((item) => next.has(item.episode.id)); selectable.forEach((item) => all ? next.delete(item.episode.id) : next.add(item.episode.id)); setPlanId(null); return next; });
  const error = episodes.error ?? options.error ?? prepare.error ?? submit.error ?? preview.error ?? control.error ?? adopt.error ?? adoptSelected.error ?? prepareReviewBatch.error ?? commitReviewBatch.error ?? prepareDelivery.error ?? submitDelivery.error ?? retryDelivery.error ?? previewCleanup.error ?? cleanup.error ?? deliveryBatches.error ?? reviewVersions.error ?? reviewTemplates.error;

  return <main className="v2-page project-delivery-page" aria-labelledby="project-delivery-title">
    <header className="project-delivery-header"><div><p className="eyebrow">项目级交付工作区</p><h2 id="project-delivery-title">整剧交付</h2><p className="muted">批量把已批准的 480p 分集成片用本机 AI 超分为 1080p；输出是新版本，不覆盖原片，也不会自动采用。</p></div><Link className="secondary" to={routes.systemCapabilities(projectId)}>配置视频超分引擎</Link></header>
    <nav className="project-delivery-tabs" aria-label="整剧交付视图">{(["episodes", "queue", "versions"] as WorkspaceView[]).map((item) => <button key={item} type="button" aria-current={view === item ? "page" : undefined} onClick={() => changeView(item)}>{item === "episodes" ? "分集成片" : item === "queue" ? "超分队列" : "版本与交付"}</button>)}</nav>
    {error && <p className="inline-error" role="alert">操作未完成：{error instanceof Error ? error.message : String(error)}</p>}
    {view === "episodes" && <div className="project-delivery-layout">
      <section className="project-delivery-main">
        <div className="project-delivery-toolbar"><label>搜索分集<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="集名或编号" /></label><button type="button" className="secondary" disabled={prepare.isPending || !presetVersionId || !profileVersionId} onClick={() => prepare.mutate("ALL_ELIGIBLE")}>选择全部符合条件并检查</button><span>已选 <strong>{selected.size}</strong> 集</span></div>
        {episodes.isLoading ? <p className="loading-state" role="status">正在读取分集成片…</p> : episodes.data?.items.length ? <EpisodeTable items={episodes.data.items} selected={selected} onToggle={toggle} onSelectPage={selectPage} onOpenVersions={(id) => changeView("versions", id)} /> : <p className="empty-state">当前项目还没有可显示的分集。</p>}
      </section>
      <aside className="upscale-config-panel" aria-label="本批超分设置">
        <p className="eyebrow">仅本批</p><h3>AI 超分设置</h3>
        <label>一键预设<select value={presetVersionId} onChange={(event) => { const id = event.target.value; const preset = options.data?.presets.find((item) => item.version_id === id); setPresetVersionId(id); if (preset) { setTileSize(Number(preset.model_options.tile_size ?? 0)); setChunkFrames(Number(preset.pipeline_options.chunk_frames ?? 240)); setCrf(Number(preset.pipeline_options.crf ?? 18)); } setPlanId(null); }}>{options.data?.presets.map((preset) => <option key={preset.version_id} value={preset.version_id}>{preset.title}</option>)}</select></label>
        <label>执行模型<select value={profileVersionId} onChange={(event) => { setProfileVersionId(event.target.value); setPlanId(null); }}><option value="">请选择已验证模型</option>{readyProfiles.map((profile) => <option key={profile.profile_version_id} value={profile.profile_version_id}>{profile.title} · v{profile.version_no}</option>)}</select></label>
        {!readyProfiles.length && !options.isLoading && <p className="upscale-config-warning">没有已发布且可执行的 NCNN 视频超分 Profile。请先到“能力与模型”完成程序、权重和真实 smoke。</p>}
        <dl className="upscale-summary"><div><dt>输出</dt><dd>横屏 1920×1080<br />竖屏 1080×1920</dd></div><div><dt>来源</dt><dd>{sourcePolicy === "APPROVED_COMPOSE" ? "只用已批准合成片" : "优先已批准交付文件"}</dd></div><div><dt>帧率</dt><dd>保持原 CFR</dd></div><div><dt>声音 / 字幕</dt><dd>声音兼容复制；字幕继承源状态</dd></div></dl>
        <details><summary>高级参数</summary><label>Tile<select aria-label="Tile" value={tileSize} onChange={(event) => { setTileSize(Number(event.target.value)); setPlanId(null); }}><option value={0}>自动</option><option value={64}>64</option><option value={128}>128</option><option value={256}>256</option><option value={512}>512</option></select><small>仅首块明确显存不足时按冻结序列回退：{tileFallbackLabel(tileSize)}</small></label><label>分块帧数<input type="number" min={48} max={480} value={chunkFrames} onChange={(event) => { setChunkFrames(Number(event.target.value)); setPlanId(null); }} /></label><label>H.264 CRF<input type="number" min={14} max={28} value={crf} onChange={(event) => { setCrf(Number(event.target.value)); setPlanId(null); }} /></label></details>
        <button type="button" className="primary-action" disabled={!selected.size || !presetVersionId || !profileVersionId || prepare.isPending} onClick={() => prepare.mutate("EXPLICIT")}>{prepare.isPending ? "正在创建预检…" : `检查所选 ${selected.size} 集`}</button>
        {activePlan && <section className="upscale-plan-summary" aria-live="polite">
          <div><strong>预检：{activePlan.status}</strong><span>{activePlan.items.filter((item) => !item.blockers.length).length} 可执行 · {activePlan.items.filter((item) => item.blockers.length).length} 阻塞</span></div>
          {activePlan.items.flatMap((item) => item.blockers).map((item, index) => <p className="inline-error" key={`${item.code}-${index}`}>{item.message}</p>)}
          {warningIds.length > 0 && <label className="upscale-warning-ack"><input type="checkbox" checked={acknowledgeWarnings} onChange={(event) => setAcknowledgeWarnings(event.target.checked)} /> 我已检查 {warningIds.length} 条画幅或输入提醒</label>}
          <div className="upscale-plan-actions"><button type="button" className="secondary" disabled={activePlan.status !== "READY" || !activePlan.plan_hash || (warningIds.length > 0 && !acknowledgeWarnings) || preview.isPending} onClick={() => preview.mutate()}>{preview.isPending ? "正在创建样片…" : "试跑首集 5 秒"}</button><button type="button" className="primary-action" disabled={activePlan.status !== "READY" || !activePlan.plan_hash || (warningIds.length > 0 && !acknowledgeWarnings) || submit.isPending} onClick={() => submit.mutate()}>{submit.isPending ? "正在原子提交…" : `将 ${activePlan.items.length} 集加入队列`}</button></div>
          {previewRun.data?.run && <div className="upscale-preview"><p><strong>样片：</strong>{stateLabel(previewRun.data.run.job_state)} · 仅用于比较，不会登记为正式成片</p>{previewRun.data.run.content_url && previewRun.data.run.source_content_url && <PreviewCompare sourceUrl={previewRun.data.run.source_content_url} resultUrl={previewRun.data.run.content_url} startMs={previewRun.data.run.sample_start_ms ?? 0} />}</div>}
        </section>}
      </aside>
    </div>}
    {view === "queue" && <section className="project-delivery-queue"><div className="panel-heading"><div><p className="eyebrow">持久后台队列</p><h3>超分批次</h3></div><div className="upscale-batch-actions"><button type="button" className="secondary" disabled={previewCleanup.isPending || cleanup.isPending} onClick={() => previewCleanup.mutate()}>{previewCleanup.isPending ? "正在检查…" : "预览过期中间文件"}</button><button type="button" className="secondary" onClick={() => void batches.refetch()}>刷新</button></div></div>{cleanupPlan && <section className="upscale-plan-summary" aria-live="polite"><div><strong>7 天保留期清理预览</strong><span>{cleanupPlan.candidate_count} 个运行目录 · {formatBytes(cleanupPlan.reclaimable_bytes)}</span></div><p className="muted">只清理无活动租约、已过保留期的超分中间目录；正式成片、交付包、运行回执和源文件不会删除。{cleanupPlan.skipped.length ? `另有 ${cleanupPlan.skipped.length} 项因路径安全检查跳过。` : ""}</p><div className="upscale-plan-actions"><button type="button" className="danger" disabled={!cleanupPlan.candidate_count || cleanup.isPending} onClick={() => cleanup.mutate()}>{cleanup.isPending ? "正在安全清理…" : `确认清理 ${cleanupPlan.candidate_count} 项`}</button><button type="button" className="secondary" disabled={cleanup.isPending} onClick={() => setCleanupPlan(null)}>取消</button></div></section>}{cleanupNotice && <p className="status-note success" role="status">{cleanupNotice}</p>}{batches.isLoading ? <p className="loading-state" role="status">正在读取批次…</p> : batches.data?.items.length ? batches.data.items.map((batch) => <BatchCard key={batch.id} batch={batch} pending={control.isPending} onControl={(target, action) => control.mutate({ batch: target, action })} />) : <p className="empty-state">还没有超分批次。任务创建后即使关闭页面也会保留。</p>}</section>}
    {view === "versions" && <section className="project-delivery-versions episode-review-batch" aria-labelledby="episode-review-batch-title">
      <div className="panel-heading"><div><p className="eyebrow">逐集人工门禁 · 原子提交</p><h3 id="episode-review-batch-title">批量审核超分成片</h3></div><span className="status-pill neutral">{reviewCandidates.length}/{reviewEpisodeIds.length} 集待审核</span></div>
      <p className="muted">沿用“分集成片”页的勾选集合；未勾选时审核当前分集。每一集必须由审核人独立确认全部检查项，不提供“全部通过”快捷操作。任一集版本过期、来源变化、QC 不通过或漏项，整批不会写入任何审核记录。</p>
      {reviewVersions.isLoading || reviewTemplates.isLoading ? <p className="loading-state" role="status">正在加载逐集审核依据…</p> : reviewCandidates.length ? <div className="episode-review-grid">{reviewCandidates.map(({ page, render }, episodeIndex) => {
        const episodeInfo = episodes.data?.items.find((item) => item.episode.id === page.episode_id)?.episode;
        const episodeLabel = episodeInfo ? `${episodeInfo.code} · ${episodeInfo.title}` : `分集 ${page.episode_id.slice(0, 8)}`;
        const ready = reviewTemplate
          ? reviewTemplate.items.filter((item) => item.required).every((item) => reviewChecks[render.id]?.[item.id])
          : false;
        return <fieldset className="episode-review-card" key={render.id} disabled={prepareReviewBatch.isPending || commitReviewBatch.isPending}>
          <legend><span>{episodeIndex + 1}. {episodeLabel}</span><span className={`status-pill ${ready ? "success" : "warning"}`}>{ready ? "检查完成" : "待逐项确认"}</span></legend>
          <p className="episode-review-evidence">候选 {render.id.slice(0, 8)} · revision {render.revision} · {render.machine_qc_passed ? "机器 QC 通过" : "机器 QC 未通过"} · {render.source_current ? "来源有效" : "来源已更新"}</p>
          <div className="episode-review-checks">{reviewTemplate?.items.map((item) => <label key={item.id}><input type="checkbox" checked={Boolean(reviewChecks[render.id]?.[item.id])} onChange={(event) => { setReviewChecks((current) => ({ ...current, [render.id]: { ...current[render.id], [item.id]: event.target.checked } })); setReviewBatchPlan(null); setReviewNotice(""); }} /><span>{item.label}{item.required ? <small>必填</small> : null}</span></label>)}</div>
          <label className="episode-review-comment">本集审核备注（可选）<textarea value={reviewComments[render.id] ?? ""} onChange={(event) => { setReviewComments((current) => ({ ...current, [render.id]: event.target.value })); setReviewBatchPlan(null); setReviewNotice(""); }} placeholder="记录本集脸部、字幕、动态纹理等复核说明" /></label>
        </fieldset>;
      })}</div> : <p className="empty-state">所选分集没有待审核的最新 AI 超分成片。</p>}
      {reviewCandidates.length !== reviewEpisodeIds.length && !reviewVersions.isLoading && <p className="inline-error" role="status">所选 {reviewEpisodeIds.length} 集中只有 {reviewCandidates.length} 集存在待审核超分版本；请先完成缺失分集的超分或移出选择。</p>}
      <div className="episode-review-batch-actions"><button type="button" className="secondary" disabled={!allReviewChecksComplete || prepareReviewBatch.isPending || commitReviewBatch.isPending} onClick={() => prepareReviewBatch.mutate()}>{prepareReviewBatch.isPending ? "正在冻结审核依据…" : `检查 ${reviewCandidates.length} 集审核计划`}</button><button type="button" className="primary-action" disabled={!reviewBatchPlan || commitReviewBatch.isPending} onClick={() => commitReviewBatch.mutate()}>{commitReviewBatch.isPending ? "正在原子提交…" : reviewBatchPlan ? `确认原子批准 ${reviewBatchPlan.would_create_review_count} 集` : "先检查后批准"}</button></div>
      {reviewBatchPlan && <p className="status-note success" role="status">计划已冻结：{reviewBatchPlan.would_create_review_count} 集的版本、文件哈希、合成根、机器 QC、模板与逐集检查结果均已记录；提交时会再次校验。</p>}
      {reviewNotice && <p className="status-note success" role="status">{reviewNotice}</p>}
    </section>}
    {view === "versions" && <section className="project-delivery-versions"><div className="panel-heading"><div><p className="eyebrow">人工门禁</p><h3>版本与采用</h3></div><select aria-label="选择分集" value={versionEpisodeId} onChange={(event) => changeView("versions", event.target.value)}>{episodes.data?.items.map((item) => <option key={item.episode.id} value={item.episode.id}>{item.episode.code} · {item.episode.title}</option>)}</select></div>{versions.isLoading ? <p className="loading-state" role="status">正在读取成片版本…</p> : versions.data?.versions.items.map((render) => { const selectedForTarget = versions.data?.versions.selections.some((item) => item.selected_render_id === render.id); const matchedTarget = matchingDeliveryTarget(configuration.data?.configuration, render); return <article className="delivery-version-card" key={render.id}><div><span className={`status-pill ${render.render_kind === "SUPER_RESOLUTION" ? "running" : "neutral"}`}>{render.render_kind === "SUPER_RESOLUTION" ? "AI 超分" : "原合成"}</span><h4>{render.id.slice(0, 8)} · {new Date(render.created_at).toLocaleString()}</h4><p>{render.approved ? "人工已批准" : "待人工审核"} · {render.machine_qc_passed ? "机器 QC 通过" : "机器 QC 待处理"} · {render.source_current ? "来源有效" : "来源已更新"} · {matchedTarget ? `目标 ${matchedTarget.title}` : "无匹配目标"}</p></div><div className="delivery-version-actions">{render.render_kind === "SUPER_RESOLUTION" && !render.approved && <Link className="secondary" to={`${routes.postReview(projectId, versionEpisodeId)}?targetKind=EPISODE_RENDER_VERSION&targetId=${encodeURIComponent(render.id)}`}>打开人工审核</Link>}<button type="button" className="primary-action" disabled={!render.adoptable || selectedForTarget || adopt.isPending || !matchedTarget} onClick={() => adopt.mutate(render.id)}>{selectedForTarget ? "当前已采用" : matchedTarget ? "采用到匹配交付目标" : "无匹配交付目标"}</button></div></article>; })}</section>}
    {view === "versions" && <section className="project-delivery-versions delivery-batch-command" aria-label="批量采用与正式交付">
      <div className="panel-heading"><div><p className="eyebrow">批量交付门禁</p><h3>采用所选版本并生成正式交付包</h3></div><span className="status-pill neutral">{selected.size || (versionEpisodeId ? 1 : 0)} 集</span></div>
      <p className="muted">使用“分集成片”页的勾选集合；未勾选时只处理当前分集。系统只采用已通过机器 QC 和人工审核、且来源仍有效的超分版本。</p>
      <div className="delivery-version-actions">
        <button type="button" className="secondary" disabled={adoptSelected.isPending || !configuration.data?.configuration?.delivery_targets.length} onClick={() => adoptSelected.mutate()}>{adoptSelected.isPending ? "正在原子采用…" : "按横竖方向批量采用最新已批准版本"}</button>
        <button type="button" className="secondary" disabled={prepareDelivery.isPending || !configuration.data?.configuration?.delivery_targets.length} onClick={() => prepareDelivery.mutate()}>{prepareDelivery.isPending ? "正在检查交付…" : "按各集目标检查批量正式交付"}</button>
        <button type="button" className="primary-action" disabled={!deliveryBuildPlan || submitDelivery.isPending} onClick={() => submitDelivery.mutate()}>{submitDelivery.isPending ? "正在原子提交…" : deliveryBuildPlan ? `提交 ${deliveryBuildPlan.items.length} 集正式打包` : "先检查后提交"}</button>
      </div>
      {deliveryBuildPlan && <p className="status-note success">计划已冻结：{deliveryBuildPlan.items.length} 集均满足当前交付目标、批准和采用门禁。</p>}
      {deliveryBatches.data?.items.map((batch) => <DeliveryBuildCard key={batch.id} batch={batch} pending={retryDelivery.isPending} onRetry={(target) => retryDelivery.mutate(target)} />)}
    </section>}
  </main>;
}

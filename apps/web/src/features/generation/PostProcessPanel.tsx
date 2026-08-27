import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createPostProcessRecipe, getBackgroundOperation, getProjectConfiguration, listPostProcessRecipes, planEnhancementRun, publishPostProcessRecipe, submitEnhancementRun, type EnhancementPlan, type EnhancementRun, type PostProcessRecipe, type ReviewInboxItem } from "../../generated/api";
import { generateMachineCode } from "../shared/autoCode";
import { ProjectLocalResourceSelect } from "../shared/ProjectLocalResourceSelect";
import { MEDIA_STAGE_LABELS, REVIEW_DECISION_LABELS, STATUS_LABELS, optionLabel } from "../shared/optionLabels";
import { resolutionFromPlan } from "../shared/effectiveDefaults";
import { routes } from "../../app/routeRegistry";

const RESOLUTION_OPTIONS = [
  { value: "1080x1920", label: "竖屏 1080P（1080×1920）" },
  { value: "1920x1080", label: "横屏 1080P（1920×1080）" },
  { value: "720x1280", label: "竖屏 720P（720×1280）" },
  { value: "1280x720", label: "横屏 720P（1280×720）" },
  { value: "1080x1080", label: "方形 1080P（1080×1080）" },
] as const;

const CRF_OPTIONS = [
  { value: "16", label: "高质量（CRF 16）" },
  { value: "18", label: "推荐（CRF 18）" },
  { value: "20", label: "均衡（CRF 20）" },
  { value: "23", label: "较小文件（CRF 23）" },
  { value: "28", label: "预览（CRF 28）" },
] as const;

const ENCODING_SPEED_OPTIONS = [
  { value: "ultrafast", label: "最快处理（文件较大）" },
  { value: "veryfast", label: "快速处理" },
  { value: "medium", label: "均衡（推荐）" },
  { value: "slow", label: "精细压缩（耗时较长）" },
] as const;

function canonicalJson(value: unknown): string {
  const normalize = (entry: unknown): unknown => {
    if (Array.isArray(entry)) return entry.map(normalize);
    if (entry && typeof entry === "object") {
      return Object.fromEntries(Object.entries(entry as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, child]) => [key, normalize(child)]));
    }
    return entry;
  };
  return JSON.stringify(normalize(value));
}

export function PostProcessPanel({ videos }: { videos: ReviewInboxItem[] }) {
  const queryClient = useQueryClient();
  const recipes = useQuery({ queryKey: ["post-process-recipes"], queryFn: () => listPostProcessRecipes() });
  const [selectedRecipeId, setSelectedRecipeId] = useState("");
  const [inputMediaVersionId, setInputMediaVersionId] = useState("");
  const [code, setCode] = useState("local-video-enhance");
  const [title, setTitle] = useState("本地视频增强链");
  const [width, setWidth] = useState("1920");
  const [height, setHeight] = useState("1080");
  const [resolutionMode, setResolutionMode] = useState<"KEEP_SOURCE" | "PROJECT" | "CUSTOM">("KEEP_SOURCE");
  const [fit, setFit] = useState("CONTAIN");
  const [codec, setCodec] = useState("H264");
  const [crf, setCrf] = useState("18");
  const [preset, setPreset] = useState("veryfast");
  const [interpolate, setInterpolate] = useState(false);
  const [targetFps, setTargetFps] = useState("24");
  const [denoise, setDenoise] = useState(false);
  const [denoiseStrength, setDenoiseStrength] = useState("1");
  const [stabilize, setStabilize] = useState(false);
  const [lutPath, setLutPath] = useState("");
  const [prepared, setPrepared] = useState<EnhancementPlan | null>(null);
  const [completed, setCompleted] = useState<EnhancementRun | null>(null);
  const [queuedJobId, setQueuedJobId] = useState<string | null>(null);
  const recipeSelectionInitialized = useRef(false);
  const items = useMemo(() => recipes.data?.items ?? [], [recipes.data?.items]);
  const selected = items.find((item) => item.id === selectedRecipeId);
  const videoItems = useMemo(() => [...new Map(videos.map((item) => [item.media_version_id, item])).values()], [videos]);
  const videoIds = useMemo(() => videoItems.map((item) => item.media_version_id), [videoItems]);
  const selectedVideo = videoItems.find((item) => item.media_version_id === inputMediaVersionId);
  const projectId = selectedVideo?.project_id ?? videoItems[0]?.project_id;
  const projectConfiguration = useQuery({ queryKey: ["project-configuration", projectId, "post-process"], queryFn: () => getProjectConfiguration(String(projectId)), enabled: Boolean(projectId) });
  const projectResolution = resolutionFromPlan(projectConfiguration.data?.configuration.production_plan?.plan);
  const optionalSteps = useMemo<Array<Record<string, unknown>>>(() => {
    const steps: Array<Record<string, unknown>> = [];
    if (interpolate) steps.push({ kind: "FRAME_INTERPOLATION", target_fps: Number(targetFps), mode: "MCI", executor_ref: "builtin:ffmpeg" });
    if (denoise) steps.push({ kind: "DENOISE", strength: Number(denoiseStrength), executor_ref: "builtin:ffmpeg" });
    if (stabilize) steps.push({ kind: "STABILIZE", mode: "DESHAKE", executor_ref: "builtin:ffmpeg" });
    if (lutPath.trim()) steps.push({ kind: "LUT_3D", path_rel: lutPath.trim(), executor_ref: "builtin:ffmpeg" });
    return steps;
  }, [denoise, denoiseStrength, interpolate, lutPath, stabilize, targetFps]);
  const editorSteps = useMemo<Array<Record<string, unknown>>>(() => [
    resolutionMode === "KEEP_SOURCE"
      ? { kind: "SCALE", mode: "KEEP_SOURCE", fit: "CONTAIN", executor_ref: "builtin:ffmpeg" }
      : { kind: "SCALE", width: Number(width), height: Number(height), fit, executor_ref: "builtin:ffmpeg" },
    ...optionalSteps,
    { kind: "TECHNICAL_QC", executor_ref: "builtin:ffprobe" },
    { kind: "ENCODE", codec, preset, crf: Number(crf), executor_ref: "builtin:ffmpeg" },
  ], [codec, crf, fit, height, optionalSteps, preset, resolutionMode, width]);
  const recipeDirty = Boolean(selected) && (title.trim() !== selected?.title || canonicalJson(editorSteps) !== canonicalJson(selected?.steps ?? []));
  const resolutionValue = `${width}x${height}`;
  const resolutionOptions = RESOLUTION_OPTIONS.some((option) => option.value === resolutionValue)
    ? RESOLUTION_OPTIONS
    : [{ value: resolutionValue, label: `当前配方（${width}×${height}）` }, ...RESOLUTION_OPTIONS];

  const updateTitle = (value: string) => {
    setTitle(value);
    if (!selected) setCode(generateMachineCode("recipe", value).toLowerCase().replace(/_/g, "-"));
  };
  const updateResolution = (value: string) => {
    setResolutionMode("CUSTOM");
    const [nextWidth, nextHeight] = value.split("x");
    setWidth(nextWidth); setHeight(nextHeight);
  };
  const updateResolutionMode = (value: "KEEP_SOURCE" | "PROJECT" | "CUSTOM") => {
    setResolutionMode(value);
    if (value === "PROJECT") { setWidth(String(projectResolution.width)); setHeight(String(projectResolution.height)); setTargetFps(String(projectResolution.fps)); }
  };

  useEffect(() => {
    if (!items.length) return;
    if (!recipeSelectionInitialized.current) {
      recipeSelectionInitialized.current = true;
      setSelectedRecipeId(items.find((item) => item.status === "ACTIVE")?.id ?? items[0]?.id ?? "");
      setPrepared(null);
      setCompleted(null);
      setQueuedJobId(null);
      return;
    }
    if (selectedRecipeId && !items.some((item) => item.id === selectedRecipeId)) {
      setSelectedRecipeId(items.find((item) => item.status === "ACTIVE")?.id ?? items[0]?.id ?? "");
      setPrepared(null);
      setCompleted(null);
      setQueuedJobId(null);
    }
  }, [items, selectedRecipeId]);
  useEffect(() => {
    if (!selected) return;
    const scale = selected.steps.find((step) => step.kind === "SCALE");
    const encode = selected.steps.find((step) => step.kind === "ENCODE");
    const interpolation = selected.steps.find((step) => step.kind === "FRAME_INTERPOLATION");
    const denoiseStep = selected.steps.find((step) => step.kind === "DENOISE");
    const stabilization = selected.steps.find((step) => step.kind === "STABILIZE");
    const lut = selected.steps.find((step) => step.kind === "LUT_3D");
    setTitle(selected.title);
    setResolutionMode(scale?.mode === "KEEP_SOURCE" ? "KEEP_SOURCE" : "CUSTOM");
    setWidth(String(scale?.width ?? 1920)); setHeight(String(scale?.height ?? 1080));
    setFit(String(scale?.fit ?? "CONTAIN")); setCodec(String(encode?.codec ?? "H264"));
    setCrf(String(encode?.crf ?? 18)); setPreset(String(encode?.preset ?? "veryfast"));
    setInterpolate(Boolean(interpolation)); setTargetFps(String(interpolation?.target_fps ?? 24));
    setDenoise(Boolean(denoiseStep)); setDenoiseStrength(String(denoiseStep?.strength ?? 1));
    setStabilize(Boolean(stabilization)); setLutPath(String(lut?.path_rel ?? ""));
    setPrepared(null); setCompleted(null); setQueuedJobId(null);
  }, [selected?.id]);
  useEffect(() => {
    if (!videoIds.includes(inputMediaVersionId)) {
      setInputMediaVersionId(videoIds[0] ?? "");
      setPrepared(null);
      setCompleted(null);
      setQueuedJobId(null);
    }
  }, [inputMediaVersionId, videoIds]);

  const createMutation = useMutation({
    mutationFn: (targetRecipe?: PostProcessRecipe) => {
      const targetWidth = Number(width), targetHeight = Number(height), targetCrf = Number(crf);
      if (!Number.isInteger(targetWidth) || !Number.isInteger(targetHeight) || !Number.isInteger(targetCrf)) throw new Error("宽高和 CRF 必须是整数");
      if (interpolate) {
        const fps = Number(targetFps);
        if (!Number.isInteger(fps) || fps < 1 || fps > 120) throw new Error("补帧目标 FPS 必须是 1—120 的整数");
      }
      if (denoise) {
        const strength = Number(denoiseStrength);
        if (!Number.isFinite(strength) || strength < 0.1 || strength > 10) throw new Error("降噪强度必须在 0.1—10.0 之间");
      }
      return createPostProcessRecipe({
        code: targetRecipe ? targetRecipe.recipe_key : code.trim(),
        title: title.trim(),
        parent_recipe_id: targetRecipe?.id,
        steps: editorSteps,
        capability_contract: { transport: "LOCAL_PROCESS", network_allowed: false, input_media_kind: "VIDEO", output_media_kind: "VIDEO", optional_steps: optionalSteps.map((step) => step.kind) },
      });
    },
    onSuccess: async ({ recipe }) => { await queryClient.invalidateQueries({ queryKey: ["post-process-recipes"] }); setSelectedRecipeId(recipe.id); },
  });
  const publishMutation = useMutation({
    mutationFn: () => publishPostProcessRecipe(selectedRecipeId),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["post-process-recipes"] }); setPrepared(null); setQueuedJobId(null); },
  });
  const planMutation = useMutation({
    mutationFn: () => planEnhancementRun({ input_media_version_id: inputMediaVersionId, recipe_id: selectedRecipeId, parameters: { requested_from: "POST_PROCESS_PANEL" } }),
    onSuccess: ({ plan }) => { setPrepared(plan); setCompleted(null); setQueuedJobId(null); },
  });
  const runMutation = useMutation({
    mutationFn: () => {
      if (!prepared) throw new Error("必须先完成当前输入和 recipe 的只读预检");
      return submitEnhancementRun({ input_media_version_id: inputMediaVersionId, recipe_id: selectedRecipeId, parameters: { requested_from: "POST_PROCESS_PANEL" }, plan_hash: prepared.plan_hash });
    },
    onSuccess: ({ job }) => setQueuedJobId(job.id),
  });
  const operation = useQuery({
    queryKey: ["background-operation", queuedJobId],
    queryFn: () => getBackgroundOperation(String(queuedJobId)),
    enabled: Boolean(queuedJobId),
    refetchInterval: (query) => ["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(String(query.state.data?.job.state)) ? false : 1500,
  });
  useEffect(() => {
    if (operation.data?.job.state === "SUCCEEDED" && operation.data.result_type === "ENHANCEMENT" && operation.data.result) {
      setCompleted(operation.data.result as EnhancementRun);
    }
  }, [operation.data]);
  const error = recipes.error ?? createMutation.error ?? publishMutation.error ?? planMutation.error ?? runMutation.error ?? operation.error;

  return <section className="panel post-process-panel" aria-labelledby="post-process-title">
    <div className="panel-heading"><div><p className="eyebrow">本地后处理</p><h3 id="post-process-title">版本化视频增强与旁路比较</h3></div><span className="status-pill neutral">输入永不覆盖</span></div>
    <div className="post-process-grid">
      <div className="post-process-config">
        <div className="section-title"><span>处理配方版本</span><small>调整尺寸 → 技术质检 → 视频编码</small></div>
        <label htmlFor="post-recipe">已有版本<select id="post-recipe" value={selectedRecipeId} onChange={(event) => { setSelectedRecipeId(event.target.value); setPrepared(null); setCompleted(null); setQueuedJobId(null); }}><option value="">新建处理配方</option>{items.map((item) => <option key={item.id} value={item.id}>{item.recipe_key} · 第 {item.version_no} 版 · {optionLabel(STATUS_LABELS, item.status)}</option>)}</select></label>
        {!selected && <div className="field-fact"><span>配方技术标识</span><strong>{code}</strong><small>由标题自动生成</small></div>}
        <label htmlFor="post-title-input">标题<input id="post-title-input" value={title} onChange={(event) => updateTitle(event.target.value)} /></label>
        <div className="post-process-dimensions"><label>分辨率来源<select value={resolutionMode} onChange={(event) => updateResolutionMode(event.target.value as typeof resolutionMode)}><option value="KEEP_SOURCE">保持源视频分辨率</option><option value="PROJECT">继承当前项目（{projectResolution.width}×{projectResolution.height}）</option><option value="CUSTOM">自定义输出</option></select></label>{resolutionMode !== "KEEP_SOURCE" && <label>输出分辨率<select value={resolutionValue} onChange={(event) => updateResolution(event.target.value)}>{resolutionOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>}<label>画面适配<select value={fit} onChange={(event) => setFit(event.target.value)} disabled={resolutionMode === "KEEP_SOURCE"}><option value="CONTAIN">完整保留（可能留边）</option><option value="COVER">铺满裁切</option><option value="STRETCH">拉伸铺满</option></select></label><label>编码质量<select value={crf} onChange={(event) => setCrf(event.target.value)}>{CRF_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label></div>
        {resolutionMode === "CUSTOM" && <div className="post-process-dimensions"><label>自定义宽度<input type="number" min={64} max={8192} step={2} value={width} onChange={(event) => setWidth(event.target.value)} /></label><label>自定义高度<input type="number" min={64} max={8192} step={2} value={height} onChange={(event) => setHeight(event.target.value)} /></label></div>}
        <div className="post-process-dimensions"><label>视频编码<select value={codec} onChange={(event) => setCodec(event.target.value)}><option value="H264">H.264（当前本机执行器已验证）</option></select></label><label htmlFor="post-preset">编码速度<select id="post-preset" value={preset} onChange={(event) => setPreset(event.target.value)}>{ENCODING_SPEED_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label></div>
        <small className="muted">分辨率缺省保持源视频；选择“继承当前项目”时会读取项目当前生效的制作方案。编码选项只展示本机执行器已验证支持的能力。</small>
        <fieldset className="post-process-options"><legend>可选本地后处理</legend><label><input type="checkbox" checked={interpolate} onChange={(event) => setInterpolate(event.target.checked)} />补帧，让运动更流畅</label>{interpolate && <label>目标帧率<select value={targetFps} onChange={(event) => setTargetFps(event.target.value)}>{[24, 25, 30, 48, 50, 60].map((value) => <option key={value} value={value}>{value} 帧/秒</option>)}</select></label>}<label><input type="checkbox" checked={denoise} onChange={(event) => setDenoise(event.target.checked)} />降低画面噪点</label>{denoise && <label>降噪强度<input type="number" min="0.1" max="10" step="0.1" value={denoiseStrength} onChange={(event) => setDenoiseStrength(event.target.value)} /></label>}<label><input type="checkbox" checked={stabilize} onChange={(event) => setStabilize(event.target.checked)} />稳定抖动画面</label><ProjectLocalResourceSelect projectId={projectId} kind="LUT" value={lutPath} onChange={setLutPath} label="项目调色文件（可选）" /><small>每个可选步骤都会产生独立的中间版本并完成技术检查；失败不会覆盖输入素材。</small></fieldset>
        <div className="post-process-actions"><button type="button" className="secondary" onClick={() => createMutation.mutate(selected)} disabled={createMutation.isPending}>{createMutation.isPending ? "保存中…" : selected ? "派生新的草稿版本" : "创建第 1 版草稿"}</button><button type="button" className="secondary" disabled={!selected || selected.status !== "DRAFT" || publishMutation.isPending} onClick={() => publishMutation.mutate()}>{publishMutation.isPending ? "发布中…" : "发布所选版本"}</button></div>
        {selected && <div className="recipe-fingerprint"><strong>{optionLabel(STATUS_LABELS, selected.status)}</strong> · 旧版本不会修改 <details><summary>高级：查看内容校验指纹</summary><code>{selected.recipe_hash.slice(0, 16)}</code></details></div>}
      </div>
      <div className="post-process-runner">
        <div className="section-title"><span>运行计划</span><small>先预检，后显式执行</small></div>
        <label htmlFor="enhancement-input">输入视频<select id="enhancement-input" value={inputMediaVersionId} onChange={(event) => { setInputMediaVersionId(event.target.value); setPrepared(null); setCompleted(null); setQueuedJobId(null); }}><option value="">请选择</option>{videoItems.map((item) => <option key={item.media_version_id} value={item.media_version_id}>{item.episode_code ?? "未分集"} · {item.shot_code ?? "未绑定镜头"} · {optionLabel(MEDIA_STAGE_LABELS, item.stage)} · {optionLabel(REVIEW_DECISION_LABELS, item.decision, "未审核")}</option>)}</select></label>
        {selectedVideo && <details><summary>高级：输入版本技术标识</summary><code>{selectedVideo.media_version_id}</code></details>}
        {recipeDirty && <p id="post-process-dirty-guidance" className="review-guidance">当前编辑尚未形成不可变版本；请先派生 DRAFT 并发布，再执行预检。现有 ACTIVE 配方和已完成计划均不会被这些表单值静默改变。</p>}
        <div className="post-process-actions"><button type="button" className="secondary" aria-describedby={recipeDirty ? "post-process-dirty-guidance" : undefined} disabled={!inputMediaVersionId || selected?.status !== "ACTIVE" || planMutation.isPending || recipeDirty} onClick={() => planMutation.mutate()}>{planMutation.isPending ? "预检中…" : "只读预检增强计划"}</button><button type="button" className="primary-action" disabled={!prepared || runMutation.isPending || Boolean(queuedJobId)} onClick={() => runMutation.mutate()}>{runMutation.isPending ? "提交后台任务中…" : operation.data?.job.state === "SUCCEEDED" ? "增强已完成" : queuedJobId ? "增强任务已排队" : "提交后台增强任务"}</button></div>
        {prepared && !completed && <div className="frame-feedback success"><strong>预检通过，尚未运行。</strong><span>不会覆盖输入，也不会连接公网。</span><details><summary>高级：查看计划标识</summary><code>{String(prepared.plan_hash ?? "").slice(0, 16) || "—"}</code></details></div>}
        {queuedJobId && !completed && <p className="frame-feedback success" role="status"><strong>{["FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(String(operation.data?.job.state)) ? "后台任务需要处理。" : "增强任务已安全进入后台队列。"}</strong> 可以离开本页，并在<a href={routes.systemJobs(projectId)}>任务中心</a>查看进度、失败原因和重试入口。</p>}
        {completed && <div className="frame-feedback success"><strong>增强输出已登记：{optionLabel(STATUS_LABELS, completed.status)}</strong><span>自动质检：{completed.qc?.passed ? "通过" : "未通过"}</span><details><summary>高级：查看输入输出校验指纹</summary><code>{String(completed.input_sha256 ?? "").slice(0, 12) || "—"}</code> → <code>{String(completed.output_sha256 ?? "").slice(0, 12) || "—"}</code></details></div>}
      </div>
    </div>
    {completed?.output_media_version_id && <div className="bypass-compare" aria-label="增强前后旁路比较"><figure><figcaption>原始输入（保留）</figcaption><video controls preload="none" poster={`/api/v1/media-versions/${encodeURIComponent(completed.input_media_version_id)}/thumbnail?size=small&frame=poster`} src={`/api/v1/media-versions/${encodeURIComponent(completed.input_media_version_id)}/content`} /></figure><figure><figcaption>增强输出（新版本）</figcaption><video controls preload="none" poster={`/api/v1/media-versions/${encodeURIComponent(completed.output_media_version_id)}/thumbnail?size=small&frame=poster`} src={`/api/v1/media-versions/${encodeURIComponent(completed.output_media_version_id)}/content`} /></figure></div>}
    {!videos.length && <p className="empty-state">当前项目没有可增强的真实视频版本；页面不会使用示例媒体填充。</p>}
    {error && <p className="inline-error" role="alert">{error.message}</p>}
  </section>;
}

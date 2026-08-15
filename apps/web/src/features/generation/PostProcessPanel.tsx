import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createPostProcessRecipe, listPostProcessRecipes, planEnhancementRun, publishPostProcessRecipe, runEnhancement, type EnhancementPlan, type EnhancementRun, type PostProcessRecipe, type ReviewInboxItem } from "../../generated/api";

export function PostProcessPanel({ videos }: { videos: ReviewInboxItem[] }) {
  const queryClient = useQueryClient();
  const recipes = useQuery({ queryKey: ["post-process-recipes"], queryFn: () => listPostProcessRecipes() });
  const [selectedRecipeId, setSelectedRecipeId] = useState("");
  const [inputMediaVersionId, setInputMediaVersionId] = useState("");
  const [code, setCode] = useState("local-video-enhance");
  const [title, setTitle] = useState("本地视频增强链");
  const [width, setWidth] = useState("1920");
  const [height, setHeight] = useState("1080");
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
  const items = useMemo(() => recipes.data?.items ?? [], [recipes.data?.items]);
  const selected = items.find((item) => item.id === selectedRecipeId);
  // Pass the current recipe explicitly when deriving.  TanStack Query keeps a
  // mutation function stable across renders, so relying only on its closure
  // can otherwise race the initial async recipe selection and create a new
  // root recipe instead of an immutable child.
  const selectedForCreate = selected ?? items.find((item) => item.status === "ACTIVE") ?? items[0];
  const videoIds = useMemo(() => [...new Set(videos.map((item) => item.media_version_id))], [videos]);

  useEffect(() => {
    if (!items.some((item) => item.id === selectedRecipeId)) {
      setSelectedRecipeId(items.find((item) => item.status === "ACTIVE")?.id ?? items[0]?.id ?? "");
      setPrepared(null);
      setCompleted(null);
    }
  }, [items, selectedRecipeId]);
  useEffect(() => {
    if (!videoIds.includes(inputMediaVersionId)) {
      setInputMediaVersionId(videoIds[0] ?? "");
      setPrepared(null);
      setCompleted(null);
    }
  }, [inputMediaVersionId, videoIds]);

  const createMutation = useMutation({
    mutationFn: (targetRecipe?: PostProcessRecipe) => {
      const targetWidth = Number(width), targetHeight = Number(height), targetCrf = Number(crf);
      if (!Number.isInteger(targetWidth) || !Number.isInteger(targetHeight) || !Number.isInteger(targetCrf)) throw new Error("宽高和 CRF 必须是整数");
      const optionalSteps: Array<Record<string, unknown>> = [];
      if (interpolate) {
        const fps = Number(targetFps);
        if (!Number.isInteger(fps) || fps < 1 || fps > 120) throw new Error("补帧目标 FPS 必须是 1—120 的整数");
        optionalSteps.push({ kind: "FRAME_INTERPOLATION", target_fps: fps, mode: "MCI", executor_ref: "builtin:ffmpeg" });
      }
      if (denoise) {
        const strength = Number(denoiseStrength);
        if (!Number.isFinite(strength) || strength < 0.1 || strength > 10) throw new Error("降噪强度必须在 0.1—10.0 之间");
        optionalSteps.push({ kind: "DENOISE", strength, executor_ref: "builtin:ffmpeg" });
      }
      if (stabilize) optionalSteps.push({ kind: "STABILIZE", mode: "DESHAKE", executor_ref: "builtin:ffmpeg" });
      if (lutPath.trim()) optionalSteps.push({ kind: "LUT_3D", path_rel: lutPath.trim(), executor_ref: "builtin:ffmpeg" });
      return createPostProcessRecipe({
        code: targetRecipe ? targetRecipe.recipe_key : code.trim(),
        title: title.trim(),
        parent_recipe_id: targetRecipe?.id,
        steps: [
          { kind: "SCALE", width: targetWidth, height: targetHeight, fit: "CONTAIN", executor_ref: "builtin:ffmpeg" },
          ...optionalSteps,
          { kind: "TECHNICAL_QC", executor_ref: "builtin:ffprobe" },
          { kind: "ENCODE", codec: "H264", preset, crf: targetCrf, executor_ref: "builtin:ffmpeg" },
        ],
        capability_contract: { transport: "LOCAL_PROCESS", network_allowed: false, input_media_kind: "VIDEO", output_media_kind: "VIDEO", optional_steps: optionalSteps.map((step) => step.kind) },
      });
    },
    onSuccess: async ({ recipe }) => { await queryClient.invalidateQueries({ queryKey: ["post-process-recipes"] }); setSelectedRecipeId(recipe.id); },
  });
  const publishMutation = useMutation({
    mutationFn: () => publishPostProcessRecipe(selectedRecipeId),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["post-process-recipes"] }); setPrepared(null); },
  });
  const planMutation = useMutation({
    mutationFn: () => planEnhancementRun({ input_media_version_id: inputMediaVersionId, recipe_id: selectedRecipeId, parameters: { requested_from: "POST_PROCESS_PANEL" } }),
    onSuccess: ({ plan }) => { setPrepared(plan); setCompleted(null); },
  });
  const runMutation = useMutation({
    mutationFn: () => {
      if (!prepared) throw new Error("必须先完成当前输入和 recipe 的只读预检");
      return runEnhancement({ input_media_version_id: inputMediaVersionId, recipe_id: selectedRecipeId, parameters: { requested_from: "POST_PROCESS_PANEL" }, plan_hash: prepared.plan_hash });
    },
    onSuccess: ({ enhancement }) => setCompleted(enhancement),
  });
  const error = recipes.error ?? createMutation.error ?? publishMutation.error ?? planMutation.error ?? runMutation.error;

  return <section className="panel post-process-panel" aria-labelledby="post-process-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-PST-001/002 · LOCAL PROCESS</p><h3 id="post-process-title">版本化视频增强与旁路比较</h3></div><span className="status-pill neutral">输入永不覆盖</span></div>
    <div className="post-process-grid">
      <div className="post-process-config">
        <div className="section-title"><span>配方版本</span><small>SCALE → TECHNICAL_QC → ENCODE</small></div>
        <label htmlFor="post-recipe">已有版本<select id="post-recipe" value={selectedRecipeId} onChange={(event) => { setSelectedRecipeId(event.target.value); setPrepared(null); setCompleted(null); }}><option value="">新建逻辑配方</option>{items.map((item) => <option key={item.id} value={item.id}>{item.recipe_key} v{item.version_no} · {item.status}</option>)}</select></label>
        {!selected && <label htmlFor="post-code">逻辑 code<input id="post-code" value={code} onChange={(event) => setCode(event.target.value)} /></label>}
        <label htmlFor="post-title-input">标题<input id="post-title-input" value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <div className="post-process-dimensions"><label>宽<input type="number" min="64" max="8192" value={width} onChange={(event) => setWidth(event.target.value)} /></label><label>高<input type="number" min="64" max="8192" value={height} onChange={(event) => setHeight(event.target.value)} /></label><label>CRF<input type="number" min="0" max="51" value={crf} onChange={(event) => setCrf(event.target.value)} /></label></div>
        <label htmlFor="post-preset">H264 preset<select id="post-preset" value={preset} onChange={(event) => setPreset(event.target.value)}>{["ultrafast", "veryfast", "medium", "slow"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <fieldset className="post-process-options"><legend>可选本地后处理（能力驱动）</legend><label><input type="checkbox" checked={interpolate} onChange={(event) => setInterpolate(event.target.checked)} />补帧 FRAME_INTERPOLATION</label>{interpolate && <label>目标 FPS<input type="number" min="1" max="120" step="1" value={targetFps} onChange={(event) => setTargetFps(event.target.value)} /></label>}<label><input type="checkbox" checked={denoise} onChange={(event) => setDenoise(event.target.checked)} />降噪 DENOISE</label>{denoise && <label>降噪强度<input type="number" min="0.1" max="10" step="0.1" value={denoiseStrength} onChange={(event) => setDenoiseStrength(event.target.value)} /></label>}<label><input type="checkbox" checked={stabilize} onChange={(event) => setStabilize(event.target.checked)} />防抖 STABILIZE（deshake）</label><label>LUT 3D 项目内相对路径（可空）<input value={lutPath} onChange={(event) => setLutPath(event.target.value)} placeholder="例如 00_admin/color/look.cube" /></label><small>每个可选步骤都会进入只读 plan、独立 FFmpeg 中间版本和技术 QC；失败不会覆盖输入，也不会冒充原生模型 FPS/分辨率。</small></fieldset>
        <div className="post-process-actions"><button type="button" className="secondary" onClick={() => createMutation.mutate(selectedForCreate)} disabled={createMutation.isPending}>{createMutation.isPending ? "保存中…" : selected ? "派生 DRAFT 新版本" : "创建 DRAFT v1"}</button><button type="button" className="secondary" disabled={!selected || selected.status !== "DRAFT" || publishMutation.isPending} onClick={() => publishMutation.mutate()}>{publishMutation.isPending ? "发布中…" : "发布所选版本"}</button></div>
        {selected && <p className="recipe-fingerprint"><strong>{selected.status}</strong> · hash <code>{selected.recipe_hash.slice(0, 16)}</code> · 旧版本不会修改</p>}
      </div>
      <div className="post-process-runner">
        <div className="section-title"><span>运行计划</span><small>先预检，后显式执行</small></div>
        <label htmlFor="enhancement-input">输入视频<select id="enhancement-input" value={inputMediaVersionId} onChange={(event) => { setInputMediaVersionId(event.target.value); setPrepared(null); setCompleted(null); }}><option value="">请选择</option>{videoIds.map((id) => <option key={id} value={id}>VIDEO · {id.slice(0, 12)}</option>)}</select></label>
        <div className="post-process-actions"><button type="button" className="secondary" disabled={!inputMediaVersionId || selected?.status !== "ACTIVE" || planMutation.isPending} onClick={() => planMutation.mutate()}>{planMutation.isPending ? "预检中…" : "只读预检增强计划"}</button><button type="button" className="primary-action" disabled={!prepared || runMutation.isPending || Boolean(completed)} onClick={() => runMutation.mutate()}>{runMutation.isPending ? "本机增强中…" : "确认运行并注册新版本"}</button></div>
        {prepared && !completed && <p className="frame-feedback success"><strong>READY，尚未运行。</strong> Plan <code>{prepared.plan_hash.slice(0, 16)}</code> · 不覆盖输入 · 零网络</p>}
        {completed && <p className="frame-feedback success"><strong>增强输出已注册：{completed.status}</strong> 输入 <code>{completed.input_sha256.slice(0, 12)}</code> → 输出 <code>{completed.output_sha256?.slice(0, 12)}</code> · QC {String(completed.qc.passed)}</p>}
      </div>
    </div>
    {completed?.output_media_version_id && <div className="bypass-compare" aria-label="增强前后旁路比较"><figure><figcaption>原始输入（保留）</figcaption><video controls preload="metadata" src={`/api/v1/media-versions/${encodeURIComponent(completed.input_media_version_id)}/content`} /></figure><figure><figcaption>增强输出（新版本）</figcaption><video controls preload="metadata" src={`/api/v1/media-versions/${encodeURIComponent(completed.output_media_version_id)}/content`} /></figure></div>}
    {!videos.length && <p className="empty-state">当前项目没有可增强的真实 VIDEO MediaVersion；不会使用示例媒体。</p>}
    {error && <p className="inline-error" role="alert">{error.message}</p>}
  </section>;
}

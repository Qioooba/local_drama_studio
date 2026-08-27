import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bindDirectorRecipe, createDirectorRecipe, createDirectorRecipeVersion, DirectorRecipeApiError, getDirectorRecipeBinding, listDirectorRecipes, listRecipeQcPolicies } from "./api";
import type { DirectorRecipeDocument } from "./types";
import "./recipes.css";
import { CheckboxChipGroup } from "../../components/ui/CheckboxChipGroup";
import { MEDIA_STAGE_LABELS, OWNER_SCOPE_LABELS, optionLabel } from "../shared/optionLabels";
import { generateMachineCode } from "../shared/autoCode";
import { getProjectConfiguration, listProfiles } from "../../generated/api";
import { findConfigValue, numberFrom, stringFrom } from "../shared/effectiveDefaults";
import { canonicalCapabilityLabel } from "../preferences-v2/canonicalCapabilities";

const DEFAULT_RECIPE: DirectorRecipeDocument = { aspect_ratio: "9:16", shot_planning: { avg_duration_ms: 3500, dialogue_coverage: "MEDIUM_CLOSEUP_BIASED" }, asset_policy: { character_required_refs: ["HERO", "FRONT", "LEFT", "RIGHT"] }, generation: { image: { capability: "IMAGE_CHARACTER" }, video: { capability: "VIDEO_FIRST_LAST_FRAME" } }, qc_policy_ref: { policy_version_id: "" } };
const FORBIDDEN = new Set(["shell", "python", "command", "commands", "executor", "exec", "code", "script", "subprocess", "powershell", "bash", "cmd", "runtime_code", "entrypoint"]);
const CHARACTER_REFERENCE_OPTIONS = ["HERO", "FRONT", "LEFT", "RIGHT", "BACK", "TOP", "BOTTOM"].map((value) => ({ value, label: value === "HERO" ? "主视觉" : value === "FRONT" ? "正面" : value === "BACK" ? "背面" : value === "TOP" ? "顶部" : value === "BOTTOM" ? "底部" : value === "LEFT" ? "左侧" : "右侧" }));

export function findForbiddenRecipePath(value: unknown, path = "recipe"): string | null {
  if (Array.isArray(value)) { for (let index = 0; index < value.length; index += 1) { const found = findForbiddenRecipePath(value[index], `${path}[${index}]`); if (found) return found; } }
  else if (value && typeof value === "object") { for (const [key, item] of Object.entries(value)) { if (FORBIDDEN.has(key.trim().toLowerCase())) return `${path}.${key}`; const found = findForbiddenRecipePath(item, `${path}.${key}`); if (found) return found; } }
  return null;
}

export function validateRecipeDuration(value: number): string | null {
  if (!Number.isFinite(value) || value < 250 || value > 120_000) return "平均时长必须在 250–120000 毫秒之间。";
  if (value % 250 !== 0) return "平均时长必须以 250 毫秒为步长，例如 3000、3250 或 3500。";
  return null;
}

const clone = (value: DirectorRecipeDocument) => JSON.parse(JSON.stringify(value)) as DirectorRecipeDocument;
const shortHash = (hash: string) => `${hash.slice(0, 12)}…${hash.slice(-8)}`;
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

export function DirectorRecipeManager({ projectId }: { projectId: string }) {
  const cache = useQueryClient();
  const recipes = useQuery({ queryKey: ["director-recipes", projectId], queryFn: () => listDirectorRecipes(projectId), enabled: Boolean(projectId) });
  const binding = useQuery({ queryKey: ["director-recipe-binding", projectId], queryFn: () => getDirectorRecipeBinding(projectId), enabled: Boolean(projectId) });
  const qcPolicies = useQuery({ queryKey: ["director-recipe-qc", projectId], queryFn: () => listRecipeQcPolicies(projectId), enabled: Boolean(projectId) });
  const projectConfiguration = useQuery({ queryKey: ["project-configuration", projectId, "director-recipe"], queryFn: () => getProjectConfiguration(projectId), enabled: Boolean(projectId) });
  const profiles = useQuery({ queryKey: ["profiles", "director-recipe"], queryFn: () => listProfiles() });
  const imageCapabilities = useMemo(() => [...new Set((profiles.data?.items ?? []).filter((item) => item.status === "PUBLISHED" && item.capability.startsWith("IMAGE_")).map((item) => item.capability))], [profiles.data?.items]);
  const videoCapabilities = useMemo(() => [...new Set((profiles.data?.items ?? []).filter((item) => item.status === "PUBLISHED" && item.capability.startsWith("VIDEO_")).map((item) => item.capability))], [profiles.data?.items]);
  const effectiveDefault = useMemo<DirectorRecipeDocument>(() => {
    const plan = projectConfiguration.data?.configuration.production_plan?.plan;
    const configuredRefs = findConfigValue(plan, ["character_required_refs", "required_reference_kinds"]);
    return {
      aspect_ratio: stringFrom(plan, ["aspect_ratio", "aspect"], DEFAULT_RECIPE.aspect_ratio),
      shot_planning: { avg_duration_ms: numberFrom(plan, ["avg_duration_ms", "average_shot_duration_ms"], DEFAULT_RECIPE.shot_planning.avg_duration_ms), dialogue_coverage: stringFrom(plan, ["dialogue_coverage"], DEFAULT_RECIPE.shot_planning.dialogue_coverage) },
      asset_policy: { character_required_refs: Array.isArray(configuredRefs) && configuredRefs.length ? configuredRefs.map(String) : DEFAULT_RECIPE.asset_policy.character_required_refs },
      generation: { image: { capability: imageCapabilities[0] ?? DEFAULT_RECIPE.generation.image.capability }, video: { capability: videoCapabilities[0] ?? DEFAULT_RECIPE.generation.video.capability } },
      qc_policy_ref: { policy_version_id: qcPolicies.data?.find((item) => item.stage === "FORMAL" && item.owner_type === "VIDEO")?.policy_version_id ?? qcPolicies.data?.[0]?.policy_version_id ?? "" },
    };
  }, [imageCapabilities, projectConfiguration.data?.configuration.production_plan?.plan, qcPolicies.data, videoCapabilities]);
  const [recipeId, setRecipeId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [draft, setDraft] = useState(clone(DEFAULT_RECIPE));
  const [mode, setMode] = useState<"VERSION" | "COPY" | "NEW">("NEW");
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [reason, setReason] = useState("");
  const [bindingReason, setBindingReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [isCreatingNew, setIsCreatingNew] = useState(false);
  const editorRef = useRef<HTMLFormElement>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (!isCreatingNew && !recipeId && recipes.data?.[0]) setRecipeId(recipes.data[0].id); }, [isCreatingNew, recipeId, recipes.data]);
  const selectedRecipe = recipes.data?.find((item) => item.id === recipeId);
  useEffect(() => { if (selectedRecipe && !selectedRecipe.versions.some((item) => item.id === versionId)) setVersionId(selectedRecipe.versions.at(-1)?.id ?? ""); }, [selectedRecipe, versionId]);
  const selectedVersion = selectedRecipe?.versions.find((item) => item.id === versionId);
  useEffect(() => { if (!isCreatingNew && selectedVersion) { setDraft(clone(selectedVersion.recipe)); setCode(`${selectedRecipe?.code ?? "recipe"}-copy`); setTitle(`${selectedRecipe?.title ?? "配方"}（副本）`); setMode("VERSION"); setReason(""); setNotice(null); } }, [isCreatingNew, selectedVersion?.id]);
  useEffect(() => {
    if (!isCreatingNew) return;
    editorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    window.requestAnimationFrame(() => titleInputRef.current?.focus());
  }, [isCreatingNew]);
  useEffect(() => { if (isCreatingNew) setDraft(clone(effectiveDefault)); }, [effectiveDefault, isCreatingNew]);
  useEffect(() => { if (!draft.qc_policy_ref.policy_version_id && qcPolicies.data?.[0]) setDraft((old) => ({ ...old, qc_policy_ref: { policy_version_id: qcPolicies.data![0].policy_version_id } })); }, [draft.qc_policy_ref.policy_version_id, qcPolicies.data]);
  const selectedHash = selectedVersion?.recipe_hash ?? "";
  const currentVersionId = binding.data?.recipe_version_id;
  const versionOptions = useMemo(() => (recipes.data ?? []).flatMap((recipe) => recipe.versions.map((version) => ({ recipe, version }))), [recipes.data]);
  const patch = <K extends keyof DirectorRecipeDocument>(key: K, value: DirectorRecipeDocument[K]) => setDraft((old) => ({ ...old, [key]: value }));
  const durationError = validateRecipeDuration(draft.shot_planning.avg_duration_ms);

  const publish = useMutation({ mutationFn: async () => {
    const forbidden = findForbiddenRecipePath(draft); if (forbidden) throw new Error(`检测到禁止的执行字段：${forbidden}`);
    if (durationError) throw new Error(durationError);
    if (!draft.qc_policy_ref.policy_version_id) throw new Error("请选择本项目的自动质检规则版本");
    if (mode === "VERSION") { if (!recipeId) throw new Error("请选择要创建新版本的导演模板"); return createDirectorRecipeVersion(projectId, recipeId, draft, reason); }
    if (!code.trim() || !title.trim()) throw new Error("新导演模板必须填写标题");
    return createDirectorRecipe(projectId, { code: code.trim(), title: title.trim(), recipe: draft, reason });
  }, onMutate: () => setNotice(null), onSuccess: async (result) => { setNotice("version_no" in result ? `不可变的第 ${result.version_no} 版已创建` : `导演模板 ${result.code} 的第 1 版已创建`); await cache.invalidateQueries({ queryKey: ["director-recipes", projectId] }); }, onError: (error) => setNotice(`发布失败：${errorText(error)}`) });

  const bind = useMutation({ mutationFn: () => { if (!versionId) throw new Error("请选择明确的导演模板版本"); return bindDirectorRecipe(projectId, versionId, bindingReason, binding.data?.revision ?? null); }, onMutate: () => setNotice(null), onSuccess: async (result) => { setNotice(`项目已明确升级到 ${result.code} 的第 ${result.version_no} 版，绑定记录 ${result.revision}`); await cache.invalidateQueries({ queryKey: ["director-recipe-binding", projectId] }); }, onError: (error) => setNotice(error instanceof DirectorRecipeApiError && error.status === 409 ? `绑定版本冲突：${error.message}。请刷新后重试。` : `绑定失败：${errorText(error)}`) });

  const initialPending = recipes.isPending || binding.isPending || qcPolicies.isPending;
  const initialErrors = [recipes.error, binding.error, qcPolicies.error].filter(Boolean);
  if (initialPending) return <section className="panel" role="status" aria-live="polite"><p className="empty-state">正在读取导演模板、项目绑定与质检规则版本…</p></section>;
  if (initialErrors.length > 0) return <section className="panel" role="alert"><div className="panel-heading"><div><p className="eyebrow">读取失败</p><h3>导演模板暂时无法打开</h3></div></div><p className="muted">没有修改项目绑定或不可变版本。{initialErrors.map(errorText).join("；")}</p><button type="button" className="secondary" onClick={() => void Promise.all([recipes.refetch(), binding.refetch(), qcPolicies.refetch()])}>重新读取</button></section>;

  return <div className="recipe-manager">
    <section className="panel recipe-provenance" aria-labelledby="recipe-provenance-title"><div className="panel-heading"><div><p className="eyebrow">当前项目绑定</p><h3 id="recipe-provenance-title">导演模板来源</h3></div><span className={`status-pill ${binding.data ? "state-active" : "state-warning"}`}>{binding.data ? "已明确绑定" : "尚未绑定"}</span></div>
      {binding.data ? <div className="recipe-binding-card"><div><strong>{binding.data.title}</strong><span>{binding.data.code} · 第 {binding.data.version_no} 版 · 绑定记录 {binding.data.revision}</span></div><details><summary>高级：查看校验信息</summary><code title={binding.data.recipe_hash}>{shortHash(binding.data.recipe_hash)}</code><small>模板版本标识 {binding.data.recipe_version_id} · 冻结：{binding.data.is_frozen ? "是" : "否"}</small></details></div> : <p className="empty-state">生成前请明确绑定一个不可变版本；系统不会悄悄跟随“最新版”。</p>}
      <div className="recipe-bind-controls"><label>目标版本<select value={versionId} onChange={(e) => { const next = versionOptions.find((item) => item.version.id === e.target.value); setIsCreatingNew(false); setRecipeId(next?.recipe.id ?? ""); setVersionId(e.target.value); }}><option value="">选择版本</option>{versionOptions.map(({ recipe, version }) => <option key={version.id} value={version.id}>{recipe.title || recipe.code} · 第 {version.version_no} 版</option>)}</select></label><label>升级原因<input value={bindingReason} onChange={(e) => setBindingReason(e.target.value)} placeholder="为什么绑定或升级？" /></label><button type="button" className="secondary" disabled={!versionId || versionId === currentVersionId || bind.isPending} title={bind.isPending ? "正在写入显式项目绑定" : !versionId ? "请先选择不可变配方版本" : versionId === currentVersionId ? "项目已经绑定此版本" : undefined} onClick={() => bind.mutate()}>{bind.isPending ? "绑定中…" : currentVersionId ? "显式升级项目" : "绑定到项目"}</button></div>
      {!versionId && <small className="muted">提示：请先在选择版本下拉框中指定目标版本，然后再绑定到当前项目。</small>}
    </section>
    <div className="recipe-workspace">
      <aside className="panel recipe-library" aria-label="导演模板版本库"><div className="panel-heading"><div><p className="eyebrow">版本库</p><h3>不可变历史</h3></div><button type="button" className="secondary" aria-controls="recipe-editor" onClick={() => { setIsCreatingNew(true); setRecipeId(""); setVersionId(""); setDraft(clone(effectiveDefault)); setCode(""); setTitle(""); setReason(""); setNotice(null); setMode("NEW"); }}>新建</button></div>{recipes.isPending && <p className="empty-state">正在读取导演模板…</p>}{recipes.data?.map((recipe) => <article key={recipe.id} className={recipe.id === recipeId ? "selected" : ""}><button type="button" onClick={() => { setIsCreatingNew(false); setRecipeId(recipe.id); }}><strong>{recipe.title}</strong><span>{recipe.code} · {recipe.versions.length} 个版本</span></button>{recipe.id === recipeId && <ol>{recipe.versions.map((version) => <li key={version.id}><button type="button" className={version.id === versionId ? "selected" : ""} onClick={() => { setIsCreatingNew(false); setVersionId(version.id); }}><span>第 {version.version_no} 版</span><code>{version.recipe_hash.slice(0, 10)}</code><small>{version.reason || "未填写变更原因"}</small></button></li>)}</ol>}</article>)}{recipes.data?.length === 0 && <p className="empty-state">还没有导演模板。</p>}</aside>
      <form ref={editorRef} id="recipe-editor" className="panel recipe-editor" onSubmit={(e) => { e.preventDefault(); publish.mutate(); }}><div className="panel-heading"><div><p className="eyebrow">模板编辑器</p><h3>{mode === "VERSION" ? `从第 ${selectedVersion?.version_no ?? "?"} 版创建新版本` : mode === "COPY" ? "复制为新导演模板" : "创建导演模板"}</h3></div>{selectedHash && <details><summary>高级：查看校验指纹</summary><code title={selectedHash}>{shortHash(selectedHash)}</code></details>}</div>
        {selectedVersion && <div className="recipe-mode-tabs" role="group" aria-label="发布方式"><button type="button" className={mode === "VERSION" ? "selected" : ""} onClick={() => setMode("VERSION")}>同一模板的新版本</button><button type="button" className={mode === "COPY" ? "selected" : ""} onClick={() => setMode("COPY")}>复制为新模板</button></div>}
        {mode !== "VERSION" && <div className="recipe-fields-2"><label>标题<input ref={titleInputRef} value={title} onChange={(e) => { const nextTitle = e.target.value; setTitle(nextTitle); setCode(generateMachineCode("recipe", nextTitle).toLowerCase()); }} placeholder="竖屏都市漫剧" /></label><div className="field-fact"><span>配方技术标识</span><strong>{code || "填写标题后自动生成"}</strong><small>系统自动生成，不需要遵守代码命名规则。</small></div></div>}
        <fieldset><legend>画幅与镜头规划</legend><div className="recipe-fields-3"><label>画幅<select value={draft.aspect_ratio} onChange={(e) => patch("aspect_ratio", e.target.value)}><option value="9:16">竖屏（9:16）</option><option value="16:9">横屏（16:9）</option><option value="1:1">方形（1:1）</option><option value="4:3">传统横屏（4:3）</option></select></label><label>平均时长（毫秒）<input type="number" min="250" max="120000" step="50" value={draft.shot_planning.avg_duration_ms} aria-invalid={Boolean(durationError)} aria-describedby={durationError ? "recipe-duration-error" : undefined} onChange={(e) => patch("shot_planning", { ...draft.shot_planning, avg_duration_ms: Number(e.target.value) })} />{durationError && <small id="recipe-duration-error" className="blocker-text" role="alert">{durationError}</small>}</label><label>对白镜头倾向<select value={draft.shot_planning.dialogue_coverage} onChange={(e) => patch("shot_planning", { ...draft.shot_planning, dialogue_coverage: e.target.value })}><option value="MEDIUM_CLOSEUP_BIASED">优先中近景，突出人物交流</option><option value="BALANCED">景别均衡</option><option value="WIDE_CONTEXT">优先大景别，交代环境</option><option value="CLOSEUP_INTIMATE">优先特写，强化亲密感</option></select></label></div></fieldset>
        <fieldset><legend>资产策略</legend><CheckboxChipGroup legend="角色必需参考类型" options={CHARACTER_REFERENCE_OPTIONS} value={draft.asset_policy.character_required_refs} onChange={(character_required_refs) => patch("asset_policy", { character_required_refs })} max={7} required /></fieldset>
        <fieldset><legend>生成能力</legend><div className="recipe-fields-2"><label>图像能力<select value={draft.generation.image.capability} onChange={(e) => patch("generation", { ...draft.generation, image: { capability: e.target.value } })}>{imageCapabilities.length === 0 && <option value={draft.generation.image.capability}>{canonicalCapabilityLabel(draft.generation.image.capability)}（安全默认）</option>}{imageCapabilities.map((capability) => <option value={capability} key={capability}>{canonicalCapabilityLabel(capability)}</option>)}</select></label><label>视频能力<select value={draft.generation.video.capability} onChange={(e) => patch("generation", { ...draft.generation, video: { capability: e.target.value } })}>{videoCapabilities.length === 0 && <option value={draft.generation.video.capability}>{canonicalCapabilityLabel(draft.generation.video.capability)}（安全默认）</option>}{videoCapabilities.map((capability) => <option value={capability} key={capability}>{canonicalCapabilityLabel(capability)}</option>)}</select></label></div><small className="muted">下拉项来自当前已发布的生成配置；新模板的画幅、镜头时长与参考图要求优先继承项目制作方案。</small></fieldset>
        <fieldset><legend>质量检查规则</legend><label>固定使用的规则版本<select value={draft.qc_policy_ref.policy_version_id} onChange={(e) => patch("qc_policy_ref", { policy_version_id: e.target.value })}><option value="">选择本项目的质量检查规则</option>{qcPolicies.data?.map((item) => <option key={item.policy_version_id} value={item.policy_version_id}>{optionLabel(MEDIA_STAGE_LABELS, item.stage)} · {optionLabel(OWNER_SCOPE_LABELS, item.owner_type)} · 第 {item.version_no} 版</option>)}</select></label></fieldset>
        <label>发布原因<textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="记录本版本改变了什么；旧版本不会被覆盖。" /></label>
        <aside className="recipe-security"><strong>安全边界：导演模板只声明制作策略</strong><p>模板不能包含系统命令或可执行代码，不能读取任意文件、启动进程，也不能绕过生成配置和自动质检检查。</p></aside>
        {notice && <p className={notice.startsWith("不可变的") || notice.startsWith("导演模板 ") || notice.startsWith("项目已") ? "recipe-notice success" : "recipe-notice error"} role="status">{notice}</p>}<div className="recipe-actions"><button type="submit" className="primary-action" disabled={publish.isPending || Boolean(durationError)} title={durationError ?? (publish.isPending ? "正在创建不可变版本" : undefined)}>{publish.isPending ? "创建中…" : mode === "VERSION" ? `创建第 ${(selectedVersion?.version_no ?? 0) + 1} 版` : "创建导演模板第 1 版"}</button></div>
      </form>
    </div>
  </div>;
}

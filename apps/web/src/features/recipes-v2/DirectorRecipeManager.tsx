import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bindDirectorRecipe, createDirectorRecipe, createDirectorRecipeVersion, DirectorRecipeApiError, getDirectorRecipeBinding, listDirectorRecipes, listRecipeQcPolicies } from "./api";
import type { DirectorRecipeDocument, DirectorRecipeVersion } from "./types";
import "./recipes.css";

const DEFAULT_RECIPE: DirectorRecipeDocument = { aspect_ratio: "9:16", shot_planning: { avg_duration_ms: 3500, dialogue_coverage: "MEDIUM_CLOSEUP_BIASED" }, asset_policy: { character_required_refs: ["HERO", "FRONT", "LEFT", "RIGHT"] }, generation: { image: { capability: "IMAGE_CHARACTER" }, video: { capability: "VIDEO_FIRST_LAST_FRAME" } }, qc_policy_ref: { policy_version_id: "" } };
const FORBIDDEN = new Set(["shell", "python", "command", "commands", "executor", "exec", "code", "script", "subprocess", "powershell", "bash", "cmd", "runtime_code", "entrypoint"]);

export function findForbiddenRecipePath(value: unknown, path = "recipe"): string | null {
  if (Array.isArray(value)) { for (let index = 0; index < value.length; index += 1) { const found = findForbiddenRecipePath(value[index], `${path}[${index}]`); if (found) return found; } }
  else if (value && typeof value === "object") { for (const [key, item] of Object.entries(value)) { if (FORBIDDEN.has(key.trim().toLowerCase())) return `${path}.${key}`; const found = findForbiddenRecipePath(item, `${path}.${key}`); if (found) return found; } }
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
  const [recipeId, setRecipeId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [draft, setDraft] = useState(clone(DEFAULT_RECIPE));
  const [mode, setMode] = useState<"VERSION" | "COPY" | "NEW">("NEW");
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [reason, setReason] = useState("");
  const [bindingReason, setBindingReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => { if (!recipeId && recipes.data?.[0]) setRecipeId(recipes.data[0].id); }, [recipeId, recipes.data]);
  const selectedRecipe = recipes.data?.find((item) => item.id === recipeId);
  useEffect(() => { if (selectedRecipe && !selectedRecipe.versions.some((item) => item.id === versionId)) setVersionId(selectedRecipe.versions.at(-1)?.id ?? ""); }, [selectedRecipe, versionId]);
  const selectedVersion = selectedRecipe?.versions.find((item) => item.id === versionId);
  useEffect(() => { if (selectedVersion) { setDraft(clone(selectedVersion.recipe)); setCode(`${selectedRecipe?.code ?? "recipe"}-copy`); setTitle(`${selectedRecipe?.title ?? "配方"}（副本）`); setMode("VERSION"); setReason(""); setNotice(null); } }, [selectedVersion?.id]);
  useEffect(() => { if (!draft.qc_policy_ref.policy_version_id && qcPolicies.data?.[0]) setDraft((old) => ({ ...old, qc_policy_ref: { policy_version_id: qcPolicies.data![0].policy_version_id } })); }, [draft.qc_policy_ref.policy_version_id, qcPolicies.data]);
  const selectedHash = selectedVersion?.recipe_hash ?? "";
  const currentVersionId = binding.data?.recipe_version_id;
  const versionOptions = useMemo(() => (recipes.data ?? []).flatMap((recipe) => recipe.versions.map((version) => ({ recipe, version }))), [recipes.data]);
  const patch = <K extends keyof DirectorRecipeDocument>(key: K, value: DirectorRecipeDocument[K]) => setDraft((old) => ({ ...old, [key]: value }));

  const publish = useMutation({ mutationFn: async () => {
    const forbidden = findForbiddenRecipePath(draft); if (forbidden) throw new Error(`检测到禁止的执行字段：${forbidden}`);
    if (!draft.qc_policy_ref.policy_version_id) throw new Error("请选择本项目 QC Policy version");
    if (mode === "VERSION") { if (!recipeId) throw new Error("请选择要创建新版本的 Recipe"); return createDirectorRecipeVersion(projectId, recipeId, draft, reason); }
    if (!code.trim() || !title.trim()) throw new Error("新 Recipe 必须填写 code 与标题");
    return createDirectorRecipe(projectId, { code: code.trim(), title: title.trim(), recipe: draft, reason });
  }, onMutate: () => setNotice(null), onSuccess: async (result) => { setNotice("version_no" in result ? `不可变版本 v${result.version_no} 已创建` : `Recipe ${result.code} 与 v1 已创建`); await cache.invalidateQueries({ queryKey: ["director-recipes", projectId] }); }, onError: (error) => setNotice(`发布失败：${errorText(error)}`) });

  const bind = useMutation({ mutationFn: () => { if (!versionId) throw new Error("请选择明确的 Recipe version"); return bindDirectorRecipe(projectId, versionId, bindingReason, binding.data?.revision ?? null); }, onMutate: () => setNotice(null), onSuccess: async (result) => { setNotice(`项目已显式升级到 ${result.code} v${result.version_no}，binding revision ${result.revision}`); await cache.invalidateQueries({ queryKey: ["director-recipe-binding", projectId] }); }, onError: (error) => setNotice(error instanceof DirectorRecipeApiError && error.status === 409 ? `绑定版本冲突：${error.message}。请刷新后重试。` : `绑定失败：${errorText(error)}`) });

  const initialPending = recipes.isPending || binding.isPending || qcPolicies.isPending;
  const initialErrors = [recipes.error, binding.error, qcPolicies.error].filter(Boolean);
  if (initialPending) return <section className="panel" role="status" aria-live="polite"><p className="empty-state">正在读取 Director Recipe、项目绑定与 QC 版本…</p></section>;
  if (initialErrors.length > 0) return <section className="panel" role="alert"><div className="panel-heading"><div><p className="eyebrow">读取失败</p><h3>导演配方暂时无法打开</h3></div></div><p className="muted">没有修改项目绑定或不可变版本。{initialErrors.map(errorText).join("；")}</p><button type="button" className="secondary" onClick={() => void Promise.all([recipes.refetch(), binding.refetch(), qcPolicies.refetch()])}>重新读取</button></section>;

  return <div className="recipe-manager">
    <section className="panel recipe-provenance" aria-labelledby="recipe-provenance-title"><div className="panel-heading"><div><p className="eyebrow">当前项目绑定</p><h3 id="recipe-provenance-title">Recipe provenance</h3></div><span className={`status-pill ${binding.data ? "state-active" : "state-warning"}`}>{binding.data ? "已显式绑定" : "尚未绑定"}</span></div>
      {binding.data ? <div className="recipe-binding-card"><div><strong>{binding.data.title}</strong><span>{binding.data.code} · v{binding.data.version_no} · binding revision {binding.data.revision}</span></div><code title={binding.data.recipe_hash}>{shortHash(binding.data.recipe_hash)}</code><small>version {binding.data.recipe_version_id} · 冻结：{binding.data.is_frozen ? "是" : "否"}</small></div> : <p className="empty-state">生成前请显式绑定一个不可变版本；系统不会静默跟随“最新版”。</p>}
      <div className="recipe-bind-controls"><label>目标版本<select value={versionId} onChange={(e) => { const next = versionOptions.find((item) => item.version.id === e.target.value); setRecipeId(next?.recipe.id ?? ""); setVersionId(e.target.value); }}><option value="">选择版本</option>{versionOptions.map(({ recipe, version }) => <option key={version.id} value={version.id}>{recipe.code} · v{version.version_no} · {version.recipe_hash.slice(0, 10)}</option>)}</select></label><label>升级原因<input value={bindingReason} onChange={(e) => setBindingReason(e.target.value)} placeholder="为什么绑定或升级？" /></label><button type="button" className="primary-action" disabled={!versionId || versionId === currentVersionId || bind.isPending} title={bind.isPending ? "正在写入显式项目绑定" : !versionId ? "请先选择不可变 Recipe 版本" : versionId === currentVersionId ? "项目已经绑定此版本" : undefined} onClick={() => bind.mutate()}>{bind.isPending ? "绑定中…" : currentVersionId ? "显式升级项目" : "绑定到项目"}</button></div>
    </section>
    <div className="recipe-workspace">
      <aside className="panel recipe-library" aria-label="Recipe 版本库"><div className="panel-heading"><div><p className="eyebrow">版本库</p><h3>不可变历史</h3></div><button type="button" className="secondary" onClick={() => { setRecipeId(""); setVersionId(""); setDraft(clone(DEFAULT_RECIPE)); setCode(""); setTitle(""); setMode("NEW"); }}>新建</button></div>{recipes.isPending && <p className="empty-state">读取 Recipe…</p>}{recipes.data?.map((recipe) => <article key={recipe.id} className={recipe.id === recipeId ? "selected" : ""}><button type="button" onClick={() => setRecipeId(recipe.id)}><strong>{recipe.title}</strong><span>{recipe.code} · {recipe.versions.length} 个版本</span></button>{recipe.id === recipeId && <ol>{recipe.versions.map((version) => <li key={version.id}><button type="button" className={version.id === versionId ? "selected" : ""} onClick={() => setVersionId(version.id)}><span>v{version.version_no}</span><code>{version.recipe_hash.slice(0, 10)}</code><small>{version.reason || "未填写变更原因"}</small></button></li>)}</ol>}</article>)}{recipes.data?.length === 0 && <p className="empty-state">还没有 Director Recipe。</p>}</aside>
      <form className="panel recipe-editor" onSubmit={(e) => { e.preventDefault(); publish.mutate(); }}><div className="panel-heading"><div><p className="eyebrow">声明式编辑器</p><h3>{mode === "VERSION" ? `从 v${selectedVersion?.version_no ?? "?"} 创建新版本` : mode === "COPY" ? "复制为新 Recipe" : "创建 Recipe"}</h3></div>{selectedHash && <code title={selectedHash}>{shortHash(selectedHash)}</code>}</div>
        {selectedVersion && <div className="recipe-mode-tabs" role="group" aria-label="发布方式"><button type="button" className={mode === "VERSION" ? "selected" : ""} onClick={() => setMode("VERSION")}>同 Recipe 新版本</button><button type="button" className={mode === "COPY" ? "selected" : ""} onClick={() => setMode("COPY")}>复制为新 Recipe</button></div>}
        {mode !== "VERSION" && <div className="recipe-fields-2"><label>Recipe code<input value={code} onChange={(e) => setCode(e.target.value.toLowerCase())} placeholder="vertical-drama" pattern="[a-z][a-z0-9_-]{1,79}" /></label><label>标题<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="竖屏都市漫剧" /></label></div>}
        <fieldset><legend>画幅与镜头规划</legend><div className="recipe-fields-3"><label>画幅<select value={draft.aspect_ratio} onChange={(e) => patch("aspect_ratio", e.target.value)}><option>9:16</option><option>16:9</option><option>1:1</option><option>4:3</option></select></label><label>平均时长（毫秒）<input type="number" min="250" max="120000" step="250" value={draft.shot_planning.avg_duration_ms} onChange={(e) => patch("shot_planning", { ...draft.shot_planning, avg_duration_ms: Number(e.target.value) })} /></label><label>对白覆盖<select value={draft.shot_planning.dialogue_coverage} onChange={(e) => patch("shot_planning", { ...draft.shot_planning, dialogue_coverage: e.target.value })}><option>MEDIUM_CLOSEUP_BIASED</option><option>BALANCED</option><option>WIDE_CONTEXT</option><option>CLOSEUP_INTIMATE</option></select></label></div></fieldset>
        <fieldset><legend>资产策略</legend><label>角色必需参考类型<input value={draft.asset_policy.character_required_refs.join(", ")} onChange={(e) => patch("asset_policy", { character_required_refs: e.target.value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean).slice(0, 20) })} /><small>逗号分隔，1–20 项。</small></label></fieldset>
        <fieldset><legend>生成能力</legend><div className="recipe-fields-2"><label>图像能力<select value={draft.generation.image.capability} onChange={(e) => patch("generation", { ...draft.generation, image: { capability: e.target.value } })}><option>IMAGE_CHARACTER</option><option>IMAGE_SCENE</option><option>IMAGE_CONCEPT</option><option>IMAGE_EDIT</option></select></label><label>视频能力<select value={draft.generation.video.capability} onChange={(e) => patch("generation", { ...draft.generation, video: { capability: e.target.value } })}><option>VIDEO_FIRST_LAST_FRAME</option><option>VIDEO_FIRST_FRAME</option><option>VIDEO_I2V</option><option>VIDEO_REFERENCE</option></select></label></div></fieldset>
        <fieldset><legend>QC Policy 固定引用</legend><label>不可变 QC Policy version<select value={draft.qc_policy_ref.policy_version_id} onChange={(e) => patch("qc_policy_ref", { policy_version_id: e.target.value })}><option value="">选择本项目 policy version</option>{qcPolicies.data?.map((item) => <option key={item.policy_version_id} value={item.policy_version_id}>{item.stage} · {item.owner_type} · v{item.version_no} · {item.policy_version_id.slice(0, 8)}</option>)}</select></label></fieldset>
        <label>发布原因<textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="记录本版本改变了什么；旧版本不会被覆盖。" /></label>
        <aside className="recipe-security"><strong>安全边界：Recipe 只声明策略</strong><p>前后端均拒绝 shell、python、command、executor、code、script 等执行字段。Recipe 不能读取任意文件、启动进程或绕过 Profile/QC 门禁。</p></aside>
        {notice && <p className={notice.startsWith("不可变版本") || notice.startsWith("Recipe ") || notice.startsWith("项目已") ? "recipe-notice success" : "recipe-notice error"} role="status">{notice}</p>}<div className="recipe-actions"><button type="submit" className="primary-action" disabled={publish.isPending}>{publish.isPending ? "创建中…" : mode === "VERSION" ? `创建 v${(selectedVersion?.version_no ?? 0) + 1}` : "创建 Recipe v1"}</button></div>
      </form>
    </div>
  </div>;
}

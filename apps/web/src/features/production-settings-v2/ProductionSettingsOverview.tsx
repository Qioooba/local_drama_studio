import { useMemo } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getCapacitySnapshot, getProjectConfiguration, getProjectHealth, listEpisodes, listSeasons } from "../../generated/api";
import { listGenerationPreferences } from "../preferences-v2/api";
import { listQcPolicies } from "../qc-policy-v2/api";
import { getDirectorRecipeBinding } from "../recipes-v2/api";
import type { GenerationPreference } from "../preferences-v2/types";
import { queryKeys } from "../../query/queryKeys";
import "./production-settings.css";
import { routes } from "../../app/routeRegistry";
import { ProductionSpecEditor } from "./ProductionSpecEditor";
import { ProjectTargetDurationEditor } from "./ProjectTargetDurationEditor";

const formatBytes = (bytes: number | null | undefined) => {
  if (bytes == null) return "未观测";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes; let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
};
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const capabilityLabel = (capability: string) => ({ IMAGE_CHARACTER: "默认角色图像", IMAGE_SCENE: "默认场景图像", VIDEO_I2V: "默认图生视频", VIDEO_FIRST_LAST_FRAME: "默认首尾帧视频", TTS: "默认语音合成" }[capability] ?? "其他生成能力");
const sourceLabel = (item: GenerationPreference | undefined) => item ? item.owner_type === "PROJECT" ? `项目明确设置 · ${item.resolution_mode === "EXPLICIT" ? "固定版本" : "自动选择"}` : "分集或镜头已覆盖" : "系统自动选择";
const qcStageLabel = (stage: string) => ({ IMAGE: "图像", VIDEO: "视频", AUDIO: "声音", CONTINUITY: "连续性", DELIVERY: "交付" }[stage] ?? "其他阶段");
const SETTINGS_SEASON_LIMIT = 8;
const SETTINGS_EPISODE_LIMIT = 100;
type SettingsSeason = Awaited<ReturnType<typeof listSeasons>>["items"][number] & { number?: number; display_order?: number };
type SettingsEpisode = Awaited<ReturnType<typeof listEpisodes>>["items"][number] & { number?: number; display_order?: number };
const settingsOrder = (item: { number?: number; display_order?: number }) => item.number ?? item.display_order ?? Number.MAX_SAFE_INTEGER;
const isDeliveredOrApproved = (episode: SettingsEpisode) => /DELIVERED|APPROVED/.test(episode.production_status.toUpperCase());
type ReadinessItem = { text: string; to: string };

export function ProductionSettingsOverview({ projectId }: { projectId: string }) {
  const configuration = useQuery({ queryKey: queryKeys.productionSettings.section(projectId, "configuration"), queryFn: () => getProjectConfiguration(projectId), enabled: Boolean(projectId) });
  const health = useQuery({ queryKey: queryKeys.projects.health(projectId), queryFn: () => getProjectHealth(projectId), enabled: Boolean(projectId) });
  const capacity = useQuery({ queryKey: queryKeys.capacity.scope(projectId), queryFn: () => getCapacitySnapshot(projectId), enabled: Boolean(projectId) });
  const preferences = useQuery({ queryKey: queryKeys.productionSettings.section(projectId, "preferences"), queryFn: () => listGenerationPreferences(projectId), enabled: Boolean(projectId) });
  const policies = useQuery({ queryKey: queryKeys.productionSettings.section(projectId, "qc"), queryFn: () => listQcPolicies(projectId), enabled: Boolean(projectId) });
  const recipe = useQuery({ queryKey: queryKeys.productionSettings.section(projectId, "recipe"), queryFn: () => getDirectorRecipeBinding(projectId), enabled: Boolean(projectId) });
  const seasons = useQuery({ queryKey: queryKeys.seasons.list(projectId), queryFn: () => listSeasons(projectId), enabled: Boolean(projectId) });
  const orderedSeasons = useMemo(() => ((seasons.data?.items ?? []) as SettingsSeason[]).slice().sort((left, right) => settingsOrder(left) - settingsOrder(right) || left.code.localeCompare(right.code)).slice(0, SETTINGS_SEASON_LIMIT), [seasons.data?.items]);
  const episodeQueries = useQueries({ queries: orderedSeasons.map((season) => ({ queryKey: queryKeys.episodes.list(season.id, SETTINGS_EPISODE_LIMIT), queryFn: () => listEpisodes(season.id), enabled: Boolean(projectId) })) });
  const deliveryEpisodes = orderedSeasons.flatMap((_season, index) => ((episodeQueries[index]?.data?.items ?? []) as SettingsEpisode[]).slice().sort((left, right) => settingsOrder(left) - settingsOrder(right) || left.code.localeCompare(right.code)).slice(0, SETTINGS_EPISODE_LIMIT));
  const deliveryEpisode = deliveryEpisodes.find((episode) => !isDeliveredOrApproved(episode)) ?? deliveryEpisodes[0];
  const firstEpisodeId = deliveryEpisode?.id;

  const config = configuration.data?.configuration;
  const plan = config?.production_plan?.plan ?? {};
  const productionSpec = config?.production_spec as { status?: string; blockers?: Array<{ code?: string; message?: string }> } | undefined;
  const presentation = typeof plan.presentation === "object" && plan.presentation ? plan.presentation as Record<string, unknown> : plan;
  const aspectRatio = typeof presentation.aspect_ratio === "string" ? presentation.aspect_ratio : "未在生产计划声明";
  const selectedTarget = config?.delivery_targets.find((target) => target.version_id === config.selected_delivery_target_version_id);
  const projectPreferences = (preferences.data ?? []).filter((item) => item.owner_type === "PROJECT");
  const overrideCount = (preferences.data ?? []).length - projectPreferences.length;
  const projectPolicies = (policies.data ?? []).filter((item) => item.owner_type === "PROJECT");
  const policyOverrideCount = (policies.data ?? []).length - projectPolicies.length;
  const keyCapabilities = ["IMAGE_CHARACTER", "VIDEO_FIRST_LAST_FRAME", "TTS"];
  const missingStages = ["IMAGE", "VIDEO", "AUDIO", "CONTINUITY", "DELIVERY"].filter((stage) => !projectPolicies.some((item) => item.stage === stage));
  // Keep this list limited to facts that can stop a production command or its
  // immutable compose/delivery stages.  A missing Director Recipe or project
  // QC policy is intentionally not in this list: EpisodeProductionRun treats
  // both as optional project defaults (the worker applies safe fallbacks), so
  // presenting either as a launch blocker would be misleading.
  const blockers: ReadinessItem[] = [
    ...(productionSpec?.status && productionSpec.status !== "READY" ? (productionSpec.blockers ?? [{ message: "生产规格尚未解析" }]).slice(0, 2).map((item) => ({ text: item.message ?? "生产规格尚未解析", to: "#production-spec-editor-title" })) : []),
    ...(health.data?.blockers ?? []).map((text) => ({ text, to: "/diagnostics" })),
    ...(!selectedTarget ? [{ text: "尚未选择交付规格", to: firstEpisodeId ? `/projects/${projectId}/episodes/${firstEpisodeId}/delivery` : `/projects/${projectId}` }] : []),
  ];
  const notices: ReadinessItem[] = [
    ...(recipe.isSuccess && !recipe.data ? [{ text: "尚未绑定导演模板（提醒）：不会阻止 EpisodeProductionRun；如需冻结制作策略，可绑定不可变版本。", to: `/projects/${projectId}/director-recipes` }] : []),
    ...(policies.isSuccess && missingStages.length ? [{ text: `项目级自动质检规则尚缺：${missingStages.map(qcStageLabel).join("、")}（提醒）：当前使用安全的有限默认策略，不会阻止 EpisodeProductionRun。`, to: `/projects/${projectId}/qc-policies` }] : []),
  ];
  const queries = [configuration, health, capacity, preferences, policies, recipe, seasons, ...episodeQueries];
  const loading = queries.some((query) => query.isPending);
  const firstError = queries.find((query) => query.isError)?.error;

  return <div className="production-settings-overview">
    {loading && <p className="empty-state" aria-live="polite">正在汇总项目生产事实…</p>}
    {firstError && <div className="workspace-error" role="alert"><div><strong>生产配置读取不完整</strong><p>{errorText(firstError)}</p></div></div>}
    {!loading && config && <ProjectTargetDurationEditor projectId={projectId} configuration={config} episodes={deliveryEpisodes} />}
    {!loading && <ProductionSpecEditor projectId={projectId} configuration={config} />}
    {!loading && <section className={`panel production-settings-readiness ${blockers.length ? "blocked" : "ready"}`} aria-labelledby="production-readiness-title"><div className="panel-heading"><div><p className="eyebrow">生产就绪性</p><h3 id="production-readiness-title">{blockers.length ? `${blockers.length} 项必须处理` : "本集制作可启动"}</h3></div><span className={`status-pill ${blockers.length ? "state-blocked" : "state-active"}`}>{blockers.length ? "有硬阻塞" : "已就绪"}</span></div>{blockers.length ? <ul>{blockers.map((item) => <li key={item.text}><span>{item.text}</span><Link to={item.to}>处理</Link></li>)}</ul> : <p>未发现会阻止 EpisodeProductionRun 的项目级硬阻塞；进入具体生产步骤时仍会执行实时检查。</p>}</section>}
    {!loading && notices.length > 0 && <section className="panel production-settings-notices" aria-labelledby="production-notices-title"><div className="panel-heading"><div><p className="eyebrow">可选默认提醒</p><h3 id="production-notices-title">{notices.length} 项可选配置</h3></div><span className="status-pill state-warning">提醒</span></div><ul>{notices.map((item) => <li key={item.text}><span>{item.text}</span><Link to={item.to}>配置</Link></li>)}</ul><p>提醒不会伪造默认，也不会改变本项目或其他项目的已冻结输入；只有在页面中明确保存后才会生效。</p></section>}

    <div className="production-settings-grid">
      <section className="panel production-setting-card" aria-labelledby="defaults-setting-title"><div className="production-setting-card__head"><div><span>01</span><h3 id="defaults-setting-title">项目默认</h3></div><Link to={`/projects/${projectId}/assets`}>管理风格资产</Link></div><dl><div><dt>默认画幅</dt><dd>{aspectRatio}</dd></div><div><dt>生产计划</dt><dd>{config?.production_plan ? `${config.production_plan.title} · v${config.production_plan.version_no}` : "未配置"}</dd></div><div><dt>默认风格资产</dt><dd>由资产圣经 Style 分类与引用版本生效</dd></div></dl><p>来源：Project Configuration 与资产圣经；本页不复制配置副本。</p></section>

      <section className="panel production-setting-card" aria-labelledby="generation-setting-title"><div className="production-setting-card__head"><div><span>02</span><h3 id="generation-setting-title">生成能力</h3></div><Link to={routes.settings(projectId, "capabilities")}>配置偏好</Link></div><ul className="production-source-list">{keyCapabilities.map((capability) => { const item = projectPreferences.find((entry) => entry.capability === capability); const fallbackBinding = config?.profile_bindings.find((entry) => entry.capability === capability); return <li key={capability}><div><strong>{capabilityLabel(capability)}</strong><small>{sourceLabel(item)}</small></div><span className={`status-pill ${item || fallbackBinding ? "state-active" : "neutral"}`}>{item?.resolution_mode === "EXPLICIT" ? "固定版本" : fallbackBinding ? fallbackBinding.profile_title : "自动选择"}</span></li>; })}</ul><p>{projectPreferences.length} 项项目默认 · {overrideCount} 项分集/镜头覆盖。系统会在具体生产步骤中重新确认来源，不会悄悄换用其他配置。</p></section>

      <section className="panel production-setting-card" aria-labelledby="qc-setting-title"><div className="production-setting-card__head"><div><span>03</span><h3 id="qc-setting-title">自动质检规则</h3></div><Link to={`/projects/${projectId}/qc-policies`}>管理策略</Link></div><ul className="production-source-list">{projectPolicies.map((policy) => <li key={policy.stage}><div><strong>{qcStageLabel(policy.stage)}</strong><small>项目默认 · 第 {policy.version_no} 版</small></div><span className="status-pill neutral">最多自动重试 {policy.max_auto_rerolls} 次</span></li>)}{!projectPolicies.length && <li className="empty-row">没有项目级自动质检默认规则</li>}</ul><p>{policyOverrideCount} 项分集/镜头覆盖。机器质检证据不能代替人工批准。</p></section>

      <section className="panel production-setting-card" aria-labelledby="recipe-setting-title"><div className="production-setting-card__head"><div><span>04</span><h3 id="recipe-setting-title">导演模板</h3></div><Link to={`/projects/${projectId}/director-recipes`}>管理模板</Link></div>{recipe.data ? <div className="production-recipe-summary"><strong>{recipe.data.title}</strong><span>{recipe.data.code} · 第 {recipe.data.version_no} 版</span><details><summary>高级：查看校验信息</summary><code title={recipe.data.recipe_hash}>内容校验指纹 {recipe.data.recipe_hash.slice(0, 12)}…</code><small>来源：项目明确绑定 · 绑定版本 {recipe.data.revision}</small></details></div> : <p className="empty-state">尚未绑定。项目不会自动跟随任一导演模板的最新版。</p>}</section>

      <section className="panel production-setting-card production-setting-card--wide" aria-labelledby="delivery-setting-title"><div className="production-setting-card__head"><div><span>05</span><h3 id="delivery-setting-title">输出规格与本机容量</h3></div>{firstEpisodeId ? <Link to={`/projects/${projectId}/episodes/${firstEpisodeId}/delivery`}>打开交付</Link> : <Link to={`/projects/${projectId}`}>先创建分集</Link>}</div><div className="production-capacity-grid"><dl><div><dt>输出规格</dt><dd>{selectedTarget ? `${selectedTarget.title} · 第 ${selectedTarget.version_no} 版` : "未选择"}</dd></div><div><dt>传输边界</dt><dd>{config?.impact.remote_transport_allowed ? "允许远程" : "仅本机；不允许远程传输"}</dd></div></dl><dl><div><dt>磁盘可用</dt><dd>{formatBytes(capacity.data?.snapshot.disk?.free_bytes ?? health.data?.disk.free_bytes)}</dd></div><div><dt>显卡并行任务</dt><dd>{capacity.data ? `${capacity.data.snapshot.gpu_active_count} / ${capacity.data.snapshot.gpu_concurrency_limit}` : "未观测"}</dd></div><div><dt>队列 / 后台服务</dt><dd>{capacity.data ? `${capacity.data.snapshot.queued_count} 排队 · ${capacity.data.snapshot.active_worker_count} 活动` : "未观测"}</dd></div></dl></div><p>容量是当前只读观测，不是性能承诺；正式交付仍需校验交付清单并由人工批准。</p></section>
    </div>
  </div>;
}

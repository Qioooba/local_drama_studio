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

const formatBytes = (bytes: number | null | undefined) => {
  if (bytes == null) return "未观测";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes; let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
};
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const capabilityLabel = (capability: string) => ({ IMAGE_CHARACTER: "默认角色图像", IMAGE_SCENE: "默认场景图像", VIDEO_I2V: "默认图生视频", VIDEO_FIRST_LAST_FRAME: "默认首尾帧视频", TTS: "默认 TTS" }[capability] ?? capability);
const sourceLabel = (item: GenerationPreference | undefined) => item ? item.owner_type === "PROJECT" ? `项目显式 · ${item.resolution_mode}` : `${item.owner_type} 覆盖` : "系统自动解析";
const SETTINGS_SEASON_LIMIT = 8;
const SETTINGS_EPISODE_LIMIT = 100;
type SettingsSeason = Awaited<ReturnType<typeof listSeasons>>["items"][number] & { number?: number; display_order?: number };
type SettingsEpisode = Awaited<ReturnType<typeof listEpisodes>>["items"][number] & { number?: number; display_order?: number };
const settingsOrder = (item: { number?: number; display_order?: number }) => item.number ?? item.display_order ?? Number.MAX_SAFE_INTEGER;
const isDeliveredOrApproved = (episode: SettingsEpisode) => /DELIVERED|APPROVED/.test(episode.production_status.toUpperCase());

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
  const presentation = typeof plan.presentation === "object" && plan.presentation ? plan.presentation as Record<string, unknown> : plan;
  const aspectRatio = typeof presentation.aspect_ratio === "string" ? presentation.aspect_ratio : "未在生产计划声明";
  const selectedTarget = config?.delivery_targets.find((target) => target.version_id === config.selected_delivery_target_version_id);
  const projectPreferences = (preferences.data ?? []).filter((item) => item.owner_type === "PROJECT");
  const overrideCount = (preferences.data ?? []).length - projectPreferences.length;
  const projectPolicies = (policies.data ?? []).filter((item) => item.owner_type === "PROJECT");
  const policyOverrideCount = (policies.data ?? []).length - projectPolicies.length;
  const keyCapabilities = ["IMAGE_CHARACTER", "VIDEO_FIRST_LAST_FRAME", "TTS"];
  const missingStages = ["IMAGE", "VIDEO", "AUDIO", "CONTINUITY", "DELIVERY"].filter((stage) => !projectPolicies.some((item) => item.stage === stage));
  const blockers: Array<{ text: string; to: string }> = [
    ...(health.data?.blockers ?? []).map((text) => ({ text, to: "/diagnostics" })),
    ...(!recipe.isPending && !recipe.data ? [{ text: "项目尚未显式绑定 Director Recipe", to: `/projects/${projectId}/director-recipes` }] : []),
    ...(missingStages.length ? [{ text: `项目级 QC 尚缺：${missingStages.join("、")}`, to: `/projects/${projectId}/qc-policies` }] : []),
    ...(!selectedTarget ? [{ text: "尚未选择交付规格", to: firstEpisodeId ? `/projects/${projectId}/episodes/${firstEpisodeId}/delivery` : `/projects/${projectId}` }] : []),
  ];
  const queries = [configuration, health, capacity, preferences, policies, recipe, seasons, ...episodeQueries];
  const loading = queries.some((query) => query.isPending);
  const firstError = queries.find((query) => query.isError)?.error;

  return <div className="production-settings-overview">
    {loading && <p className="empty-state" aria-live="polite">正在汇总项目生产事实…</p>}
    {firstError && <div className="workspace-error" role="alert"><div><strong>生产配置读取不完整</strong><p>{errorText(firstError)}</p></div></div>}
    {!loading && <section className={`panel production-settings-readiness ${blockers.length ? "blocked" : "ready"}`} aria-labelledby="production-readiness-title"><div className="panel-heading"><div><p className="eyebrow">生产就绪性</p><h3 id="production-readiness-title">{blockers.length ? `${blockers.length} 项需要处理` : "项目默认配置完整"}</h3></div><span className={`status-pill ${blockers.length ? "state-blocked" : "state-active"}`}>{blockers.length ? "BLOCKED" : "READY"}</span></div>{blockers.length ? <ul>{blockers.map((item) => <li key={item.text}><span>{item.text}</span><Link to={item.to}>处理</Link></li>)}</ul> : <p>配置事实均来自当前 API；进入具体生产步骤时仍会执行实时预检。</p>}</section>}

    <div className="production-settings-grid">
      <section className="panel production-setting-card" aria-labelledby="defaults-setting-title"><div className="production-setting-card__head"><div><span>01</span><h3 id="defaults-setting-title">项目默认</h3></div><Link to={`/projects/${projectId}/assets`}>管理风格资产</Link></div><dl><div><dt>默认画幅</dt><dd>{aspectRatio}</dd></div><div><dt>生产计划</dt><dd>{config?.production_plan ? `${config.production_plan.title} · v${config.production_plan.version_no}` : "未配置"}</dd></div><div><dt>默认风格资产</dt><dd>由资产圣经 Style 分类与引用版本生效</dd></div></dl><p>来源：Project Configuration 与资产圣经；本页不复制配置副本。</p></section>

      <section className="panel production-setting-card" aria-labelledby="generation-setting-title"><div className="production-setting-card__head"><div><span>02</span><h3 id="generation-setting-title">生成能力</h3></div><Link to={`/models?project=${encodeURIComponent(projectId)}`}>配置偏好</Link></div><ul className="production-source-list">{keyCapabilities.map((capability) => { const item = projectPreferences.find((entry) => entry.capability === capability); const fallbackBinding = config?.profile_bindings.find((entry) => entry.capability === capability); return <li key={capability}><div><strong>{capabilityLabel(capability)}</strong><small>{sourceLabel(item)}</small></div><span className={`status-pill ${item || fallbackBinding ? "state-active" : "neutral"}`}>{item?.resolution_mode === "EXPLICIT" ? "显式" : fallbackBinding ? fallbackBinding.profile_title : "AUTO"}</span></li>; })}</ul><p>{projectPreferences.length} 项项目默认 · {overrideCount} 项分集/镜头覆盖。最终来源在具体上下文实时解析，不静默 fallback。</p></section>

      <section className="panel production-setting-card" aria-labelledby="qc-setting-title"><div className="production-setting-card__head"><div><span>03</span><h3 id="qc-setting-title">QC Policy</h3></div><Link to={`/projects/${projectId}/qc-policies`}>管理策略</Link></div><ul className="production-source-list">{projectPolicies.map((policy) => <li key={policy.stage}><div><strong>{policy.stage}</strong><small>项目默认 · v{policy.version_no}</small></div><span className="status-pill neutral">最多 {policy.max_auto_rerolls} 次</span></li>)}{!projectPolicies.length && <li className="empty-row">没有项目级 QC 默认</li>}</ul><p>{policyOverrideCount} 项分集/镜头覆盖。机器 QC 证据永远不等于人工批准。</p></section>

      <section className="panel production-setting-card" aria-labelledby="recipe-setting-title"><div className="production-setting-card__head"><div><span>04</span><h3 id="recipe-setting-title">Director Recipe</h3></div><Link to={`/projects/${projectId}/director-recipes`}>管理配方</Link></div>{recipe.data ? <div className="production-recipe-summary"><strong>{recipe.data.title}</strong><span>{recipe.data.code} · v{recipe.data.version_no}</span><code title={recipe.data.recipe_hash}>hash {recipe.data.recipe_hash.slice(0, 12)}…</code><small>来源：项目显式绑定 · revision {recipe.data.revision}</small></div> : <p className="empty-state">尚未绑定。项目不会静默跟随任一 Recipe 的最新版。</p>}</section>

      <section className="panel production-setting-card production-setting-card--wide" aria-labelledby="delivery-setting-title"><div className="production-setting-card__head"><div><span>05</span><h3 id="delivery-setting-title">输出规格与本机容量</h3></div>{firstEpisodeId ? <Link to={`/projects/${projectId}/episodes/${firstEpisodeId}/delivery`}>打开交付</Link> : <Link to={`/projects/${projectId}`}>先创建分集</Link>}</div><div className="production-capacity-grid"><dl><div><dt>输出规格</dt><dd>{selectedTarget ? `${selectedTarget.title} · v${selectedTarget.version_no}` : "未选择"}</dd></div><div><dt>传输边界</dt><dd>{config?.impact.remote_transport_allowed ? "允许远程" : "仅本机；不允许远程传输"}</dd></div></dl><dl><div><dt>磁盘可用</dt><dd>{formatBytes(capacity.data?.snapshot.disk?.free_bytes ?? health.data?.disk.free_bytes)}</dd></div><div><dt>GPU 并发</dt><dd>{capacity.data ? `${capacity.data.snapshot.gpu_active_count} / ${capacity.data.snapshot.gpu_concurrency_limit}` : "未观测"}</dd></div><div><dt>队列 / Worker</dt><dd>{capacity.data ? `${capacity.data.snapshot.queued_count} 排队 · ${capacity.data.snapshot.active_worker_count} 活动` : "未观测"}</dd></div></dl></div><p>容量是当前只读观测，不是基准承诺；正式交付仍要求 manifest 校验与人工批准。</p></section>
    </div>
  </div>;
}

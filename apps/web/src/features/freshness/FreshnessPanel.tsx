import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useProjectEventInvalidation } from "../events/useProjectEventInvalidation";
import { getProductionFreshness, type FreshnessItem, type FreshnessScopeType, type FreshnessVersion } from "./api";
import { queryKeys } from "../../query/queryKeys";
import "./freshness.css";

const LIMITS = [25, 50, 100, 200] as const;
const FRESHNESS_EVENTS = [
  "SHOT_REVISION_CREATED",
  "SHOT_PRODUCTION_READY",
  "episode.shot_plan.changed",
  "JOB_FINISHED",
  "JOB_REQUEUED",
  "ARTIFACT_REGISTERED",
  "CONTINUITY_STALE_PROPAGATED",
] as const;

const factLabel = (type: FreshnessItem["fact_type"]) => ({ VARIANT: "生成候选", FRAME_BRIDGE: "Frame Bridge", TIMELINE: "时间线" })[type];
const reasonLabel = (code: string) => ({
  ASSET_REFERENCE_CHANGED: "资产参考已变化",
  ASSET_STATE_CHANGED: "资产状态已变化",
  FRAME_BRIDGE_CHANGED: "Frame Bridge 已变化",
  SELECTION_CHANGED: "当前选择已变化",
  SHOT_REVISION_CHANGED: "镜头 revision 已变化",
  PROFILE_CHANGED: "生成模型已变化",
  PROMPT_CHANGED: "生成提示已变化",
  POST_PROCESS_CHANGED: "后期增强配方已变化",
}[code] ?? code);

const revisionText = (version: FreshnessVersion | null) => version
  ? `${version.entity_type} · ${version.entity_id} · rev ${version.revision ?? "未知"}`
  : "未记录";

type FreshnessGroup = {
  item: FreshnessItem;
  factCount: number;
  factIds: Set<string>;
  sourceVersions: Set<string>;
  currentVersions: Set<string>;
};

const versionKey = (version: FreshnessVersion | null) => version
  ? [version.entity_type, version.entity_id, version.revision ?? null]
  : null;

const compactVersionKey = (version: FreshnessVersion | null) => JSON.stringify(versionKey(version));

// One remediation action repairs the active shot/timeline concern once. Historical
// revisions remain counted as evidence, but must not become dozens of identical
// action cards. Keep different reason families and remediation routes separate.
const groupKey = (item: FreshnessItem) => JSON.stringify({
  factType: item.fact_type,
  status: item.status,
  projectId: item.project_id,
  episodeId: item.episode_id,
  shotId: item.shot_id,
  reasons: [...new Set(item.reasons.map((reason) => reason.code))].sort(),
  remediations: item.remediation_links.map((action) => [action.rel, action.href, action.method]).sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right))),
});

export function groupFreshnessItems(items: FreshnessItem[]): FreshnessGroup[] {
  const grouped = new Map<string, FreshnessGroup>();
  items.forEach((item) => {
    const key = groupKey(item);
    const existing = grouped.get(key);
    if (existing) {
      existing.factCount += 1;
      existing.factIds.add(item.id);
      existing.sourceVersions.add(compactVersionKey(item.source));
      existing.currentVersions.add(compactVersionKey(item.current));
    } else {
      grouped.set(key, {
        item,
        factCount: 1,
        factIds: new Set([item.id]),
        sourceVersions: new Set([compactVersionKey(item.source)]),
        currentVersions: new Set([compactVersionKey(item.current)]),
      });
    }
  });
  return [...grouped.values()];
}

function remediationHref(item: FreshnessItem, rel: string, backendHref: string) {
  if (backendHref.startsWith("/projects/")) return backendHref;
  if (rel === "create-timeline-revision" && item.episode_id) return `/projects/${item.project_id}/episodes/${item.episode_id}/timeline`;
  if (item.episode_id && item.shot_id) return `/projects/${item.project_id}/episodes/${item.episode_id}/direct/${item.shot_id}`;
  return `/projects/${item.project_id}`;
}

export function FreshnessPanel({ projectId, scopeType, scopeId, initialLimit = 50 }: {
  projectId: string;
  scopeType: FreshnessScopeType;
  scopeId: string;
  initialLimit?: typeof LIMITS[number];
}) {
  const [limit, setLimit] = useState<number>(initialLimit);
  const key = queryKeys.freshness.report(scopeType, scopeId, limit);
  const report = useQuery({ queryKey: key, queryFn: () => getProductionFreshness(scopeType, scopeId, limit), enabled: Boolean(scopeId) });
  useProjectEventInvalidation(projectId, FRESHNESS_EVENTS, [queryKeys.freshness.scope(scopeType, scopeId)]);
  const groups = groupFreshnessItems(report.data?.items ?? []);
  const staleGroups = groups.filter(({ item }) => item.status === "STALE").length;
  const currentGroups = groups.length - staleGroups;

  return <section className="panel freshness-panel" aria-labelledby={`freshness-title-${scopeType.toLowerCase()}`}>
    <div className="panel-heading freshness-panel__heading">
      <div><p className="eyebrow">Production Freshness · 只读</p><h3 id={`freshness-title-${scopeType.toLowerCase()}`}>生产时效报告</h3></div>
      <div className="freshness-panel__controls">
        <label>报告上限<select aria-label="Freshness 报告上限" value={limit} onChange={(event) => setLimit(Number(event.target.value))}>{LIMITS.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <button className="secondary" type="button" onClick={() => void report.refetch()} disabled={report.isFetching}>{report.isFetching ? "刷新中…" : "刷新"}</button>
      </div>
    </div>
    <p className="muted freshness-panel__honesty">报告只聚合当前 SQLite 事实，不改写历史 Variant、审批、Frame Bridge 或 Timeline。机器证据和 freshness 均不等于人工批准。</p>
    {report.isPending && <p className="empty-state" role="status">正在核对资产引用与下游版本…</p>}
    {report.isError && <div className="inline-error" role="alert">读取失败：{report.error instanceof Error ? report.error.message : String(report.error)}</div>}
    {report.data && <>
      <div className="freshness-summary" aria-label="Freshness 汇总">
        <div className={staleGroups ? "attention" : ""}><span>待处置分组</span><strong>{staleGroups}<small> / {report.data.summary.stale} 条事实</small></strong></div>
        <div><span>当前有效分组</span><strong>{currentGroups}<small> / {report.data.summary.current} 条事实</small></strong></div>
        <div><span>处置对象</span><strong>{groups.length}<small> 组</small></strong></div>
        <div><span>已返回原始事实</span><strong>{report.data.summary.returned}<small> / limit {report.data.audit.query_limit}</small></strong></div>
      </div>
      {report.data.summary.truncated && <p className="freshness-limit-note" role="note">结果已达到 limit；可提高“报告上限”继续查看，不代表未显示条目有效。</p>}
      {groups.length === 0 ? <p className="empty-state">当前范围没有可评估的 Variant、Frame Bridge 或 Timeline 条目。</p> : <div className="freshness-list">
        {groups.map(({ item, factCount, factIds, sourceVersions, currentVersions }) => <details className={`freshness-item freshness-item--${item.status.toLowerCase()}`} key={groupKey(item)} open={item.status === "STALE"}>
          <summary><span><strong>{factLabel(item.fact_type)}</strong><small>{item.shot_id ? `镜头 ${item.shot_id}` : item.id}</small></span><span className="freshness-item__summary-status">{factCount > 1 && <span className="freshness-count" aria-label={`包含 ${factCount} 条原始事实`}>{factCount} 条事实</span>}<span className={`status-pill state-${item.status.toLowerCase()}`}>{item.status}</span></span></summary>
          <div className="freshness-item__body">
            {factCount > 1 && <p className="freshness-current-note">已把 {factCount} 条历史事实聚合为一次处置；涉及 {factIds.size} 个事实对象、{sourceVersions.size} 组生成时来源与 {currentVersions.size} 组当前来源。下方展示一条代表证据，原始事实仍完整保留。</p>}
            {item.reasons.length ? <ul className="freshness-reasons">{item.reasons.map((reason, index) => <li key={`${reason.code}:${index}`}><strong>{reasonLabel(reason.code)}</strong><span>{reason.message}</span>{(reason.source_revision != null || reason.current_revision != null) && <small>原因 revision：{reason.source_revision ?? "未知"} → {reason.current_revision ?? "未知"}</small>}</li>)}</ul> : <p className="freshness-current-note">当前未发现上游变化。</p>}
            <dl className="freshness-versions"><div><dt>生成时来源</dt><dd>{revisionText(item.source)}</dd></div><div><dt>当前来源</dt><dd>{revisionText(item.current)}</dd></div></dl>
            {item.remediation_links.length > 0 && <nav className="freshness-actions" aria-label={`${factLabel(item.fact_type)} 处置入口`}>{item.remediation_links.map((action) => <Link className="secondary" key={action.rel} to={remediationHref(item, action.rel, action.href)}>{action.label}</Link>)}</nav>}
          </div>
        </details>)}
      </div>}
      <p className="muted freshness-panel__audit">只读查询 · {report.data.audit.query_count} 条 SQL · 未执行写入 · 未访问公网</p>
    </>}
  </section>;
}

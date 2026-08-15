import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listAuditEvents, type AuditEventFilters } from "../../generated/api";

type Props = { projectId?: string | null };

function formatDate(value: string) {
  const parsed = new Date(value.replace(" ", "T") + (value.endsWith("Z") ? "" : "Z"));
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

export function AuditHistoryPanel({ projectId }: Props) {
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [subjectType, setSubjectType] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [occurredAfter, setOccurredAfter] = useState("");
  const [occurredBefore, setOccurredBefore] = useState("");
  const [cursor, setCursor] = useState(0);
  const filters = useMemo<AuditEventFilters>(() => ({
    project_id: projectId || undefined,
    action: action.trim() || undefined,
    actor: actor.trim() || undefined,
    subject_type: subjectType.trim() || undefined,
    subject_id: subjectId.trim() || undefined,
    occurred_after: occurredAfter ? new Date(occurredAfter).toISOString() : undefined,
    occurred_before: occurredBefore ? new Date(occurredBefore).toISOString() : undefined,
    cursor,
    limit: 50,
  }), [action, actor, cursor, occurredAfter, occurredBefore, projectId, subjectId, subjectType]);
  const history = useQuery({ queryKey: ["audit-events", filters], queryFn: () => listAuditEvents(filters), staleTime: 5000 });
  const reset = () => {
    setCursor(0);
  };
  const hasFilters = Boolean(action || actor || subjectType || subjectId || occurredAfter || occurredBefore);
  return <section className="panel audit-history-panel" aria-labelledby="audit-history-title">
    <div className="panel-heading">
      <div><p className="eyebrow">FR-AUDT-001 · APPEND-ONLY HISTORY</p><h3 id="audit-history-title">审计历史</h3></div>
      <span className="status-pill neutral">只读 · LOCAL_ONLY · 脱敏</span>
    </div>
    <p className="muted">批准、选择、配置、发布和网络策略拒绝均由本机 SQLite 记录；查询不修改审计日志，也不读取远程服务。</p>
    <div className="audit-filters" role="search" aria-label="审计事件筛选">
      <label>动作<input value={action} onChange={(event) => { setAction(event.target.value); reset(); }} placeholder="例如 REVIEW_SUBMITTED" /></label>
      <label>主体<input value={subjectType} onChange={(event) => { setSubjectType(event.target.value); reset(); }} placeholder="例如 media_version" /></label>
      <label>主体 ID<input value={subjectId} onChange={(event) => { setSubjectId(event.target.value); reset(); }} placeholder="可选 subject ID" /></label>
      <label>操作者<input value={actor} onChange={(event) => { setActor(event.target.value); reset(); }} placeholder="例如 local-user" /></label>
      <label>起始时间<input type="datetime-local" value={occurredAfter} onChange={(event) => { setOccurredAfter(event.target.value); reset(); }} /></label>
      <label>结束时间<input type="datetime-local" value={occurredBefore} onChange={(event) => { setOccurredBefore(event.target.value); reset(); }} /></label>
      {hasFilters && <button className="secondary" type="button" onClick={() => { setAction(""); setActor(""); setSubjectType(""); setSubjectId(""); setOccurredAfter(""); setOccurredBefore(""); reset(); }}>清除筛选</button>}
    </div>
    {history.isPending ? <p className="empty-state">正在读取本机审计事件…</p> : history.error ? <p className="inline-error" role="alert">审计读取失败：{String(history.error)}</p> : history.data?.items.length ? <>
      <div className="table-wrap audit-history-table"><table><caption className="sr-only">审计事件列表</caption><thead><tr><th>时间</th><th>动作</th><th>主体</th><th>操作者</th><th>摘要</th><th>详情</th></tr></thead><tbody>{history.data.items.map((item) => <tr key={item.event_id}><td><time dateTime={item.occurred_at}>{formatDate(item.occurred_at)}</time><small>#{item.event_id}</small></td><td><strong>{item.action}</strong><small>{item.role_context}</small></td><td><code>{item.subject_type}</code><small>{item.subject_id.slice(0, 12)}</small></td><td>{item.actor}</td><td>{item.summary}</td><td><details><summary>查看脱敏字段</summary><pre>{JSON.stringify(item.metadata, null, 2)}</pre></details></td></tr>)}</tbody></table></div>
      <div className="audit-pagination"><span className="muted">已加载 {history.data.items.length} 条 · 游标稳定降序</span><button className="secondary" type="button" onClick={() => setCursor(history.data?.next_cursor ?? 0)} disabled={!history.data?.next_cursor || history.isFetching}>更早事件</button>{cursor > 0 && <button className="secondary" type="button" onClick={reset} disabled={history.isFetching}>回到最新</button>}</div>
    </> : <p className="empty-state">当前筛选没有审计事件。</p>}
  </section>;
}

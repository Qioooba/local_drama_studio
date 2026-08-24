import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getAuditProof, listAuditEvents, type AuditEventFilters, type AuditProof } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";

type Props = { projectId?: string | null };

const AUDIT_ACTION_SUGGESTIONS = ["REVIEW_SUBMITTED", "JOB_QUEUED", "GENERATION_VARIANT_CREATED", "GENERATION_VARIANT_SUBMITTED", "SHOT_REVISION_CREATED", "SHOT_PRODUCTION_READY", "MEDIA_VERSION_DERIVED", "CHARACTER_VOICE_BOUND", "EPISODE_TTS_BATCH_SUBMITTED", "AUTOMATION_WORKFLOW_CREATED"];
const AUDIT_SUBJECT_SUGGESTIONS = ["project", "shot", "media_version", "media_asset", "job", "generation_variant", "review", "dialogue_text_revision", "execution_profile_version", "automation_workflow"];
const AUDIT_ACTION_LABELS: Record<string, string> = {
  REVIEW_SUBMITTED: "已提交审核决定",
  JOB_QUEUED: "任务已加入队列",
  GENERATION_VARIANT_CREATED: "已创建生成候选",
  GENERATION_VARIANT_SUBMITTED: "已提交生成候选",
  SHOT_REVISION_CREATED: "已创建镜头新版本",
  SHOT_PRODUCTION_READY: "镜头已可进入正式生产",
  MEDIA_VERSION_DERIVED: "已生成媒体派生版本",
  CHARACTER_VOICE_BOUND: "已绑定角色音色",
  EPISODE_TTS_BATCH_SUBMITTED: "已提交整集语音合成",
  AUTOMATION_WORKFLOW_CREATED: "已创建自动化流程",
};
const AUDIT_SUBJECT_LABELS: Record<string, string> = {
  project: "项目",
  shot: "镜头",
  media_version: "媒体版本",
  media_asset: "媒体资产",
  job: "后台任务",
  generation_variant: "生成候选",
  review: "审核记录",
  dialogue_text_revision: "对白文本版本",
  execution_profile_version: "模型执行配置版本",
  automation_workflow: "自动化流程",
};
const AUDIT_ACTOR_LABELS: Record<string, string> = {
  "local-user": "当前本机用户",
  worker: "后台执行器",
  "local-llm": "本机语言模型",
};

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function formatDate(value: string): string {
  const parsed = new Date(value.replace(" ", "T") + (value.endsWith("Z") ? "" : "Z"));
  if (Number.isNaN(parsed.valueOf())) return value;
  const y = parsed.getFullYear();
  const m = pad2(parsed.getMonth() + 1);
  const d = pad2(parsed.getDate());
  const hh = pad2(parsed.getHours());
  const mm = pad2(parsed.getMinutes());
  const ss = pad2(parsed.getSeconds());
  return `${y}/${m}/${d} ${hh}:${mm}:${ss}`;
}

export function AuditHistoryPanel({ projectId }: Props) {
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [subjectType, setSubjectType] = useState("");
  const [subjectId, setSubjectId] = useState("");
  const [occurredAfter, setOccurredAfter] = useState("");
  const [occurredBefore, setOccurredBefore] = useState("");
  const [cursor, setCursor] = useState(0);
  const [proof, setProof] = useState<AuditProof | null>(null);
  const [proofBusy, setProofBusy] = useState(false);
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
  const history = useQuery({ queryKey: queryKeys.audit.eventList(filters), queryFn: () => listAuditEvents(filters), staleTime: 5000 });
  const reset = () => {
    setCursor(0);
    setProof(null);
  };
  const generateProof = async () => {
    setProofBusy(true);
    try {
      setProof(await getAuditProof(filters, 1000));
    } finally {
      setProofBusy(false);
    }
  };
  const hasFilters = Boolean(action || actor || subjectType || subjectId || occurredAfter || occurredBefore);
  return <section className="panel audit-history-panel" aria-labelledby="audit-history-title">
    <div className="panel-heading">
      <div><p className="eyebrow">追加审计历史</p><h3 id="audit-history-title">审计历史</h3></div>
      <span className="status-pill neutral">只读 · 仅本地 · 脱敏</span>
    </div>
    <p className="muted">批准、选择、配置、发布和网络策略拒绝均由本机 SQLite 记录；查询不修改审计日志，也不读取远程服务。</p>
    <div className="audit-filters" role="search" aria-label="审计事件筛选">
      <label>动作<select value={action} onChange={(event) => { setAction(event.target.value); reset(); }}><option value="">全部动作</option>{[...new Set([...AUDIT_ACTION_SUGGESTIONS, ...(history.data?.items.map((item) => item.action) ?? [])])].map((value, index) => <option key={value} value={value}>{AUDIT_ACTION_LABELS[value] ?? `其他系统动作 ${index + 1}`}</option>)}</select></label>
      <label>主体类型<select value={subjectType} onChange={(event) => { setSubjectType(event.target.value); setSubjectId(""); reset(); }}><option value="">全部主体</option>{[...new Set([...AUDIT_SUBJECT_SUGGESTIONS, ...(history.data?.items.map((item) => item.subject_type) ?? [])])].map((value, index) => <option key={value} value={value}>{AUDIT_SUBJECT_LABELS[value] ?? `其他主体类型 ${index + 1}`}</option>)}</select></label>
      <label>具体主体<select value={subjectId} onChange={(event) => { setSubjectId(event.target.value); reset(); }} disabled={!subjectType}><option value="">该类型的全部记录</option>{[...new Map((history.data?.items ?? []).filter((item) => !subjectType || item.subject_type === subjectType).map((item) => [item.subject_id, item])).values()].map((item) => <option key={item.subject_id} value={item.subject_id}>{item.summary || item.subject_id.slice(0, 16)}</option>)}</select></label>
      <label>操作者<select value={actor} onChange={(event) => { setActor(event.target.value); reset(); }}><option value="">全部操作者</option>{[...new Set(["local-user", "worker", "local-llm", ...(history.data?.items.map((item) => item.actor) ?? [])])].map((value, index) => <option key={value} value={value}>{AUDIT_ACTOR_LABELS[value] ?? `其他系统账号 ${index + 1}`}</option>)}</select></label>
      <label>起始时间<input type="datetime-local" value={occurredAfter} onChange={(event) => { setOccurredAfter(event.target.value); reset(); }} /></label>
      <label>结束时间<input type="datetime-local" value={occurredBefore} onChange={(event) => { setOccurredBefore(event.target.value); reset(); }} /></label>
      {hasFilters && <button className="secondary" type="button" onClick={() => { setAction(""); setActor(""); setSubjectType(""); setSubjectId(""); setOccurredAfter(""); setOccurredBefore(""); reset(); }}>清除筛选</button>}
      <button className="secondary" type="button" onClick={() => void generateProof()} disabled={proofBusy}>{proofBusy ? "生成中…" : "生成哈希证明"}</button>
    </div>
    {proof && <div className="audit-proof" role="status"><strong>导出证明</strong><span>{proof.event_count} 条事件 · {proof.truncated ? "达到上限，结果已截断" : "完整筛选结果"}</span><code title={proof.chain_sha256}>{proof.chain_sha256}</code></div>}
    {history.isPending ? <p className="empty-state">正在读取本机审计事件…</p> : history.error ? <p className="inline-error" role="alert">审计读取失败：{String(history.error)}</p> : history.data?.items.length ? <>
      <div className="table-wrap audit-history-table"><table><caption className="sr-only">审计事件列表</caption><thead><tr><th>时间</th><th>动作</th><th>主体</th><th>操作者</th><th>摘要</th><th>详情</th></tr></thead><tbody>{history.data.items.map((item) => <tr key={item.event_id}><td><time dateTime={item.occurred_at}>{formatDate(item.occurred_at)}</time><small>#{item.event_id}</small></td><td><strong>{item.action}</strong><small>{item.role_context}</small></td><td><code title={item.subject_id}>{item.subject_type}</code><small title={item.subject_id}>{item.subject_id.slice(0, 12)}</small></td><td title={item.actor}>{item.actor}</td><td title={item.summary}>{item.summary}</td><td><details><summary>查看脱敏字段</summary><pre>{JSON.stringify(item.metadata, null, 2)}</pre></details></td></tr>)}</tbody></table></div>
      <div className="audit-pagination"><span className="muted">已加载 {history.data.items.length} 条 · 游标稳定降序</span><button className="secondary" type="button" onClick={() => setCursor(history.data?.next_cursor ?? 0)} disabled={!history.data?.next_cursor || history.isFetching}>更早事件</button>{cursor > 0 && <button className="secondary" type="button" onClick={reset} disabled={history.isFetching}>回到最新</button>}</div>
    </> : <p className="empty-state">当前筛选没有审计事件。</p>}
  </section>;
}

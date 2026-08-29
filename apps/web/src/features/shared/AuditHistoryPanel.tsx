import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getAuditProof,
  listAuditEvents,
  type AuditEvent,
  type AuditEventFilters,
  type AuditProof,
} from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";

type Props = { projectId?: string | null };

const AUDIT_ACTION_SUGGESTIONS = [
  "REVIEW_SUBMITTED",
  "JOB_QUEUED",
  "JOB_DELETED",
  "GENERATION_VARIANT_CREATED",
  "SHOT_REVISION_CREATED",
  "SHOT_PRODUCTION_READY",
  "MEDIA_VERSION_DERIVED",
  "CHARACTER_VOICE_BOUND",
  "EPISODE_TTS_BATCH_SUBMITTED",
  "AUTOMATION_WORKFLOW_CREATED",
  "WORKFLOW_PUBLISHED",
  "PROFILE_BOUND",
  "PROFILE_VERSION_PUBLISHED_FROM_EVIDENCE",
  "LOCAL_LLM_PROFILE_PUBLISHED",
  "LOCAL_AI_SERVICE_MODELS_REGISTERED",
  "DIAGNOSTIC_RUN",
  "MANIFEST_SYNCED",
];

const AUDIT_ACTION_LABELS: Record<string, string> = {
  REVIEW_SUBMITTED: "提交了审核决定",
  JOB_QUEUED: "任务已加入队列",
  JOB_DELETED: "删除了任务历史",
  GENERATION_VARIANT_CREATED: "创建了生成候选",
  GENERATION_VARIANT_SUBMITTED: "提交了生成候选",
  SHOT_REVISION_CREATED: "创建了镜头新版本",
  SHOT_PRODUCTION_READY: "将镜头标记为可生产",
  MEDIA_VERSION_DERIVED: "生成了媒体派生版本",
  CHARACTER_VOICE_BOUND: "绑定了角色音色",
  EPISODE_TTS_BATCH_SUBMITTED: "提交了整集语音合成",
  AUTOMATION_WORKFLOW_CREATED: "创建了自动化流程",
  WORKFLOW_PUBLISHED: "发布了工作流",
  PROFILE_BOUND: "绑定了执行配置",
  PROFILE_VERSION_PUBLISHED_FROM_EVIDENCE: "根据验证证据发布了执行配置",
  LOCAL_LLM_PROFILE_PUBLISHED: "发布了本机语言模型配置",
  LOCAL_LLM_PROFILE_SYNCED: "同步了本机语言模型配置",
  LOCAL_AI_SERVICE_MODELS_REGISTERED: "登记了本机 AI 服务模型",
  JOB_ARTIFACT_PROMOTED: "将任务产物转为正式资产",
  NEW_MODEL_PRODUCTION_WORKFLOWS_LINKED: "关联了新模型生产工作流",
  DIAGNOSTIC_RUN: "运行了环境检查",
  MANIFEST_SYNCED: "同步了模型清单",
};

const AUDIT_SUBJECT_SUGGESTIONS = [
  "project",
  "shot",
  "media_version",
  "media_asset",
  "job",
  "generation_variant",
  "review",
  "dialogue_text_revision",
  "execution_profile_version",
  "automation_workflow",
  "workflow_version",
  "diagnostic_run",
  "manifest",
  "runtime",
  "model_suite",
];

const AUDIT_SUBJECT_LABELS: Record<string, string> = {
  project: "项目",
  shot: "镜头",
  media_version: "媒体版本",
  media_asset: "媒体资产",
  job: "后台任务",
  generation_variant: "生成候选",
  review: "审核记录",
  dialogue_text_revision: "对白文本版本",
  execution_profile_version: "执行配置版本",
  automation_workflow: "自动化流程",
  workflow_version: "工作流版本",
  diagnostic_run: "环境检查",
  manifest: "模型清单",
  runtime: "本机运行环境",
  model_suite: "模型套件",
};

const AUDIT_ACTOR_LABELS: Record<string, string> = {
  "local-user": "当前本机用户",
  worker: "后台执行器",
  "local-llm": "本机语言模型",
  system: "系统",
};

function actionLabel(value: string): string {
  return AUDIT_ACTION_LABELS[value] ?? "其他系统操作";
}

function subjectLabel(value: string): string {
  return AUDIT_SUBJECT_LABELS[value] ?? "其他系统对象";
}

function actorLabel(value: string): string {
  return AUDIT_ACTOR_LABELS[value] ?? "系统组件";
}

function uniqueOptions(values: string[], labeler: (value: string) => string): string[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const label = labeler(value);
    if (seen.has(label)) return false;
    seen.add(label);
    return true;
  });
}

function normalizeDate(value: string): Date | null {
  const withTime = value.includes("T") ? value : value.replace(" ", "T");
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(withTime) ? withTime : `${withTime}Z`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.valueOf()) ? null : parsed;
}

function formatDate(value: string): string {
  const parsed = normalizeDate(value);
  if (!parsed) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function eventTitle(item: AuditEvent): string {
  return item.summary?.trim() || actionLabel(item.action);
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
  const [proofError, setProofError] = useState("");

  const filters = useMemo<AuditEventFilters>(() => ({
    project_id: projectId || undefined,
    action: action || undefined,
    actor: actor || undefined,
    subject_type: subjectType || undefined,
    subject_id: subjectId || undefined,
    occurred_after: occurredAfter ? new Date(occurredAfter).toISOString() : undefined,
    occurred_before: occurredBefore ? new Date(occurredBefore).toISOString() : undefined,
    cursor,
    limit: 50,
  }), [action, actor, cursor, occurredAfter, occurredBefore, projectId, subjectId, subjectType]);

  const history = useQuery({
    queryKey: queryKeys.audit.eventList(filters),
    queryFn: () => listAuditEvents(filters),
    staleTime: 5000,
  });

  const resetCursorAndProof = () => {
    setCursor(0);
    setProof(null);
    setProofError("");
  };

  const clearFilters = () => {
    setAction("");
    setActor("");
    setSubjectType("");
    setSubjectId("");
    setOccurredAfter("");
    setOccurredBefore("");
    resetCursorAndProof();
  };

  const generateProof = async () => {
    setProofBusy(true);
    setProofError("");
    try {
      setProof(await getAuditProof(filters, 1000));
    } catch (error) {
      setProof(null);
      setProofError(error instanceof Error ? error.message : String(error));
    } finally {
      setProofBusy(false);
    }
  };

  const items = history.data?.items ?? [];
  const actionOptions = uniqueOptions([...AUDIT_ACTION_SUGGESTIONS, ...items.map((item) => item.action)], actionLabel);
  const subjectOptions = uniqueOptions([...AUDIT_SUBJECT_SUGGESTIONS, ...items.map((item) => item.subject_type)], subjectLabel);
  const actorOptions = uniqueOptions(["local-user", "worker", "local-llm", "system", ...items.map((item) => item.actor)], actorLabel);
  const subjectItems = subjectType
    ? [...new Map(items.filter((item) => item.subject_type === subjectType).map((item) => [item.subject_id, item])).values()]
    : [];
  const hasAdvancedFilters = Boolean(actor || subjectType || subjectId || occurredAfter || occurredBefore);

  return (
    <section className="panel audit-history-panel" aria-labelledby="audit-history-title">
      <div className="panel-heading">
        <div><p className="eyebrow">不可改写的本机记录</p><h3 id="audit-history-title">操作记录</h3></div>
        <span className="status-pill neutral">只读 · 已脱敏</span>
      </div>
      <p className="muted">用于回答“谁在什么时候做了什么”。技术标识和哈希证明仅在核查时展开。</p>

      <div className="audit-toolbar" role="search" aria-label="操作记录筛选">
        <label>
          操作类型
          <select value={action} onChange={(event) => { setAction(event.target.value); resetCursorAndProof(); }}>
            <option value="">全部操作</option>
            {actionOptions.map((value) => <option key={value} value={value}>{actionLabel(value)}</option>)}
          </select>
        </label>
        <details className="audit-advanced-filters" open={hasAdvancedFilters}>
          <summary>更多筛选{hasAdvancedFilters ? "（已启用）" : ""}</summary>
          <div className="audit-filters">
            <label>对象类型<select value={subjectType} onChange={(event) => { setSubjectType(event.target.value); setSubjectId(""); resetCursorAndProof(); }}><option value="">全部对象</option>{subjectOptions.map((value) => <option key={value} value={value}>{subjectLabel(value)}</option>)}</select></label>
            <label>具体对象<select value={subjectId} onChange={(event) => { setSubjectId(event.target.value); resetCursorAndProof(); }} disabled={!subjectType}><option value="">该类型的全部记录</option>{subjectItems.map((item) => <option key={item.subject_id} value={item.subject_id}>{eventTitle(item)}</option>)}</select></label>
            <label>操作者<select value={actor} onChange={(event) => { setActor(event.target.value); resetCursorAndProof(); }}><option value="">全部操作者</option>{actorOptions.map((value) => <option key={value} value={value}>{actorLabel(value)}</option>)}</select></label>
            <label>起始时间<input type="datetime-local" value={occurredAfter} onChange={(event) => { setOccurredAfter(event.target.value); resetCursorAndProof(); }} /></label>
            <label>结束时间<input type="datetime-local" value={occurredBefore} onChange={(event) => { setOccurredBefore(event.target.value); resetCursorAndProof(); }} /></label>
          </div>
        </details>
        {(action || hasAdvancedFilters) ? <button className="secondary" type="button" onClick={clearFilters}>清除筛选</button> : null}
        <button className="secondary audit-proof-action" type="button" onClick={() => void generateProof()} disabled={proofBusy}>{proofBusy ? "正在生成…" : "生成哈希证明"}</button>
      </div>

      {proofError ? <div className="inline-error" role="alert"><strong>哈希证明生成失败</strong><span>{proofError}</span></div> : null}
      {proof ? (
        <details className="audit-proof">
          <summary>哈希证明已生成 · {proof.event_count} 条记录</summary>
          <div><span>{proof.truncated ? "达到 1000 条上限，结果已截断" : "覆盖当前完整筛选结果"}</span><code>{proof.chain_sha256}</code></div>
        </details>
      ) : null}

      {history.isPending ? (
        <p className="empty-state">正在读取本机操作记录…</p>
      ) : history.error ? (
        <div className="inline-error" role="alert"><strong>操作记录读取失败</strong><span>{history.error instanceof Error ? history.error.message : String(history.error)}</span></div>
      ) : items.length ? (
        <>
          <ol className="audit-event-list" aria-label="操作记录列表">
            {items.map((item) => (
              <li key={item.event_id}>
                <article className="audit-event">
                  <time dateTime={item.occurred_at}>{formatDate(item.occurred_at)}</time>
                  <div className="audit-event__copy">
                    <strong>{eventTitle(item)}</strong>
                    <p>{actionLabel(item.action)} · {subjectLabel(item.subject_type)} · {actorLabel(item.actor)}</p>
                  </div>
                  <details className="audit-event__details">
                    <summary>技术详情</summary>
                    <dl>
                      <div><dt>事件</dt><dd>#{item.event_id}</dd></div>
                      <div><dt>操作代码</dt><dd><code>{item.action}</code></dd></div>
                      <div><dt>对象标识</dt><dd><code>{item.subject_id}</code></dd></div>
                      <div><dt>操作者标识</dt><dd><code>{item.actor}</code></dd></div>
                    </dl>
                    <pre>{JSON.stringify(item.metadata, null, 2)}</pre>
                  </details>
                </article>
              </li>
            ))}
          </ol>
          <div className="audit-pagination">
            <span className="muted">本页 {items.length} 条，按时间从新到旧</span>
            <div>
              {cursor > 0 ? <button className="secondary" type="button" onClick={() => setCursor(0)} disabled={history.isFetching}>回到最新</button> : null}
              <button className="secondary" type="button" onClick={() => setCursor(history.data?.next_cursor ?? 0)} disabled={!history.data?.next_cursor || history.isFetching}>更早记录</button>
            </div>
          </div>
        </>
      ) : (
        <p className="empty-state">当前范围内没有操作记录。</p>
      )}
    </section>
  );
}

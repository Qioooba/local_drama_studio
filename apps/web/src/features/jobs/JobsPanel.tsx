import { useEffect, useState } from "react";
import { ConceptGuide, Drawer } from "../../components/ui";
import { cancelJob, cloneJob, deleteJob, retryJob, type CapacitySnapshot, type Job } from "../../generated/api";
import { progressiveSlice } from "../shared/progressive";
import { JOB_CHANNEL_LABELS, JOB_PHASE_LABELS, JOB_TYPE_LABELS, WEBHOOK_STATUS_LABELS, statusLabel, userFacingLabel } from "../shared/optionLabels";
import { JobDetailsPanel } from "./JobDetailsPanel";

const LIST_STEP = 12;

function jobProgress(job: Job) {
  const progress = (job.progress ?? {}) as Record<string, unknown>;
  const phase = String(progress.phase ?? job.state ?? "UNKNOWN");
  const numeric = Number(progress.percent);
  const normalized = numeric > 0 && numeric <= 1 ? numeric * 100 : numeric;
  const percent = Number.isFinite(numeric) ? Math.max(0, Math.min(100, Math.round(normalized))) : null;
  if (job.state === "SUCCEEDED") return { phase: "已完成", percent: 100 };
  if (["FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"].includes(job.state)) return { phase: statusLabel(job.state), percent: null };
  return { phase: userFacingLabel(JOB_PHASE_LABELS, phase, "正在处理"), percent };
}

export function JobsPanel({ jobs, loading, onChanged, focusJobId, onFocusJob, scopeKey = "all", capacity, projectTitles = {}, showProjectScope = false }: { jobs: Job[]; loading: boolean; onChanged?: () => void; focusJobId?: string | null; onFocusJob?: (jobId: string | null) => void; scopeKey?: string; capacity?: CapacitySnapshot; projectTitles?: Record<string, string>; showProjectScope?: boolean }) {
  const [visibleCount, setVisibleCount] = useState(LIST_STEP);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailJobId, setDetailJobId] = useState<string | null>(null);
  const [workerCommandCopied, setWorkerCommandCopied] = useState(false);
  useEffect(() => { setVisibleCount(LIST_STEP); setDetailJobId(null); }, [scopeKey]);
  useEffect(() => { if (focusJobId) setDetailJobId(focusJobId); }, [focusJobId]);
  const validJobs = jobs.filter((job) => Boolean(job?.id)).sort((left, right) => Number(right.id === focusJobId) - Number(left.id === focusJobId));
  const visibleJobs = progressiveSlice(validJobs, visibleCount);
  const workerUnavailable = Boolean(capacity && capacity.queued_count > 0 && capacity.active_worker_count === 0);
  const copyWorkerCommand = async () => {
    try {
      await navigator.clipboard.writeText(".\\scripts\\start-worker.ps1");
      setWorkerCommandCopied(true);
    } catch {
      setWorkerCommandCopied(false);
    }
  };
  const mutate = async (job: Job, action: "cancel" | "retry" | "clone" | "delete") => {
    if (action === "delete" && !window.confirm("确定删除这条任务记录吗？它会从任务列表移除，但已经生成并被项目引用的素材不会删除。")) return;
    setBusy(`${action}:${job.id}`); setError(null);
    try { if (action === "cancel") await cancelJob(job.id); else if (action === "retry") await retryJob(job.id); else if (action === "clone") await cloneJob(job.id); else await deleteJob(job.id); onChanged?.(); }
    catch (caught) { setError(`任务操作失败：${String(caught)}`); }
    finally { setBusy(null); }
  };
  const openDetails = (jobId: string) => {
    setDetailJobId(jobId);
    onFocusJob?.(jobId);
  };
  const closeDetails = () => {
    setDetailJobId(null);
    onFocusJob?.(null);
  };
  return <section className="panel"><div className="panel-heading"><div><p className="eyebrow">任务与机器</p><h3>后台任务队列与本机处理服务</h3></div><span className="status-pill neutral">每 1.5 秒自动刷新</span></div><p className="muted">页面关闭后任务仍会继续；后台处理服务重启后会自动接管过期执行。<strong>“故障重试”继续原任务；想更换提示词或随机结果，请去生成工作台新建候选。</strong>历史输入和产物不会被覆盖。</p><ConceptGuide title="任务页名词说明" items={[{ term: "后台任务", description: "一次可恢复的处理工作，例如生成镜头、制作缩略图或合成交付包。" }, { term: "执行记录", description: "同一任务每次开始处理都会留下独立记录，方便追查失败原因。" }, { term: "自动恢复", description: "处理服务重启后会自动识别过期执行，无需手动扫描。" }]} />{workerUnavailable && <div className="review-guidance" role="status"><strong>已有 {capacity?.queued_count} 个任务排队，但后台任务服务未运行。</strong><p>任务不会丢失。请在项目目录打开 PowerShell，运行 <code>.\scripts\start-worker.ps1</code>；状态变为“已常驻”后会自动继续。</p><button type="button" className="secondary" onClick={() => void copyWorkerCommand()}>{workerCommandCopied ? "启动命令已复制" : "复制后台服务启动命令"}</button></div>}{loading ? <p className="empty-state">正在读取任务…</p> : validJobs.length === 0 ? <p className="empty-state">当前筛选范围没有任务。</p> : <><div className="job-list job-list--bounded" aria-label="后台任务列表">{visibleJobs.map((job) => { const isGeneration = job.type === "GENERATION_VARIANT"; const state = String(job.state ?? "UNKNOWN"); const progress = jobProgress(job); const canDelete = ["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"].includes(state); const taskLabel = userFacingLabel(JOB_TYPE_LABELS, job.type, "其他后台任务"); const projectLabel = projectTitles[String(job.project_id ?? "")] ?? (job.project_id ? `项目 ${String(job.project_id).slice(0, 8)}` : "非项目任务"); return <div className="job-row progressive-row" key={job.id}><strong>{taskLabel}<small>{job.type && !JOB_TYPE_LABELS[job.type] ? "（新任务类型）" : ""}</small></strong><span>{showProjectScope ? `${projectLabel} · ` : ""}{userFacingLabel(JOB_CHANNEL_LABELS, job.channel, "默认处理通道")}</span><span className={`status-pill state-${state.toLowerCase()}`}>{statusLabel(state)}</span><div className="job-progress-summary"><small>{progress.phase}{progress.percent === null ? "" : ` · ${progress.percent}%`} · 优先级 {job.priority ?? "—"} · 版本 {job.revision ?? "—"}</small>{progress.percent !== null && <progress max={100} value={progress.percent} aria-label={`${taskLabel}进度 ${progress.percent}%`} />}</div><div className="job-actions"><button type="button" className="secondary" aria-haspopup="dialog" onClick={() => openDetails(job.id)}>查看详情和产物</button><button type="button" className="secondary" onClick={() => void mutate(job, "cancel")} disabled={busy !== null || !["QUEUED", "RUNNING", "CLAIMED", "WAITING"].includes(job.state)}>{busy === `cancel:${job.id}` ? "取消中…" : "取消"}</button><button type="button" className="secondary" title="继续原任务，不创建新的创作候选" onClick={() => void mutate(job, "retry")} disabled={busy !== null || !["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(job.state)}>{busy === `retry:${job.id}` ? "故障重试中…" : "重试原任务"}</button>{!isGeneration ? <button type="button" className="secondary" title="复制输入并创建新任务，不改变原任务" onClick={() => void mutate(job, "clone")} disabled={busy !== null}>{busy === `clone:${job.id}` ? "复制中…" : "复制为新任务"}</button> : <span className="muted" title="前往生成工作台创建不同的创作候选">换一版 → 生成工作台</span>}{canDelete ? <button type="button" className="secondary danger-outline" onClick={() => void mutate(job, "delete")} disabled={busy !== null}>{busy === `delete:${job.id}` ? "删除中…" : "删除记录"}</button> : null}</div></div>; })}</div>{visibleJobs.length < validJobs.length && <button type="button" className="secondary list-more" onClick={() => setVisibleCount((count) => count + LIST_STEP)}>继续显示任务（{visibleJobs.length}/{validJobs.length}）</button>}</>}{error && <p className="inline-error" role="alert">{error}</p>}<Drawer open={Boolean(detailJobId)} title="任务详情与产物" placement="right" width={560} onClose={closeDetails}><JobDetailsPanel jobId={detailJobId} onChanged={onChanged} /></Drawer></section>;
}

export function CapacitySnapshotPanel({ snapshot }: { snapshot?: CapacitySnapshot }) {
  if (!snapshot) return <section className="panel"><p className="empty-state">正在读取真实队列产能快照…</p></section>;
  const extended = snapshot as CapacitySnapshot & { duration_seconds?: { average?: number | null; max?: number | null }; failure_rate?: number | null; retry_rate?: number | null; review?: { approval_rate?: number | null }; disk?: { free_bytes?: number | null }; gpu?: { name?: string | null; driver?: string | null } };
  return <section className="panel capacity-panel" aria-labelledby="capacity-title">
    <div className="panel-heading"><div><p className="eyebrow">产能观测</p><h3 id="capacity-title">本机队列产能快照</h3></div><span className="status-pill neutral">只读 · 未基准测试</span></div>
    <p className="muted">这里只统计已保存到本机任务库的真实任务与执行记录；外部事件通知默认关闭，不会因此创建任务或抢占处理资源。下列数字用于观察当前负载，不代表性能基准测试。</p>
    <div className="configuration-grid capacity-grid">
      <div className="configuration-card"><small>排队</small><strong>{snapshot.queued_count}</strong><span>{snapshot.oldest_queued_age_seconds === null ? "暂无排队" : `最老 ${snapshot.oldest_queued_age_seconds}s`}</span></div>
      <div className="configuration-card"><small>正在执行</small><strong>{snapshot.active_attempt_count}</strong><span>{snapshot.active_worker_count} 个后台处理服务</span></div>
      <div className="configuration-card"><small>显卡生成占用</small><strong>{snapshot.gpu_active_count}/{snapshot.gpu_concurrency_limit}</strong><span>当前任务 / 本机并发上限</span></div>
      <div className="configuration-card"><small>近 24 小时完成</small><strong>{snapshot.completed_last_24h}</strong><span>仅用于负载观察</span></div>
      <div className="configuration-card"><small>耗时 / 失败</small><strong>{extended.duration_seconds?.average == null ? "—" : `${extended.duration_seconds.average}s`}</strong><span>失败率 {extended.failure_rate == null ? "—" : `${Math.round(extended.failure_rate * 100)}%`}</span></div>
      <div className="configuration-card"><small>重试 / 审核通过</small><strong>{extended.retry_rate == null ? "—" : `${Math.round(extended.retry_rate * 100)}%`}</strong><span>通过率 {extended.review?.approval_rate == null ? "—" : `${Math.round(extended.review.approval_rate * 100)}%`}</span></div>
      <div className="configuration-card"><small>可用磁盘 / 显卡</small><strong>{extended.disk?.free_bytes == null ? "—" : `${Math.round(extended.disk.free_bytes / 1024 / 1024 / 1024)} GB`}</strong><span>{extended.gpu?.name ?? "未登记显卡"}{extended.gpu?.driver ? ` · 驱动 ${extended.gpu.driver}` : ""}</span></div>
    </div>
    <div className="canvas-status"><span>外部事件通知：{userFacingLabel(WEBHOOK_STATUS_LABELS, snapshot.webhook_status, "使用自定义设置")}</span><span>只读观察，不会启动任务</span></div>
  </section>;
}

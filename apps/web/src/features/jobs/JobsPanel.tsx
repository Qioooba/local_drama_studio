import { useEffect, useState } from "react";
import { Drawer } from "../../components/ui";
import { cancelJob, cloneJob, reconcileJobs, retryJob, type CapacitySnapshot, type Job } from "../../generated/api";
import { progressiveSlice } from "../shared/progressive";
import { JobDetailsPanel } from "./JobDetailsPanel";

const LIST_STEP = 12;

function jobProgress(job: Job) {
  const progress = (job.progress ?? {}) as Record<string, unknown>;
  const phase = String(progress.phase ?? job.state ?? "UNKNOWN");
  const numeric = Number(progress.percent);
  const percent = Number.isFinite(numeric) ? Math.max(0, Math.min(100, Math.round(numeric))) : null;
  if (job.state === "SUCCEEDED") return { phase: "已完成", percent: 100 };
  if (["FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"].includes(job.state)) return { phase: job.state, percent: null };
  return { phase, percent };
}

export function JobsPanel({ jobs, loading, onChanged, focusJobId, onFocusJob, scopeKey = "all", capacity }: { jobs: Job[]; loading: boolean; onChanged?: () => void; focusJobId?: string | null; onFocusJob?: (jobId: string | null) => void; scopeKey?: string; capacity?: CapacitySnapshot }) {
  const [visibleCount, setVisibleCount] = useState(LIST_STEP);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailJobId, setDetailJobId] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [reconcileFeedback, setReconcileFeedback] = useState<string | null>(null);
  const [workerCommandCopied, setWorkerCommandCopied] = useState(false);
  useEffect(() => { setVisibleCount(LIST_STEP); }, [scopeKey]);
  useEffect(() => { if (focusJobId) setDetailJobId(focusJobId); }, [focusJobId]);
  useEffect(() => {
    if (!reconcileFeedback) return;
    const timer = window.setTimeout(() => setReconcileFeedback(null), 7000);
    return () => window.clearTimeout(timer);
  }, [reconcileFeedback]);
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
  const mutate = async (job: Job, action: "cancel" | "retry" | "clone") => {
    setBusy(`${action}:${job.id}`); setError(null);
    try { if (action === "cancel") await cancelJob(job.id); else if (action === "retry") await retryJob(job.id); else await cloneJob(job.id); onChanged?.(); }
    catch (caught) { setError(`任务操作失败：${String(caught)}`); }
    finally { setBusy(null); }
  };
  const reconcile = async () => {
    setReconciling(true);
    setError(null);
    setReconcileFeedback(null);
    try {
      const response = await reconcileJobs();
      const count = Number(response.result.reconciled ?? 0);
      setReconcileFeedback(count > 0 ? `过期租约扫描完成：已接管 ${count} 个 Attempt。` : "过期租约扫描完成：没有需要接管的 Attempt。");
      onChanged?.();
    } catch (caught) {
      setError(`恢复扫描失败：${String(caught)}`);
    } finally {
      setReconciling(false);
    }
  };
  const openDetails = (jobId: string) => {
    setDetailJobId(jobId);
    onFocusJob?.(jobId);
  };
  const closeDetails = () => {
    setDetailJobId(null);
    onFocusJob?.(null);
  };
  return <section className="panel"><div className="panel-heading"><div><p className="eyebrow">任务与机器</p><h3>持久任务队列与本地 Worker</h3></div><div className="action-row"><button className="secondary" type="button" onClick={() => void reconcile()} disabled={reconciling}>{reconciling ? "恢复扫描中…" : "扫描过期租约"}</button><span className="status-pill neutral">实时更新 / 发件箱</span></div></div><p className="muted">状态来自本机持久任务队列；页面关闭后队列继续运行，Worker 失联后可由恢复扫描接管。<strong>故障重试只恢复同一任务并新增尝试记录；创作重抽必须前往生成工作台创建新的候选与任务。</strong>历史输入和产物不会被覆盖。</p>{workerUnavailable && <div className="review-guidance" role="status"><strong>已有 {capacity?.queued_count} 个任务排队，但本机 Worker 未运行。</strong><p>任务不会丢失。请在项目目录打开 PowerShell，运行 <code>.\scripts\start-worker.ps1</code>，健康状态变为“已常驻”后会自动继续。</p><button type="button" className="secondary" onClick={() => void copyWorkerCommand()}>{workerCommandCopied ? "启动命令已复制" : "复制 Worker 启动命令"}</button></div>}{reconcileFeedback && <p className="review-success dismissible" role="status"><span>{reconcileFeedback}</span><button type="button" aria-label="关闭扫描结果" onClick={() => setReconcileFeedback(null)}>×</button></p>}{loading ? <p className="empty-state">正在读取任务…</p> : validJobs.length === 0 ? <p className="empty-state">当前项目没有任务。</p> : <><div className="job-list job-list--bounded" aria-label="持久任务列表">{visibleJobs.map((job) => { const isGeneration = job.type === "GENERATION_VARIANT"; const state = String(job.state ?? "UNKNOWN"); const progress = jobProgress(job); return <div className="job-row progressive-row" key={job.id}><strong>{String(job.type ?? "未知任务")}</strong><span>{String(job.channel ?? "—")}</span><span className={`status-pill state-${state.toLowerCase()}`}>{state}</span><div className="job-progress-summary"><small>{progress.phase}{progress.percent === null ? "" : ` · ${progress.percent}%`} · 优先级 {job.priority ?? "—"} · 修订 {job.revision ?? "—"}</small>{progress.percent !== null && <progress max={100} value={progress.percent} aria-label={`${String(job.type ?? "任务")}进度 ${progress.percent}%`} />}</div><div className="job-actions"><button type="button" className="secondary" aria-haspopup="dialog" onClick={() => openDetails(job.id)}>详情 · 产物</button><button type="button" className="secondary" onClick={() => void mutate(job, "cancel")} disabled={busy !== null || !["QUEUED", "RUNNING", "CLAIMED", "WAITING"].includes(job.state)}>{busy === `cancel:${job.id}` ? "取消中…" : "取消"}</button><button type="button" className="secondary" title="仅恢复同一任务；不会创建新的创作候选" onClick={() => void mutate(job, "retry")} disabled={busy !== null || !["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(job.state)}>{busy === `retry:${job.id}` ? "故障重试中…" : "故障重试（同一任务）"}</button>{!isGeneration ? <button type="button" className="secondary" title="复制任务输入并创建新任务，不改变原任务" onClick={() => void mutate(job, "clone")} disabled={busy !== null}>{busy === `clone:${job.id}` ? "复制中…" : "复制任务"}</button> : <span className="muted" title="创作重抽会在生成工作台创建新的候选与任务">创作重抽 → 生成工作台</span>}</div></div>; })}</div>{visibleJobs.length < validJobs.length && <button type="button" className="secondary list-more" onClick={() => setVisibleCount((count) => count + LIST_STEP)}>继续显示任务（{visibleJobs.length}/{validJobs.length}）</button>}</>}{error && <p className="inline-error" role="alert">{error}</p>}<Drawer open={Boolean(detailJobId)} title="任务详情与产物" placement="right" width={560} onClose={closeDetails}><JobDetailsPanel jobId={detailJobId} onChanged={onChanged} /></Drawer></section>;
}

export function CapacitySnapshotPanel({ snapshot }: { snapshot?: CapacitySnapshot }) {
  if (!snapshot) return <section className="panel"><p className="empty-state">正在读取真实队列产能快照…</p></section>;
  const extended = snapshot as CapacitySnapshot & { duration_seconds?: { average?: number | null; max?: number | null }; failure_rate?: number | null; retry_rate?: number | null; review?: { approval_rate?: number | null }; disk?: { free_bytes?: number | null }; gpu?: { name?: string | null; driver?: string | null } };
  return <section className="panel capacity-panel" aria-labelledby="capacity-title">
    <div className="panel-heading"><div><p className="eyebrow">产能观测</p><h3 id="capacity-title">本机队列产能快照</h3></div><span className="status-pill neutral">只读 · 未基准测试</span></div>
    <p className="muted">仅统计 SQLite 已持久化的真实 Job/Attempt；Webhook 仅支持显式 loopback 批量投递，默认不投递、不创建任务、不抢占 Worker。吞吐数字不是 benchmark。</p>
    <div className="configuration-grid capacity-grid">
      <div className="configuration-card"><small>排队</small><strong>{snapshot.queued_count}</strong><span>{snapshot.oldest_queued_age_seconds === null ? "暂无排队" : `最老 ${snapshot.oldest_queued_age_seconds}s`}</span></div>
      <div className="configuration-card"><small>活跃 Attempt</small><strong>{snapshot.active_attempt_count}</strong><span>{snapshot.active_worker_count} 个 Worker</span></div>
      <div className="configuration-card"><small>GPU_H3</small><strong>{snapshot.gpu_active_count}/{snapshot.gpu_concurrency_limit}</strong><span>并发上限来自本地策略</span></div>
      <div className="configuration-card"><small>近 24h 完成</small><strong>{snapshot.completed_last_24h}</strong><span>仅观测 · 非基准</span></div>
      <div className="configuration-card"><small>耗时 / 失败</small><strong>{extended.duration_seconds?.average == null ? "—" : `${extended.duration_seconds.average}s`}</strong><span>失败率 {extended.failure_rate == null ? "—" : `${Math.round(extended.failure_rate * 100)}%`}</span></div>
      <div className="configuration-card"><small>重试 / 审核通过</small><strong>{extended.retry_rate == null ? "—" : `${Math.round(extended.retry_rate * 100)}%`}</strong><span>通过率 {extended.review?.approval_rate == null ? "—" : `${Math.round(extended.review.approval_rate * 100)}%`}</span></div>
      <div className="configuration-card"><small>本机磁盘 / GPU</small><strong>{extended.disk?.free_bytes == null ? "—" : `${Math.round(extended.disk.free_bytes / 1024 / 1024 / 1024)} GB`}</strong><span>{extended.gpu?.name ?? "GPU 未登记"}{extended.gpu?.driver ? ` · 驱动 ${extended.gpu.driver}` : ""}</span></div>
    </div>
    <div className="canvas-status"><span>Webhook：{snapshot.webhook_status}</span><span>只读观测 · 未接触运行时</span></div>
  </section>;
}

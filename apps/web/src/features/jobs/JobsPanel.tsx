import { useEffect, useState } from "react";
import type { CapacitySnapshot, Job } from "../../generated/api";
import { INITIAL_LIST_WINDOW, progressiveSlice } from "../shared/progressive";

export function JobsPanel({ jobs, loading }: { jobs: Job[]; loading: boolean }) {
  const [visibleCount, setVisibleCount] = useState(INITIAL_LIST_WINDOW);
  useEffect(() => { setVisibleCount(INITIAL_LIST_WINDOW); }, [jobs]);
  const visibleJobs = progressiveSlice(jobs, visibleCount);
  return <section className="panel"><div className="panel-heading"><div><p className="eyebrow">G5 TASKS & MACHINES</p><h3>持久任务队列与本地 worker</h3></div><span className="status-pill">SSE / OUTBOX</span></div><p className="muted">状态来自 SQLite jobs/attempts/outbox；页面关闭后队列继续运行，worker lease 过期由 reconcile 接管。</p>{loading ? <p className="empty-state">正在读取任务…</p> : jobs.length === 0 ? <p className="empty-state">当前项目没有任务。</p> : <><div className="job-list">{visibleJobs.map((job) => <div className="job-row progressive-row" key={job.id}><strong>{job.type}</strong><span>{job.channel}</span><span className={job.state === "SUCCEEDED" ? "status-pill" : "blocker-text"}>{job.state}</span><small>priority {job.priority} · rev {job.revision}</small></div>)}</div>{visibleJobs.length < jobs.length && <button className="secondary list-more" onClick={() => setVisibleCount((count) => count + INITIAL_LIST_WINDOW)}>继续显示任务（{visibleJobs.length}/{jobs.length}）</button>}</>}</section>;
}

export function CapacitySnapshotPanel({ snapshot }: { snapshot?: CapacitySnapshot }) {
  if (!snapshot) return <section className="panel"><p className="empty-state">正在读取真实队列产能快照…</p></section>;
  return <section className="panel capacity-panel" aria-labelledby="capacity-title">
    <div className="panel-heading"><div><p className="eyebrow">G9 CAPACITY OBSERVATION</p><h3 id="capacity-title">本机队列产能快照</h3></div><span className="status-pill neutral">只读 · 未基准测试</span></div>
    <p className="muted">仅统计 SQLite 已持久化的真实 Job/Attempt；Webhook 仅支持显式 loopback 批量投递，默认不投递、不创建任务、不抢占 Worker。吞吐数字不是 benchmark。</p>
    <div className="configuration-grid capacity-grid">
      <div className="configuration-card"><small>排队</small><strong>{snapshot.queued_count}</strong><span>{snapshot.oldest_queued_age_seconds === null ? "暂无排队" : `最老 ${snapshot.oldest_queued_age_seconds}s`}</span></div>
      <div className="configuration-card"><small>活跃 Attempt</small><strong>{snapshot.active_attempt_count}</strong><span>{snapshot.active_worker_count} 个 Worker</span></div>
      <div className="configuration-card"><small>GPU_H3</small><strong>{snapshot.gpu_active_count}/{snapshot.gpu_concurrency_limit}</strong><span>并发上限来自本地策略</span></div>
      <div className="configuration-card"><small>近 24h 完成</small><strong>{snapshot.completed_last_24h}</strong><span>OBSERVED_NOT_BENCHMARKED</span></div>
    </div>
    <div className="canvas-status"><span>Webhook：{snapshot.webhook_status}</span><span>runtime_contacted=false</span><span>network_contacted=false</span><span>mutated=false</span></div>
  </section>;
}

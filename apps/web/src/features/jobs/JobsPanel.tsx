import { useEffect, useState } from "react";
import { ConceptGuide, Drawer } from "../../components/ui";
import {
  batchCancelJobs,
  batchDeleteJobs,
  batchPauseJobs,
  batchResumeJobs,
  batchRetryJobs,
  cancelJob,
  cloneJob,
  deleteJob,
  pauseJob,
  resumeJob,
  retryJob,
  type CapacitySnapshot,
  type Job,
} from "../../generated/api";
import { progressiveSlice } from "../shared/progressive";
import {
  JOB_CHANNEL_LABELS,
  JOB_PHASE_LABELS,
  JOB_TYPE_LABELS,
  WEBHOOK_STATUS_LABELS,
  statusLabel,
  userFacingLabel,
} from "../shared/optionLabels";
import { JobDetailsPanel } from "./JobDetailsPanel";
import "./jobs-panel.css";

const LIST_STEP = 12;

function compareJobsBySubmission(left: Job, right: Job): number {
  const leftCreatedAt = Date.parse(String((left as Job & { created_at?: string | null }).created_at ?? ""));
  const rightCreatedAt = Date.parse(String((right as Job & { created_at?: string | null }).created_at ?? ""));
  if (Number.isFinite(leftCreatedAt) && Number.isFinite(rightCreatedAt) && leftCreatedAt !== rightCreatedAt) return rightCreatedAt - leftCreatedAt;
  if (Number.isFinite(leftCreatedAt) !== Number.isFinite(rightCreatedAt)) return Number.isFinite(rightCreatedAt) ? 1 : -1;
  const leftId = String(left.id);
  const rightId = String(right.id);
  return rightId > leftId ? 1 : rightId < leftId ? -1 : 0;
}

function stableJobs(jobs: Job[]): Job[] {
  const byId = new Map<string, Job>();
  for (const job of jobs) {
    if (job?.id) byId.set(String(job.id), job);
  }
  return [...byId.values()].sort(compareJobsBySubmission);
}

function jobProgress(job: Job) {
  const progress = (job.progress ?? {}) as Record<string, unknown>;
  const phase = String(progress.phase ?? job.state ?? "UNKNOWN");
  const numeric = Number(progress.percent);
  const normalized = numeric > 0 && numeric <= 1 ? numeric * 100 : numeric;
  const percent = Number.isFinite(numeric) ? Math.max(0, Math.min(100, Math.round(normalized))) : null;
  if (job.state === "SUCCEEDED") return { phase: "已完成", percent: 100 };
  if (job.state === "PAUSED") return { phase: "已暂停", percent };
  if (["FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"].includes(job.state)) return { phase: statusLabel(job.state), percent: null };
  return { phase: userFacingLabel(JOB_PHASE_LABELS, phase, "正在处理"), percent };
}

function WaitingForExecutorNotice({ queuedCount }: { queuedCount: number }) {
  return <div className="review-guidance" role="status">
    <strong>已有 {queuedCount} 个任务等待本机执行器恢复。</strong>
    <p>任务已安全保留；本机服务恢复后会自动接管。若状态持续未恢复，请在任务详情查看可操作诊断，无需运行命令。</p>
  </div>;
}

export function JobsPanel({
  jobs,
  loading,
  onChanged,
  focusJobId,
  onFocusJob,
  scopeKey = "all",
  capacity,
  projectTitles = {},
  showProjectScope = false,
}: {
  jobs: Job[];
  loading: boolean;
  onChanged?: () => void;
  focusJobId?: string | null;
  onFocusJob?: (jobId: string | null) => void;
  scopeKey?: string;
  capacity?: CapacitySnapshot;
  projectTitles?: Record<string, string>;
  showProjectScope?: boolean;
}) {
  const [visibleCount, setVisibleCount] = useState(LIST_STEP);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailJobId, setDetailJobId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    setVisibleCount(LIST_STEP);
    setDetailJobId(null);
    setSelectedIds(new Set());
  }, [scopeKey]);

  useEffect(() => {
    if (focusJobId) setDetailJobId(focusJobId);
  }, [focusJobId]);

  // Infinite-query pages are refreshed independently; dedupe and sort from the
  // persisted submission timestamp so a refresh cannot move an open row to a
  // different nth position. Focusing a job must not reorder the queue.
  const validJobs = stableJobs(jobs);
  const visibleJobs = progressiveSlice(validJobs, visibleCount);
  const workerUnavailable = Boolean(capacity && capacity.queued_count > 0 && capacity.active_worker_count === 0);

  // Computed candidate lists for global actions
  const pausableJobs = validJobs.filter((j) => ["QUEUED", "RUNNING", "CLAIMED", "WAITING"].includes(j.state));
  const resumableJobs = validJobs.filter((j) => ["PAUSED", "FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(j.state));
  const cancellableJobs = validJobs.filter((j) => ["QUEUED", "RUNNING", "CLAIMED", "WAITING", "PAUSED"].includes(j.state));

  // Computed candidate lists for selected items
  const selectedList = validJobs.filter((j) => selectedIds.has(String(j.id)));
  const selectedPausable = selectedList.filter((j) => ["QUEUED", "RUNNING", "CLAIMED", "WAITING"].includes(j.state));
  const selectedResumable = selectedList.filter((j) => ["PAUSED", "FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(j.state));
  const selectedCancellable = selectedList.filter((j) => ["QUEUED", "RUNNING", "CLAIMED", "WAITING", "PAUSED"].includes(j.state));
  const selectedRetryable = selectedList.filter((j) => ["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(j.state));
  const selectedDeletable = selectedList.filter((j) => ["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED", "PAUSED"].includes(j.state));

  const mutate = async (job: Job, action: "cancel" | "retry" | "clone" | "delete" | "pause" | "resume") => {
    if (action === "delete" && !window.confirm("确定删除这条任务记录吗？它会从任务列表移除，但已经生成并被项目引用的素材不会删除。")) return;
    setBusy(`${action}:${job.id}`);
    setError(null);
    try {
      if (action === "cancel") await cancelJob(job.id);
      else if (action === "pause") await pauseJob(job.id);
      else if (action === "resume") await resumeJob(job.id);
      else if (action === "retry") await retryJob(job.id);
      else if (action === "clone") await cloneJob(job.id);
      else await deleteJob(job.id);
      onChanged?.();
    } catch (caught) {
      setError(`任务操作失败：${String(caught)}`);
    } finally {
      setBusy(null);
    }
  };

  const handleGlobalResume = async () => {
    setBusy("global:resume");
    setError(null);
    try {
      const scopeProjectId = scopeKey !== "all" ? scopeKey : undefined;
      await batchResumeJobs({ project_id: scopeProjectId });
      onChanged?.();
    } catch (caught) {
      setError(`一键开始失败：${String(caught)}`);
    } finally {
      setBusy(null);
    }
  };

  const handleGlobalPause = async () => {
    setBusy("global:pause");
    setError(null);
    try {
      const scopeProjectId = scopeKey !== "all" ? scopeKey : undefined;
      await batchPauseJobs({ project_id: scopeProjectId });
      onChanged?.();
    } catch (caught) {
      setError(`一键暂停失败：${String(caught)}`);
    } finally {
      setBusy(null);
    }
  };

  const handleGlobalCancel = async () => {
    if (!window.confirm("确定取消当前范围的所有进行中与排队任务吗？")) return;
    setBusy("global:cancel");
    setError(null);
    try {
      const scopeProjectId = scopeKey !== "all" ? scopeKey : undefined;
      await batchCancelJobs({ project_id: scopeProjectId });
      onChanged?.();
    } catch (caught) {
      setError(`一键取消失败：${String(caught)}`);
    } finally {
      setBusy(null);
    }
  };

  const toggleSelect = (jobId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  };

  const toggleSelectAll = () => {
    const visibleIds = visibleJobs.map((j) => String(j.id));
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(visibleIds));
    }
  };

  const handleBatchAction = async (action: "pause" | "resume" | "cancel" | "retry" | "delete") => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    if (action === "cancel" && !window.confirm(`确定取消所选的 ${ids.length} 个任务吗？`)) return;
    if (action === "delete" && !window.confirm(`确定删除所选的 ${ids.length} 条任务记录吗？`)) return;

    setBusy(`batch:${action}`);
    setError(null);
    try {
      if (action === "pause") await batchPauseJobs({ job_ids: ids });
      else if (action === "resume") await batchResumeJobs({ job_ids: ids });
      else if (action === "cancel") await batchCancelJobs({ job_ids: ids });
      else if (action === "retry") await batchRetryJobs({ job_ids: ids });
      else if (action === "delete") await batchDeleteJobs({ job_ids: ids });
      setSelectedIds(new Set());
      onChanged?.();
    } catch (caught) {
      setError(`批量操作失败：${String(caught)}`);
    } finally {
      setBusy(null);
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

  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">任务与机器</p>
          <h3>后台任务队列与本机执行器</h3>
        </div>
        <span className="status-pill neutral">每 1.5 秒自动刷新</span>
      </div>
      <p className="muted">
        页面关闭后任务仍会继续；本机执行器恢复后会自动接管过期执行。<strong>“故障重试”继续原任务；想更换提示词或随机结果，请去生成工作台新建候选。</strong>历史输入和产物不会被覆盖。
      </p>
      <ConceptGuide
        title="任务页名词说明"
        items={[
          { term: "后台任务", description: "一次可恢复的处理工作，例如生成镜头、制作缩略图或合成交付包。" },
          { term: "执行记录", description: "同一任务每次开始处理都会留下独立记录，方便追查失败原因。" },
          { term: "自动恢复", description: "本机执行器恢复后会自动识别过期执行，无需手动扫描。" },
        ]}
      />
      {workerUnavailable ? <WaitingForExecutorNotice queuedCount={capacity?.queued_count ?? 0} /> : null}

      <div className="job-toolbar" role="toolbar" aria-label="任务队列全局控制">
        <div className="job-global-actions" role="group" aria-label="一键操作">
          <button
            type="button"
            className="secondary btn-start"
            title="开始或恢复当前范围的所有已暂停与失败任务"
            onClick={() => void handleGlobalResume()}
            disabled={busy !== null || resumableJobs.length === 0}
          >
            {busy === "global:resume" ? "正在全部开始…" : `一键开始 (${resumableJobs.length})`}
          </button>
          <button
            type="button"
            className="secondary btn-pause"
            title="暂停当前范围的所有排队中与运行中任务"
            onClick={() => void handleGlobalPause()}
            disabled={busy !== null || pausableJobs.length === 0}
          >
            {busy === "global:pause" ? "正在全部暂停…" : `一键暂停 (${pausableJobs.length})`}
          </button>
          <button
            type="button"
            className="secondary danger-outline"
            title="取消当前范围的所有活动任务"
            onClick={() => void handleGlobalCancel()}
            disabled={busy !== null || cancellableJobs.length === 0}
          >
            {busy === "global:cancel" ? "正在全部取消…" : `一键取消 (${cancellableJobs.length})`}
          </button>
        </div>
        {validJobs.length > 0 ? (
          <label className="job-select-all">
            <input
              type="checkbox"
              aria-label="全选当前显示任务"
              checked={visibleJobs.length > 0 && visibleJobs.every((j) => selectedIds.has(String(j.id)))}
              onChange={toggleSelectAll}
            />
            <span>全选显示项</span>
          </label>
        ) : null}
      </div>

      {selectedIds.size > 0 ? (
        <div className="job-batch-bar" role="group" aria-label="批量操作栏">
          <span>已选 <strong>{selectedIds.size}</strong> 项</span>
          <button
            type="button"
            className="secondary btn-start"
            onClick={() => void handleBatchAction("resume")}
            disabled={busy !== null || selectedResumable.length === 0}
            title="开始或恢复所选已暂停或失败的任务"
          >
            {busy === "batch:resume" ? "启动中…" : `多选开始 (${selectedResumable.length})`}
          </button>
          <button
            type="button"
            className="secondary btn-pause"
            onClick={() => void handleBatchAction("pause")}
            disabled={busy !== null || selectedPausable.length === 0}
            title="暂停所选排队中或运行中的任务"
          >
            {busy === "batch:pause" ? "暂停中…" : `多选暂停 (${selectedPausable.length})`}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => void handleBatchAction("cancel")}
            disabled={busy !== null || selectedCancellable.length === 0}
            title="取消所选活动任务"
          >
            {busy === "batch:cancel" ? "取消中…" : `多选取消 (${selectedCancellable.length})`}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => void handleBatchAction("retry")}
            disabled={busy !== null || selectedRetryable.length === 0}
            title="重试所选失败或异常任务"
          >
            {busy === "batch:retry" ? "重试中…" : `多选重试 (${selectedRetryable.length})`}
          </button>
          {selectedDeletable.length > 0 ? (
            <button
              type="button"
              className="secondary danger-outline"
              onClick={() => void handleBatchAction("delete")}
              disabled={busy !== null}
              title="删除所选已终结或已暂停任务记录"
            >
              {busy === "batch:delete" ? "删除中…" : `多选删除 (${selectedDeletable.length})`}
            </button>
          ) : null}
          <button
            type="button"
            className="secondary"
            onClick={() => setSelectedIds(new Set())}
            disabled={busy !== null}
          >
            取消选择
          </button>
        </div>
      ) : null}

      {loading ? <p className="empty-state">正在读取任务…</p> : null}
      {!loading && validJobs.length === 0 ? <p className="empty-state">当前筛选范围没有任务。</p> : null}
      {!loading && validJobs.length > 0 ? (
        <>
          <div className="job-list job-list--bounded" aria-label="后台任务列表">
            {visibleJobs.map((job) => {
              const isGeneration = job.type === "GENERATION_VARIANT";
              const state = String(job.state ?? "UNKNOWN");
              const progress = jobProgress(job);
              const isSelected = selectedIds.has(String(job.id));
              const isPaused = state === "PAUSED";
              const canPause = ["QUEUED", "RUNNING", "CLAIMED", "WAITING"].includes(state);
              const canResume = isPaused;
              const canCancel = ["QUEUED", "RUNNING", "CLAIMED", "WAITING", "PAUSED"].includes(state);
              const canRetry = ["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(state);
              const canDelete = ["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED", "PAUSED"].includes(state);
              const taskLabel = userFacingLabel(JOB_TYPE_LABELS, job.type, "其他后台任务");
              const projectLabel = projectTitles[String(job.project_id ?? "")] ?? (job.project_id ? `项目 ${String(job.project_id).slice(0, 8)}` : "非项目任务");

              return (
                <div className={`job-row progressive-row${isSelected ? " selected" : ""}`} key={job.id}>
                  <label className="job-row-checkbox">
                    <input
                      type="checkbox"
                      aria-label={`选择任务 ${taskLabel} ${job.id}`}
                      checked={isSelected}
                      onChange={() => toggleSelect(String(job.id))}
                    />
                  </label>
                  <strong>{taskLabel}<small>{job.type && !JOB_TYPE_LABELS[job.type] ? "（新任务类型）" : ""}</small></strong>
                  <span>{showProjectScope ? `${projectLabel} · ` : ""}{userFacingLabel(JOB_CHANNEL_LABELS, job.channel, "默认处理通道")}</span>
                  <span className={`status-pill state-${state.toLowerCase()}`}>{statusLabel(state)}</span>
                  <div className="job-progress-summary">
                    <small>{progress.phase}{progress.percent === null ? "" : ` · ${progress.percent}%`} · 优先级 {job.priority ?? "—"} · 版本 {job.revision ?? "—"}</small>
                    {progress.percent !== null ? <progress max={100} value={progress.percent} aria-label={`${taskLabel}进度 ${progress.percent}%`} /> : null}
                  </div>
                  <div className="job-actions">
                    <button type="button" className="secondary" aria-haspopup="dialog" onClick={() => openDetails(job.id)}>查看详情和产物</button>
                    {canResume ? (
                      <button type="button" className="secondary btn-start" onClick={() => void mutate(job, "resume")} disabled={busy !== null}>
                        {busy === `resume:${job.id}` ? "启动中…" : "继续/开始"}
                      </button>
                    ) : null}
                    {canPause ? (
                      <button type="button" className="secondary btn-pause" onClick={() => void mutate(job, "pause")} disabled={busy !== null}>
                        {busy === `pause:${job.id}` ? "暂停中…" : "暂停"}
                      </button>
                    ) : null}
                    <button type="button" className="secondary" onClick={() => void mutate(job, "cancel")} disabled={busy !== null || !canCancel}>
                      {busy === `cancel:${job.id}` ? "取消中…" : "取消"}
                    </button>
                    <button type="button" className="secondary" title="继续原任务，不创建新的创作候选" onClick={() => void mutate(job, "retry")} disabled={busy !== null || !canRetry}>
                      {busy === `retry:${job.id}` ? "故障重试中…" : "重试原任务"}
                    </button>
                    {!isGeneration ? (
                      <button type="button" className="secondary" title="复制输入并创建新任务，不改变原任务" onClick={() => void mutate(job, "clone")} disabled={busy !== null}>
                        {busy === `clone:${job.id}` ? "复制中…" : "复制为新任务"}
                      </button>
                    ) : (
                      <span className="muted" title="前往生成工作台创建不同的创作候选">换一版 → 生成工作台</span>
                    )}
                    {canDelete ? (
                      <button type="button" className="secondary danger-outline" onClick={() => void mutate(job, "delete")} disabled={busy !== null}>
                        {busy === `delete:${job.id}` ? "删除中…" : "删除记录"}
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
          {visibleJobs.length < validJobs.length ? (
            <button type="button" className="secondary list-more" onClick={() => setVisibleCount((count) => count + LIST_STEP)}>
              继续显示任务（{visibleJobs.length}/{validJobs.length}）
            </button>
          ) : null}
        </>
      ) : null}
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      <Drawer open={Boolean(detailJobId)} title="任务详情与产物" placement="right" width={560} onClose={closeDetails}>
        <JobDetailsPanel jobId={detailJobId} onChanged={onChanged} />
      </Drawer>
    </section>
  );
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

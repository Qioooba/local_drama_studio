import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import type { Job } from "../../generated/api";

type BreakdownModelOption = {
  profileVersionId: string;
  name: string;
};

type BreakdownJobMonitorProps = {
  projectId: string;
  jobs: Job[];
  models: BreakdownModelOption[];
  isPending: boolean;
  error: unknown;
  jobAction: string | null;
  onRefetch: () => void;
  onMutate: (job: Job, action: "cancel" | "retry" | "delete") => Promise<void>;
  onDraftReady?: (job: Job) => void;
};
const JOB_STATE_LABELS: Record<string, string> = { QUEUED: "已排队", CLAIMED: "已领取", RUNNING: "运行中", SUCCEEDED: "草稿已就绪", FAILED: "运行失败", CANCELLED: "已取消", NEEDS_ATTENTION: "需要处理", ORPHANED: "等待恢复" };
const JOB_PHASE_LABELS: Record<string, string> = { WAITING_FOR_WORKER: "等待后台服务", CALLING_LOCAL_LLM: "本地模型生成中", DRAFT_READY: "待审核草稿已保存", FAILED: "运行未完成" };
const JOB_ERROR_LABELS: Record<string, string> = { LOCAL_LLM_LOOPBACK_UNAVAILABLE: "本地模型服务暂不可用" };

export function BreakdownJobMonitor({
  projectId,
  jobs,
  models,
  isPending,
  error,
  jobAction,
  onRefetch,
  onMutate,
  onDraftReady,
}: BreakdownJobMonitorProps) {
  return (
    <section className="breakdown-job-monitor" aria-labelledby="breakdown-job-monitor-title">
      <div className="section-title">
        <span id="breakdown-job-monitor-title">AI 拆解任务进度</span>
        <small>刷新恢复 · 取消 · 失败后显式重试</small>
      </div>
      <p className="muted">
        拆解会在 Windows 服务端后台持续运行，关闭客户端页面也不会丢失。若长时间没有进展，请到“任务与机器”查看并恢复运行环境。
      </p>
      {isPending ? <p className="empty-state">正在读取已持久化任务…</p> : null}
      {error ? (
        <div className="query-error-actions">
          <p className="inline-error" role="alert">任务读取失败：{String(error)}</p>
          <button type="button" className="secondary" onClick={onRefetch}>重新读取任务</button>
        </div>
      ) : null}
      {jobs.length ? (
        <div className="breakdown-job-list">
          {jobs.map((job) => {
            const state = String(job.state ?? "UNKNOWN");
            const progress = Number(job.progress?.percent ?? (state === "SUCCEEDED" ? 100 : 0));
            const boundedProgress = Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : 0;
            const phase = String(job.progress?.phase ?? (state === "QUEUED" ? "WAITING_FOR_WORKER" : state));
            const isLocalModelGenerating = state === "RUNNING" && phase === "CALLING_LOCAL_LLM";
            const canCancel = ["QUEUED", "CLAIMED", "RUNNING"].includes(state);
            const canRetry = ["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(state);
            const canDelete = ["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"].includes(state);
            const subjectSession = String(job.subject_id ?? "");
            const jobSnapshot = job.input_snapshot && typeof job.input_snapshot === "object"
              ? job.input_snapshot as Record<string, unknown>
              : {};
            const frozenProfileId = String(job.execution_profile_version_id ?? jobSnapshot.profile_version_id ?? "");
            const frozenModel = models.find((model) => model.profileVersionId === frozenProfileId);
            const frozenModelLabel = frozenModel?.name || String(jobSnapshot.model ?? "未知模型");
            return (
              <article className="breakdown-job-card" key={job.id} aria-label={`AI 拆解任务 ${job.id}`}>
                <div className="breakdown-job-heading">
                  <div>
                    <strong>{subjectSession ? `导入会话 ${subjectSession.slice(0, 12)}…` : "剧本拆解"}</strong>
                    <small>模型：{frozenModelLabel} · Job {job.id.slice(0, 12)}… · 失败重试沿用此模型</small>
                  </div>
                  <span className={`status-pill state-${state.toLowerCase()}`}>{JOB_STATE_LABELS[state] ?? "状态待确认"}</span>
                </div>
                <label className="breakdown-progress-label">
                  <span>{isLocalModelGenerating ? "本地模型正在生成（worker 心跳正常）" : JOB_PHASE_LABELS[phase] ?? (phase === state ? "等待下一步" : phase)}</span>
                  <span>{isLocalModelGenerating ? "运行中" : `${Math.round(boundedProgress)}%`}</span>
                  <progress
                    value={isLocalModelGenerating ? undefined : boundedProgress}
                    max={100}
                    aria-label={isLocalModelGenerating ? "AI 拆解正在由本地模型生成" : `AI 拆解进度 ${Math.round(boundedProgress)}%`}
                  />
                </label>
                {isLocalModelGenerating ? (
                  <p className="muted">模型生成阶段无法可靠换算百分比；页面每 3 秒检查持久化状态，进度条保持活动表示任务仍在运行。</p>
                ) : null}
                {job.last_error_code ? (
                  <p className="inline-error" role="alert">
                    {JOB_ERROR_LABELS[job.last_error_code] ?? "任务未完成"}：{String(job.last_error_detail_redacted ?? "请检查本地模型与后台服务后重试。")}
                  </p>
                ) : null}
                {state === "SUCCEEDED" ? (
                  <div className="frame-feedback success">
                    <p>草稿已生成，但尚未应用。请进入审核阶段选择目标分集并人工确认。</p>
                    {onDraftReady ? (
                      <button type="button" className="secondary" onClick={() => onDraftReady(job)}>打开审核阶段</button>
                    ) : <a href="#story-review">打开审核阶段</a>}
                  </div>
                ) : null}
                {state === "CANCEL_REQUESTED" ? (
                  <p className="muted">正在等待本地 llama.cpp 调用返回；取消会在草稿持久化前再次检查。</p>
                ) : null}
                <div className="breakdown-job-actions">
                  <Link className="secondary v2-inline-link" to={`${routes.systemJobs(projectId)}&job=${encodeURIComponent(job.id)}`}>查看任务详情</Link>
                  {canCancel ? (
                    <button type="button" className="secondary" disabled={jobAction !== null} onClick={() => void onMutate(job, "cancel")}>
                      {jobAction === `cancel:${job.id}` ? "取消中…" : "取消任务"}
                    </button>
                  ) : null}
                  {canRetry ? (
                    <button type="button" className="secondary" disabled={jobAction !== null} onClick={() => void onMutate(job, "retry")}>
                      {jobAction === `retry:${job.id}` ? "重新排队中…" : "失败重试"}
                    </button>
                  ) : null}
                  {canDelete ? (
                    <button type="button" className="secondary danger-outline" disabled={jobAction !== null} onClick={() => void onMutate(job, "delete")}>
                      {jobAction === `delete:${job.id}` ? "删除中…" : "删除记录"}
                    </button>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      ) : !isPending && !error ? (
        <p className="empty-state">当前项目还没有 AI 拆解任务。</p>
      ) : null}
    </section>
  );
}

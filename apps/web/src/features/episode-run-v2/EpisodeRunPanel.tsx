import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Drawer, TabPanel, Tabs, type TabItem } from "../../components/ui";
import {
  cancelEpisodeRun,
  getEpisodeRun,
  pauseEpisodeRun,
  preflightEpisodeRun,
  resumeEpisodeRun,
  startEpisodeRun,
  type EpisodeCheckpointPolicy,
  type EpisodeProductionMode,
  type EpisodeProductionRun,
  type EpisodeRunStage,
} from "./api";
import "./episode-run.css";
import { useProjectEventInvalidation } from "../events/useProjectEventInvalidation";

const RUN_STATUS: Record<string, string> = {
  PENDING: "等待开始",
  RUNNING: "自动生产中",
  PAUSED_HITL: "已暂停，等待你确认",
  SUCCEEDED: "整集生产完成",
  COMPLETED: "整集生产完成",
  FAILED: "生产遇到问题",
  CANCELLED: "已取消",
  CANCEL_REQUESTED: "正在安全停止",
};

const STAGE_STATUS: Record<EpisodeRunStage["status"], string> = {
  PENDING: "等待",
  RUNNING: "进行中",
  PAUSED: "已暂停",
  BLOCKED: "需处理",
  COMPLETED: "完成",
};

const PRODUCTION_MODES: Array<{ value: EpisodeProductionMode; label: string; detail: string; takes: string }> = [
  { value: "DRAFT", label: "草稿", detail: "快速验证叙事与节奏", takes: "每镜 1 个候选" },
  { value: "BALANCED", label: "平衡", detail: "兼顾选择空间与本机耗时", takes: "每镜 2 个候选" },
  { value: "QUALITY", label: "精品", detail: "正式制作，保留更多选择", takes: "每镜 4 个候选" },
];

const CHECKPOINT_POLICIES: Array<{ value: EpisodeCheckpointPolicy; label: string; detail: string }> = [
  { value: "ON_EXCEPTION", label: "遇到例外时停下", detail: "推荐。正常自动推进；失败、冲突或需人工判断时暂停。" },
  { value: "AFTER_ASSETS", label: "资产确认后停下", detail: "在进入镜头画面生产前，先确认资产与角色一致性。" },
  { value: "AFTER_SHOT_PLAN", label: "分镜确认后停下", detail: "在首个关键帧任务前，检查镜头规划与导演意图。" },
  { value: "BEFORE_VIDEO", label: "生成视频前停下", detail: "先完成并选择镜头画面，再由你确认是否进入视频生成。" },
  { value: "AUTO_CONTINUE", label: "全程自动推进", detail: "不设置计划暂停；遇到失败和硬性阻塞仍会安全停下。" },
];

function RunIcon({ name }: { name: "check" | "pause" | "play" | "stop" | "warning" | "refresh" }) {
  const paths = {
    check: <path d="m5 10 3 3 7-7" />,
    pause: <><path d="M7 5v10M13 5v10" /></>,
    play: <path d="m7 5 8 5-8 5z" />,
    stop: <rect x="6" y="6" width="8" height="8" rx="1" />,
    warning: <><path d="M10 3 3 16h14z" /><path d="M10 8v3.5M10 14h.01" /></>,
    refresh: <><path d="M15 7a6 6 0 1 0 .5 5" /><path d="M15 3v4h-4" /></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 20 20">{paths[name]}</svg>;
}

function StageCard({ stage, projectId, episodeId }: { stage: EpisodeRunStage; projectId: string; episodeId: string }) {
  const progress = stage.total > 0 ? Math.min(100, Math.round((stage.completed / stage.total) * 100)) : stage.status === "COMPLETED" ? 100 : 0;
  return (
    <li className={`episode-run-stage stage-${stage.status.toLowerCase()}`}>
      <div className="episode-stage-marker"><span>{stage.ordinal}</span><i aria-hidden="true" /></div>
      <div className="episode-stage-card">
        <div className="episode-stage-heading"><strong>{stage.label}</strong><span>{STAGE_STATUS[stage.status]}</span></div>
        <div className="episode-stage-progress" role="progressbar" aria-label={`${stage.label}进度`} aria-valuemin={0} aria-valuemax={stage.total || 1} aria-valuenow={stage.completed}>
          <i style={{ width: `${progress}%` }} />
        </div>
        <div className="episode-stage-meta">
          <span>已完成 {stage.completed}</span>
          <span>剩余 {stage.remaining_count}</span>
          <span>失败 {stage.failed}</span>
          {stage.needs_human_decision > 0 && <span>{stage.needs_human_decision} 项待人工决定</span>}
          {stage.status === "BLOCKED" && <Link to={`/projects/${projectId}/episodes/${episodeId}/direct?filter=failed`}>打开失败镜头</Link>}
        </div>
        <small className="episode-stage-estimate">{stage.estimate_status === "AVAILABLE" && stage.estimated_remaining_seconds !== null ? `预计剩余 ${Math.ceil(stage.estimated_remaining_seconds / 60)} 分钟` : "尚无本机估时"}</small>
        {stage.jobs && stage.jobs.length > 0 && <details className="episode-stage-jobs"><summary>查看 {stage.jobs.length} 个后台任务</summary><ul>{stage.jobs.map((job) => <li key={job.task_id}><code>{job.item_key}</code><span>{job.job_state ?? job.status}</span></li>)}</ul></details>}
      </div>
    </li>
  );
}

export function EpisodeRunPanel({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [stageEvidenceOpen, setStageEvidenceOpen] = useState(false);
  const runId = searchParams.get("run") ?? "";
  const ttsEnabled = searchParams.get("tts") !== "off";
  const requestedMode = searchParams.get("mode")?.toUpperCase();
  const productionMode: EpisodeProductionMode = requestedMode === "DRAFT" || requestedMode === "QUALITY" ? requestedMode : "BALANCED";
  const requestedCheckpoint = searchParams.get("checkpoint")?.toUpperCase();
  const checkpointPolicy = CHECKPOINT_POLICIES.some((item) => item.value === requestedCheckpoint)
    ? requestedCheckpoint as EpisodeCheckpointPolicy
    : "ON_EXCEPTION";
  const updateOption = (key: "tts" | "mode" | "checkpoint", value: string | null) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (value === null) next.delete(key);
      else next.set(key, value);
      next.delete("run");
      return next;
    }, { replace: true });
  };

  const preflight = useQuery({
    queryKey: ["episode-production-preflight", episodeId, ttsEnabled, productionMode, checkpointPolicy],
    queryFn: () => preflightEpisodeRun(episodeId, ttsEnabled, productionMode, checkpointPolicy),
    enabled: !runId,
  });
  const runQuery = useQuery({
    queryKey: ["episode-production-run", runId],
    queryFn: () => getEpisodeRun(runId),
    enabled: Boolean(runId),
  });
  useProjectEventInvalidation(
    projectId,
    ["JOB_QUEUED", "JOB_CLAIMED", "JOB_HEARTBEAT", "JOB_FINISHED", "JOB_REQUEUED", "JOB_RECONCILED", "JOB_CANCEL_REQUESTED", "ARTIFACT_REGISTERED"],
    runId ? [["episode-production-run", runId]] : [["episode-production-preflight", episodeId]],
  );

  const updateRun = (run: EpisodeProductionRun) => queryClient.setQueryData(["episode-production-run", run.id], run);
  const start = useMutation({
    mutationFn: () => startEpisodeRun(episodeId, ttsEnabled, productionMode, checkpointPolicy),
    onSuccess: (run) => {
      updateRun(run);
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("run", run.id);
        return next;
      }, { replace: true });
    },
  });
  const pause = useMutation({ mutationFn: () => pauseEpisodeRun(runId), onSuccess: updateRun });
  const resume = useMutation({ mutationFn: () => resumeEpisodeRun(runId), onSuccess: updateRun });
  const cancel = useMutation({ mutationFn: () => cancelEpisodeRun(runId), onSuccess: updateRun });
  const run = runQuery.data;
  const isPaused = run?.status === "PAUSED_HITL";
  const canPause = run?.status === "RUNNING";
  const isTerminal = Boolean(run && ["SUCCEEDED", "COMPLETED", "FAILED", "CANCELLED"].includes(run.status));
  const actionPending = pause.isPending || resume.isPending || cancel.isPending;
  const actionError = start.error ?? pause.error ?? resume.error ?? cancel.error;
  const requestedStage = searchParams.get("stage");
  const defaultStage = run?.stages.find((stage) => ["RUNNING", "PAUSED", "BLOCKED"].includes(stage.status)) ?? run?.stages[0] ?? null;
  const activeStage = run?.stages.find((stage) => stage.code === requestedStage) ?? defaultStage;
  const pendingGate = run?.pending_gate ?? null;
  const stageTabs: TabItem[] = (run?.stages ?? []).map((stage) => ({ id: stage.code, label: `${stage.ordinal} ${stage.label}`, badge: STAGE_STATUS[stage.status] }));
  const selectStage = (stageCode: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("stage", stageCode);
      return next;
    }, { replace: true });
  };

  if (runId && runQuery.isPending) return <section className="episode-run-loading" role="status">正在恢复整集生产视图…</section>;
  if (runId && runQuery.error) return <section className="episode-run-error" role="alert"><strong>无法读取这次整集生产</strong><span>{String(runQuery.error)}</span><button type="button" onClick={() => setSearchParams({}, { replace: true })}>返回生产预检</button></section>;

  return (
    <div className="episode-run-workspace">
      <header className="episode-run-hero">
        <div><p className="eyebrow">Episode Production Run</p><h2>{run ? RUN_STATUS[run.status] ?? run.status : "一键生产整集"}</h2><p>系统按八个创作阶段自动推进；你可以随时暂停，已完成的阶段不会丢失。</p></div>
        {run && <div className="episode-run-state-stack"><span className="episode-run-mode">{run.mode_policy?.label ?? run.production_mode} · {CHECKPOINT_POLICIES.find((item) => item.value === run.checkpoint_policy)?.label ?? run.checkpoint_policy}</span><span className={`episode-run-state state-${run.status.toLowerCase()}`}>{RUN_STATUS[run.status] ?? run.status}</span></div>}
      </header>

      {!run && <section className="episode-preflight" aria-labelledby="preflight-title">
        <div className="episode-section-heading"><div><p className="eyebrow">生产预检</p><h3 id="preflight-title">开始前确认创作输入与本机能力</h3></div><button type="button" className="episode-icon-button" aria-label="重新运行预检" onClick={() => void preflight.refetch()} disabled={preflight.isFetching}><RunIcon name="refresh" /></button></div>
        <fieldset className="episode-mode-picker"><legend>生产质量</legend><div>{PRODUCTION_MODES.map((mode) => <label key={mode.value} className={productionMode === mode.value ? "selected" : ""}><input type="radio" name="production-mode" value={mode.value} checked={productionMode === mode.value} onChange={() => updateOption("mode", mode.value === "BALANCED" ? null : mode.value.toLowerCase())} /><span><strong>{mode.label}</strong><small>{mode.detail}</small><em>{mode.takes}</em></span></label>)}</div></fieldset>
        <label className="episode-checkpoint-picker"><span><strong>人工确认节点</strong><small>{CHECKPOINT_POLICIES.find((item) => item.value === checkpointPolicy)?.detail}</small></span><select aria-label="人工确认节点" value={checkpointPolicy} onChange={(event) => updateOption("checkpoint", event.target.value === "ON_EXCEPTION" ? null : event.target.value.toLowerCase())}>{CHECKPOINT_POLICIES.map((policy) => <option key={policy.value} value={policy.value}>{policy.label}</option>)}</select></label>
        <label className="episode-run-option"><span><strong>自动生成人声</strong><small>关闭后跳过 TTS，不影响对白和字幕内容。</small></span><input type="checkbox" checked={ttsEnabled} onChange={(event) => updateOption("tts", event.target.checked ? null : "off")} /></label>
        {preflight.isPending && <p className="episode-preflight-pending" role="status">正在检查镜头、资产、模型和本机容量…</p>}
        {preflight.error && <p className="episode-inline-error" role="alert">{String(preflight.error)}</p>}
        {preflight.data && <>
          <div className={`episode-preflight-summary ${preflight.data.status.toLowerCase()}`}><RunIcon name={preflight.data.status === "PASS" ? "check" : "warning"} /><div><strong>{preflight.data.status === "PASS" ? "已具备整集生产条件" : `${preflight.data.blockers.length} 项需要先处理`}</strong><span>预检只读取本地状态，不创建任务、不联系外部网络。</span></div></div>
          {preflight.data.blockers.length > 0 && <ul className="episode-blocker-list">{preflight.data.blockers.map((blocker) => <li key={blocker.code}><RunIcon name="warning" /><div><strong>{blocker.label}</strong><span>{blocker.detail}</span></div>{blocker.code.includes("SHOT") || blocker.code.includes("PLAN") ? <Link to={`/projects/${projectId}/episodes/${episodeId}/plan`}>去处理</Link> : blocker.code.includes("ASSET") ? <Link to={`/projects/${projectId}/assets`}>去处理</Link> : <Link to="/diagnostics">去检查</Link>}</li>)}</ul>}
          <details className="episode-check-details"><summary>查看全部 {preflight.data.checks.length} 项检查</summary><ul>{preflight.data.checks.map((check) => <li key={check.code}><span className={check.status === "PASS" ? "check-pass" : "check-blocked"}>{check.status === "PASS" ? "通过" : "阻塞"}</span><div><strong>{check.label}</strong><small>{check.detail}</small></div></li>)}</ul></details>
          <div className="episode-start-bar"><div><strong>全程本机运行</strong><span>自动化不是黑盒：开始后可暂停、继续或安全取消。</span></div><button type="button" className="episode-primary-action" onClick={() => start.mutate()} disabled={preflight.data.status !== "PASS" || start.isPending}>{start.isPending ? "正在启动…" : "开始整集生产"}</button></div>
        </>}
      </section>}

      {run && <>
        <section className="episode-stage-section" aria-labelledby="stage-title">
          <div className="episode-section-heading"><div><p className="eyebrow">八阶段生产进度</p><h3 id="stage-title">选择一个阶段处理当前任务</h3></div><button type="button" className="secondary" onClick={() => setStageEvidenceOpen(true)} disabled={!activeStage}>阻塞与任务事件</button></div>
          {activeStage && <Tabs items={stageTabs} selectedId={activeStage.code} onChange={selectStage} ariaLabel="整集生产阶段">
            <TabPanel id={activeStage.code} selectedId={activeStage.code}>
              <ol className="episode-run-stages" aria-label={`${activeStage.label}详情`}><StageCard stage={activeStage} projectId={projectId} episodeId={episodeId} /></ol>
            </TabPanel>
          </Tabs>}
        </section>
        <aside className="episode-run-controls" aria-label="整集生产控制">
          <div><strong>{isPaused ? "生产已停在安全节点" : isTerminal ? "本次运行已结束" : "自动化正在本机持续运行"}</strong><span>{run.updated_at ? `最近更新 ${new Date(run.updated_at).toLocaleString()}` : "正在等待第一次状态更新"}</span></div>
          <div>
            {canPause && <button type="button" onClick={() => pause.mutate()} disabled={actionPending}><RunIcon name="pause" />暂停</button>}
            {isPaused && <button type="button" className="episode-primary-action" onClick={() => resume.mutate()} disabled={actionPending}><RunIcon name="play" />继续生产</button>}
            {!isTerminal && <button type="button" className="episode-danger-action" onClick={() => window.confirm("确定取消这次整集生产？已完成的创作结果会保留，但未开始的阶段将停止。") && cancel.mutate()} disabled={actionPending}><RunIcon name="stop" />取消</button>}
            {isTerminal && <button type="button" onClick={() => setSearchParams((current) => { const next = new URLSearchParams(current); next.delete("run"); return next; }, { replace: true })}>新建生产运行</button>}
          </div>
        </aside>
      </>}
      {actionError && <p className="episode-inline-error" role="alert">{String(actionError)}</p>}
      <Drawer open={stageEvidenceOpen} onClose={() => setStageEvidenceOpen(false)} title={activeStage ? `${activeStage.label} · 阻塞与任务事件` : "阻塞与任务事件"} width={520}>
        {activeStage ? <div className="episode-stage-evidence">
          <dl className="episode-stage-evidence__summary"><div><dt>阶段状态</dt><dd>{STAGE_STATUS[activeStage.status]}</dd></div><div><dt>失败</dt><dd>{activeStage.failed}</dd></div><div><dt>待人工决定</dt><dd>{activeStage.needs_human_decision}</dd></div><div><dt>运行中任务</dt><dd>{activeStage.running_jobs}</dd></div></dl>
          <section aria-labelledby="run-gate-title"><h3 id="run-gate-title">当前人工 Gate</h3>{pendingGate && Object.keys(pendingGate).length > 0 ? <dl className="episode-stage-gate">{Object.entries(pendingGate).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? String(value) : JSON.stringify(value)}</dd></div>)}</dl> : <p className="empty-state">当前没有待处理的人工 Gate。</p>}</section>
          <section aria-labelledby="run-jobs-title"><h3 id="run-jobs-title">阶段任务事件</h3>{activeStage.jobs?.length ? <ul className="episode-stage-event-list">{activeStage.jobs.map((job) => <li key={job.task_id}><div><strong>{job.item_key}</strong><code>{job.task_id}</code></div><span className="status-pill neutral">{job.job_state ?? job.status}</span></li>)}</ul> : <p className="empty-state">当前响应没有返回阶段任务事件；运行事实仍保留在 Jobs。</p>}</section>
          <Link className="secondary" to={`/projects/${projectId}/jobs`}>打开项目 Jobs</Link>
        </div> : <p className="empty-state">尚无可查看的运行阶段。</p>}
      </Drawer>
    </div>
  );
}

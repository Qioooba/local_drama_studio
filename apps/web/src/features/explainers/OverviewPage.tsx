/**
 * 总览与生产 (overview): run state, chapter/beat progress, budget, blockers and
 * the one-click production controls.
 */

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { controlExplainerRun, preflightExplainerPlan, startExplainerRun } from "../../generated/api";
import { stableIdempotencyKey } from "../../services/commandId";
import { queryKeys } from "../../query/queryKeys";
import { AuthorityBadge, InlineError, InlineOk, Panel, SettingRow, StateNotice, type PageState } from "./components";
import { estimateStageLabel, formatMs, resolveOutputsForExplainer, runStatusLabel, stepIsBlockingDependents, stepStatusLabel } from "./viewModels";
import { useExplainerOverview, useExplainerRun } from "./useExplainerQueries";
import "./explainers.css";

export function ExplainerOverviewPage() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const overview = useExplainerOverview(projectId);
  const run = useExplainerRun(overview.data?.latest_run?.id ?? null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const start = useMutation({
    mutationFn: async () => {
      const outputs = resolveOutputsForExplainer(
        overview.data?.editions as Array<Record<string, unknown>> | undefined,
        overview.data?.video?.source_locale as string | undefined,
        overview.data?.video?.aspect_ratio as string | undefined,
      );
      const report = await preflightExplainerPlan(projectId, { outputs });
      if (!report.executable) {
        const first = report.blockers[0];
        throw new Error(`预检未通过：${first ? `${first.message}（${first.next_step || first.code}）` : "存在阻塞项"}`);
      }
      return startExplainerRun(
        projectId,
        { plan_hash: report.plan_hash, outputs, start_workflow: true },
        stableIdempotencyKey("explainer-run", { projectId, planHash: report.plan_hash }),
      );
    },
    onSuccess: async (started) => {
      setError(null);
      setFeedback(`已提交生产：运行 ${started.id}（${runStatusLabel(started.projected_status)}）。`);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const control = useMutation({
    mutationFn: ({ action }: { action: "pause" | "resume" | "cancel" }) =>
      controlExplainerRun(overview.data?.latest_run?.id ?? "", action, { reason: "用户操作", actor: "local-user" }),
    onSuccess: async (_result, variables) => {
      setError(null);
      setFeedback(
        variables.action === "cancel"
          ? "已请求取消：停止后续调度并向底层作业发送取消；迟到结果会被隔离，不会被采用或发布。"
          : variables.action === "pause"
            ? "已请求暂停：运行中资源无法立即终止时显示“正在停止，当前片段可能完成”。"
            : "已请求继续。",
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => setError(mutationError instanceof Error ? mutationError.message : String(mutationError)),
  });

  const currentRun = run.data?.run ?? overview.data?.latest_run ?? null;

  const state = useMemo<PageState | null>(() => {
    if (overview.isPending) return { kind: "loading", message: "正在载入解说作品总览…" };
    if (overview.isError) {
      return {
        kind: "failed",
        title: "无法载入总览",
        body: overview.error instanceof Error ? overview.error.message : "未知错误",
      };
    }
    if (!overview.data) {
      return { kind: "empty", title: "没有解说作品", body: "该项目还没有解说作品记录。", action: <Link to={routes.explainers()}>返回作品列表</Link> };
    }
    if (overview.data.capability_snapshot && overview.data.capability_snapshot.probed === false) {
      return {
        kind: "no_capability",
        title: "尚未接入能力探查",
        body: "无法确认本地模型与工作流版本，因此不会排队 GPU，也不会给出乐观的预计耗时。",
        action: <Link to={routes.systemCapabilities(projectId)}>前往能力与模型</Link>,
      };
    }
    if (currentRun && runStatusLabel(currentRun.projected_status) === "失败") {
      return {
        kind: "failed",
        title: "生产失败",
        body: "失败步骤只锁定依赖其结果的控件；GPU 等待不会被显示为生成失败。",
      };
    }
    if (currentRun && currentRun.projected_status === "WAITING_INPUT") {
      return {
        kind: "partial",
        title: "等待处理",
        body: "存在需要用户决策的异常；其余自动步骤会继续，已完成内容不会重复生成。",
        action: <Link to={routes.explainerPage(projectId, "review")}>查看问题</Link>,
      };
    }
    return null;
  }, [currentRun, overview.data, overview.error, overview.isError, overview.isPending, projectId]);

  const steps = currentRun?.steps ?? [];
  const completed = steps.filter((step) => step.status === "SUCCEEDED" || step.status === "SKIPPED_WITH_REASON").length;

  return <div className="explainer-page">
    <div className="explainer-grid">
      <Panel
        title="本次生产"
        subtitle="真实项目由后台持久工作流运行；关闭浏览器不会停止生产。"
        actions={currentRun ? <span className="badge blue">{runStatusLabel(currentRun.projected_status)}</span> : <span className="badge">尚未开始</span>}
      >
        <StateNotice state={state} />
        {/*
          * The production controls stay available in every run state.  They used to
          * be rendered only when there was no notice, so the moment the run needed a
          * human decision ("等待处理") the page hid 继续 along with them and the
          * operator had no way to hand the paused workflow back to the machine.
          */}
        <>
          <div className="explainer-actions">
            <button
              type="button"
              className="primary-action"
              disabled={start.isPending}
              onClick={() => start.mutate()}
            >
              {start.isPending ? "正在检查并提交…" : "检查并一键生成"}
            </button>
            <button type="button" disabled={!currentRun || control.isPending} onClick={() => control.mutate({ action: "pause" })}>暂停</button>
            <button type="button" disabled={!currentRun || control.isPending} onClick={() => control.mutate({ action: "resume" })}>继续</button>
            <button type="button" disabled={!currentRun || control.isPending} onClick={() => control.mutate({ action: "cancel" })}>取消</button>
            <Link className="explainer-issue-link" to={routes.explainerPage(projectId, "review")}>只修问题</Link>
          </div>
          <InlineOk message={feedback} />
          <InlineError message={error} />
        </>

        {overview.data ? (
          <div className="explainer-summary-strip">
            <div>
              <small>目标时长</small>
              <strong>{Math.round(overview.data.video.target_seconds / 60)} <small>分钟</small></strong>
            </div>
            <div>
              <small>画面段</small>
              <strong>{overview.data.beat_count} <small>段</small></strong>
            </div>
            <div>
              <small>输出版本</small>
              <strong>{overview.data.editions.length} <small>版</small></strong>
            </div>
            <div>
              <small>需处理</small>
              <strong>{overview.data.open_issue_count} <small>项</small></strong>
            </div>
          </div>
        ) : null}

        {steps.length > 0 ? (
          <div className="explainer-stages">
            {steps.map((step, index) => {
              const done = step.status === "SUCCEEDED" || step.status === "SKIPPED_WITH_REASON";
              const running = step.status === "RUNNING";
              const failed = stepIsBlockingDependents(step);
              const className = `explainer-stage${done ? " done" : running ? " current" : failed ? " failed" : ""}`;
              return (
                <div className={className} key={step.id}>
                  <span className="explainer-stage-num">{done ? "✓" : index + 1}</span>
                  <div>
                    <p className="explainer-stage-title">{step.output_kind || step.planned_step_code}</p>
                    <small>
                      {stepStatusLabel(step.status)}
                      {step.job_state ? ` · 作业 ${step.job_state}` : ""}
                      {step.attempt_count > 0 ? ` · 尝试 ${step.attempt_count}` : ""}
                      {step.skip_reason ? ` · ${step.skip_reason}` : ""}
                    </small>
                    {running ? <div className="explainer-progress"><span style={{ width: "60%" }} /></div> : null}
                  </div>
                  {step.blocker_code ? <span className="badge danger">{step.blocker_code}</span> : null}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="explainer-note">
            尚未提交生产。点击“检查并一键生成”会先冻结输入、栏目版本、能力、预算与任务骨架；无阻塞时自动提交。
          </p>
        )}

        {currentRun ? (
          <p className="explainer-note">
            已完成 {completed} / {steps.length} 步。执行状态权威来源：{currentRun.execution_authority.source_of_truth}；
            解说运行只是业务范围、快照与投影。
          </p>
        ) : null}
      </Panel>

      <div className="explainer-stack">
        <Panel title="需要处理" subtitle="优先展示需要决策的事项；运行等待另行提示。">
          {!overview.data || overview.data.open_issues.length === 0 ? (
            <p className="muted">当前没有需要处理的异常。</p>
          ) : (
            overview.data.open_issues.slice(0, 6).map((issue, index) => (
              <div className={`explainer-issue-row${String(issue.severity) === "BLOCKER" ? " danger" : ""}`} key={`${String(issue.id)}-${index}`}>
                <span className="explainer-issue-mark">!</span>
                <div>
                  <p>{String(issue.observed || issue.issue_kind || "问题")}</p>
                  <p className="muted">
                    {String(issue.detector ?? "检测器")}
                    {issue.start_ms !== null && issue.start_ms !== undefined ? ` · ${formatMs(Number(issue.start_ms))}` : ""}
                  </p>
                  <Link className="explainer-source-link" to={routes.explainerPage(projectId, "review")}>查看证据与修复方案 →</Link>
                </div>
              </div>
            ))
          )}
        </Panel>

        <Panel title="生产方式" subtitle="机器检查与人工确认使用不同状态。">
          {overview.data ? (
            <>
              <SettingRow label="自动程度" value={automationLabel(overview.data.video.automation_mode)} />
              <SettingRow label="模型推理" value={overview.data.video.inference_mode === "LOCAL_ONLY" ? "纯本地" : overview.data.video.inference_mode} />
              <SettingRow label="资料采集" value={overview.data.video.research_mode === "OFFLINE_IMPORT" ? "纯离线导入" : "允许联网研究"} />
              <SettingRow label="预算上限" value={currentRun ? `${String(currentRun.budget_json.max_gpu_seconds ?? "—")} GPU 秒` : "提交后冻结"} />
              <SettingRow label="估算阶段" value={estimateStageLabel(planEstimateStage(currentRun?.plan_json))} />
              <div className="explainer-actions" style={{ marginTop: 12 }}>
                <AuthorityBadge kind={overview.data.latest_run ? "machine" : "none"} />
                {overview.data.active_decisions.some((decision) => String(decision.decision_kind) === "HUMAN_APPROVED")
                  ? <AuthorityBadge kind="human" />
                  : null}
              </div>
            </>
          ) : <p className="muted">载入后显示。</p>}
        </Panel>

        <Panel title="输出版本" subtitle="中英文与横竖屏是同一作品的 edition。">
          {!overview.data || overview.data.editions.length === 0 ? (
            <p className="muted">尚无输出版本；预检时会按所选语言与画幅创建。</p>
          ) : (
            <div className="explainer-table-wrap">
              <table className="explainer-table">
                <thead><tr><th>edition</th><th>配音</th><th>画幅</th><th>字幕</th></tr></thead>
                <tbody>
                  {overview.data.editions.map((edition) => (
                    <tr key={String(edition.id)}>
                      <td>{String(edition.edition_key)}</td>
                      <td>{String(edition.voice_locale)}</td>
                      <td>{String(edition.aspect_ratio)}</td>
                      <td>{String(edition.subtitle_mode)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </div>
  </div>;
}

function automationLabel(mode: string): string {
  switch (mode) {
    case "AUTO_WITH_EXCEPTIONS":
      return "自动成片 · 异常时暂停";
    case "REVIEW_BEFORE_RENDER":
      return "成片前确认一次";
    case "MANUAL_REVIEW":
      return "人工审查";
    default:
      return mode;
  }
}

/** Read the frozen estimate stage out of a run's plan snapshot. */
function planEstimateStage(plan: unknown): string {
  if (!plan || typeof plan !== "object") return "";
  const estimate = (plan as Record<string, unknown>).estimate;
  if (!estimate || typeof estimate !== "object") return "";
  return String((estimate as Record<string, unknown>).stage ?? "");
}

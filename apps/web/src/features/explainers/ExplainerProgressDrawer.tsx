/**
 * 制作进度 (progress drawer) — the retirement home of the old 总览与生产 page.
 *
 * It carries the same real facts the standalone overview page used to show
 * (production controls, needs-attention issues, output versions, run note) and
 * adds the §B8 run-state → allowed-action mapping.  Two rules from the spec are
 * load-bearing here:
 *
 * * No hard-coded percentage.  Progress is only drawn when the server gave a
 *   trustworthy ratio (real task rows, or the run's own completed/total steps).
 * * An unrecognised run status is 回执未知: the drawer asks for the original
 *   receipt and refuses to pretend the run is idle or running.
 *
 * The drawer is rendered through the shared `Drawer` primitive, so focus moves
 * into it on open and returns to the trigger on close.  Its content is wrapped
 * in `.explainer-workspace` because the primitive portals it out of the shell's
 * DOM subtree, and every explainer style is scoped to that class.
 */

import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { EXPLAINER_PAGES, EXPLAINER_PAGE_LABELS, routes, type ExplainerPage } from "../../app/routeRegistry";
import {
  controlExplainerRun,
  getExplainerRun,
  preflightExplainerPlan,
  startExplainerRun,
  type ExplainerOverview,
  type ExplainerRun,
} from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { stableIdempotencyKey } from "../../services/commandId";
import { ContextMenu, Drawer, StatusBadge, type ContextMenuItem } from "../../components/ui/primitives";
import { InlineError, InlineOk, SettingRow } from "./components";
import {
  EXPLAINER_RUN_STATE_ROWS,
  EXPLAINER_STEP_HINTS,
  EXPLAINER_STEP_STATUS_LABELS,
  EXPLAINER_AUTOMATION_LABELS,
  explainerRunGuide,
  explainerRunIsTerminal,
  type ExplainerStepStatusMap,
} from "./ExplainerSteps";
import {
  aspectLabel,
  estimateStageLabel,
  formatMs,
  issueSeverityTone,
  resolveOutputsForExplainer,
  runStatusLabel,
  SEVERITY_LABELS,
  stepStatusLabel,
  subtitleModeLabel,
} from "./viewModels";
import "./explainers.css";

const DRAWER_WIDTH = 440;

/* -------------------------------------------------------------------------- */
/* one-click start (shared by the header button and the drawer footer)         */
/* -------------------------------------------------------------------------- */

export type ExplainerPreviewStart = {
  /** Runs the real preflight and, when it is executable, submits the run. */
  start: () => void;
  pending: boolean;
  label: string;
  feedback: string | null;
  error: string | null;
  clear: () => void;
};

/**
 * "一键生成到预览": preflight the frozen inputs, then submit.  The server rejects
 * non-executable plans, so this never queues a blocked run and never shows an
 * optimistic success.
 */
export function useExplainerPreviewStart({
  projectId,
  overview,
  onStarted,
}: {
  projectId: string;
  overview: ExplainerOverview | undefined;
  onStarted?: (run: ExplainerRun) => void;
}): ExplainerPreviewStart {
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () => {
      const outputs = resolveOutputsForExplainer(
        overview?.editions as Array<Record<string, unknown>> | undefined,
        overview?.video?.source_locale as string | undefined,
        overview?.video?.aspect_ratio as string | undefined,
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
      setFeedback(`已提交生产：运行 ${started.id}（${runStatusLabel(started.projected_status)}）。浏览器关闭后由服务端继续。`);
      onStarted?.(started);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  return {
    start: () => mutation.mutate(),
    pending: mutation.isPending,
    label: mutation.isPending ? "正在检查并提交…" : "一键生成到预览",
    feedback,
    error,
    clear: () => {
      setFeedback(null);
      setError(null);
    },
  };
}

/** Real run rows for the drawer; polling stops on a terminal status. */
function useDrawerRun(runId: string | null) {
  return useQuery({
    queryKey: queryKeys.explainers.run(runId ?? ""),
    queryFn: () => getExplainerRun(runId as string),
    enabled: Boolean(runId),
    refetchInterval: (query) => (explainerRunIsTerminal(query.state.data?.run?.projected_status) ? false : 4_000),
  });
}

/* -------------------------------------------------------------------------- */
/* drawer                                                                     */
/* -------------------------------------------------------------------------- */

export function ExplainerProgressDrawer({
  open,
  onClose,
  projectId,
  activePage,
  overview,
  statuses,
  start,
}: {
  open: boolean;
  onClose: () => void;
  projectId: string;
  activePage: ExplainerPage | null;
  overview: ExplainerOverview | undefined;
  statuses: ExplainerStepStatusMap;
  start: ExplainerPreviewStart;
}) {
  const queryClient = useQueryClient();
  const latestRun = overview?.latest_run ?? null;
  const runQuery = useDrawerRun(latestRun?.id ?? null);
  const run = runQuery.data?.run ?? latestRun;
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null);
  const moreRef = useRef<HTMLButtonElement | null>(null);

  const control = useMutation({
    mutationFn: ({ action }: { action: "pause" | "resume" | "cancel" }) =>
      controlExplainerRun(run?.id ?? "", action, { reason: "用户操作", actor: "local-user" }),
    onSuccess: async (_result, variables) => {
      setError(null);
      setFeedback(
        variables.action === "cancel"
          ? "已请求取消：只终止之后的任务并处理当前任务，已生成素材不会被删除；迟到结果不会被采用。"
          : variables.action === "pause"
            ? "已请求暂停：不再派发新任务，等待正在执行的安全完成点。"
            : "已请求继续：从未完成部分恢复，复用成功产物。",
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const guide = explainerRunGuide(run?.projected_status ?? null);
  const allows = (action: string) => guide.allowed.includes(action);

  // Trustworthy ratio only: real task rows, or the run's own completed/total
  // counters.  Otherwise no bar is drawn at all.
  const progress = useMemo(() => {
    const steps = run?.steps ?? [];
    if (steps.length > 0) {
      const completed = steps.filter((step) => step.status === "SUCCEEDED" || step.status === "SKIPPED_WITH_REASON").length;
      return { completed, total: steps.length, source: "按真实任务状态统计" };
    }
    const raw = (run?.progress_json ?? {}) as Record<string, unknown>;
    const completed = Number(raw.completed_steps);
    const total = Number(raw.total_steps);
    if (Number.isFinite(completed) && Number.isFinite(total) && total > 0) {
      return { completed, total, source: "按服务端步骤计数统计" };
    }
    return null;
  }, [run]);

  if (!open) return null;

  const moreItems: ContextMenuItem[] = [
    {
      id: "cancel-run",
      label: "取消本次运行",
      danger: true,
      disabledReason: run && allows("暂停后续") ? undefined : "当前没有可取消的运行",
      onSelect: () => control.mutate({ action: "cancel" }),
    },
  ];

  return <Drawer open={open} onClose={onClose} title="制作进度" width={DRAWER_WIDTH}>
    <div className="explainer-workspace explainer-progress-drawer">
      <section aria-label="六步就绪摘要">
        <h3>六步就绪摘要</h3>
        <ol className="explainer-progress-steps">
          {EXPLAINER_PAGES.map((page, index) => (
            <li key={page} className={`is-${statuses[page].toLowerCase().replace(/_/g, "-")}`}>
              <Link to={routes.explainerPage(projectId, page)} aria-current={activePage === page ? "step" : undefined}>
                <span aria-hidden="true">{index + 1}</span>
                <strong>{EXPLAINER_PAGE_LABELS[page]}</strong>
                <span className="explainer-progress-steps__status">{EXPLAINER_STEP_STATUS_LABELS[statuses[page]]}</span>
              </Link>
              <small>{EXPLAINER_STEP_HINTS[page]}</small>
            </li>
          ))}
        </ol>
      </section>

      <section aria-label="本次运行">
        <h3>本次运行</h3>
        <SettingRow label="运行状态" value={run ? runStatusLabel(run.projected_status) : "尚未开始"} />
        <SettingRow label="§B8 状态归类" value={guide.state} />
        <p className="explainer-note">{guide.note}</p>
        {guide.unknownReceipt ? (
          <p className="explainer-note warn" role="alert">
            收到无法识别的运行状态“{String(run?.projected_status)}”。在查询到原回执之前，不会提交同类新任务，避免重复占用 GPU。
          </p>
        ) : null}
        <div className="explainer-actions">
          {allows("一键生成到预览") ? (
            // Deliberately not the highlighted primary: the step's one primary
            // action belongs to the bottom bar, and the drawer must not create a
            // second highlighted action in the same viewport.
            <button type="button" className="explainer-head__button" disabled={start.pending} onClick={start.start}>
              {start.label}
            </button>
          ) : null}
          {run && (allows("暂停后续") || run.projected_status === "PAUSING") ? (
            <button type="button" disabled={control.isPending} onClick={() => control.mutate({ action: "pause" })}>暂停后续</button>
          ) : null}
          {run && (allows("继续") || run.projected_status === "PAUSED") ? (
            <button type="button" disabled={control.isPending} onClick={() => control.mutate({ action: "resume" })}>继续</button>
          ) : null}
          {allows("查询原回执") ? (
            <button type="button" disabled={runQuery.isFetching} onClick={() => { void runQuery.refetch(); }}>查询原回执</button>
          ) : null}
          {allows("去处理") && activePage ? (
            <Link className="explainer-issue-link" to={routes.explainerPage(projectId, activePage)}>去处理当前步骤</Link>
          ) : null}
          {(allows("进入具体步骤") || allows("查看当前步骤")) && activePage ? (
            <Link className="explainer-issue-link" to={routes.explainerPage(projectId, activePage)}>查看当前步骤</Link>
          ) : null}
          {allows("仅重试失败项") ? (
            <Link className="explainer-issue-link" to={routes.explainerPage(projectId, "review")}>去处理失败项</Link>
          ) : null}
          {run && allows("去预览") ? (
            <Link className="explainer-issue-link" to={routes.explainerPage(projectId, "review")}>去预览</Link>
          ) : null}
        </div>
        {run ? (
          <div className="explainer-actions">
            <button
              ref={moreRef}
              type="button"
              aria-haspopup="menu"
              aria-expanded={menuAt !== null}
              onClick={() => {
                const rect = moreRef.current?.getBoundingClientRect();
                setMenuAt({ x: rect?.left ?? 0, y: (rect?.bottom ?? 0) + 4 });
              }}
            >
              更多
            </button>
          </div>
        ) : null}
        <ContextMenu
          open={menuAt !== null}
          x={menuAt?.x ?? 0}
          y={menuAt?.y ?? 0}
          items={moreItems}
          label="制作进度更多操作"
          onClose={() => setMenuAt(null)}
        />
      </section>

      <section aria-label="生产进度">
        <h3>生产进度</h3>
        {progress ? (
          <>
            <p className="explainer-progress-figures">
              已完成 <strong>{progress.completed}</strong> / {progress.total} 项任务
            </p>
            <div className="explainer-progress" aria-hidden="true">
              <span style={{ width: `${Math.round((progress.completed / progress.total) * 100)}%` }} />
            </div>
            <p className="muted">{progress.source}；没有可信估时不显示剩余时间。</p>
          </>
        ) : (
          <p className="muted">还没有可信的进度比例：未提交生产，或运行没有返回任务级状态。这里不会显示估算百分比。</p>
        )}
        {runQuery.isError ? (
          <InlineError message={`进度读取失败：${runQuery.error instanceof Error ? runQuery.error.message : "未知错误"}。已保留上一次读取的数据，这不表示任务失败。`} />
        ) : null}
      </section>

      <section aria-label="真实任务列表">
        <h3>实际任务</h3>
        {!run ? (
          <p className="muted">尚未提交生产。点击“一键生成到预览”会先冻结输入、栏目版本、能力与预算；无阻塞时自动提交。</p>
        ) : (run.steps ?? []).length === 0 ? (
          <p className="muted">该运行没有返回任务行；这不等于任务已经完成。</p>
        ) : (
          <div className="explainer-table-wrap">
            <table className="explainer-table">
              <thead><tr><th>任务</th><th>状态</th><th>作业</th><th>尝试</th></tr></thead>
              <tbody>
                {(run.steps ?? []).map((step) => (
                  <tr key={step.id}>
                    <td>{String(step.output_kind || step.planned_step_code)}</td>
                    <td>
                      {stepStatusLabel(step.status)}
                      {step.skip_reason ? <small> · {String(step.skip_reason)}</small> : null}
                      {step.blocker_code ? <small> · {String(step.blocker_code)}</small> : null}
                    </td>
                    <td>{step.job_state ? String(step.job_state) : "—"}</td>
                    <td>{Number(step.attempt_count ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-label="需要处理">
        <h3>需要处理</h3>
        {!overview ? (
          <p className="muted">总览读取失败，这里不会假装没有问题。</p>
        ) : (overview.open_issues ?? []).length === 0 ? (
          <p className="muted">当前没有需要处理的异常。</p>
        ) : (
          overview.open_issues.slice(0, 6).map((issue, index) => {
            const severity = String((issue as Record<string, unknown>).severity ?? "");
            const tone = issueSeverityTone(severity);
            return <div className={`explainer-issue-row${tone === "danger" ? " danger" : ""}`} key={`${String(issue.id)}-${index}`}>
              <span className="explainer-issue-mark" aria-hidden="true">!</span>
              <div>
                <p>{String(issue.observed || issue.issue_kind || "问题")}</p>
                <p className="muted">
                  {SEVERITY_LABELS[severity] ?? (severity || "未定级")} · {String(issue.detector ?? "检测器")}
                  {issue.start_ms !== null && issue.start_ms !== undefined ? ` · ${formatMs(Number(issue.start_ms))}` : ""}
                </p>
                <Link className="explainer-source-link" to={routes.explainerPage(projectId, "review")}>查看证据与修复方案 →</Link>
              </div>
            </div>;
          })
        )}
      </section>

      <section aria-label="输出版本">
        <h3>输出版本</h3>
        {!overview || (overview.editions ?? []).length === 0 ? (
          <p className="muted">尚无输出版本；预检时会按所选语言与画幅创建。</p>
        ) : (
          <div className="explainer-table-wrap">
            <table className="explainer-table">
              <thead><tr><th>版本</th><th>配音</th><th>画幅</th><th>字幕</th></tr></thead>
              <tbody>
                {overview.editions.map((edition) => (
                  <tr key={String(edition.id)}>
                    <td>{String(edition.edition_key)}</td>
                    <td>{String(edition.voice_locale)}</td>
                    <td>{aspectLabel(String(edition.aspect_ratio ?? ""))}</td>
                    <td>{subtitleModeLabel(String(edition.subtitle_mode ?? ""))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-label="制作事实">
        <h3>制作事实</h3>
        {overview ? (
          <>
            <SettingRow label="生产方式" value={EXPLAINER_AUTOMATION_LABELS[String(overview.video.automation_mode)] ?? String(overview.video.automation_mode)} />
            <SettingRow label="模型推理" value={overview.video.inference_mode === "LOCAL_ONLY" ? "纯本地" : String(overview.video.inference_mode)} />
            <SettingRow label="资料采集" value={overview.video.research_mode === "OFFLINE_IMPORT" ? "纯离线导入" : "允许联网研究"} />
            <SettingRow label="画面段" value={`${Number(overview.beat_count ?? 0)} 段`} />
            <SettingRow label="预算上限" value={run ? `${String(run.budget_json?.max_gpu_seconds ?? "—")} GPU 秒` : "提交后冻结"} />
            <SettingRow label="估算阶段" value={estimateStageLabel(planEstimateStage(run?.plan_json))} />
            <p className="explainer-note">
              执行状态权威来源：{run ? String(run.execution_authority?.source_of_truth ?? "未知") : "尚未提交"}；
              解说运行只是业务范围、快照与投影。浏览器关闭后任务由服务端继续。
            </p>
          </>
        ) : <p className="muted">载入后显示。</p>}
      </section>

      <section aria-label="运行状态与允许操作">
        <h3>运行状态与允许操作</h3>
        <p className="muted">当前：{guide.state}（{guide.headline}）</p>
        <ul className="explainer-progress-allowed">
          {guide.allowed.map((item) => <li key={item}><StatusBadge tone="info">{item}</StatusBadge></li>)}
        </ul>
        <details>
          <summary>全部运行状态与允许操作（§B8）</summary>
          <div className="explainer-table-wrap">
            <table className="explainer-table">
              <thead><tr><th>运行状态</th><th>用户文案</th><th>允许操作</th></tr></thead>
              <tbody>
                {EXPLAINER_RUN_STATE_ROWS.map((row) => (
                  <tr key={row.state} aria-current={row.state === guide.state ? "true" : undefined}>
                    <td>{row.state}</td>
                    <td>{row.headline}</td>
                    <td>{row.allowed.join("、")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <InlineOk message={start.feedback} />
      <InlineError message={start.error} />
      <InlineOk message={feedback} />
      <InlineError message={error} />
    </div>
  </Drawer>;
}

/** Read the frozen estimate stage out of a run's plan snapshot. */
function planEstimateStage(plan: unknown): string {
  if (!plan || typeof plan !== "object") return "";
  const estimate = (plan as Record<string, unknown>).estimate;
  if (!estimate || typeof estimate !== "object") return "";
  return String((estimate as Record<string, unknown>).stage ?? "");
}

/**
 * The one production navigation for the explainer factory: a numeric step bar
 * plus the pure status model behind it (design §B1.1 item 3, §B1.4, §F2.1).
 *
 * Two rules this module exists to keep:
 *
 * 1. Every step status is derived from real workspace/run/step data.  There is
 *    no fake progress: no timer ticks, no invented percentage, no "looks done"
 *    default.  When nothing is known yet, the honest answer is 未开始.
 * 2. Every step stays reachable.  A step that is not complete is *not* a greyed
 *    out dead entry — the user can always open it and read its preconditions.
 */

import { useId, useMemo } from "react";
import { NavLink } from "react-router-dom";
import {
  EXPLAINER_PAGES,
  EXPLAINER_PAGE_LABELS,
  routes,
  type ExplainerPage,
} from "../../app/routeRegistry";
import type { ExplainerOverview, ExplainerRunStatus, ExplainerStep } from "../../generated/api";
import "./explainers.css";

/* -------------------------------------------------------------------------- */
/* step status model                                                          */
/* -------------------------------------------------------------------------- */

export type ExplainerStepStatusValue =
  | "NOT_STARTED"
  | "RUNNING"
  | "NEEDS_SELECTION"
  | "DONE"
  | "NEEDS_UPDATE"
  | "FAILED";

/** Chinese labels are the documented §B1.4 wording, in the documented order. */
export const EXPLAINER_STEP_STATUS_VALUES: readonly ExplainerStepStatusValue[] = [
  "NOT_STARTED",
  "RUNNING",
  "NEEDS_SELECTION",
  "DONE",
  "NEEDS_UPDATE",
  "FAILED",
];

export const EXPLAINER_STEP_STATUS_LABELS: Record<ExplainerStepStatusValue, string> = {
  NOT_STARTED: "未开始",
  RUNNING: "处理中",
  NEEDS_SELECTION: "待选择",
  DONE: "已完成",
  NEEDS_UPDATE: "需更新",
  FAILED: "失败",
};

/** Text glyphs — every one of them is rendered next to its Chinese label. */
export const EXPLAINER_STEP_STATUS_ICONS: Record<ExplainerStepStatusValue, string> = {
  NOT_STARTED: "○",
  RUNNING: "◐",
  NEEDS_SELECTION: "◈",
  DONE: "✓",
  NEEDS_UPDATE: "↑",
  FAILED: "✕",
};

/**
 * Business tasks that belong to each user step (design §F1).
 *
 * The mapping is by business task / output, never by `stage_code`: both
 * IDENTITY_ASSETS and VISUAL_GENERATION carry the stage code
 * `EXPLAINER_STORYBOARD`, so a stage-code mapping would point two different
 * problems at the same step.
 */
export const EXPLAINER_STEP_TASKS: Record<ExplainerPage, readonly string[]> = {
  script: ["RESEARCH_ACQUIRE", "FACT_EXTRACT", "NARRATION_WRITE"],
  assets: ["IDENTITY_ASSETS"],
  audio: ["NARRATION_TTS", "NARRATION_ALIGN", "SUBTITLE_BUILD"],
  storyboard: ["EXPLAINER_STORYBOARD", "VISUAL_GENERATION", "EXPLAINER_VISUAL_QC"],
  // §F1: the video products of the one generation task belong to step 5.  The
  // workspace projection still exposes a single generation task, so step 5
  // reports the same aggregate as the image side until the projection splits
  // the two artifact kinds — visibly honest, never a fabricated "done".
  clips: ["VISUAL_GENERATION"],
  review: ["COMPOSITION_RENDER", "COMPOSITION_QC", "EXPLAINER_POLICY_EVALUATE", "EXPLAINER_EXPORT"],
};

/** One line of explanation per page (design §B1.1 item 4 — at most one line). */
export const EXPLAINER_STEP_HINTS: Record<ExplainerPage, string> = {
  script: "先确认这份讲稿，后续配音与画面都以它为准。",
  assets: "先定下人物与场景参考，后面每张画面才会稳定沿用。",
  audio: "配音先定稿，字幕与画面时长都按实测音频对齐。",
  storyboard: "按实测旁白拆分画面段，并决定每一段用哪种生成方式。",
  clips: "为每个画面段选定片段；每个片段都必须来自真实 AI 图生视频。",
  review: "只有真实可播放的成片才算完成，导出与人工确认分开记录。",
};

export type ExplainerStepStatusMap = Record<ExplainerPage, ExplainerStepStatusValue>;

const ALL_NOT_STARTED: ExplainerStepStatusMap = {
  script: "NOT_STARTED",
  assets: "NOT_STARTED",
  audio: "NOT_STARTED",
  storyboard: "NOT_STARTED",
  clips: "NOT_STARTED",
  review: "NOT_STARTED",
};

const SETTLED_SUCCESS = new Set(["SUCCEEDED", "SKIPPED_WITH_REASON"]);
/**
 * A step row is created as PENDING the moment a run is submitted, so PENDING on
 * its own means "not dispatched yet" — never 处理中.  Only a really executing
 * task, or a task bound to an active job, counts as running.
 */
const ACTIVE_TASK_STATUSES = new Set(["RUNNING"]);
const FAILED_TASK_STATUSES = new Set(["RETRYABLE_FAILED", "TERMINAL_FAILED"]);
const ACTIVE_JOB_STATES = new Set(["QUEUED", "RUNNING", "CLAIMED", "PREFLIGHT", "PENDING", "RETRYING"]);

/**
 * Optional server-side readiness projection (`getExplainerWorkspaceReadiness`).
 *
 * When it is present it is the better authority for the six statuses; when it is
 * absent the overview-derived model below is used, so the step bar works today
 * and improves without a signature change once the projection is wired in.
 * Only the documented six values are accepted — anything else is ignored.
 */
export type ExplainerStepStatusSource = {
  steps?: Array<{ page?: string | null; status?: string | null }> | null;
} | null | undefined;

function isStepStatusValue(value: unknown): value is ExplainerStepStatusValue {
  return typeof value === "string" && (EXPLAINER_STEP_STATUS_VALUES as readonly string[]).includes(value);
}

function stepsByCode(run: ExplainerOverview["latest_run"] | null | undefined): Map<string, ExplainerStep> {
  const map = new Map<string, ExplainerStep>();
  for (const step of run?.steps ?? []) {
    const code = String(step.planned_step_code ?? "");
    if (code) map.set(code, step);
  }
  return map;
}

function isTaskActive(step: ExplainerStep): boolean {
  if (ACTIVE_TASK_STATUSES.has(String(step.status))) return true;
  return step.job_state !== null && step.job_state !== undefined && ACTIVE_JOB_STATES.has(String(step.job_state));
}

/** Real required artifacts.  A task row is a claim; these are the products. */
function artifactAvailable(
  page: ExplainerPage,
  overview: ExplainerOverview,
  byCode: Map<string, ExplainerStep>,
): boolean {
  const succeeded = (code: string) => SETTLED_SUCCESS.has(String(byCode.get(code)?.status ?? ""));
  const beats = Number(overview.beat_count ?? 0);
  switch (page) {
    case "script":
      // The video's current revision is the projection-relevant fact; the task
      // row is the fallback for payloads that do not carry it yet.
      return Boolean(overview.video?.current_script_revision_id) || succeeded("NARRATION_WRITE");
    case "assets":
      return succeeded("IDENTITY_ASSETS");
    case "audio":
      return succeeded("NARRATION_TTS") && succeeded("NARRATION_ALIGN");
    case "storyboard":
      return succeeded("EXPLAINER_STORYBOARD") && beats > 0;
    case "clips":
      return succeeded("VISUAL_GENERATION") && beats > 0;
    case "review":
      return succeeded("COMPOSITION_RENDER") && (overview.editions?.length ?? 0) > 0;
  }
}

function openBlockingIssueStepCodes(overview: ExplainerOverview): Set<string> {
  const codes = new Set<string>();
  for (const issue of overview.open_issues ?? []) {
    const severity = String((issue as Record<string, unknown>).severity ?? "");
    const responsible = String((issue as Record<string, unknown>).responsible_step_code ?? "");
    if (responsible && (severity === "BLOCKER" || severity === "MAJOR")) codes.add(responsible);
  }
  return codes;
}

/**
 * Derive one of the six §B1.4 statuses for every step from real data only.
 *
 * Order of precedence: a failed task, then a really active task, then an
 * upstream change that made the product stale, then an open blocking issue the
 * user must decide on, then "product complete", and otherwise 未开始.
 */
export function explainerStepStatuses(
  overview: ExplainerOverview | null | undefined,
  readiness?: ExplainerStepStatusSource,
): ExplainerStepStatusMap {
  const result: ExplainerStepStatusMap = { ...ALL_NOT_STARTED };
  if (!overview) return result;

  const run = overview.latest_run ?? null;
  const byCode = stepsByCode(run);
  const blocking = openBlockingIssueStepCodes(overview);
  const readinessByPage = new Map<string, ExplainerStepStatusValue>();
  for (const entry of readiness?.steps ?? []) {
    const page = String(entry?.page ?? "");
    const status = entry?.status;
    if (isStepStatusValue(status) && (EXPLAINER_PAGES as readonly string[]).includes(page)) {
      readinessByPage.set(page, status);
    }
  }

  for (const page of EXPLAINER_PAGES) {
    const override = readinessByPage.get(page);
    if (override) {
      result[page] = override;
      continue;
    }

    const tasks = EXPLAINER_STEP_TASKS[page].map((code) => byCode.get(code)).filter(Boolean) as ExplainerStep[];
    const codes = EXPLAINER_STEP_TASKS[page];

    if (tasks.some((task) => FAILED_TASK_STATUSES.has(String(task.status)))) {
      result[page] = "FAILED";
      continue;
    }
    if (tasks.some(isTaskActive)) {
      result[page] = "RUNNING";
      continue;
    }
    if (tasks.some((task) => String(task.status) === "STALE")) {
      result[page] = "NEEDS_UPDATE";
      continue;
    }
    if (codes.some((code) => blocking.has(code))) {
      result[page] = "NEEDS_SELECTION";
      continue;
    }

    const present = artifactAvailable(page, overview, byCode);
    const tasksSettled = tasks.length > 0 && tasks.every((task) => SETTLED_SUCCESS.has(String(task.status)));
    if (present && tasksSettled) {
      result[page] = "DONE";
      continue;
    }
    // The workspace already carries a real artifact even though no run recorded
    // the producing task (for example a script frozen without a production run).
    if (page === "script" && Boolean(overview.video?.current_script_revision_id)) {
      result[page] = "DONE";
    }
  }
  return result;
}

/** How many steps a trustworthy run/step projection reports as finished. */
export function countCompletedSteps(statuses: ExplainerStepStatusMap): number {
  return EXPLAINER_PAGES.filter((page) => statuses[page] === "DONE").length;
}

/**
 * The step a user should be sent to: the first step that is not done, in
 * production order.  When every step is done the last step (预览与导出) is the
 * right place to be.
 */
export function firstStepNeedingAttention(statuses: ExplainerStepStatusMap): ExplainerPage {
  return EXPLAINER_PAGES.find((page) => statuses[page] !== "DONE") ?? EXPLAINER_PAGES[EXPLAINER_PAGES.length - 1];
}

export function nextExplainerPage(page: ExplainerPage): ExplainerPage | null {
  const index = EXPLAINER_PAGES.indexOf(page);
  return index >= 0 && index < EXPLAINER_PAGES.length - 1 ? EXPLAINER_PAGES[index + 1] : null;
}

export function previousExplainerPage(page: ExplainerPage): ExplainerPage | null {
  const index = EXPLAINER_PAGES.indexOf(page);
  return index > 0 ? EXPLAINER_PAGES[index - 1] : null;
}

/** `?panel=progress` opens the 制作进度 drawer and a refresh keeps it open. */
export const EXPLAINER_PROGRESS_PANEL = "progress";

export function explainerProgressPanelOpen(search: string | null | undefined): boolean {
  if (!search) return false;
  const value = search.startsWith("?") ? search.slice(1) : search;
  return new URLSearchParams(value).get("panel") === EXPLAINER_PROGRESS_PANEL;
}

/* -------------------------------------------------------------------------- */
/* step bar                                                                   */
/* -------------------------------------------------------------------------- */

export function ExplainerSteps({
  projectId,
  activePage,
  statuses,
}: {
  projectId: string;
  activePage: ExplainerPage | null;
  statuses: ExplainerStepStatusMap;
}) {
  const baseId = useId().replace(/:/g, "");
  const items = useMemo(
    () =>
      EXPLAINER_PAGES.map((page, index) => {
        const status = statuses[page];
        return {
          page,
          index,
          status,
          label: EXPLAINER_PAGE_LABELS[page],
          statusLabel: EXPLAINER_STEP_STATUS_LABELS[status],
          statusId: `${baseId}-step-${page}-status`,
        };
      }),
    [baseId, statuses],
  );

  return <nav className="explainer-steps" aria-label="解说制作步骤">
    <ol>
      {items.map((item) => {
        const current = item.page === activePage;
        const statusClass = `is-${item.status.toLowerCase().replace(/_/g, "-")}`;
        return <li
          key={item.page}
          className={`explainer-step ${statusClass}${current ? " is-current" : ""}`}
          data-step-status={item.status}
        >
          <NavLink
            className="explainer-step__link"
            to={routes.explainerPage(projectId, item.page)}
            // The accessible name stays exactly the step label; the status is
            // announced as the description so no state is carried by colour.
            aria-label={item.label}
            aria-describedby={item.statusId}
            aria-current={current ? "step" : undefined}
          >
            <span className="explainer-step__num" aria-hidden="true">{item.index + 1}</span>
            <span className="explainer-step__label" aria-hidden="true">{item.label}</span>
            <span className="explainer-step__status" id={item.statusId}>
              <span className="explainer-step__status-icon" aria-hidden="true">{EXPLAINER_STEP_STATUS_ICONS[item.status]}</span>
              <span className="explainer-step__status-text">{item.statusLabel}</span>
            </span>
          </NavLink>
        </li>;
      })}
    </ol>
  </nav>;
}

/** The §B8 run-state rows used by the progress drawer's reference table. */
export type ExplainerRunStateRow = {
  /** §B8 row name, e.g. 排队运行中 */
  state: string;
  /** precise status wording (design §F2.2) */
  headline: string;
  allowed: readonly string[];
  note: string;
};

export const EXPLAINER_RUN_STATE_ROWS: readonly ExplainerRunStateRow[] = [
  { state: "未启动", headline: "尚未开始", allowed: ["一键生成到预览", "进入具体步骤"], note: "先看六步产物就绪摘要，再决定分步还是一键。" },
  { state: "排队/运行中", headline: "等待开始 / 正在制作", allowed: ["暂停后续", "查看当前步骤", "关闭抽屉"], note: "没有可信估时不显示百分比或剩余分钟。" },
  { state: "已暂停", headline: "已暂停", allowed: ["继续", "确认最新输入后追加后续任务"], note: "继续从未完成部分恢复，复用成功产物。" },
  { state: "等待用户", headline: "需要处理 1 项问题", allowed: ["去处理", "查看当前步骤"], note: "处理成功后自动更新，不要求手工重建作品。" },
  { state: "部分失败", headline: "本次制作未完成", allowed: ["仅重试失败项", "继续其它独立任务"], note: "保留已完成部分与已采用素材。" },
  { state: "回执未知", headline: "原操作正在核对", allowed: ["查询原回执"], note: "核对完成前禁止提交同类新任务，避免重复 GPU 消耗。" },
  { state: "已完成", headline: "草稿已完成 / 视频已保存", allowed: ["去预览", "下载", "局部修改"], note: "只有真实可播放文件存在才显示已完成。" },
];

export type ExplainerRunGuide = {
  state: string;
  headline: string;
  allowed: readonly string[];
  note: string;
  /** true when the current status could not be recognised at all */
  unknownReceipt: boolean;
};

/**
 * Map a real run status onto the §B8 allowed-action row.  An unrecognised
 * status is treated as 回执未知: the UI must ask for the original receipt
 * instead of guessing whether the run is idle or running.
 */
export function explainerRunGuide(status: ExplainerRunStatus | string | null | undefined): ExplainerRunGuide {
  const row = (state: string): ExplainerRunStateRow =>
    EXPLAINER_RUN_STATE_ROWS.find((item) => item.state === state) ?? EXPLAINER_RUN_STATE_ROWS[0];
  if (!status) return { ...row("未启动"), unknownReceipt: false };
  switch (status) {
    case "QUEUED":
    case "PREFLIGHT":
    case "RUNNING":
    case "QC_RUNNING":
    case "PAUSING":
    case "CANCELLING":
      return { ...row("排队/运行中"), unknownReceipt: false };
    case "PAUSED":
      return { ...row("已暂停"), unknownReceipt: false };
    case "WAITING_INPUT":
      return { ...row("等待用户"), unknownReceipt: false };
    case "FAILED":
      return { ...row("部分失败"), unknownReceipt: false };
    case "READY_TO_EXPORT":
    case "EXPORTING":
    case "COMPLETED":
      return { ...row("已完成"), unknownReceipt: false };
    case "CANCELLED":
      return { ...row("未启动"), unknownReceipt: false };
    default:
      return { ...row("回执未知"), unknownReceipt: true };
  }
}

/** Statuses a real *run* reports as finished; used to stop dense polling. */
export function explainerRunIsTerminal(status: ExplainerRunStatus | string | null | undefined): boolean {
  return status === "COMPLETED" || status === "FAILED" || status === "CANCELLED";
}

export const EXPLAINER_AUTOMATION_LABELS: Record<string, string> = {
  AUTO_WITH_EXCEPTIONS: "自动成片 · 异常时暂停",
  REVIEW_BEFORE_RENDER: "成片前确认一次",
  MANUAL_REVIEW: "人工审查",
};

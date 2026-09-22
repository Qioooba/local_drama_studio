/**
 * Explainer factory view models and small pure helpers.
 *
 * Everything here is presentation logic.  The two rules it exists to keep are:
 *
 * 1. "Planned" and "actual" are never merged.  Plan frame ranges come from the
 *    plan, measured durations come from real audio, and a degraded shot keeps
 *    both its planned and its actual render type.
 * 2. Machine acceptance, human review and publication authorization are three
 *    different facts and get three different labels.
 */

import type { ExplainerStep, ExplainerStepStatus } from "../../generated/api";

export const RUN_STATUS_LABELS: Record<string, string> = {
  QUEUED: "排队中",
  PREFLIGHT: "预检中",
  RUNNING: "生产中",
  QC_RUNNING: "质检中",
  READY_TO_EXPORT: "待导出",
  EXPORTING: "导出中",
  COMPLETED: "已完成",
  PAUSING: "正在暂停",
  PAUSED: "已暂停",
  WAITING_INPUT: "等待处理",
  FAILED: "失败",
  CANCELLING: "正在取消",
  CANCELLED: "已取消",
};

export const STEP_STATUS_LABELS: Record<ExplainerStepStatus, string> = {
  PENDING: "待执行",
  BLOCKED: "等待上游",
  RUNNING: "执行中",
  SUCCEEDED: "已完成",
  SKIPPED_WITH_REASON: "已跳过（有原因）",
  RETRYABLE_FAILED: "可重试失败",
  TERMINAL_FAILED: "终止失败",
  CANCELLED: "已取消",
  STALE: "已过期",
};

export const RENDER_TYPE_LABELS: Record<string, string> = {
  STILL_MOTION: "插画轻动",
  PARALLAX: "分层视差",
  I2V: "图生视频",
  INFOGRAPHIC: "信息图 / 时间线",
  LICENSED_MEDIA: "授权素材",
};

export const STATEMENT_TYPE_LABELS: Record<string, string> = {
  FACT: "事实",
  ORIGINAL_EXPLANATION: "原创解释",
  TRANSITION: "过渡语",
  FICTION: "明确虚构",
  QUESTION: "提问",
};

export const CLAIM_STATUS_LABELS: Record<string, string> = {
  SUPPORTED: "有来源支持",
  DISPUTED: "来源存在冲突",
  UNVERIFIED: "尚未核验",
  EXCLUDED: "已排除",
};

export const SEVERITY_LABELS: Record<string, string> = {
  BLOCKER: "阻塞",
  MAJOR: "重要",
  MINOR: "轻微",
  INFO: "提示",
  UNKNOWN: "未确定",
};

/**
 * Run statuses that genuinely need a person.  Everything else is either the
 * machine working or the machine waiting on a resource, and must not be dressed
 * up as a user decision.
 */
const HUMAN_ACTION_STATUSES = new Set(["WAITING_INPUT", "PAUSED", "PAUSING"]);

export function needsHumanAction(status: string | null | undefined): boolean {
  return status ? HUMAN_ACTION_STATUSES.has(status) : false;
}

export function runStatusLabel(status: string | null | undefined): string {
  if (!status) return "尚未开始";
  return RUN_STATUS_LABELS[status] ?? status;
}

export function stepStatusLabel(status: string | null | undefined): string {
  if (!status) return "未知";
  return STEP_STATUS_LABELS[status as ExplainerStepStatus] ?? status;
}

export function stepIsBlockingDependents(step: ExplainerStep): boolean {
  return step.status === "BLOCKED" || step.status === "RETRYABLE_FAILED" || step.status === "TERMINAL_FAILED";
}

export function isStepTerminal(step: ExplainerStep): boolean {
  return ["SUCCEEDED", "SKIPPED_WITH_REASON", "TERMINAL_FAILED", "CANCELLED"].includes(step.status);
}

export function plannedVsActual(beat: Record<string, unknown>): { planned: string; actual: string | null; degraded: boolean } {
  const planned = String(beat.render_type ?? "");
  const actual = beat.render_type_actual ? String(beat.render_type_actual) : null;
  return { planned, actual, degraded: Boolean(actual && actual !== planned) };
}

export function formatSeconds(totalSeconds: number | null | undefined): string {
  if (totalSeconds === null || totalSeconds === undefined || Number.isNaN(totalSeconds)) return "—";
  const safe = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function formatMs(totalMs: number | null | undefined): string {
  if (totalMs === null || totalMs === undefined || Number.isNaN(totalMs)) return "尚未生成";
  return formatSeconds(totalMs / 1000);
}

/**
 * A duration target is never a promise.  Until real TTS exists the UI must say
 * which estimate stage it is showing.
 */
export function estimateStageLabel(stage: string | null | undefined): string {
  switch (stage) {
    case "MEASURED_TTS":
      return "按真实配音实测";
    case "SCRIPT_ESTIMATE":
      return "按讲稿字数估算";
    case "TOPIC_ROUGH_ESTIMATE":
      return "按题目粗估";
    default:
      return "待校准";
  }
}

export function coverageRows(coverage: Record<string, unknown> | null | undefined) {
  const value = coverage ?? {};
  const total = Number(value.total_frames ?? 0);
  const decoded = Number(value.decoded_frames ?? 0);
  const technical = Number(value.technical_checked_frames ?? 0);
  const semantic = Number(value.semantic_checked_frames ?? 0);
  const human = Number(value.human_reviewed_frames ?? 0);
  const pct = (part: number) => (total > 0 ? `${Math.round((part / total) * 100)}%（${part} / ${total} 帧）` : "未执行");
  return [
    { key: "decoded", label: "技术解码", detail: pct(decoded), note: "全帧解码覆盖，不等于内容理解" },
    { key: "technical", label: "技术检测", detail: pct(technical), note: "黑帧、冻结、花屏、PTS、尺寸、音轨" },
    { key: "semantic", label: "视觉语义", detail: pct(semantic), note: "抽样检测；未抽样区域不算已检查" },
    { key: "human", label: "人工审阅", detail: pct(human), note: "必须记录审阅者与时间范围" },
  ];
}

export function issueSeverityTone(severity: string | null | undefined): "danger" | "warn" | "muted" {
  if (severity === "BLOCKER") return "danger";
  if (severity === "MAJOR" || severity === "MINOR") return "warn";
  return "muted";
}

export function describeSkip(step: ExplainerStep): string | null {
  if (step.status !== "SKIPPED_WITH_REASON") return null;
  return step.skip_reason ? `已跳过：${step.skip_reason}` : "已跳过（缺少原因，属于缺陷）";
}

export function localeLabel(locale: string | null | undefined): string {
  if (!locale) return "未指定";
  if (locale.toLowerCase().startsWith("zh")) return "中文";
  if (locale.toLowerCase().startsWith("en")) return "English";
  return locale;
}

export function aspectLabel(aspect: string | null | undefined): string {
  switch (aspect) {
    case "16:9":
      return "横版 16:9";
    case "9:16":
      return "竖版 9:16";
    case "3:4":
      return "3:4";
    case "1:1":
      return "1:1";
    default:
      return aspect ?? "—";
  }
}

export function subtitleModeLabel(mode: string | null | undefined): string {
  switch (mode) {
    case "NONE":
      return "无字幕";
    case "BURNED":
      return "烧录字幕";
    case "SOFT":
      return "软字幕";
    case "BILINGUAL_BURNED":
      return "双语烧录";
    default:
      return mode ?? "—";
  }
}

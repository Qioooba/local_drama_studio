/**
 * 第 6 步：预览与导出 (review) — design §B7, §B8, §B12.5, §F2.2, §F3.
 *
 * Layout: the centre two thirds is the real film player with a simple timeline
 * under it (positioning, segment/beat click, previous/next frame); the right
 * third holds 字幕 / 背景音乐 / 输出 / 需处理问题.  There is no second giant audit
 * column any more — coverage and the machine/human/publication records are folded
 * into 详情, because the default view must stay readable.
 *
 * Honesty rules this page exists to keep (each one is covered by a test):
 *
 * * 「已提交」 is only printed for a real job id, 「草稿已生成/可下载」 only for a
 *   render whose media is really playable, and 「导出完成」 is never claimed at
 *   all — the download link appears only next to a playable file.
 * * A new preview keeps the previous film playable and labels it 旧版 until a
 *   different render id really arrives.
 * * `planExplainerRepairs` alone never means "已返工": the page submits the
 *   confirming call and, when nothing could be scheduled, says it is a plan and
 *   points at the responsible step instead.
 * * A blocked collection stage offers 「用现有结果继续」, which calls
 *   `continueExplainerCollection` with an Idempotency-Key and renders the real
 *   `REPLACED` / `NO_OP` / `BLOCKED` receipt (or the real missing-owner error).
 * * Problems are understandable sentences (「第 8 镜缺视频」「第 3 段配音失败」
 *   「2 条核心事实待核对」) with a jump to the right step *and object*.
 * * 只改字幕样式/音乐 never creates an image or TTS task; this page cannot.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  continueExplainerCollection,
  createExplainerEdition,
  discoverLocalSapiVoices,
  getExplainerSubtitles,
  listExplainerAssets,
  listExplainerBeatCandidates,
  listExplainerBeats,
  listExplainerEntityCandidates,
  listSubtitleStyleTemplates,
  planExplainerRepairs,
  preflightExplainerPlan,
  recordExplainerDecision,
  saveSubtitleStyleTemplate,
  runExplainerCompositionQc,
  startExplainerExport,
  startExplainerRender,
  type ExplainerCollectionContinueReceipt,
} from "../../generated/api";
import { routes } from "../../app/routeRegistry";
import { queryKeys } from "../../query/queryKeys";
import { completeOperation, operationIdempotencyKey, stableIdempotencyKey } from "../../services/commandId";
import { Drawer } from "../../components/ui/primitives";
import { MediaPicker } from "../media-picker/MediaPicker";
import { useExplainerActionBar } from "./ExplainerStepActionBar";
// The exclusive-playback rule (「starting one player pauses the others」, §E3) is
// shared with step 3: both pages own several real players.
import { useExclusivePlayback } from "./AudioPage";
import {
  AuthorityBadge,
  InlineError,
  InlineOk,
  Panel,
  SettingRow,
  StateNotice,
  type PageState,
} from "./components";
import {
  RenderPlayer,
  ReviewTimeline,
  clampFrame,
  frameForMs,
  lanesFromManifest,
  useSortedIssues,
  type RenderMedia,
} from "./media";
import {
  SEVERITY_LABELS,
  coverageRows,
  formatMs,
  issueSeverityTone,
  localeLabel,
  outputFor,
  subtitleModeLabel,
} from "./viewModels";
import type { ExplainerOutputRequest } from "../../generated/api";
import { useExplainerEditions, useExplainerNarration, useExplainerOverview, useExplainerQc, useExplainerRun, useExplainerSubtitles } from "./useExplainerQueries";
import "./explainers.css";
import "./explainers.steps36.css";

/* -------------------------------------------------------------------------- */
/* §B8 / §F2.2 run-state wording                                              */
/* -------------------------------------------------------------------------- */

export const RUN_STATE_WORDING: Record<string, string> = {
  QUEUED: "等待开始 / 等待 GPU",
  PREFLIGHT: "检查制作条件",
  RUNNING: "正在制作",
  QC_RUNNING: "正在检查草稿",
  PAUSING: "正在暂停",
  PAUSED: "已暂停",
  WAITING_INPUT: "需要处理 1 项问题",
  FAILED: "本次制作未完成",
  CANCELLING: "正在停止",
  CANCELLED: "已停止",
  READY_TO_EXPORT: "草稿已生成",
  EXPORTING: "正在保存视频",
  COMPLETED: "草稿已完成 / 视频已保存",
};

/**
 * Map a real run status onto the documented user wording.
 *
 * An unrecognised status is reported as an unknown receipt: the page asks for the
 * original operation instead of guessing whether the run is idle or working.
 */
export function runStateWording(
  status: string | null | undefined,
  options: { currentItem?: string | null; problemCount?: number } = {},
): { text: string; unknownReceipt: boolean } {
  if (!status) return { text: "尚未开始制作", unknownReceipt: false };
  const base = RUN_STATE_WORDING[String(status).toUpperCase()];
  if (!base) return { text: `回执未知（${String(status)}）`, unknownReceipt: true };
  if (status === "RUNNING" && options.currentItem) return { text: `正在制作：${options.currentItem}`, unknownReceipt: false };
  if (status === "WAITING_INPUT" && options.problemCount) {
    return { text: `需要处理 ${options.problemCount} 项问题`, unknownReceipt: false };
  }
  return { text: base, unknownReceipt: false };
}

/**
 * A percentage is only drawn from a trustworthy real ratio (§B8).  Everything
 * else reports "no trustworthy ratio" and shows no bar.
 */
export function trustworthyProgress(
  steps: Array<Record<string, unknown>> | null | undefined,
  progressJson: Record<string, unknown> | null | undefined,
): { completed: number; total: number; source: string } | null {
  const rows = steps ?? [];
  if (rows.length > 0) {
    const completed = rows.filter((step) => ["SUCCEEDED", "SKIPPED_WITH_REASON"].includes(String(step.status))).length;
    return { completed, total: rows.length, source: "按真实任务状态统计" };
  }
  const completed = Number(progressJson?.completed_steps);
  const total = Number(progressJson?.total_steps);
  if (Number.isFinite(completed) && Number.isFinite(total) && total > 0) {
    return { completed, total, source: "按服务端步骤计数统计" };
  }
  return null;
}

/* -------------------------------------------------------------------------- */
/* problem wording and jump targets (§B7)                                     */
/* -------------------------------------------------------------------------- */

export type ProblemContext = {
  beatOrdinal?: (beatId: string) => number | null;
  segmentOrdinal?: (segmentId: string) => number | null;
};

export type ProblemTarget = {
  page: "script" | "assets" | "audio" | "storyboard" | "clips" | "review";
  search: string;
  label: string;
};

const VIDEO_ISSUE_KINDS = ["MISSING_VIDEO", "VIDEO_MISSING", "NO_VIDEO", "CLIP_MISSING", "MISSING_CLIP", "VIDEO_FAILED"];
const IMAGE_ISSUE_KINDS = ["MISSING_IMAGE", "IMAGE_MISSING", "MISSING_KEYFRAME", "KEYFRAME_MISSING", "IMAGE_FAILED", "NO_IMAGE"];
const NARRATION_ISSUE_KINDS = ["TTS_FAILED", "NARRATION_FAILED", "MISSING_NARRATION", "ALIGNMENT_FAILED", "MISSING_ALIGNMENT", "SILENCE", "LOUDNESS"];
const FACT_ISSUE_KINDS = ["UNVERIFIED_CLAIM", "CLAIM_CONFLICT", "DISPUTED_CLAIM", "FACT_UNVERIFIED", "NEEDS_FACT_CHECK"];

/** The one aggregated sentence for fact problems (「2 条核心事实待核对」). */
export const FACT_PROBLEM_SENTENCE = "核心事实需要核对";

function issueKind(issue: Record<string, unknown>): string {
  return String(issue.issue_kind ?? "").toUpperCase();
}

function issueStep(issue: Record<string, unknown>): string {
  return String(issue.responsible_step_code ?? "").toUpperCase();
}

function matchesKind(kind: string, candidates: string[]): boolean {
  return candidates.some((item) => kind.includes(item));
}

function ordinalFor(
  id: string,
  lookup?: (value: string) => number | null,
): number | null {
  if (!id || !lookup) return null;
  const value = lookup(id);
  return value === null || value === undefined ? null : value;
}

/** The understandable sentence the default view shows (§B7). */
export function problemSentence(issue: Record<string, unknown>, context: ProblemContext = {}): string {
  const kind = issueKind(issue);
  const step = issueStep(issue);
  const beatId = issue.beat_id ? String(issue.beat_id) : "";
  const segmentId = issue.narration_segment_id ? String(issue.narration_segment_id) : "";
  const beatNo = ordinalFor(beatId, context.beatOrdinal);
  const segmentNo = ordinalFor(segmentId, context.segmentOrdinal);

  if (matchesKind(kind, VIDEO_ISSUE_KINDS) || (step === "VISUAL_GENERATION" && beatId && !matchesKind(kind, IMAGE_ISSUE_KINDS))) {
    return beatNo ? `第 ${beatNo} 镜缺视频` : "有画面段还缺可用视频";
  }
  if (matchesKind(kind, IMAGE_ISSUE_KINDS)) {
    return beatNo ? `第 ${beatNo} 镜缺画面` : "有画面段还缺已采用的画面";
  }
  if (matchesKind(kind, NARRATION_ISSUE_KINDS) || step === "NARRATION_TTS" || step === "NARRATION_ALIGN") {
    const failed = matchesKind(kind, ["FAILED", "MISSING"]);
    return segmentNo
      ? `第 ${segmentNo} 段配音${failed ? "失败" : "需要处理"}`
      : `有段落配音${failed ? "失败" : "需要处理"}`;
  }
  if (matchesKind(kind, FACT_ISSUE_KINDS) || step === "FACT_EXTRACT" || step === "NARRATION_WRITE" || step === "RESEARCH_ACQUIRE") {
    return FACT_PROBLEM_SENTENCE;
  }
  const observed = String(issue.observed ?? "").trim();
  if (observed) return observed.length > 60 ? `${observed.slice(0, 60)}…` : observed;
  return "需要处理一项检查问题";
}

/** Which step *and object* fixes this problem (§B7). */
export function problemTarget(issue: Record<string, unknown>, context: ProblemContext = {}): ProblemTarget {
  const kind = issueKind(issue);
  const step = issueStep(issue);
  const beatId = issue.beat_id ? String(issue.beat_id) : "";
  const segmentId = issue.narration_segment_id ? String(issue.narration_segment_id) : "";
  const beatNo = ordinalFor(beatId, context.beatOrdinal);
  const segmentNo = ordinalFor(segmentId, context.segmentOrdinal);

  if (beatId && (matchesKind(kind, VIDEO_ISSUE_KINDS) || step === "VISUAL_GENERATION" || step === "EXPLAINER_VISUAL_QC")) {
    return {
      page: "storyboard",
      search: `?beat=${encodeURIComponent(beatId)}`,
      label: beatNo ? `修改第 ${beatNo} 镜` : "修改镜头",
    };
  }
  if (beatId) {
    return {
      page: "storyboard",
      search: `?beat=${encodeURIComponent(beatId)}`,
      label: beatNo ? `修改第 ${beatNo} 镜` : "修改镜头",
    };
  }
  if (segmentId || step === "NARRATION_TTS" || step === "NARRATION_ALIGN" || matchesKind(kind, NARRATION_ISSUE_KINDS)) {
    return {
      page: "audio",
      search: segmentId ? `?segment=${encodeURIComponent(segmentId)}` : "",
      label: segmentNo ? `修改第 ${segmentNo} 段配音` : "修改配音",
    };
  }
  if (step === "IDENTITY_ASSETS") return { page: "assets", search: "", label: "去第 2 步处理人物与场景参考" };
  if (step === "VISUAL_GENERATION") return { page: "clips", search: "", label: "去第 5 步处理视频片段" };
  if (step === "COMPOSITION_RENDER" || step === "COMPOSITION_QC" || step === "EXPLAINER_EXPORT") {
    return { page: "review", search: "", label: "在本页更新预览" };
  }
  return { page: "script", search: "", label: "去第 1 步核对内容" };
}

/** §F2.4: only these stages can be resumed with the results already produced. */
export const COLLECTION_STEP_CODES = new Set([
  "IMAGE_COLLECT",
  "VIDEO_COLLECT",
  "IDENTITY_COLLECT",
  "VISUAL_CLIP_COLLECT",
  "VISUAL_GENERATION",
]);

export function collectionSteps(run: { steps?: Array<Record<string, unknown>> } | null | undefined) {
  return (run?.steps ?? []).filter(
    (step) =>
      COLLECTION_STEP_CODES.has(String(step.planned_step_code ?? "")) &&
      Boolean(step.job_id) &&
      ["BLOCKED", "RETRYABLE_FAILED", "NEEDS_ATTENTION"].includes(String(step.status)),
  );
}

/** Whether a probed capability is really available (never assumed). */
export function capabilityAvailable(snapshot: Record<string, unknown> | null | undefined, code: string): boolean {
  const rows = Array.isArray(snapshot?.capabilities) ? (snapshot?.capabilities as Array<Record<string, unknown>>) : [];
  return rows.some((row) => {
    const name = String(row.name ?? row.code ?? row.capability ?? "").toUpperCase();
    const status = String(row.status ?? row.availability ?? "").toUpperCase();
    return name === code.toUpperCase() && ["AVAILABLE", "READY", "OK"].includes(status);
  });
}

/** The tier a render really has; only the real pixel height is claimed. */
export function resolutionTierLabel(width: unknown, height: unknown): string {
  const value = Number(height ?? 0);
  if (!Number.isFinite(value) || value <= 0) return "当前原生档位（未记录像素）";
  const widthValue = Number(width ?? 0);
  const tier = ["4320", "2160", "1440", "1080", "720", "480", "360", "240"].find((item) => value >= Number(item));
  return tier ? `${tier}p${widthValue > 0 ? `（${widthValue}×${value}）` : ""}` : `当前原生档位（${value}p）`;
}

/* -------------------------------------------------------------------------- */
/* page                                                                       */
/* -------------------------------------------------------------------------- */

type PreparedExport = {
  /** A real job id, or null when the server did not schedule anything. */
  jobId: string | null;
  status: string;
  packageId: string | null;
  note: string;
};

export function ExplainerReviewPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const overview = useExplainerOverview(projectId);
  const editions = useExplainerEditions(projectId);
  const editionList = (editions.data?.editions ?? []) as Array<Record<string, unknown>>;
  const editionIdParam = searchParams.get("edition");
  const activeEdition = editionList.find((edition) => String(edition.id) === editionIdParam) ?? editionList[0] ?? null;
  const editionId = activeEdition ? String(activeEdition.id) : null;
  const renderMedia = (activeEdition?.current_render as RenderMedia | null) ?? null;
  const qc = useExplainerQc(editionId, renderMedia?.id ?? null);
  const latestRun = overview.data?.latest_run ?? null;
  const run = useExplainerRun(latestRun?.id ?? null);
  const runRow = run.data?.run ?? latestRun;
  const voiceLocale = activeEdition ? String(activeEdition.voice_locale ?? "zh-CN") : "zh-CN";
  const beatsQuery = useQuery({
    queryKey: queryKeys.explainers.beats(projectId, editionId ?? null),
    queryFn: () => listExplainerBeats(projectId, editionId ?? undefined),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
  const beats = beatsQuery.data?.beats ?? [];
  const narration = useExplainerNarration(editionId, voiceLocale);
  const segments = (narration.data?.segments as Array<Record<string, unknown>> | undefined) ?? [];
  const subtitles = useExplainerSubtitles(editionId, voiceLocale, "SRT");
  const assets = useQuery({
    queryKey: queryKeys.explainers.assets(projectId),
    queryFn: () => listExplainerAssets(projectId),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });

  const [requestedFrame, setRequestedFrame] = useState(0);
  const [playheadFrame, setPlayheadFrame] = useState(0);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewNote, setReviewNote] = useState("");
  const [selectedIssueId, setSelectedIssueId] = useState<string | null>(null);
  const [renderRequestedAt, setRenderRequestedAt] = useState(0);
  const [seenRenderId, setSeenRenderId] = useState<string | null>(null);
  const [exportReceipt, setExportReceipt] = useState<PreparedExport | null>(null);
  const [repairReport, setRepairReport] = useState<Record<string, unknown> | null>(null);
  const [continueReceipt, setContinueReceipt] = useState<ExplainerCollectionContinueReceipt | null>(null);
  const [versionDrawerOpen, setVersionDrawerOpen] = useState(false);
  const [newVersionLocale, setNewVersionLocale] = useState("zh-CN");
  const [newVersionAspect, setNewVersionAspect] = useState<"16:9" | "9:16" | "3:4" | "1:1">("16:9");
  const [newVersionSubtitleMode, setNewVersionSubtitleMode] = useState<ExplainerOutputRequest["subtitle_mode"]>("BURNED");
  // Step 6 preview-only controls.  They change this page's preview and a saved
  // project style template; they never start an image or TTS task (E18).
  const [subtitleTemplateId, setSubtitleTemplateId] = useState("");
  const [subtitleSize, setSubtitleSize] = useState<"小" | "标准" | "大">("标准");
  const [subtitlePosition, setSubtitlePosition] = useState<"底部" | "顶部">("底部");
  const [subtitleSafeArea, setSubtitleSafeArea] = useState<"标准" | "加宽">("标准");
  const [musicChoice, setMusicChoice] = useState<"NONE" | "LIBRARY">("NONE");
  const [musicMediaId, setMusicMediaId] = useState("");
  const [musicVolumeDb, setMusicVolumeDb] = useState("-18");
  const [duckNarration, setDuckNarration] = useState(true);
  const [includeSubtitleFile, setIncludeSubtitleFile] = useState(true);
  const [includeNarrationStem, setIncludeNarrationStem] = useState(false);
  const playerRef = useRef<HTMLDivElement | null>(null);
  const pageRef = useRef<HTMLDivElement | null>(null);
  useExclusivePlayback(pageRef);

  // Keep the previous film playable when a new preview is submitted: the read
  // model only exposes `current_render`, so the page holds the last playable one
  // and labels it 旧版 until a *different* render id really arrives.
  useEffect(() => {
    if (renderMedia?.id && renderMedia.id !== seenRenderId) {
      setSeenRenderId(renderMedia.id);
      setRenderRequestedAt(0);
    }
  }, [renderMedia?.id, seenRenderId]);
  const filmIsOldVersion = renderRequestedAt > 0 && Boolean(renderMedia);

  const issues = useMemo(() => {
    const open = (qc.data?.open_issues ?? []) as Array<Record<string, unknown>>;
    const all = (qc.data?.issues ?? []) as Array<Record<string, unknown>>;
    return open.concat(all.filter((issue) => !open.some((item) => String(item.id) === String(issue.id))));
  }, [qc.data]);

  const beatOrdinal = useMemo(() => {
    const map = new Map<string, number>();
    for (const beat of beats) map.set(String(beat.id), Number(beat.ordinal ?? 0) + 1);
    return (beatId: string) => map.get(beatId) ?? null;
  }, [beats]);
  const segmentOrdinal = useMemo(() => {
    const map = new Map<string, number>();
    for (const segment of segments) map.set(String(segment.id), Number(segment.ordinal ?? 0) + 1);
    return (segmentId: string) => map.get(segmentId) ?? null;
  }, [segments]);
  const problemContext: ProblemContext = { beatOrdinal, segmentOrdinal };

  const factProblemCount = issues.filter(
    (issue) => problemSentence(issue, problemContext) === FACT_PROBLEM_SENTENCE,
  ).length;
  // Fact problems are represented by the one aggregated sentence below, so the
  // detailed rows stay reserved for the problems that have a concrete object to
  // jump to (§B7).
  const listedIssues = issues.filter((issue) => problemSentence(issue, problemContext) !== FACT_PROBLEM_SENTENCE);
  const issueList = useSortedIssues(listedIssues, { pageSize: 6 });
  const selectedIssue =
    issueList.sorted.find((issue) => String(issue.id) === selectedIssueId) ?? issueList.sorted[0] ?? null;

  const coverage = coverageRows(qc.data?.coverage as Record<string, unknown> | undefined);
  const compositionItems = (activeEdition?.composition_items ?? []) as Array<Record<string, unknown>>;
  const timelineLanes = useMemo(
    () =>
      lanesFromManifest(compositionItems, {
        fpsNum: Number((activeEdition?.composition as Record<string, unknown> | null)?.fps_num ?? 0),
        fpsDen: Number((activeEdition?.composition as Record<string, unknown> | null)?.fps_den ?? 0),
      }),
    [activeEdition, compositionItems],
  );
  const videoItems = compositionItems.filter((item) => String(item.track ?? "").toUpperCase() === "VIDEO");

  const voicesQuery = useQuery({
    queryKey: ["explainer-tts-voices"],
    queryFn: () => discoverLocalSapiVoices(),
    staleTime: 60_000,
  });
  const availableLanguages = useMemo(() => {
    const found = new Set<string>();
    for (const voice of voicesQuery.data?.items ?? []) {
      const culture = String(voice.culture ?? "").trim();
      if (culture) found.add(culture);
    }
    return [...found].sort();
  }, [voicesQuery.data]);

  const templatesQuery = useQuery({
    queryKey: ["explainer-subtitle-style-templates", projectId],
    queryFn: () => listSubtitleStyleTemplates(projectId),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
  const styleTemplates = templatesQuery.data?.items ?? [];
  const selectedTemplate = styleTemplates.find((item) => String(item.id) === subtitleTemplateId) ?? null;

  /* ------------------------------------------------------- §B8 run wording */
  const worded = runStateWording(runRow?.projected_status ?? null, {
    currentItem: runRow?.current_stage_code ? String(runRow.current_stage_code) : null,
    problemCount: Number(overview.data?.open_issue_count ?? issues.length),
  });
  const progress = trustworthyProgress(
    (runRow?.steps ?? []) as unknown as Array<Record<string, unknown>>,
    (runRow?.progress_json ?? null) as Record<string, unknown> | null,
  );

  /* --------------------------------------------------------------- mutations */
  const render = useMutation({
    mutationFn: async () => {
      if (!editionId) throw new Error("选择一个输出版本");
      const plan = await startExplainerRender(
        editionId,
        { freeze: true, confirm: false },
        stableIdempotencyKey("explainer-render-plan", { editionId }),
      );
      const planRecord = plan as Record<string, unknown>;
      const planStatus = String(planRecord.status ?? "");
      // Only READY_TO_START may be confirmed.  A BLOCKED / CAPABILITY_UNAVAILABLE
      // plan writes nothing and must never be reported as 已提交.
      if (planStatus === "BLOCKED" || planStatus === "CAPABILITY_UNAVAILABLE") {
        return { outcome: "BLOCKED" as const, plan: planRecord };
      }
      if (planStatus !== "READY_TO_START") {
        return { outcome: "UNEXPECTED" as const, plan: planRecord };
      }
      const submitted = await startExplainerRender(
        editionId,
        { freeze: true, confirm: true, composition_revision_id: planRecord.composition_revision_id ?? null },
        stableIdempotencyKey("explainer-render", { editionId, composition: planRecord.composition_revision_id }),
      );
      return { outcome: "SUBMITTED" as const, submitted: submitted as Record<string, unknown> };
    },
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
      if (result.outcome === "BLOCKED") {
        const blockers = (result.plan.blockers as Array<Record<string, unknown>> | undefined) ?? [];
        setFeedback(null);
        setError(`预览被阻塞，未提交任何任务：${blockers.map((item) => String(item.message ?? "")).join("；") || "缺少可渲染的前置条件"}`);
        return;
      }
      if (result.outcome === "UNEXPECTED") {
        setFeedback(null);
        setError(`预览预检返回了未预期的状态 ${String(result.plan.status ?? "")}，未提交。`);
        return;
      }
      const submitted = result.submitted;
      const jobId = submitted.job_id ? String(submitted.job_id) : "";
      if (String(submitted.status) === "ACCEPTED" && jobId) {
        setError(null);
        setRenderRequestedAt(Date.now());
        setFeedback(`已提交预览合成任务 ${jobId}；旧版成片仍然可以播放，新版到达前不会隐藏它。`);
        return;
      }
      setFeedback(null);
      setError(`预览未被接受（${String(submitted.status ?? "未知")}）：没有创建合成任务，画面不会被替换。`);
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  /**
   * 「运行技术质检」.  The delivery package ships the QC report of the render it was
   * built from; when the film was driven stage by stage that report was written as
   * `NOT_RUN`, because `COMPOSITION_QC` was only reachable from a whole graph run.
   * This submits the same real stage command the graph would have.
   */
  const compositionQc = useMutation({
    mutationFn: async () => {
      if (!editionId) throw new Error("选择一个输出版本");
      return runExplainerCompositionQc(
        editionId,
        { render_id: String(renderMedia?.id ?? "") },
        stableIdempotencyKey("explainer-composition-qc", { editionId, renderId: String(renderMedia?.id ?? "") }),
      );
    },
    onSuccess: async (receipt) => {
      const record = receipt as unknown as Record<string, unknown>;
      const status = String(record.status ?? "");
      const jobId = record.job_id ? String(record.job_id) : "";
      if (status === "ACCEPTED" && jobId) {
        setError(null);
        setFeedback(`已提交成片技术质检任务 ${jobId}；报告到达前界面不会声称已通过。`);
      } else {
        const blockers = (record.blockers as Array<Record<string, unknown>> | undefined) ?? [];
        setFeedback(null);
        setError(
          `技术质检未提交（${status || "未知"}）：${
            blockers.map((item) => String(item.message ?? "")).join("；") || String(record.reason ?? "没有可领取的执行器")
          }`,
        );
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  /**
   * 「只修复这项」.  The preview call only *plans* (`would_create_jobs=false`), so
   * the confirming call is what may create real jobs; when the confirming call
   * schedules nothing the page says it is a plan and points at the step instead
   * of claiming the rework was submitted.
   */
  const repair = useMutation({
    mutationFn: async () => {
      if (!selectedIssue) throw new Error("先选择一个要修复的问题");
      const revision = Number(overview.data?.video?.revision ?? 1);
      const scope = `explainer-repairs:${projectId}:${String(selectedIssue.id)}`;
      const request = { issue_ids: [String(selectedIssue.id)], expected_revision: revision };
      const planned = await planExplainerRepairs(projectId, { ...request, confirm: false });
      const confirmed = await planExplainerRepairs(
        projectId,
        { ...request, confirm: true },
        operationIdempotencyKey(scope, request),
      );
      completeOperation(scope);
      return { planned: planned as Record<string, unknown>, confirmed: confirmed as Record<string, unknown> };
    },
    onSuccess: async (result) => {
      const plan = result.planned.plan as Record<string, unknown> | undefined;
      const confirmed = result.confirmed;
      const jobIds = Array.isArray(confirmed.job_ids) ? (confirmed.job_ids as unknown[]).map(String) : [];
      const unschedulable = Array.isArray(confirmed.unschedulable) ? (confirmed.unschedulable as Array<Record<string, unknown>>) : [];
      setRepairReport({ plan: plan ?? {}, confirmed });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
      if (confirmed.submitted === true && jobIds.length > 0) {
        setError(null);
        setFeedback(
          `已提交局部返工：${jobIds.length} 个真实任务（${jobIds.join("、")}）；影响 ${Number(plan?.task_count ?? 0)} 项，人工锁定镜头不在批次范围内。`,
        );
        return;
      }
      setError(null);
      setFeedback(
        `这是修复计划，没有创建任务（${String(confirmed.status ?? "REPAIR_NOT_SCHEDULABLE")}）：` +
          `${unschedulable.length > 0 ? `${unschedulable.length} 个责任步骤不能单独执行，请在该步骤重跑；` : ""}` +
          "计划里只有影响范围，返工需要真实任务才算提交。",
      );
    },
    onError: (mutationError) => {
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  /**
   * 「用现有结果继续」 for a collection stage blocked by failed candidates.
   * The candidate ids are read from the real candidate lists, never invented.
   */
  const continueCollection = useMutation({
    mutationFn: async (step: Record<string, unknown>) => {
      if (!runRow?.id) throw new Error("本次运行不存在，无法继续收集");
      const taskCode = String(step.planned_step_code ?? "");
      const selected: string[] = [];
      if (taskCode === "IDENTITY_COLLECT") {
        const assetView = await listExplainerAssets(projectId);
        for (const entity of assetView.entities ?? []) {
          const page = await listExplainerEntityCandidates(projectId, String(entity.id), {});
          const candidates = (page.candidates ?? []) as unknown as Array<Record<string, unknown>>;
          selected.push(
            ...candidates
              .filter((candidate) => String(candidate.status) === "READY" && (candidate.selected || candidate.adopted))
              .map((candidate) => String(candidate.id)),
          );
        }
      } else {
        const purpose = ["VIDEO_COLLECT", "VISUAL_CLIP_COLLECT"].includes(taskCode) ? "VISUAL" : "KEYFRAME";
        const beatView = await listExplainerBeats(projectId, editionId ?? undefined);
        for (const beat of beatView.beats ?? []) {
          const page = await listExplainerBeatCandidates(projectId, String(beat.id), {
            purpose,
            edition_id: editionId ?? undefined,
          });
          const candidates = (page.candidates ?? []) as unknown as Array<Record<string, unknown>>;
          selected.push(
            ...candidates
              .filter((candidate) => String(candidate.status) === "READY" && (candidate.selected || candidate.adopted))
              .map((candidate) => String(candidate.id)),
          );
        }
      }
      return continueExplainerCollection(
        String(runRow.id),
        String(step.id),
        {
          expected_old_job_id: step.job_id ? String(step.job_id) : null,
          expected_task_revision: step.revision === undefined || step.revision === null ? null : Number(step.revision),
          selected_candidate_ids: selected,
          actor: "local-user",
          reason: "使用已有结果继续",
        },
        stableIdempotencyKey("explainer-collection-continue", {
          runId: runRow.id,
          stepBindingId: step.id,
          oldJobId: step.job_id ?? null,
          selected,
        }),
      );
    },
    onSuccess: async (receipt) => {
      setContinueReceipt(receipt);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
      if (receipt.status === "REPLACED" && receipt.replacement_job_id) {
        setError(null);
        setFeedback(
          `已用现有结果继续：替换了收集任务，新作业 ${receipt.replacement_job_id}（旧作业 ${String(receipt.previous_job_id ?? "—")} 已取消）。` +
            "失败的候选与原始记录保留在审计里。",
        );
        return;
      }
      setFeedback(null);
      setError(`没有替换任务（${receipt.status}）${receipt.reason ? `：${receipt.reason}` : "。"}`);
    },
    onError: (mutationError) => {
      setContinueReceipt(null);
      const message = mutationError instanceof Error ? mutationError.message : String(mutationError);
      setFeedback(null);
      setError(
        message.includes("REQUIRED_OWNER_MISSING")
          ? `${message}（没有创建或替换任何作业；请先补齐这些必需对象）`
          : message,
      );
    },
  });

  const decide = useMutation({
    mutationFn: async (kind: "HUMAN_APPROVED" | "CHANGES_REQUESTED" | "PUBLICATION_AUTHORIZED") => {
      if (!editionId) throw new Error("选择一个输出版本");
      if (!renderMedia) throw new Error("该版本还没有可确认的成片；没有媒体就不能登记“确认成片”");
      if (!renderMedia.sha256) throw new Error("当前成片还没有内容哈希，不能登记决定");
      return recordExplainerDecision(editionId, {
        decision_kind: kind,
        subject_kind: "COMPOSITION_RENDER",
        subject_revision_id: renderMedia.id,
        subject_hash: renderMedia.sha256,
        actor: "local-user",
        reviewed_intervals: reviewedIntervals(playheadFrame, requestedFrame),
        note: reviewNote,
      });
    },
    onSuccess: async (_result, kind) => {
      setError(null);
      setFeedback(
        kind === "PUBLICATION_AUTHORIZED"
          ? "已记录发布授权（与人工确认、机器检查分开记录）。"
          : "已记录人工确认：这是本机操作者的真实动作，不等于认证到某个自然人，也不代表发布授权。",
      );
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const exportVideo = useMutation({
    mutationFn: async () => {
      if (!editionId) throw new Error("选择一个输出版本");
      const result = await startExplainerExport(
        editionId,
        {
          render_id: renderMedia?.id ?? null,
          include_subtitles: includeSubtitleFile,
          include_stems: includeNarrationStem,
          confirm: true,
        },
        stableIdempotencyKey("explainer-export", {
          editionId,
          renderId: renderMedia?.id ?? null,
          include_subtitles: includeSubtitleFile,
          include_stems: includeNarrationStem,
        }),
      );
      const record = result as Record<string, unknown>;
      const jobId = record.job_id ? String(record.job_id) : "";
      const status = String(record.status ?? "");
      if (status === "ACCEPTED" && jobId) {
        return { jobId, status, packageId: record.package_id ? String(record.package_id) : null, note: "" } satisfies PreparedExport;
      }
      const blockers = Array.isArray(record.blockers) ? (record.blockers as Array<Record<string, unknown>>) : [];
      return {
        jobId: null,
        status,
        packageId: record.package_id ? String(record.package_id) : null,
        note: blockers.map((item) => String(item.message ?? "")).join("；") || String(record.reason ?? ""),
      } satisfies PreparedExport;
    },
    onSuccess: async (receipt) => {
      setExportReceipt(receipt);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
      if (receipt.jobId) {
        setError(null);
        setFeedback(`已提交导出任务 ${receipt.jobId}（包 ${receipt.packageId ?? "—"}）；文件可用后这里才会出现下载入口。`);
        return;
      }
      setFeedback(null);
      setError(`导出未创建任务（${receipt.status}）${receipt.note ? `：${receipt.note}` : "。"}没有生成可下载文件。`);
    },
    onError: (mutationError) => {
      setExportReceipt(null);
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const downloadSubtitles = useMutation({
    mutationFn: async () => {
      if (!editionId) throw new Error("选择一个输出版本");
      const payload = await getExplainerSubtitles(editionId, { locale: voiceLocale, format: "SRT" });
      const rendered = typeof payload.rendered === "string" ? payload.rendered : "";
      if (!rendered.trim()) throw new Error("该版本还没有字幕 revision，不能生成字幕附件");
      return rendered;
    },
    onSuccess: (rendered) => {
      setError(null);
      const saved = saveTextFile(rendered, `subtitles-${voiceLocale}.srt`, "application/x-subrip");
      setFeedback(
        saved
          ? "已下载字幕附件（由真实字幕 revision 序列化）。"
          : "这份字幕文本已从服务端取回，但当前环境无法生成下载文件；请用接口返回的内容自行保存。",
      );
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const saveStyleTemplate = useMutation({
    mutationFn: async () => {
      const code = `explainer-caption-${subtitleSize === "小" ? "s" : subtitleSize === "大" ? "l" : "m"}-${subtitlePosition === "底部" ? "bottom" : "top"}`;
      return saveSubtitleStyleTemplate(projectId, {
        code,
        title: `解说字幕 · ${subtitleSize} · ${subtitlePosition}`,
        style: {
          size: subtitleSize === "小" ? 40 : subtitleSize === "大" ? 60 : 48,
          position: subtitlePosition === "底部" ? "BOTTOM" : "TOP",
          safe_area: subtitleSafeArea === "加宽" ? "WIDE" : "STANDARD",
        },
        change_note: "第 6 步字幕样式调整（只影响字幕排版与合成，不触发图像或 TTS）",
      });
    },
    onSuccess: async (result) => {
      setError(null);
      setFeedback(
        `已保存项目字幕样式模板「${String((result.template as unknown as Record<string, unknown>).title ?? "")}」（版本 ${String((result.template as unknown as Record<string, unknown>).version_no ?? "")}）。` +
          "这次保存只改字幕排版与后续合成，不会重跑图像或 TTS 任务。",
      );
      await queryClient.invalidateQueries({ queryKey: ["explainer-subtitle-style-templates", projectId] });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const bilingualProbe = useMutation({
    mutationFn: () =>
      preflightExplainerPlan(projectId, {
        outputs: [outputFor(voiceLocale, "16:9" as ExplainerOutputRequest["aspect_ratio"], "BILINGUAL_BURNED", [voiceLocale, "en-US"])],
      }),
  });
  const bilingualExecutable = Boolean(bilingualProbe.data?.executable);

  const addVersion = useMutation({
    mutationFn: async () => {
      const output = outputFor(
        newVersionLocale,
        newVersionAspect,
        newVersionSubtitleMode,
        newVersionSubtitleMode === "NONE" ? [] : [newVersionLocale],
      );
      const report = await preflightExplainerPlan(projectId, { outputs: [output] });
      if (!report.executable) {
        return {
          created: false as const,
          blockers: report.blockers.map((blocker) => blocker.message),
        };
      }
      const created = await createExplainerEdition(projectId, output as unknown as Record<string, unknown>);
      return { created: true as const, edition: created };
    },
    onSuccess: async (result) => {
      if (result.created) {
        setError(null);
        setFeedback(`已创建新的输出版本（${newVersionLocale} · ${newVersionAspect} · ${subtitleModeLabel(newVersionSubtitleMode)}）；它需要自己的配音与合成，当前版本保持可读。`);
        setVersionDrawerOpen(false);
        await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
        return;
      }
      setFeedback(null);
      setError(`新增版本的能力预检未通过，没有创建任何版本：${result.blockers.join("；") || "存在阻塞项"}`);
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  /* ------------------------------------------------------------------ states */
  const state = useMemo<PageState | null>(() => {
    if (editions.isPending) return { kind: "loading", message: "正在载入输出版本…" };
    if (editions.isError && !editions.data) {
      return {
        kind: "failed",
        title: "无法载入输出版本",
        body: editions.error instanceof Error ? editions.error.message : "未知错误",
        action: <button type="button" onClick={() => { void editions.refetch(); }}>重新读取</button>,
      };
    }
    if (editionList.length === 0) {
      return {
        kind: "empty",
        title: "还没有输出版本",
        body: "先在总览提交预检并完成生产，才会有成片可预览与导出。",
      };
    }
    if (overview.data?.capability_snapshot && overview.data.capability_snapshot.probed === false && !activeEdition?.current_render) {
      return {
        kind: "no_capability",
        title: "尚未接入能力探查",
        body: "无法确认本机的合成与导出能力，因此不会排队渲染任务，也不会给出乐观的完成提示。",
        action: <Link to={routes.systemCapabilities(projectId)}>前往能力与模型</Link>,
      };
    }
    if (!activeEdition?.current_render) {
      return {
        kind: "empty",
        title: "还没有可审的成片",
        body: "渲染完成后才能预览与导出；已提交渲染不等于制作成功。",
      };
    }
    if (runRow?.projected_status === "QC_RUNNING" || runRow?.projected_status === "RUNNING") {
      return { kind: "running", title: "生产或质检进行中", body: "覆盖报告会在对应阶段完成后更新。", progress: null };
    }
    if (qc.data?.status === "STALE") {
      return { kind: "stale", title: "质检报告已过期", body: "对象哈希已变化，旧报告不能用于放行；请重新检查受影响节点。" };
    }
    return null;
  }, [
    activeEdition,
    editionList.length,
    editions.data,
    editions.error,
    editions.isError,
    editions.isPending,
    overview.data?.capability_snapshot,
    projectId,
    qc.data?.status,
    runRow?.projected_status,
  ]);

  /* ---------------------------------------------------------------- bar + UI */
  const filmPlayable = Boolean(renderMedia?.playable);
  const blockedCollections = collectionSteps(runRow as unknown as { steps?: Array<Record<string, unknown>> } | null);
  const upscaleAvailable = capabilityAvailable(
    overview.data?.capability_snapshot as Record<string, unknown> | undefined,
    "UPSCALE_VIDEO",
  );
  const duckingFromProfile = profileDuckingSupported(assets.data?.channel_profile_version as Record<string, unknown> | null);

  const primary = filmPlayable
    ? {
      label: "导出视频",
      onClick: () => {
        setFeedback(null);
        setError(null);
        exportVideo.mutate();
      },
      disabled: !editionId || exportVideo.isPending,
      disabledReason: editionId ? null : "先选择一个输出版本。",
      busy: exportVideo.isPending,
    }
    : {
      label: "生成预览",
      onClick: () => {
        setFeedback(null);
        setError(null);
        render.mutate();
      },
      disabled: !editionId || render.isPending,
      disabledReason: !editionId
        ? "还没有输出版本；先在第 1 步提交内容并完成生产。"
        : null,
      busy: render.isPending,
    };

  useExplainerActionBar({
    primary,
    defer: { label: "稍后处理", onClick: () => navigate(routes.explainerPage(projectId, "clips")) },
    summary: filmPlayable
      ? `${filmIsOldVersion ? "旧版成片" : "当前成片"} · ${formatMs(renderMedia?.duration_ms ?? null)} · ${renderMedia?.frame_count ?? "?"} 帧`
      : wordedSummary(worded.text, progress),
  });

  const setSelectedEdition = (value: string) => {
    setSearchParams((params) => {
      const next = new URLSearchParams(params);
      next.set("edition", value);
      return next;
    });
  };

  const selectIssue = (issue: Record<string, unknown>) => {
    setSelectedIssueId(String(issue.id));
    setExportReceipt(null);
    if (issue.start_ms !== null && issue.start_ms !== undefined && renderMedia?.fps_num && renderMedia?.fps_den) {
      const frame = frameForMs(Number(issue.start_ms), renderMedia.fps_num, renderMedia.fps_den);
      if (frame !== null) setRequestedFrame(clampFrame(frame, renderMedia.frame_count));
    }
  };

  const currentCue = currentCueFor(playheadFrame, subtitles.data?.cues, renderMedia?.fps_num, renderMedia?.fps_den);

  return <div className="explainer-page" ref={pageRef}>
    <Panel
      title="成片版本"
      subtitle="预览、人工确认与导出都绑定冻结版本，不读取“最新”。"
      actions={
        <>
          {editionList.length > 1 ? (
            <label className="explainer-field">
              输出版本
              <select value={editionId ?? ""} onChange={(event) => setSelectedEdition(event.target.value)}>
                {editionList.map((edition) => (
                  <option key={String(edition.id)} value={String(edition.id)}>
                    {localeLabel(String(edition.voice_locale))} · {String(edition.aspect_ratio)} · {subtitleModeLabel(String(edition.subtitle_mode))}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <AuthorityBadge
            kind={qc.data?.publication_decision ? "publication" : qc.data?.human_decision ? "human" : qc.data?.machine_decision ? "machine" : "none"}
          />
        </>
      }
    >
      <div className="explainer-run-state" role="status">
        <span className="badge">{filmIsOldVersion ? "旧版" : "当前版本"}</span>
        <strong>{worded.text}</strong>
        {progress ? (
          <span className="muted">
            已完成 {progress.completed} / {progress.total} 项任务（{progress.source}）
          </span>
        ) : (
          <span className="muted">没有可信的总量比例，因此不显示百分比或剩余时间。</span>
        )}
        {worded.unknownReceipt ? <span className="badge warn">在核对原回执前不会提交同类新任务</span> : null}
      </div>
      <SettingRow
        label="当前输出版本"
        value={`${localeLabel(voiceLocale)} · ${String(activeEdition?.aspect_ratio ?? "—")} · ${subtitleModeLabel(String(activeEdition?.subtitle_mode ?? "NONE"))}`}
      />
      <StateNotice state={state} />
      {editions.isError && editions.data ? (
        <InlineError message={`输出版本更新失败：${editions.error instanceof Error ? editions.error.message : "未知错误"}。已保留上一次读取到的版本与成片。`} />
      ) : null}
      <InlineOk message={feedback} />
      <InlineError message={error} />
    </Panel>

    <div className="explainer-review-layout">
      <div className="explainer-stack">
        <Panel
          title="成片播放器"
          subtitle="播放、逐帧与定位都绑定真实成片；进度来自解码器而不是假帧数。"
          actions={filmIsOldVersion ? <span className="badge warn">这是旧版成片，仍在播放</span> : null}
        >
          <div className="explainer-subtitle-stage" ref={playerRef}>
            <RenderPlayer
              media={renderMedia}
              seekToFrame={requestedFrame}
              onFrameChange={setPlayheadFrame}
            />
            {currentCue ? (
              <div
                className={`explainer-subtitle-overlay${subtitlePosition === "顶部" ? " is-top" : ""}`}
                aria-hidden="true"
                style={{ fontSize: subtitleSize === "小" ? 14 : subtitleSize === "大" ? 22 : 18 }}
              >
                <span className="line-primary">{String(currentCue.text ?? "")}</span>
                {currentCue.paired_text ? <span className="line-secondary">{String(currentCue.paired_text)}</span> : null}
              </div>
            ) : null}
          </div>
          <div className="explainer-actions" style={{ marginTop: 10 }}>
            <button type="button" disabled={!filmPlayable} onClick={() => playerRef.current?.querySelector("video")?.play()}>播放</button>
            <button type="button" disabled={!filmPlayable} onClick={() => playerRef.current?.querySelector("video")?.pause()}>暂停</button>
            <button
              type="button"
              disabled={!filmPlayable}
              onClick={() => setRequestedFrame((value) => clampFrame(value - 1, renderMedia?.frame_count))}
            >
              前一帧
            </button>
            <button
              type="button"
              disabled={!filmPlayable}
              onClick={() => setRequestedFrame((value) => clampFrame(value + 1, renderMedia?.frame_count))}
            >
              后一帧
            </button>
            <button type="button" disabled={!filmPlayable} onClick={() => setRequestedFrame(0)}>回到首帧</button>
            <button
              type="button"
              disabled={!selectedIssue || selectedIssue.start_ms === null || selectedIssue.start_ms === undefined}
              onClick={() => selectedIssue && selectIssue(selectedIssue)}
            >
              定位到选中问题
            </button>
          </div>
          <p className="explainer-note">
            目标帧 {requestedFrame}
            {renderMedia?.frame_count ? ` / ${Number(renderMedia.frame_count) - 1}` : ""} · 实际帧 {playheadFrame}；
            浏览器对压缩关键帧的 seek 不保证逐帧精确，所以这里显示解码器的真实进度。
          </p>
          <ReviewTimeline lanes={timelineLanes} busy={editions.isPending} />
          {videoItems.length > 0 ? (
            <div className="explainer-actions" role="group" aria-label="按镜头定位">
              {videoItems.slice(0, 60).map((item, index) => {
                const beatId = item.beat_id ? String(item.beat_id) : "";
                const ordinal = beatId ? beatOrdinal(beatId) : null;
                return (
                  <button
                    type="button"
                    key={String(item.id ?? `video-${index}`)}
                    disabled={!filmPlayable}
                    onClick={() => setRequestedFrame(clampFrame(Number(item.start_frame ?? 0), renderMedia?.frame_count))}
                    title={`${formatMs(Number(item.start_frame ?? 0) * 1000 / Math.max(1, Number(renderMedia?.fps_num ?? 25)))} 起`}
                  >
                    {ordinal ? `第 ${ordinal} 镜` : `片段 ${index + 1}`}
                  </button>
                );
              })}
            </div>
          ) : null}
        </Panel>

        <details className="explainer-folded">
          <summary>审查覆盖范围</summary>
          <p className="muted">分层报告，默认折叠：技术解码、技术检测、视觉语义与人工审阅各自陈述。</p>
          <div className="explainer-coverage">
            {coverage.map((row) => (
              <div key={row.key}>
                <small>{row.label}</small>
                <strong>{row.detail}</strong>
                <small>{row.note}</small>
              </div>
            ))}
          </div>
          {qc.data?.unverified_checks && qc.data.unverified_checks.length > 0 ? (
            <p className="explainer-note warn">
              未检查项：
              <span>{qc.data.unverified_checks.join("、")}</span>
            </p>
          ) : qc.data ? (
            <p className="explainer-note">未检查项：本版本没有记录未检查项。</p>
          ) : null}
          <p className="explainer-note">
            技术全量解码 100% 不等于全帧语义理解；未抽样区域不会被写成“已检查”。缺少视觉能力时按所选策略阻塞或标记 UNCHECKED。
          </p>
        </details>

        <Panel
          title="人工确认与发布授权"
          subtitle="人工确认、机器检查与发布授权分开记录，互不代替。"
        >
          <p className="explainer-note">
            HTTP 客户端不能自填机器接受：机器检查只由内部策略处理器写入，人工确认与发布授权是两次独立动作。
          </p>
          <label className="explainer-field">
            审阅范围（帧区间 {Math.min(playheadFrame, requestedFrame)}–{Math.max(playheadFrame, requestedFrame)}）
            <span className="muted">决定绑定当前成片的 kind/id/hash 与该帧区间。</span>
          </label>
          <label className="explainer-field">
            审查说明
            <textarea
              value={reviewNote}
              onChange={(event) => setReviewNote(event.target.value)}
              rows={3}
              placeholder="例如：全片通看一遍；第 3 分钟旁白节奏偏快，其余可接受。"
            />
          </label>
          <div className="explainer-actions">
            <button
              type="button"
              disabled={decide.isPending || !renderMedia?.playable}
              title="确认当前预览：记录当前版本的人工确认，不自动代表发布授权"
              onClick={() => decide.mutate("HUMAN_APPROVED")}
            >
              确认当前成片
            </button>
            <button type="button" disabled={decide.isPending || !filmPlayable} onClick={() => decide.mutate("CHANGES_REQUESTED")}>
              要求修改
            </button>
            <button type="button" disabled={decide.isPending || !filmPlayable} onClick={() => decide.mutate("PUBLICATION_AUTHORIZED")}>
              记录发布授权
            </button>
          </div>
          <details className="explainer-folded">
            <summary>详情：机器 / 人工 / 发布三种决策记录</summary>
            <SettingRow
              label="决定主体"
              value={renderMedia ? `${renderMedia.id.slice(0, 8)}… · ${String(renderMedia.sha256 ?? "").slice(0, 12)}…` : "没有可确认的成片"}
            />
            <SettingRow label="机器检查" value={qc.data?.machine_decision ? "政策接受（自动，未人工审阅）" : "尚未产生"} />
            <SettingRow
              label="人工确认"
              value={qc.data?.human_decision ? String((qc.data.human_decision as Record<string, unknown>).actor ?? "已记录") : "尚未记录"}
            />
            <SettingRow label="发布授权" value={qc.data?.publication_decision ? "已记录" : "尚未记录"} />
            <p className="explainer-note">
              机器政策接受只声明规则、阈值与检测证据，不等于人工审阅，也不构成发布授权。
            </p>
          </details>
        </Panel>
      </div>

      <div className="explainer-stack">
        <Panel
          title="需处理问题"
          subtitle="默认只显示可理解的句子；检测器、证据与决策记录在详情里。"
          actions={<span className="badge">{issues.length} 项</span>}
        >
          <div className="explainer-actions">
            {["BLOCKER", "MAJOR", "MINOR", "UNKNOWN"].map((severity) => (
              <span
                className={`badge ${issueSeverityTone(severity) === "danger" ? "danger" : issueSeverityTone(severity) === "warn" ? "warn" : ""}`}
                key={severity}
              >
                {SEVERITY_LABELS[severity]} {issues.filter((issue) => String(issue.severity) === severity).length}
              </span>
            ))}
          </div>

          {factProblemCount > 0 ? (
            <div className="explainer-problem-row">
              <span className="explainer-issue-mark" aria-hidden="true">!</span>
              <div>
                <p className="explainer-problem-sentence">{factProblemCount} 条核心事实待核对</p>
                <div className="explainer-problem-actions">
                  <Link className="explainer-source-link" to={routes.explainerPage(projectId, "script")}>
                    去第 1 步核对事实与来源 →
                  </Link>
                </div>
                <p className="muted">事实账本与来源证据在第 1 步；这里不重复内部审计字段。</p>
              </div>
            </div>
          ) : null}

          {issues.length === 0 ? (
            <p className="muted">没有记录到问题；issues 为空不等于检测已运行。</p>
          ) : issueList.visible.map((issue) => {
            const sentence = problemSentence(issue, problemContext);
            const target = problemTarget(issue, problemContext);
            const isSelected = selectedIssue && String(selectedIssue.id) === String(issue.id);
            return (
              <div
                className={`explainer-problem-row${isSelected ? " is-selected" : ""}`}
                key={String(issue.id)}
              >
                <span className="explainer-issue-mark" aria-hidden="true">!</span>
                <div>
                  <p className="explainer-problem-sentence">{sentence}</p>
                  <p className="muted">
                    {SEVERITY_LABELS[String(issue.severity)] ?? String(issue.severity ?? "未定级")} ·{" "}
                    {issue.start_ms !== null && issue.start_ms !== undefined ? `${formatMs(Number(issue.start_ms))} · ` : ""}
                    {String(issue.detector ?? "检测器")}
                  </p>
                  <div className="explainer-problem-actions">
                    <Link
                      className="explainer-source-link"
                      to={`${routes.explainerPage(projectId, target.page)}${target.search}`}
                    >
                      {target.label} →
                    </Link>
                    <button type="button" onClick={() => selectIssue(issue)}>选择并定位</button>
                    {isSelected ? (
                      <button
                        type="button"
                        disabled={repair.isPending}
                        onClick={() => repair.mutate()}
                      >
                        {repair.isPending ? "正在提交…" : "只修复这项"}
                      </button>
                    ) : null}
                  </div>
                  {isSelected ? (
                    <details className="explainer-folded">
                      <summary>详情：证据与修复建议</summary>
                      <p className="muted">建议修复：{String(issue.suggested_repair || "按根因节点生成新版本，并重查受影响下游。")}</p>
                      <p className="muted">预期：{String(issue.expected || "—")}</p>
                      <p className="muted">
                        证据：{String(issue.evidence_text_span || JSON.stringify(issue.evidence_json ?? {}))}
                      </p>
                      <p className="muted">问题类型：{String(issue.issue_kind ?? "—")} · 责任步骤：{String(issue.responsible_step_code ?? "—")}</p>
                    </details>
                  ) : null}
                </div>
              </div>
            );
          })}
          {issueList.hiddenCount > 0 ? (
            <div className="explainer-actions">
              <button type="button" onClick={issueList.showMore}>
                查看余下 {Math.min(6, issueList.hiddenCount)} 项（共 {issueList.sorted.length} 项）
              </button>
              <button type="button" onClick={issueList.showAll}>显示全部 {issueList.sorted.length} 项</button>
            </div>
          ) : null}

          {repairReport ? (
            <details className="explainer-folded">
              <summary>返工回执与影响范围</summary>
              <SettingRow label="回执状态" value={String((repairReport.confirmed as Record<string, unknown>).status ?? "—")} />
              <SettingRow
                label="创建的真实任务"
                value={`${Array.isArray((repairReport.confirmed as Record<string, unknown>).job_ids) ? ((repairReport.confirmed as Record<string, unknown>).job_ids as unknown[]).length : 0} 个`}
              />
              <SettingRow label="计划是否建任务" value={String((repairReport.plan as Record<string, unknown>).would_create_jobs ?? "false")} />
              <SettingRow
                label="影响任务数"
                value={String((repairReport.plan as Record<string, unknown>).task_count ?? "—")}
              />
              <p className="muted">
                计划的 would_create_jobs=false 只说明影响范围；只有确认后返回的真实 job id 才代表返工已经排队。
              </p>
            </details>
          ) : null}

          {blockedCollections.length > 0 ? (
            <div className="explainer-note warn">
              <strong>收集阶段被阻塞：{blockedCollections.length} 项</strong>
              {blockedCollections.map((step) => (
                <div key={String(step.id)} className="explainer-problem-actions">
                  <span className="muted">
                    {String(step.planned_step_code)} · 作业 {String(step.job_id)} · 状态 {String(step.status)}
                    {step.blocker_code ? ` · ${String(step.blocker_code)}` : ""}
                  </span>
                  <button
                    type="button"
                    disabled={continueCollection.isPending}
                    onClick={() => continueCollection.mutate(step)}
                  >
                    用现有结果继续
                  </button>
                </div>
              ))}
              <p className="muted">
                继续只替换同一任务的收集作业，失败的候选与原始记录保留；仍有关键对象没有可用候选时不会创建任何任务。
              </p>
              {continueReceipt ? (
                <p className="muted" role="status">
                  回执：{continueReceipt.status}
                  {continueReceipt.replacement_job_id ? ` · 新作业 ${continueReceipt.replacement_job_id}` : ""}
                  {continueReceipt.previous_job_id ? ` · 旧作业 ${continueReceipt.previous_job_id}` : ""}
                  {continueReceipt.reason ? ` · ${continueReceipt.reason}` : ""}
                  {continueReceipt.idempotent_replay ? "（幂等重放，没有新增作业）" : ""}
                </p>
              ) : null}
            </div>
          ) : null}
        </Panel>

        <Panel title="字幕" subtitle="默认中文字幕；样式只影响字幕排版与合成。">
          <ul className="explainer-choice-list">
            <li>
              <label>
                <input type="radio" name="subtitle-mode" checked={subtitleModeOf(activeEdition) === "NONE"} disabled readOnly />
                无字幕
              </label>
            </li>
            <li>
              <label>
                <input type="radio" name="subtitle-mode" checked={subtitleModeOf(activeEdition) !== "NONE"} disabled readOnly />
                中文字幕
              </label>
            </li>
          </ul>
          <p className="muted">
            当前版本：{subtitleModeLabel(String(activeEdition?.subtitle_mode ?? "NONE"))}。字幕方式是输出版本的属性，改它需要新增一个版本
            （不会覆盖或重排当前版本）。
          </p>
          <div className="explainer-actions">
            <button
              type="button"
              onClick={() => {
                setNewVersionSubtitleMode(subtitleModeOf(activeEdition) === "NONE" ? "BURNED" : "NONE");
                setVersionDrawerOpen(true);
              }}
            >
              用另一种字幕方式新增版本
            </button>
            <button type="button" disabled={bilingualProbe.isPending} onClick={() => bilingualProbe.mutate()}>
              检查中英双语是否可用
            </button>
          </div>
          {bilingualProbe.isError ? (
            <p className="muted">中英双语可用性检查失败：{bilingualProbe.error instanceof Error ? bilingualProbe.error.message : "未知错误"}。</p>
          ) : null}
          {bilingualProbe.isSuccess ? (
            bilingualExecutable ? (
              <p className="muted">
                能力预检通过，可以新增中英双语版本（仍需翻译链与输出路径都接通后才会真正生成英语内容）。
              </p>
            ) : (
              <p className="muted">
                预检未通过，因此不提供中英双语选项：
                {(bilingualProbe.data?.blockers ?? []).map((blocker) => blocker.message).join("；") || "存在阻塞项"}。
                只有文字存在不代表可以生成英语配音。
              </p>
            )
          ) : (
            <p className="muted">中英双语只有完整翻译、双语字幕与输出路径接通且本次能力预检通过时才提供，因此默认不显示。</p>
          )}

          <label className="explainer-field" style={{ marginTop: 10 }}>
            字幕样式模板
            <select value={subtitleTemplateId} onChange={(event) => setSubtitleTemplateId(event.target.value)}>
              <option value="">项目样式（默认）</option>
              {styleTemplates.map((template) => (
                <option key={String(template.id)} value={String(template.id)}>
                  {String((template as unknown as Record<string, unknown>).title ?? template.code)} v{String((template as unknown as Record<string, unknown>).version_no ?? "")}
                </option>
              ))}
            </select>
          </label>
          {templatesQuery.isError ? <p className="muted">字幕样式模板读取失败；这里不会显示虚构模板。</p> : null}
          <div className="explainer-form-grid">
            <label className="explainer-field">
              字号
              <select value={subtitleSize} onChange={(event) => setSubtitleSize(event.target.value as "小" | "标准" | "大")}>
                <option value="小">小</option>
                <option value="标准">标准</option>
                <option value="大">大</option>
              </select>
            </label>
            <label className="explainer-field">
              位置
              <select value={subtitlePosition} onChange={(event) => setSubtitlePosition(event.target.value as "底部" | "顶部")}>
                <option value="底部">底部位置</option>
                <option value="顶部">顶部位置</option>
              </select>
            </label>
            <label className="explainer-field">
              安全区
              <select value={subtitleSafeArea} onChange={(event) => setSubtitleSafeArea(event.target.value as "标准" | "加宽")}>
                <option value="标准">标准</option>
                <option value="加宽">加宽</option>
              </select>
            </label>
          </div>
          <details className="explainer-folded">
            <summary>高级排版</summary>
            <SettingRow label="每语言最多行数" value="2 行（按画幅重算安全区）" />
            <SettingRow label="当前采用的模板" value={selectedTemplate ? String((selectedTemplate as unknown as Record<string, unknown>).title ?? "") : "项目样式"} />
            <p className="muted">
              高级排版沿用真实字幕 revision 的布局报告；本轮不提供逐字排版编辑器。
            </p>
          </details>
          <div className="explainer-actions">
            <button type="button" disabled={saveStyleTemplate.isPending} onClick={() => saveStyleTemplate.mutate()}>
              保存为项目字幕样式模板
            </button>
          </div>
          <p className="explainer-note">
            改字幕样式只影响字幕排版与合成，不会重跑图像或 TTS；本页也不会因此创建任何生成任务。把新样式烧进成片需要一次新的
            预览合成（服务端尚未提供“只重排字幕 revision”的独立命令）。
          </p>
        </Panel>

        <Panel title="背景音乐" subtitle="默认不使用；从不虚构曲目。">
          <label className="explainer-field">
            背景音乐
            <select value={musicChoice} onChange={(event) => setMusicChoice(event.target.value as "NONE" | "LIBRARY")}>
              <option value="NONE">不使用</option>
              <option value="LIBRARY">从项目媒体库选择</option>
            </select>
          </label>
          {musicChoice === "LIBRARY" ? (
            <MediaPicker
              projectId={projectId}
              value={musicMediaId}
              onChange={(mediaVersionId) => setMusicMediaId(mediaVersionId)}
              mediaKind="AUDIO"
              label="选择背景音乐"
            />
          ) : null}
          {musicChoice === "LIBRARY" && musicMediaId ? (
            <>
              <label className="explainer-field">
                音乐音量
                <select value={musicVolumeDb} onChange={(event) => setMusicVolumeDb(event.target.value)}>
                  <option value="-24">-24 dB</option>
                  <option value="-18">-18 dB</option>
                  <option value="-14">-14 dB</option>
                  <option value="-10">-10 dB</option>
                </select>
              </label>
              <label className="explainer-field">
                <span>
                  <input
                    type="checkbox"
                    checked={duckNarration}
                    disabled={!duckingFromProfile}
                    onChange={(event) => setDuckNarration(event.target.checked)}
                  />{" "}
                  旁白压低背景
                </span>
              </label>
              <p className="muted">
                {duckingFromProfile
                  ? "当前项目混音配置声明支持旁白压低背景。"
                  : "当前项目混音配置未声明旁白压低背景，因此这项不可切换（不会假装已经压低）。"}
              </p>
            </>
          ) : null}
          <p className="explainer-note">
            音乐选择保存在本页草稿；更换音乐或调音量只会影响混音与合成，不会创建图像或 TTS 任务。
            {assets.isError ? " 冻结的栏目混音配置读取失败，因此这里不显示任何推断出来的默认值。" : ""}
          </p>
        </Panel>

        <Panel title="输出" subtitle="本地成片导出；高级发布包不在首屏。">
          <div className="explainer-output-row">
            <span>导出画幅</span>
            <strong>{String(activeEdition?.aspect_ratio ?? "—")}（继承第 1 步）</strong>
          </div>
          <div className="explainer-output-row">
            <span>导出清晰度</span>
            <strong>{resolutionTierLabel(activeEdition?.width, activeEdition?.height)}</strong>
          </div>
          <p className="muted">
            清晰度默认是当前原生档位，不会因为文件叫成片就暗示高清。
            {upscaleAvailable
              ? "本机报告存在超分能力，但超分是独立操作：解说导出链尚未提供该命令，因此这里不提供按钮。"
              : "本机没有报告视频超分能力，因此不提供超分选项。"}
          </p>
          <label className="explainer-field">
            输出文件
            <span>
              <input type="checkbox" checked disabled /> MP4 成片（默认）
            </span>
            <span>
              <input
                type="checkbox"
                checked={includeSubtitleFile}
                onChange={(event) => setIncludeSubtitleFile(event.target.checked)}
              />{" "}
              字幕文件（.srt）
            </span>
            <span>
              <input
                type="checkbox"
                checked={includeNarrationStem}
                onChange={(event) => setIncludeNarrationStem(event.target.checked)}
              />{" "}
              纯配音（旁白音轨）
            </span>
          </label>
          <div className="explainer-actions">
            <button
              type="button"
              disabled={!filmPlayable || render.isPending}
              title={filmPlayable ? undefined : "还没有成片；用底部的“生成预览”提交第一次合成"}
              onClick={() => {
                setFeedback(null);
                setError(null);
                render.mutate();
              }}
            >
              {render.isPending ? "正在提交…" : "更新预览"}
            </button>
            <button
              type="button"
              disabled={!filmPlayable || compositionQc.isPending}
              title={
                filmPlayable
                  ? "对当前成片运行本机技术质检（响度、真峰值、静音断档、全片解码、字幕安全区）"
                  : "还没有成片；先提交合成"
              }
              onClick={() => {
                setFeedback(null);
                setError(null);
                compositionQc.mutate();
              }}
            >
              {compositionQc.isPending ? "正在提交质检…" : "运行技术质检"}
            </button>
            <button type="button" onClick={() => setVersionDrawerOpen(true)}>添加输出版本</button>
          </div>
          {exportReceipt ? (
            <div className="explainer-output-row">
              <span>导出回执</span>
              <strong>
                {exportReceipt.jobId
                  ? `已提交任务 ${exportReceipt.jobId}`
                  : `未创建任务（${exportReceipt.status}）${exportReceipt.note ? `：${exportReceipt.note}` : ""}`}
              </strong>
            </div>
          ) : null}
          <div className="explainer-output-row">
            <span>下载</span>
            <span>
              {filmPlayable && renderMedia?.playback_url ? (
                <a href={renderMedia.playback_url} download={`explainer-${renderMedia.id}.mp4`}>下载成片</a>
              ) : (
                <span className="muted">还不可下载：没有真实可播放的成片文件</span>
              )}
              {" · "}
              <button type="button" disabled={!editionId || downloadSubtitles.isPending} onClick={() => downloadSubtitles.mutate()}>
                下载字幕附件
              </button>
            </span>
          </div>
          <details className="explainer-folded">
            <summary>更多：发布包与授权范围</summary>
            <p className="muted">
              本轮默认目标是本地成片导出；发布包、地区授权与平台字段不占用主界面，需要时在这里展开。
            </p>
            <InlineOk message={exportReceipt?.packageId ? `发布包 ${exportReceipt.packageId}` : null} />
          </details>
        </Panel>
      </div>
    </div>

    <Drawer open={versionDrawerOpen} onClose={() => setVersionDrawerOpen(false)} title="添加输出版本" width={420}>
      <div className="explainer-workspace explainer-stack">
        <label className="explainer-field">
          配音语言
          <select value={newVersionLocale} onChange={(event) => setNewVersionLocale(event.target.value)}>
            {(availableLanguages.length > 0 ? availableLanguages : [voiceLocale]).map((language) => (
              <option key={language} value={language}>{localeLabel(language)}（{language}）</option>
            ))}
          </select>
        </label>
        <p className="muted">
          {availableLanguages.length > 0
            ? "只列出本机 TTS 实际支持的语言；新增版本会走一次真实能力预检。"
            : "本机没有报告可用 TTS 语言；新增版本会在预检时报告缺失能力，而不是创建无法配音的版本。"}
        </p>
        <label className="explainer-field">
          画幅
          <select value={newVersionAspect} onChange={(event) => setNewVersionAspect(event.target.value as "16:9" | "9:16" | "3:4" | "1:1")}>
            <option value="16:9">16:9 横屏</option>
            <option value="9:16">9:16 竖屏</option>
            <option value="3:4">3:4</option>
            <option value="1:1">1:1</option>
          </select>
        </label>
        <p className="muted">改画幅会建立新版本并重新构图，不会通过中心裁切假装已经重构图。</p>
        <label className="explainer-field">
          字幕
          <select
            value={newVersionSubtitleMode}
            onChange={(event) => setNewVersionSubtitleMode(event.target.value as ExplainerOutputRequest["subtitle_mode"])}
          >
            <option value="BURNED">中文字幕（烧录）</option>
            <option value="SOFT">中文字幕（软字幕）</option>
            <option value="NONE">无字幕</option>
            {bilingualExecutable ? <option value="BILINGUAL_BURNED">中英双语（预检已通过）</option> : null}
          </select>
        </label>
        <InlineError message={addVersion.isError ? (addVersion.error instanceof Error ? addVersion.error.message : "未知错误") : null} />
        <div className="explainer-actions">
          <button type="button" disabled={addVersion.isPending} onClick={() => addVersion.mutate()}>
            {addVersion.isPending ? "正在预检并创建…" : "添加输出版本"}
          </button>
          <button type="button" onClick={() => setVersionDrawerOpen(false)}>取消</button>
        </div>
        <p className="explainer-note">
          新增版本只有在“该路径已完整接通”且“本次能力预检可用”时才会创建；旧版本保持只读可访问，界面上不显示内部
          edition 标识。
        </p>
      </div>
    </Drawer>
  </div>;
}

/* -------------------------------------------------------------------------- */
/* small local helpers                                                        */
/* -------------------------------------------------------------------------- */

export function reviewedIntervals(playheadFrame: number, requestedFrame: number): number[][] {
  if (!playheadFrame && !requestedFrame) return [[0, 0]];
  return [[Math.min(playheadFrame, requestedFrame), Math.max(playheadFrame, requestedFrame)]];
}

function wordedSummary(runText: string, progress: { completed: number; total: number } | null): string {
  if (!progress) return `${runText} · 没有可信的总量比例`;
  return `${runText} · ${progress.completed} / ${progress.total} 项任务`;
}

export function subtitleModeOf(edition: Record<string, unknown> | null | undefined): string {
  return String(edition?.subtitle_mode ?? "NONE").toUpperCase();
}

/** Does the frozen channel profile really declare background ducking? */
export function profileDuckingSupported(profile: Record<string, unknown> | null | undefined): boolean {
  if (!profile) return false;
  const policy = profile.bgm_policy_json ?? profile.bgm_policy;
  let value: unknown = policy;
  if (typeof policy === "string" && policy.trim()) {
    try {
      value = JSON.parse(policy);
    } catch {
      return false;
    }
  }
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return Boolean(record.ducking ?? record.sidechain ?? record.narrator_duck_db ?? record.duck_narration);
}

export function currentCueFor(
  playheadFrame: number,
  cues: unknown,
  fpsNum: number | null | undefined,
  fpsDen: number | null | undefined,
): Record<string, unknown> | null {
  const rows = Array.isArray(cues) ? (cues as Array<Record<string, unknown>>) : [];
  if (rows.length === 0) return null;
  const num = Number(fpsNum ?? 0);
  const den = Number(fpsDen ?? 0);
  if (!(num > 0 && den > 0)) return rows[0];
  const ms = (playheadFrame * den * 1000) / num;
  return rows.find((cue) => ms >= Number(cue.start_ms ?? 0) && ms < Number(cue.end_ms ?? 0)) ?? null;
}

/** A real text file the operator can open; used for the subtitle sidecar. */
function saveTextFile(content: string, filename: string, mimeType: string): boolean {
  if (typeof document === "undefined" || typeof URL === "undefined" || typeof URL.createObjectURL !== "function") return false;
  try {
    const blob = new Blob([content], { type: `${mimeType};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    anchor.click();
    URL.revokeObjectURL?.(url);
    return true;
  } catch {
    // A browser that cannot hand out a file must not be told the file exists.
    return false;
  }
}

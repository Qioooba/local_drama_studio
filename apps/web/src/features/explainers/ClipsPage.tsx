/**
 * 第 5 步：视频片段 (clips) — 运动与视频候选 (spec §B6, §B9, §D4.2).
 *
 * The page reuses 第 4 步's shot list and candidate workspace instead of building a
 * second image/video card implementation (§B6): the shared pieces live in
 * `StoryboardPage` and are imported here (both files are owned by steps 4/5).
 *
 * Semantics carried over from §B9 / §B12.3:
 *
 *  * `预览候选` is a pure front-end selection; it never writes an active selection.
 *  * `采用` writes the adoption; `采用并锁定` is the separate, explicit human lock.
 *  * `再生成` is a new operation (new seeds); `重试失败片段` re-runs the original job
 *    with the original seed; `重新载入` only re-reads media/lists.
 *  * Every visual clip comes from real AI 图生视频: a shot with no generated video is
 *    reported as 尚未生成, never as a locally composed still-motion clip (静图推拉 was
 *    removed from the product).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  batchRetryJobs,
  getExplainerBeatImpact,
  getExplainerOverview,
  listExplainerOwnerCandidates,
  planExplainerBeatGeneration,
  retryJob,
  selectExplainerBeatCandidate,
  submitExplainerBeatGeneration,
  unlockExplainerSelection,
  type ExplainerBeat,
  type ExplainerCandidatePage,
  type ExplainerCandidatePurpose,
  type ExplainerGenerationMode,
  type ExplainerMediaCandidate,
} from "../../generated/api";
import { newCommandId, stableIdempotencyKey } from "../../services/commandId";
import { queryKeys } from "../../query/queryKeys";
import { routes } from "../../app/routeRegistry";
import { MediaThumb } from "../../components/ui/primitives";
import { CapabilityPicker, effectiveCapabilityProfile, useCapabilityOptions } from "../model-config/CapabilityPicker";
import { fallbackToOriginalVideo } from "../shared/mediaPlaybackPolicy";
import { useExplainerActionBar } from "./ExplainerStepActionBar";
import { GenerationControls, MediaCandidateCompare, MediaCandidateGrid, candidateShortLabel } from "./candidates";
import { InlineError, InlineOk, Panel, SettingRow, StateNotice, type PageState } from "./components";
import {
  CAMERA_MOVEMENT_OPTIONS,
  CoverageDrawer,
  GenerationReceiptView,
  MediaPickDrawer,
  PRESENTATION_OPTIONS,
  ShotListPane,
  VIDEO_CAPABILITY,
  beatAdoptedCandidate,
  candidateThumbUrl,
  candidateVideoOriginalSrc,
  candidateVideoSrc,
  frozenSubmitBody,
  mediaVersionThumbUrl,
  narrationSummaryOf,
  normalizeCandidatePage,
  presentationOf,
  profileSupportsInputSlot,
  shotTypeShortLabel,
  useExplainerGenerationDraw,
  useExplainerShotCandidates,
  type ExplainerDrawCommand,
  type PresentationKey,
} from "./StoryboardPage";
import { useExplainerBeats, useExplainerEditions } from "./useExplainerQueries";
import {
  submitExplainerVisualGeneration,
  type ExplainerVisualGenerationSubmitResult,
} from "../../generated/api";
import { formatMs, isRetiredRenderType, plannedVsActual, renderTypeLabel, RETIRED_RENDER_TYPE_NOTE } from "./viewModels";
import "./explainers.css";
import "./explainers.steps45.css";

/* -------------------------------------------------------------------------- */
/* labels kept for the existing step-5 contract                               */
/* -------------------------------------------------------------------------- */

/**
 * §F1 wording: AI 动态 / 图形动画 / 上传素材 must never be merged.  A retired stored
 * value (a legacy still-motion row) reads as an outdated plan, never as a still
 * label and never as AI 动态.
 */
export function clipModeLabel(renderType: string | null | undefined): string {
  switch (String(renderType ?? "").toUpperCase()) {
    case "I2V":
      return "AI 动态（真实图生视频）";
    case "LICENSED_MEDIA":
      return "上传素材";
    case "INFOGRAPHIC":
      return "图形动画（信息图）";
    default:
      return renderTypeLabel(renderType);
  }
}

export const CLIP_CANDIDATE_STATUS_LABELS: Record<string, string> = {
  PENDING: "已排队",
  GENERATING: "生成中",
  READY: "可用",
  REJECTED: "已拒绝",
  FAILED: "生成失败",
  SUPERSEDED: "已被替换",
};

export function clipCandidateStatusLabel(status: string | null | undefined): string {
  if (!status) return "状态未知";
  return CLIP_CANDIDATE_STATUS_LABELS[status] ?? status;
}

/** A candidate is a video only when its own record says so. */
export function candidateIsVideo(candidate: Record<string, unknown>, beat: ExplainerBeat | null): boolean {
  const renderType = String(candidate.render_type_actual ?? candidate.render_type_planned ?? beat?.render_type ?? "");
  return renderType === "I2V";
}

/** The render types that already describe a real, playable clip product. */
const CLIP_RENDER_TYPES = new Set(["I2V", "LICENSED_MEDIA", "INFOGRAPHIC"]);

/**
 * A clip is usable only when it is a real generated video.
 *
 * 静图推拉 (deterministic local composition) was removed: every explainer clip now
 * comes from real AI 图生视频.  A beat whose adopted VISUAL record is a retired
 * still-motion value — or that has no adopted clip at all — is therefore reported as
 * 尚未生成, never as a still-motion clip.  图形动画 and 已有视频/上传素材 remain their
 * own real clip sources and are not merged into AI 动态.
 */
export function clipIsRealVideo(beat: ExplainerBeat): boolean {
  const adopted = beatAdoptedCandidate(beat, "VISUAL");
  if (!adopted) return false;
  const record = adopted as Record<string, unknown>;
  const renderType = String(
    record.render_type_actual ?? record.render_type_planned ?? beat.render_type ?? "",
  ).toUpperCase();
  if (isRetiredRenderType(renderType)) return false;
  if (CLIP_RENDER_TYPES.has(renderType)) return true;
  return String(record.media_kind ?? "").toUpperCase() === "VIDEO";
}

/** Backwards-compatible helper used by earlier step-5 tests. */
export function thumbnailUrl(mediaVersionId: string): string {
  return mediaVersionThumbUrl(mediaVersionId);
}

/**
 * §B12.2: an unsubmitted motion draft must survive "更换源图" (which navigates to
 * step 4) and coming back.  The module-level cache is process memory, not storage:
 * it is explicitly cleared on reload, and the draft registry still reports the
 * unsaved state to the shell while the page is mounted.
 */
type MotionDraft = {
  motion: string;
  camera: string;
  negative: string;
  stepCount: string;
  presentation: PresentationKey;
  candidateCount: number;
  durationMs: number | null;
};

const motionDraftCache = new Map<string, MotionDraft>();

export function clearMotionDraftCache(): void {
  motionDraftCache.clear();
}

function narrationText(beat: ExplainerBeat): string {
  return narrationSummaryOf(beat);
}

/**
 * The picture-stage command's refusal, rendered from the backend's own words.
 *
 * `blockers[].message` is shown verbatim, and `next_step` is appended verbatim when
 * the server sent one: the page may not soften, translate or replace either, because
 * the operator's next action depends on the backend's exact prerequisite.
 */
export function visualGenerationBlockedText(result: ExplainerVisualGenerationSubmitResult): string {
  const blockers = result.blockers ?? [];
  const detail = blockers.length
    ? blockers
        .map((blocker) =>
          blocker.next_step ? `${blocker.message}（建议下一步：${blocker.next_step}）` : blocker.message,
        )
        .join("；")
    : result.reason ?? "服务端未返回原因";
  switch (result.status) {
    case "CAPABILITY_UNAVAILABLE":
      return `无法开始生成缺失片段：本机没有可执行的图生视频能力。${detail}`;
    case "RECOVERY_REQUIRED":
      return `无法开始生成缺失片段：上一次画面阶段还没结束或需要先恢复。${detail}`;
    default:
      return `无法开始生成缺失片段：${detail}`;
  }
}

/** §B6.1: the motion description comes from the storyboard plan, never the narration. */
export function defaultMotionText(beat: ExplainerBeat): string {
  const record = beat as unknown as Record<string, unknown>;
  const planned = String(record.prompt_intent ?? "").trim();
  if (planned) return planned;
  const intent = String(record.visual_intent ?? "").trim();
  return intent;
}

export function ExplainerClipsPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const editions = useExplainerEditions(projectId);
  const firstEditionId = editions.data?.editions?.[0]
    ? String((editions.data.editions[0] as Record<string, unknown>).id)
    : null;
  const editionId = searchParams.get("edition") ?? firstEditionId;
  const beatsQuery = useExplainerBeats(projectId, editionId);
  const beats = beatsQuery.data?.beats ?? [];
  const selectedBeatId = searchParams.get("beat") ?? beats[0]?.id ?? null;
  const selectedBeat = beats.find((beat) => beat.id === selectedBeatId) ?? null;

  const purpose: ExplainerCandidatePurpose = "VISUAL";
  const { query: candidatesQuery, page } = useExplainerShotCandidates({
    projectId,
    beatId: selectedBeatId,
    purpose,
    editionId,
  });
  const keyframeQuery = useExplainerShotCandidates({
    projectId,
    beatId: selectedBeatId,
    purpose: "KEYFRAME",
    editionId,
  });

  const [previewId, setPreviewId] = useState<string | null>(null);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareLimit, setCompareLimit] = useState(2);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [pendingVideoVersionId, setPendingVideoVersionId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ beatId: string | null; text: string } | null>(null);
  const [error, setError] = useState<{ beatId: string | null; text: string } | null>(null);
  const [conflict, setConflict] = useState<{ message: string } | null>(null);
  const [impactReport, setImpactReport] = useState<Record<string, unknown> | null>(null);
  /**
   * 生成缺失片段 is a whole-film (page-level) command, so its receipt is kept apart
   * from the per-beat `feedback` / `error` slots: it must stay visible even when no
   * single 画面段 is selected.
   */
  const [stageNotice, setStageNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [playing, setPlaying] = useState(false);

  const [draft, setDraft] = useState<MotionDraft>(() => ({
    motion: selectedBeat ? defaultMotionText(selectedBeat) : "",
    camera: "AUTO",
    negative: "",
    stepCount: "",
    presentation: "AI_VIDEO",
    candidateCount: 1,
    durationMs: null,
  }));

  // Initialise the motion draft once per working object: from the cached draft when
  // the operator already edited it, otherwise from the real storyboard plan.  A
  // later beats refetch must never overwrite what the operator typed.
  const initialisedDraftKey = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedBeat) return;
    const key = `${projectId}:${selectedBeat.id}`;
    if (initialisedDraftKey.current === key) return;
    initialisedDraftKey.current = key;
    const cached = motionDraftCache.get(key);
    const planned = plannedVsActual(selectedBeat as unknown as Record<string, unknown>);
    setDraft(
      cached ?? {
        motion: defaultMotionText(selectedBeat),
        camera: "AUTO",
        negative: "",
        stepCount: "",
        presentation: presentationOf(planned.actual ?? selectedBeat.render_type),
        candidateCount: 1,
        durationMs: selectedBeat.preferred_duration_ms ?? null,
      },
    );
    setPreviewId(null);
    setPendingVideoVersionId(null);
  }, [projectId, selectedBeat]);

  const patchDraft = (patch: Partial<MotionDraft>) => {
    setDraft((current) => {
      const next = { ...current, ...patch };
      if (selectedBeat) motionDraftCache.set(`${projectId}:${selectedBeat.id}`, next);
      return next;
    });
  };

  const lastGoodPage = useRef<ExplainerCandidatePage | null>(null);
  if (page) lastGoodPage.current = page;
  const effectivePage = page ?? lastGoodPage.current;
  const candidates = effectivePage?.candidates ?? [];
  const adoptedCandidate = candidates.find((candidate) => candidate.selected) ?? null;
  const previewCandidate = candidates.find((candidate) => candidate.id === previewId) ?? null;
  const displayCandidate = previewCandidate ?? adoptedCandidate;
  const displayIsVideo = displayCandidate?.media_kind === "VIDEO" && Boolean(displayCandidate.media_version_id);

  const keyframePage = keyframeQuery.page;
  const adoptedStill = selectedBeat ? beatAdoptedCandidate(selectedBeat, "KEYFRAME") : null;
  const stillMediaVersionId =
    String((keyframePage?.active_selection as Record<string, unknown> | null)?.media_version_id ?? "") ||
    String(adoptedStill?.media_version_id ?? "");
  const keyframeSelectionId = String((keyframePage?.active_selection as Record<string, unknown> | null)?.id ?? "");

  const videoOptions = useCapabilityOptions(VIDEO_CAPABILITY, { projectId });
  const videoProfile = effectiveCapabilityProfile(videoOptions, "");
  const videoProfileReady = videoProfile.ready;
  const supportsEndFrame = profileSupportsInputSlot(videoProfile.option, /END_FRAME|LAST_FRAME|TAIL/i);
  const supportsCameraControl = profileSupportsInputSlot(videoProfile.option, /CAMERA/i);

  // Every visual clip is produced by real AI 图生视频, so `IMAGE_TO_VIDEO` is the one
  // executable generation mode this step has.  图形动画 and 已有视频 are real choices,
  // but neither is a model draw: the draw stays unavailable with a named reason
  // instead of submitting an I2V job for them.
  const isAiVideo = draft.presentation === "AI_VIDEO";
  const effectiveMode: ExplainerGenerationMode = "IMAGE_TO_VIDEO";
  const unit = "段";
  const drawUnsupportedType = !isAiVideo;
  const unsupportedReason =
    draft.presentation === "GRAPHIC_ANIMATION"
      ? "图形动画当前没有声明可执行的生成命令：只能使用已有受支持模板；本片其余画面段仍走真实 AI 图生视频。"
      : "已有视频不调用生成模型：请用「上传视频 / 选择视频」登记素材。";

  const drawCommand: ExplainerDrawCommand = useMemo(
    () => ({
      edition_id: editionId ?? null,
      purpose,
      mode: effectiveMode,
      candidate_count: draft.candidateCount,
      expected_beat_revision: selectedBeat ? Number((selectedBeat as unknown as Record<string, unknown>).revision ?? 1) : null,
      expected_selection_id: String((effectivePage?.active_selection as Record<string, unknown> | null)?.id ?? "") || null,
      input_keyframe_selection_id: keyframeSelectionId || null,
      motion_prompt: draft.motion.trim() ? draft.motion : null,
      camera_movement: supportsCameraControl ? draft.camera : null,
      negative_override: draft.negative.trim() ? draft.negative : null,
      reference_selections: [],
      run_overrides: {},
    }),
    [
      draft.camera,
      draft.candidateCount,
      draft.motion,
      draft.negative,
      editionId,
      effectiveMode,
      effectivePage,
      keyframeSelectionId,
      selectedBeat,
      supportsCameraControl,
    ],
  );

  const draw = useExplainerGenerationDraw({ projectId, beatId: selectedBeatId, purpose, command: drawCommand });

  /**
   * A clip is usable only when it is a real generated video:
   *
   *  * the beat must have an adopted VISUAL candidate, and
   *  * that record must not be a retired still-motion value (a legacy DB row).
   *
   * A real AI 图生视频 (`I2V`) record, an uploaded/licensed video (已有视频) and a
   * rendered 图形动画 clip all count — they are kept distinct by their labels, never
   * merged into AI 动态.  A legacy 静图推拉 record does NOT count: it is reported as
   * 尚未生成, because every visual clip now has to come from real AI 图生视频.
   */
  const readyClips = beats.filter((beat) => clipIsRealVideo(beat));
  const missingVideoBeats = beats.filter((beat) => !clipIsRealVideo(beat));
  const readyCount = readyClips.length;

  /**
   * 生成缺失片段 is offered only when the page really knows something is missing:
   * `beats` is empty while the plan is still loading, and a disabled button beats
   * queueing the whole film's picture stage for a plan the page has not read yet.
   * When every 画面段 already has a real clip there is nothing to generate.
   */
  const canGenerateMissingClips = beats.length > 0 && missingVideoBeats.length > 0;

  /* ---------- the seven §B9 actions ---------- */

  const rollbackRef = useRef<{ key: readonly unknown[]; page: ExplainerCandidatePage | null } | null>(null);

  const adopt = useMutation({
    mutationFn: ({ candidateId, lock }: { candidateId: string; lock: boolean }) =>
      selectExplainerBeatCandidate(projectId, selectedBeatId ?? "", {
        expected_revision: Number((selectedBeat as unknown as Record<string, unknown> | null)?.revision ?? 1),
        candidate_id: candidateId,
        edition_id: editionId,
        purpose,
        expected_selection_id: String((effectivePage?.active_selection as Record<string, unknown> | null)?.id ?? "") || null,
        lock,
        actor: lock ? "local-user" : null,
      }),
    onMutate: async ({ candidateId, lock }) => {
      const key = queryKeys.explainers.beatCandidates(projectId, selectedBeatId ?? "", purpose, editionId);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = (queryClient.getQueryData(key) as ExplainerCandidatePage | undefined) ?? null;
      rollbackRef.current = { key, page: previous ?? lastGoodPage.current };
      const base = previous ?? lastGoodPage.current;
      if (base) {
        queryClient.setQueryData(key, {
          ...base,
          candidates: base.candidates.map((candidate) => ({
            ...candidate,
            selected: candidate.id === candidateId,
            adopted: candidate.id === candidateId,
            locked: candidate.id === candidateId ? lock : candidate.locked,
          })),
        });
      }
      return { previous };
    },
    onSuccess: async (result) => {
      const record = result as Record<string, unknown>;
      const editionsAffected = Number(impactReport?.affected_edition_count ?? 0);
      setConflict(null);
      setError(null);
      if (record.degraded) {
        setFeedback({
          beatId: selectedBeatId,
          text: `已采用片段；实际运动方式与计划不同（回退原因：${String(record.fallback_reason ?? "服务端未提供")}）。受影响输出版本：${editionsAffected || "按服务端依赖范围"} 个。`,
        });
      } else {
        setFeedback({
          beatId: selectedBeatId,
          text: `已采用片段（AI 动态（真实图生视频））；将更新该镜头与 ${editionsAffected || "相关"} 个输出版本的合成，旧片段与旧成片仍可回看。`,
        });
      }
      setPreviewId(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) => {
      const rollback = rollbackRef.current;
      if (rollback) queryClient.setQueryData(rollback.key, rollback.page);
      const message = mutationError instanceof Error ? mutationError.message : String(mutationError);
      if (/409|CONFLICT|STALE_REVISION/i.test(message)) {
        setConflict({ message });
        setError({ beatId: selectedBeatId, text: `${message}（已保留你的选择意图与运动描述文本）` });
      } else {
        setError({ beatId: selectedBeatId, text: message });
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onSettled: () => {
      rollbackRef.current = null;
    },
  });

  const unlock = useMutation({
    mutationFn: (candidate: ExplainerMediaCandidate) =>
      unlockExplainerSelection(projectId, selectedBeatId ?? "", {
        selection_id: String((effectivePage?.active_selection as Record<string, unknown> | null)?.id ?? "") || candidate.id,
        purpose,
        edition_id: editionId,
        expected_revision: Number((selectedBeat as unknown as Record<string, unknown> | null)?.revision ?? 1),
        actor: "local-user",
      }),
    onSuccess: async () => {
      setError(null);
      setFeedback({ beatId: selectedBeatId, text: "已解锁该镜头的人工锁定；当前采用片段保留，将来允许被替换。" });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  /** §B9 item 4: retry the original task (original seed/inputs), never a new draw. */
  const retryFailed = useMutation({
    mutationFn: (candidate: ExplainerMediaCandidate) => {
      if (!candidate.job_id) throw new Error("该失败候选没有可重试的真实任务记录。");
      return retryJob(candidate.job_id);
    },
    onSuccess: async () => {
      setError(null);
      setFeedback({ beatId: selectedBeatId, text: "已重试原片段任务；沿用原首帧、参数与种子，不占用新的创作候选额度。" });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  const failedCandidates = candidates.filter(
    (candidate) => candidate.status === "FAILED" && Boolean(candidate.job_id),
  );

  /** 仅重试失败片段 (§B6.2): batch retry of the failed jobs, no new seeds. */
  const retryFailedBatch = useMutation({
    mutationFn: () => batchRetryJobs({ job_ids: failedCandidates.map((candidate) => candidate.job_id as string) }),
    onSuccess: async (result) => {
      setError(null);
      setFeedback({
        beatId: selectedBeatId,
        text: `仅重试失败片段：已重试 ${result.retried_count} 个原任务（沿用原种子与输入），成功片段不会被重做。`,
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  const impact = useMutation({
    mutationFn: () => getExplainerBeatImpact(projectId, selectedBeatId ?? ""),
    onSuccess: (result) => {
      setError(null);
      setImpactReport(result as Record<string, unknown>);
    },
    onError: (mutationError) =>
      setError({ beatId: selectedBeatId, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  const overview = useQuery({
    queryKey: queryKeys.explainers.workspace(projectId),
    queryFn: () => getExplainerOverview(projectId),
    enabled: Boolean(projectId),
    retry: 1,
  });

  /* ---------- 补齐缺失片段: only real AI 图生视频, in one honest batch ---------- */

  const backfill = useMutation({
    mutationFn: async () => {
      let submitted = 0;
      let accepted = 0;
      const failures: string[] = [];
      for (const beat of missingVideoBeats) {
        const record = beat as unknown as Record<string, unknown>;
        const target = presentationOf(plannedVsActual(record).actual ?? beat.render_type);
        if (target === "GRAPHIC_ANIMATION" || target === "SOURCE_VIDEO") {
          failures.push(
            `${beat.code}：${
              target === "GRAPHIC_ANIMATION"
                ? "图形动画没有可执行的生成命令，请改用已有受支持模板或上传素材"
                : "已有视频需要上传/选择素材，不由生成模型产出"
            }`,
          );
          continue;
        }
        const nonce = newCommandId();
        // AI 动态必须先有已采用首帧的 KEYFRAME selection 标识。
        let stillId = "";
        try {
          const keyframePage = await listExplainerOwnerCandidates(projectId, "BEAT", beat.id, {
            purpose: "KEYFRAME",
            edition_id: editionId ?? undefined,
          });
          const normalized = normalizeCandidatePage(keyframePage, {
            projectId,
            ownerId: beat.id,
            purpose: "KEYFRAME",
            editionId,
          });
          stillId = String((normalized.active_selection as Record<string, unknown> | null)?.id ?? "");
        } catch {
          stillId = "";
        }
        if (!stillId) {
          failures.push(`${beat.code}：缺少已采用首帧（KEYFRAME selection），未提交 AI 动态任务`);
          continue;
        }
        const mode: ExplainerGenerationMode = "IMAGE_TO_VIDEO";
        const body = {
          edition_id: editionId ?? null,
          purpose,
          mode,
          candidate_count: 1,
          expected_beat_revision: Number(record.revision ?? 1),
          input_keyframe_selection_id: stillId || null,
          motion_prompt: defaultMotionText(beat) || null,
          reference_selections: [],
          run_overrides: {},
          operation_id: stableIdempotencyKey("explainer-beat-draw", {
            projectId,
            beatId: beat.id,
            purpose,
            nonce,
            command: { mode },
          }),
        };
        const plan = await planExplainerBeatGeneration(projectId, beat.id, body);
        if (plan.status !== "EXECUTABLE") {
          failures.push(`${beat.code}：${plan.blockers.map((blocker) => blocker.message).join("；") || "计划被阻塞"}`);
          continue;
        }
        const receipt = await submitExplainerBeatGeneration(
          projectId,
          beat.id,
          frozenSubmitBody(body, plan),
          stableIdempotencyKey("explainer-backfill-submit", { operation_id: body.operation_id }),
        );
        submitted += 1;
        accepted += receipt.accepted_count;
        for (const item of receipt.items) {
          if (item.submission_status === "NOT_SUBMITTED") {
            failures.push(`${beat.code}：${item.error?.message ?? item.error?.code ?? "未入队"}`);
          }
        }
      }
      return { requested: missingVideoBeats.length, submitted, accepted, failures };
    },
    onSuccess: async (result) => {
      setError(result.failures.length && result.submitted === 0 ? { beatId: null, text: result.failures.join("；") } : null);
      setFeedback({
        beatId: null,
        text: `补齐缺失片段：已为 ${result.submitted} / ${result.requested} 个镜头提交 ${result.accepted} 个真实图生视频任务（已排队）。${
          result.failures.length ? `未提交：${result.failures.join("；")}` : ""
        }已完成的人工选择未被改动。`,
      });
      await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
    },
    onError: (mutationError) =>
      setError({ beatId: null, text: mutationError instanceof Error ? mutationError.message : String(mutationError) }),
  });

  /* ---------- 生成缺失片段: the whole film's picture stage, in one real command ---------- */

  /**
   * The stage reuses every human-adopted keyframe and every 画面段 that already has a
   * READY VISUAL candidate, and generates a real AI 图生视频 clip for the rest — so it
   * is safe to press repeatedly and it never re-draws accepted work.
   *
   * `ACCEPTED` only means a real Job was queued (`job_id`), never that clips exist: the
   * notice names the job and says the list refreshes when the job finishes.  A refusal
   * (`BLOCKED` / `CAPABILITY_UNAVAILABLE` / `RECOVERY_REQUIRED`) shows the backend's own
   * blocker sentences verbatim.
   */
  const generateMissingClips = useMutation({
    mutationFn: () =>
      submitExplainerVisualGeneration(
        projectId,
        { edition_id: editionId ?? null, beat_ids: [] },
        // One stable key per user intent (project + edition + whole film): a double
        // click or a timeout retry replays the same command instead of queueing a
        // second job, while another edition is a different intent.
        stableIdempotencyKey("explainer-visual-generation-submit", {
          projectId,
          edition_id: editionId ?? null,
          beat_ids: [],
        }),
      ),
    onSuccess: async (result) => {
      if (result.status === "ACCEPTED") {
        setError(null);
        const job = result.job_id
          ? `已启动后台任务 ${result.job_id}（状态：${result.job_state ?? "已排队"}）`
          : "服务端已受理画面阶段（未返回任务编号）";
        setStageNotice({
          kind: "ok",
          text:
            `${job}：片段生成在后台任务里运行，任务完成后本页会重新读取并刷新片段列表；` +
            "现在还没有生成任何片段，「已受理」不等于「片段已就绪」。已采用的首帧与已有片段会被复用，不会被重画。" +
            (result.idempotent_replay ? "该意图此前已提交过，服务端复用了同一次提交。" : ""),
        });
        await queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });
        return;
      }
      setFeedback(null);
      setStageNotice({ kind: "error", text: visualGenerationBlockedText(result) });
    },
    onError: (mutationError) => {
      setFeedback(null);
      setStageNotice({
        kind: "error",
        text: `无法开始生成缺失片段：${mutationError instanceof Error ? mutationError.message : String(mutationError)}`,
      });
    },
  });

  /* ---------- page state: three different renderings ---------- */

  const state = useMemo<PageState | null>(() => {
    if (editions.isPending || beatsQuery.isPending) return { kind: "loading", message: "正在载入片段计划…" };
    if (overview.data && overview.data.capability_snapshot?.probed === false) {
      return {
        kind: "no_capability",
        title: "尚未接入能力探查",
        body: "无法确认本地图像/视频模型与工作流版本，因此不会排队 GPU，也不会给出乐观的预计耗时。",
        action: <Link to={routes.systemCapabilities(projectId)}>前往能力与模型</Link>,
      };
    }
    if (beatsQuery.isError) {
      return {
        kind: "failed",
        title: "无法载入片段计划",
        body: beatsQuery.error instanceof Error ? beatsQuery.error.message : "未知错误",
        action: <button type="button" onClick={() => { void beatsQuery.refetch(); }}>重新读取</button>,
      };
    }
    if (beats.length === 0) {
      return {
        kind: "empty",
        title: "还没有画面段",
        body: "片段来自第 4 步的分镜计划：先按真实旁白时长拆分画面段，再为每一段生成或指定片段。",
      };
    }
    const selected = beats.filter((beat) => clipIsRealVideo(beat)).length;
    if (selected < beats.length) {
      return {
        kind: "partial",
        title: `${selected} / ${beats.length} 个镜头已有真实视频片段`,
        body: "每个画面段都必须来自真实 AI 图生视频：还没有生成视频的镜头按「尚未生成」处理，只补缺失片段。",
      };
    }
    return null;
  }, [beats, beatsQuery, editions.isPending, overview.data, projectId]);

  const goNext = () => {
    const search = new URLSearchParams();
    if (selectedBeatId) search.set("beat", selectedBeatId);
    if (editionId) search.set("edition", editionId);
    const suffix = search.toString();
    navigate(`${routes.explainerPage(projectId, "review")}${suffix ? `?${suffix}` : ""}`);
  };

  const i2vBlockedReason = !videoProfileReady
    ? `视频模型未配置或不可执行：${videoProfile.option?.blockers?.[0]?.message ?? "请先配置可执行的 VIDEO_I2V Profile"}`
    : !keyframeSelectionId
      ? "缺少已采用首帧的 KEYFRAME 选择标识：请在第 4 步先采用首帧"
      : null;

  useExplainerActionBar({
    primary:
      missingVideoBeats.length > 0
        ? {
            label: `补齐缺失片段（${missingVideoBeats.length} 个镜头）`,
            onClick: () => backfill.mutate(),
            disabled: backfill.isPending,
            busy: backfill.isPending,
          }
        : { label: "下一步：预览与导出", onClick: goNext },
    summary: `共 ${beats.length} 个镜头，${readyCount} 个已就绪`,
    defer: { label: "稍后处理", onClick: () => navigate(routes.explainerPage(projectId, "review")) },
  });

  const canAdoptPreview = Boolean(previewCandidate) && previewCandidate?.status === "READY" && !adopt.isPending;

  return (
    <div className="explainer-page explainer-steps45">
      <Panel
        title="视频片段"
        subtitle="每个画面段一段真实 AI 图生视频片段；没有生成视频的镜头按「尚未生成」报告。"
        actions={
          <>
            {editions.data?.editions && editions.data.editions.length > 1 ? (
              <label className="explainer-field">
                输出版本
                <select
                  value={editionId ?? ""}
                  onChange={(event) =>
                    setSearchParams((params) => {
                      const next = new URLSearchParams(params);
                      next.set("edition", event.target.value);
                      return next;
                    })
                  }
                >
                  {editions.data.editions.map((edition) => {
                    const record = edition as unknown as Record<string, unknown>;
                    return (
                      <option key={String(record.id)} value={String(record.id)}>
                        {String(record.edition_key)} · {String(record.aspect_ratio)}
                      </option>
                    );
                  })}
                </select>
              </label>
            ) : null}
            <span className="badge">{readyCount} / {beats.length} 已就绪</span>
            <button
              type="button"
              className="explainer-btn"
              disabled={generateMissingClips.isPending || !canGenerateMissingClips}
              aria-busy={generateMissingClips.isPending}
              title={
                generateMissingClips.isPending
                  ? "正在提交：等待服务端返回是否已排队"
                  : canGenerateMissingClips
                    ? "为全片运行画面阶段：复用已采用首帧与已有片段，只为缺失的画面段生成真实图生视频片段"
                    : beats.length === 0
                      ? "画面段计划还没读到：先等本页载入完成"
                      : "本页已知每个画面段都有真实片段：没有缺失片段需要生成"
              }
              onClick={() => generateMissingClips.mutate()}
            >
              生成缺失片段
            </button>
          </>
        }
      >
        <StateNotice state={state} />
        {/* The whole-film receipt: it is not tied to one 画面段, so it stays visible here. */}
        {stageNotice?.kind === "ok" ? <InlineOk message={stageNotice.text} /> : null}
        {stageNotice?.kind === "error" ? <InlineError message={stageNotice.text} /> : null}
        {missingVideoBeats.length > 0 ? (
          <p className="explainer-gap-notice" role="status">
            本片每个画面段都必须来自真实 AI 图生视频：{missingVideoBeats.length} 个镜头尚未生成视频片段，
            按「尚未生成」报告（旧数据里的静图推拉记录不算片段）。
          </p>
        ) : null}
        <p className="explainer-shot-overview">共 {beats.length} 个镜头，{readyCount} 个已就绪</p>
        <details className="explainer-details">
          <summary>详情：计划/实际运动方式分布与哈希</summary>
          <div className="explainer-coverage">
            <div>
              <small>计划运动方式</small>
              <strong>
                {Object.entries(beatsQuery.data?.render_type_counts ?? {})
                  .map(([type, count]) => `${clipModeLabel(type)} ${count}`)
                  .join(" · ") || "—"}
              </strong>
            </div>
            <div>
              <small>实际运动方式</small>
              <strong>
                {Object.entries(beatsQuery.data?.actual_render_type_counts ?? {})
                  .map(([type, count]) => `${clipModeLabel(type)} ${count}`)
                  .join(" · ") || "—"}
              </strong>
            </div>
            <div>
              <small>生成计划哈希</small>
              <strong>{draw.plan ? `${draw.plan.plan_hash.slice(0, 16)}…` : "尚未检查生成条件"}</strong>
            </div>
            <div>
              <small>首帧选择标识</small>
              <strong>{keyframeSelectionId ? `${keyframeSelectionId.slice(0, 8)}…` : "未返回"}</strong>
            </div>
          </div>
          <p className="explainer-setting-note">
            计划与实际分开统计：只有真实 AI 图生视频才算片段，「尚未生成」的镜头不会被算作可用；
            旧数据里的静图推拉（已停用）只显示为停用的计划方式，必须改为 AI 动态。
          </p>
        </details>
      </Panel>

      <div className="explainer-shot-workspace">
        <ShotListPane
          beats={beats}
          selectedBeatId={selectedBeatId}
          purpose={purpose}
          title="镜头"
          subtitle="按旁白顺序排列，与讲稿段落是多对多关系。"
          typeLabel={(beat) => shotTypeShortLabel(plannedVsActual(beat as unknown as Record<string, unknown>).actual ?? beat.render_type)}
          onSelect={(beatId) => {
            setPreviewId(null);
            setFeedback(null);
            setError(null);
            setPendingVideoVersionId(null);
            setSearchParams((params) => {
              const next = new URLSearchParams(params);
              next.set("beat", beatId);
              return next;
            });
          }}
        />

        <section className="explainer-shot-stage" aria-label="片段预览与候选">
          {selectedBeat ? (
            <>
              <div className={`explainer-stage-frame${stillMediaVersionId ? "" : " no-source"}`}>
                <div className="explainer-stage-media explainer-motion-stage">
                  {displayCandidate && displayIsVideo ? (
                    <video
                      key={displayCandidate.id}
                      ref={videoRef}
                      className="explainer-player-media"
                      controls
                      preload="none"
                      playsInline
                      poster={displayCandidate.thumbnail_url ?? candidateThumbUrl(displayCandidate) ?? undefined}
                      src={candidateVideoSrc(displayCandidate) ?? undefined}
                      data-original-src={candidateVideoOriginalSrc(displayCandidate) ?? undefined}
                      onError={fallbackToOriginalVideo}
                      onPlay={() => setPlaying(true)}
                      onPause={() => setPlaying(false)}
                    />
                  ) : displayCandidate && displayCandidate.media_version_id ? (
                    <MediaThumb
                      src={mediaVersionThumbUrl(displayCandidate.media_version_id, "medium")}
                      alt={`镜头 ${selectedBeat.code} 预览`}
                      aspectRatio="16 / 9"
                      objectFit="contain"
                      loading="eager"
                    />
                  ) : (
                    <div className="explainer-placeholder" role="status">
                      <div>
                        <strong>镜头 {selectedBeat.code}</strong>
                        <div>这一段还没有可播放的片段或候选。</div>
                        <div>没有媒体时不会显示占位画面假装已有片段，也不会写「视频已生成」。</div>
                      </div>
                    </div>
                  )}
                </div>
                {stillMediaVersionId ? (
                  <div className="explainer-source-image">
                    <MediaThumb
                      src={mediaVersionThumbUrl(stillMediaVersionId)}
                      alt={`镜头 ${selectedBeat.code} 已采用首帧`}
                      aspectRatio="16 / 9"
                      objectFit="cover"
                    />
                    <span>已采用首帧（源图）</span>
                    <button
                      type="button"
                      className="explainer-btn explainer-btn--quiet"
                      onClick={() => {
                        const search = new URLSearchParams();
                        search.set("beat", selectedBeat.id);
                        if (editionId) search.set("edition", editionId);
                        navigate(`${routes.explainerPage(projectId, "storyboard")}?${search.toString()}`);
                      }}
                    >
                      更换源图
                    </button>
                    <span>运动草稿已保留，返回本页会恢复。</span>
                  </div>
                ) : null}
              </div>

              <div className="explainer-preview-state">
                <span className="explainer-chip is-adopted">
                  当前采用：{adoptedCandidate ? candidateShortLabel(adoptedCandidate) : "尚无"}
                </span>
                <span className="explainer-chip">
                  正在预览：{previewCandidate ? `${candidateShortLabel(previewCandidate)}（未采用）` : "无"}
                </span>
                <span className="badge">
                  {selectedBeat && clipIsRealVideo(selectedBeat) ? "AI 动态（真实图生视频）" : "尚未生成"}
                </span>
                {selectedBeat && isRetiredRenderType(plannedVsActual(selectedBeat as unknown as Record<string, unknown>).planned)
                  ? <span className="badge warn">计划方式已停用</span>
                  : null}
                {!displayIsVideo && displayCandidate ? <span className="badge warn">当前只有静帧，不是生成的视频片段</span> : null}
              </div>

              <div className="explainer-adopt-row">
                <span className="explainer-adopt-row__label">
                  {previewCandidate
                    ? `「采用」将把 ${candidateShortLabel(previewCandidate)} 写为当前采用；预览不会改变任何选择。`
                    : "先在候选卡片中预览一段片段；预览不等于采用。"}
                </span>
                <button type="button" className="explainer-btn" onClick={() => {
                  const node = videoRef.current;
                  if (!node) return;
                  if (node.paused) void node.play().catch(() => undefined);
                  else node.pause();
                }} disabled={!displayIsVideo}>
                  {playing ? "暂停" : "播放"}
                </button>
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={!canAdoptPreview}
                  onClick={() => previewCandidate && adopt.mutate({ candidateId: previewCandidate.id, lock: false })}
                >
                  采用
                </button>
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={!canAdoptPreview}
                  onClick={() => previewCandidate && adopt.mutate({ candidateId: previewCandidate.id, lock: true })}
                >
                  采用并锁定
                </button>
                {adoptedCandidate?.locked ? (
                  <button type="button" className="explainer-btn" onClick={() => unlock.mutate(adoptedCandidate)}>解锁</button>
                ) : null}
              </div>

              {conflict ? (
                <div className="explainer-inline-error" role="alert">
                  <p>采用被服务端拒绝：{conflict.message}。你的选择意图与运动描述已保留，不会自动改用其他候选。</p>
                  <button
                    type="button"
                    className="explainer-btn"
                    onClick={async () => {
                      await candidatesQuery.refetch();
                      setConflict(null);
                    }}
                  >
                    刷新后重试
                  </button>
                </div>
              ) : null}

              {candidatesQuery.isError ? (
                <div className="explainer-inline-error" role="alert">
                  <p>
                    候选读取失败：
                    {candidatesQuery.error instanceof Error ? candidatesQuery.error.message : "未知错误"}。
                    这不表示该镜头没有候选；已知采用的片段仍保留在上方。
                  </p>
                  <button type="button" className="explainer-btn" onClick={() => { void candidatesQuery.refetch(); }}>
                    重新读取
                  </button>
                </div>
              ) : null}

              <MediaCandidateGrid
                page={effectivePage}
                loading={candidatesQuery.isPending}
                error={null}
                previewedId={previewId}
                onPreview={(candidate) => setPreviewId(candidate ? candidate.id : null)}
                onRetryFailed={(candidate) => retryFailed.mutate(candidate)}
                onReload={() => { void candidatesQuery.refetch(); }}
                onUpload={() => setPickerOpen(true)}
                onUnlock={(candidate) => unlock.mutate(candidate)}
                onCompare={() => setCompareOpen(true)}
                nextBatchCount={draft.candidateCount}
                title="片段候选"
                emptyHint="点「生成」会在服务端完成预检后创建真实任务；也可以先上传或从媒体库选择已有视频。"
              />
              <div className="explainer-actions">
                <button type="button" className="explainer-btn" onClick={() => setCompareLimit(4)} disabled={compareLimit === 4}>
                  比较（最多 4 段）
                </button>
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={failedCandidates.length === 0 || retryFailedBatch.isPending}
                  onClick={() => retryFailedBatch.mutate()}
                >
                  {retryFailedBatch.isPending ? "正在重试" : `仅重试失败片段（${failedCandidates.length}）`}
                </button>
                <button
                  type="button"
                  className="explainer-btn explainer-btn--quiet"
                  disabled={candidatesQuery.isFetching}
                  onClick={() => { void candidatesQuery.refetch(); }}
                >
                  重新载入候选
                </button>
                <span className="explainer-setting-note">比较只查看，同步播放/暂停/回到开头，不改变采用；重新载入只重新读取媒体与列表。</span>
              </div>

              {pendingVideoVersionId ? (
                <div className="explainer-receipt" role="status">
                  <h4>已选择视频素材（尚未成为候选）</h4>
                  <p className="explainer-setting-note">
                    媒体版本 {pendingVideoVersionId} 已从项目媒体库/上传选择。登记为片段候选需要服务端的候选登记命令
                    （当前接口清单里没有该命令），因此它既不是候选也不会被采用。
                  </p>
                </div>
              ) : null}

              <InlineError message={error && (error.beatId === null || error.beatId === selectedBeatId) ? error.text : null} />
              <InlineOk message={feedback && (feedback.beatId === null || feedback.beatId === selectedBeatId) ? feedback.text : null} />
            </>
          ) : (
            <Panel title="镜头" subtitle="选择一个镜头">
              <p className="muted">选择一个镜头查看它的片段与候选。</p>
            </Panel>
          )}
        </section>

        <div className="explainer-stack">
          <GenerationControls
            purpose={purpose}
            candidateCounts={[1, 2]}
            candidateCount={draft.candidateCount}
            onCandidateCountChange={(value) => patchDraft({ candidateCount: value })}
            plan={draw.plan}
            planPending={draw.planPending}
            planError={draw.error}
            mode={effectiveMode}
            availableModes={[
              // 真实 AI 图生视频 is the only executable clip generation mode left:
              // 静图推拉 was removed from the product.
              { mode: "IMAGE_TO_VIDEO", label: "AI 动态（真实图生视频）", disabledReason: videoProfileReady ? null : "VIDEO_I2V 未配置" },
            ]}
            onModeChange={() => patchDraft({ presentation: "AI_VIDEO" })}
            budgetNote={`本次 ${draft.candidateCount} 段；服务端同样校验预算，余量未知时以服务端回执为准。`}
          >
            <div className="explainer-setting-block">
              <label className="explainer-field">
                <span>最终片段方式</span>
                <select
                  value={draft.presentation}
                  onChange={(event) => {
                    const next = event.target.value as PresentationKey;
                    // A beat planned as 必须运动 must remain AI 图生视频: 图形动画 and
                    // 已有视频 cannot satisfy a required motion.
                    if (selectedBeat?.must_be_motion && next !== "AI_VIDEO") return;
                    patchDraft({ presentation: next });
                  }}
                >
                  {PRESENTATION_OPTIONS.map((option) => (
                    <option
                      key={option.value}
                      value={option.value}
                      // A beat planned as 必须运动 must remain AI 图生视频.
                      disabled={Boolean(selectedBeat?.must_be_motion) && option.value !== "AI_VIDEO"}
                    >
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <p className="explainer-setting-note">
                第 4 步保存的规划：{renderTypeLabel(selectedBeat?.render_type)}。
                这里是实际执行方式的明确选择：AI 动态必须由真实图生视频产出，没有生成视频的镜头会按「尚未生成」报告。
              </p>
              {selectedBeat && isRetiredRenderType(selectedBeat.render_type) ? (
                <div className="explainer-gap-notice" role="alert">
                  <p>{RETIRED_RENDER_TYPE_NOTE}</p>
                  <div className="explainer-actions">
                    <button type="button" className="explainer-btn" onClick={() => patchDraft({ presentation: "AI_VIDEO" })}>
                      改为 AI 动态（图生视频）
                    </button>
                  </div>
                </div>
              ) : null}
              {drawUnsupportedType ? (
                <div className="explainer-gap-notice" role="status">
                  <p>{unsupportedReason}</p>
                  <div className="explainer-actions">
                    <button type="button" className="explainer-btn" onClick={() => setPickerOpen(true)}>
                      上传视频 / 选择视频
                    </button>
                    <button type="button" className="explainer-btn" onClick={() => patchDraft({ presentation: "AI_VIDEO" })}>
                      改用 AI 动态（图生视频）
                    </button>
                  </div>
                </div>
              ) : null}

              <CapabilityPicker
                capability={VIDEO_CAPABILITY}
                label="视频模型"
                value=""
                onChange={() => undefined}
                query={videoOptions}
                showDetails={false}
                description="只列出支持所需输入（首帧/首尾帧）的动态 Profile。"
              />
              {!videoProfileReady ? (
                <div className="explainer-gap-notice" role="alert">
                  <p>
                    视频模型未配置（缺少 VIDEO_I2V 能力）。静图推拉已从产品中移除：本片只能用真实图生视频，
                    请先配置可执行的图生视频能力。
                  </p>
                  <div className="explainer-actions">
                    <Link className="explainer-btn" to={routes.systemCapabilities(projectId)}>配置模型</Link>
                  </div>
                </div>
              ) : null}

              <div className="explainer-setting-block">
                <SettingRow label="首帧 / 参考图" value={stillMediaVersionId ? `已采用 ${stillMediaVersionId.slice(0, 8)}…` : "尚无已采用首帧"} />
                {stillMediaVersionId ? (
                  <MediaThumb
                    src={mediaVersionThumbUrl(stillMediaVersionId)}
                    alt="已采用首帧缩略图"
                    aspectRatio="16 / 9"
                    objectFit="cover"
                  />
                ) : null}
                {supportsEndFrame ? (
                  <div className="explainer-setting-block" role="group" aria-label="尾帧（该 Profile 支持首尾帧）">
                    <p className="explainer-setting-note">
                      该 Profile 声明支持首尾帧输入；可选尾帧来自已生成的图片。
                      生成命令的冻结契约目前只声明首帧（input_keyframe_selection_id），没有尾帧字段，
                      因此这里只列出可用图片并保留这个缺口，不会伪造一个不会被消费的提交字段。
                    </p>
                    <ul className="explainer-shot-list">
                      {(keyframeQuery.page?.candidates ?? []).map((candidate) => (
                        <li key={candidate.id}>
                          {candidateShortLabel(candidate)} · {candidate.status}
                          {candidate.media_version_id ? ` · ${candidate.media_version_id.slice(0, 8)}…` : ""}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <p className="explainer-setting-note">
                    当前 Profile 未声明首尾帧输入槽位，因此不显示尾帧控件，也不会传了字段却不消费。
                  </p>
                )}
              </div>

              <label className="explainer-field">
                <span>片段时长（服务端 Profile 支持的离散时长）</span>
                {draw.plan?.allowed_durations_ms?.length ? (
                  <select
                    value={String(draft.durationMs ?? draw.plan.allowed_durations_ms[0])}
                    onChange={(event) => patchDraft({ durationMs: Number(event.target.value) })}
                  >
                    {draw.plan.allowed_durations_ms.map((value) => (
                      <option key={value} value={String(value)}>{formatMs(value)}</option>
                    ))}
                  </select>
                ) : (
                  <p className="explainer-setting-note">
                    尚未取得服务端允许时长：默认按画面段覆盖区间 {formatMs(selectedBeat?.preferred_duration_ms ?? null)}，
                    检查生成条件后会以 Profile 实际允许的档位为准；更长旁白需要拆镜头而不是循环片段。
                  </p>
                )}
              </label>

              <label className="explainer-field">
                <span>运动描述</span>
                <textarea
                  value={draft.motion}
                  placeholder={selectedBeat ? defaultMotionText(selectedBeat) : "来自分镜规划的运动/动作描述"}
                  onChange={(event) => patchDraft({ motion: event.target.value })}
                />
              </label>
              <p className="explainer-setting-note">
                默认来自分镜规划（{selectedBeat ? defaultMotionText(selectedBeat).slice(0, 40) : "—"}），不是讲稿原文：
                当前旁白为「{selectedBeat ? narrationText(selectedBeat).slice(0, 40) : "—"}」。
              </p>

              <label className="explainer-field">
                <span>镜头运动{supportsCameraControl ? "" : "（语义提示）"}</span>
                <select value={draft.camera} onChange={(event) => patchDraft({ camera: event.target.value })}>
                  {CAMERA_MOVEMENT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              {!supportsCameraControl ? (
                <p className="explainer-setting-note">
                  当前 Profile 未声明相机控制输入，仅作为提示词语义；没有精确轨迹控件，不伪称支持相机参数。
                </p>
              ) : null}

              <details className="explainer-details">
                <summary>高级设置：负向提示词 / 种子 / 步数</summary>
                <label className="explainer-field">
                  <span>负向提示词</span>
                  <textarea value={draft.negative} onChange={(event) => patchDraft({ negative: event.target.value })} />
                </label>
                <label className="explainer-field">
                  <span>步数（合法范围来自 Profile）</span>
                  <input type="text" inputMode="numeric" value={draft.stepCount} onChange={(event) => patchDraft({ stepCount: event.target.value })} />
                </label>
                <div className="explainer-seed-row">
                  <span>种子（只读）</span>
                  <code>{draw.plan?.candidate_seeds?.length ? draw.plan.candidate_seeds.join("、") : "尚未检查生成条件"}</code>
                  <button
                    type="button"
                    className="explainer-btn explainer-btn--quiet"
                    onClick={() => {
                      const seeds = draw.plan?.candidate_seeds?.join(",");
                      if (seeds && typeof navigator !== "undefined" && navigator.clipboard) void navigator.clipboard.writeText(seeds);
                    }}
                  >
                    复制
                  </button>
                </div>
                <p className="explainer-setting-note">新创作使用新种子；技术重试沿用原种子。</p>
              </details>

              <div className="explainer-draw-actions">
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={!selectedBeat || draw.planPending || drawUnsupportedType}
                  onClick={() => draw.start()}
                  title={drawUnsupportedType ? unsupportedReason : undefined}
                >
                  {draw.planPending
                    ? "正在检查生成条件"
                    : candidates.length > 0
                      ? `再生成 ${draft.candidateCount} 段`
                      : `生成视频（${draft.candidateCount} 段）`}
                </button>
                <button
                  type="button"
                  className="explainer-btn"
                  disabled={!draw.canSubmit || Boolean(i2vBlockedReason) || drawUnsupportedType}
                  onClick={() => draw.submit()}
                  title={drawUnsupportedType ? unsupportedReason : i2vBlockedReason ?? (draw.canSubmit ? undefined : "先检查生成条件：可执行后才允许提交")}
                >
                  {draw.submitPending ? "正在提交" : `提交生成（本次 ${draft.candidateCount} 段）`}
                </button>
                <button type="button" className="explainer-btn" onClick={() => setPickerOpen(true)} disabled={!projectId}>
                  上传视频 / 选择视频
                </button>
                <button type="button" className="explainer-btn" disabled={!selectedBeatId || impact.isPending} onClick={() => impact.mutate()}>
                  查看更换影响
                </button>
                <button type="button" className="explainer-btn" onClick={() => setCoverageOpen(true)} disabled={!selectedBeat}>
                  调整覆盖区间
                </button>
                <span className="explainer-note">
                  {i2vBlockedReason ?? (draw.canSubmit ? "已冻结计划：可执行" : "尚未检查生成条件")}
                </span>
              </div>
            </div>
          </GenerationControls>

          {impactReport ? (
            <Panel title="更换影响" subtitle="依赖范围由服务端返回，前端不猜测。">
              <div aria-label="更换影响">
                <SettingRow label="受影响输出版本" value={`${Number(impactReport.affected_edition_count ?? 0)} 个`} />
                <SettingRow
                  label="将过期"
                  value={Array.isArray(impactReport.invalidates) ? (impactReport.invalidates as string[]).join("、") : "—"}
                />
                <SettingRow
                  label="保留"
                  value={Array.isArray(impactReport.preserves) ? (impactReport.preserves as string[]).join("、") : "—"}
                />
                <SettingRow label="是否需要整片重绘" value={impactReport.would_require_full_redraw ? "是" : "否"} />
                <p className="explainer-setting-note">
                  {String(impactReport.reuses_successful_products ?? "只复用哈希与编码条件仍然匹配的产物。")}
                </p>
              </div>
            </Panel>
          ) : null}

          {draw.plan ? (
            <Panel title="生成计划（只读）" subtitle="提交前先看清本次真实能力、首帧、时长与种子。">
              <dl className="explainer-plan-grid">
                <div>
                  <dt>允许时长</dt>
                  <dd>
                    {draw.plan.allowed_durations_ms?.length
                      ? draw.plan.allowed_durations_ms.map((value) => formatMs(value)).join(" / ")
                      : "服务端未返回"}
                  </dd>
                </div>
                <div>
                  <dt>预算</dt>
                  <dd>{draw.plan.budget ? JSON.stringify(draw.plan.budget) : "服务端未返回余量（待检查）"}</dd>
                </div>
                <div>
                  <dt>计划哈希</dt>
                  <dd>{draw.plan.plan_hash.slice(0, 16)}…</dd>
                </div>
                <div>
                  <dt>提交状态</dt>
                  <dd>{draw.planIsStale ? "生成条件已改变，需重新检查" : draw.plan.status === "EXECUTABLE" ? "可提交" : "被阻塞"}</dd>
                </div>
              </dl>
              <p className="explainer-setting-note">
                计划是只读的：它不创建媒体或任务；模型、参考图容量、种子与本次提交数量显示在上方生成设置里。
              </p>
            </Panel>
          ) : null}

          {draw.receipt ? (
            <Panel title="本次生成回执" subtitle="回执只表示已排队；候选变为可用后才会显示媒体。">
              <GenerationReceiptView receipt={draw.receipt} unit={unit} candidates={candidates} />
            </Panel>
          ) : null}

          <Panel title="运动与预算" subtitle="每个片段都来自真实 AI 图生视频。">
            <p className="muted">
              一个画面段只有拿到真实的图生视频产物才算片段；没有生成视频的镜头按「尚未生成」报告，
              不会被写成「视频已生成」，也不会用本地静图合成顶替。图形动画与已有视频是另外两种来源，单独标注。
            </p>
            <p className="muted" style={{ marginTop: 8 }}>
              技术重试沿用原 seed 与输入，创作重抽是新的抽卡意图；两者分开计账，预算余量未知时写「待检查」。
            </p>
          </Panel>
        </div>
      </div>

      <MediaCandidateCompare
        open={compareOpen}
        candidates={candidates.filter((candidate) => candidate.media_version_id).slice(0, compareLimit)}
        onClose={() => setCompareOpen(false)}
      />
      <MediaPickDrawer
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        projectId={projectId}
        mediaKind="VIDEO"
        label="上传视频 / 选择视频"
        value={pendingVideoVersionId ?? ""}
        onPick={(mediaVersionId) => setPendingVideoVersionId(mediaVersionId)}
        note="选择或上传只会登记为候选来源，不会直接成为采用的片段。"
      />
      <CoverageDrawer
        open={coverageOpen}
        onClose={() => setCoverageOpen(false)}
        beat={selectedBeat}
        onReplan={() => {
          setCoverageOpen(false);
          const search = new URLSearchParams();
          if (selectedBeatId) search.set("beat", selectedBeatId);
          if (editionId) search.set("edition", editionId);
          navigate(`${routes.explainerPage(projectId, "storyboard")}?${search.toString()}`);
        }}
      />
    </div>
  );
}

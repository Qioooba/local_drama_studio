import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  createFrameAnchor,
  createGenerationIntent,
  createKeyframeCandidate,
  createPrompt,
  deriveGenerationVariantPlan,
  deriveGenerationVariantSeedBatch,
  listGenerationIntents,
  listGenerationVariants,
  planGenerationVariant,
  resolveEffectiveConfiguration,
  resolveProfileCameraPlan,
  submitGenerationVariant,
  type CameraPlan,
  type FrameAnchor,
  type G6Readiness,
  type GenerationResourceEstimate,
  type GenerationVariantDraft,
  type GenerationVariantPlan,
  type I2VProbePlan,
  type Job,
  type Profile,
  type ReviewInboxItem,
} from "../../generated/api";
import { GateStatusIcon } from "../../components/icons";
import { Dialog } from "../../components/ui";
import { GenerationExperimentPanel } from "./GenerationExperimentPanel";
import { GenerationControlPanel } from "./GenerationControlPanel";
import { MotionControlPanel } from "./MotionControlPanel";
import { MediaPicker } from "../media-picker/MediaPicker";
import { creatorProfileTitle } from "../preferences-v2/canonicalCapabilities";
import { ProfileExecutionDetailButton } from "../model-config/ProfileExecutionDetailButton";
import { MEDIA_STAGE_LABELS, optionLabel } from "../shared/optionLabels";
import "./generation-workbench.css";

type Shot = Record<string, unknown>;
export type GenerationStep = "setup" | "inputs" | "preflight" | "variants" | "review";

type TakeSubmissionItem = {
  index: number;
  takeNumber: number;
  seed: number;
  status: "SUCCEEDED" | "FAILED";
  job?: Job;
  variantId?: string;
  error?: string;
};

type TakeBatchReport = {
  requested: number;
  results: TakeSubmissionItem[];
};

function mergeTakeBatchResults(
  previous: TakeBatchReport | null,
  incoming: TakeSubmissionItem[],
  requested: number,
): TakeBatchReport {
  const byIndex = new Map(previous?.results.map((item) => [item.index, item]) ?? []);
  incoming.forEach((item) => byIndex.set(item.index, item));
  return { requested, results: [...byIndex.values()].sort((left, right) => left.index - right.index) };
}

export interface GenerationWorkbenchProps {
  projectId: string | null;
  profiles: Profile[];
  candidates: ReviewInboxItem[];
  h3?: { status: string; release_root?: string; missing_sidecars?: string[] };
  g6Readiness?: G6Readiness;
  i2vProbePlan?: I2VProbePlan;
  shots: Shot[];
  selectedShotId: string | null;
  initialSourceImageId?: string;
  onSelectShot: (id: string) => void;
  onOpenProfiles: () => void;
  onOpenReviews?: (mediaVersionId?: string) => void;
  onOpenJobs?: (jobId: string) => void;
  onSubmitted?: () => void;
  aspectRatio?: "9:16" | "16:9";
  activeStep?: GenerationStep;
  onStepChange?: (step: GenerationStep) => void;
}

export const generationSteps: Array<{ id: GenerationStep; label: string; detail: string }> = [
  { id: "setup", label: "方式与镜头", detail: "绑定生产上下文" },
  { id: "inputs", label: "输入与控制", detail: "冻结创作参数" },
  { id: "preflight", label: "检查并启动", detail: "后台预检后确认" },
  { id: "variants", label: "创作分支", detail: "保留原结果继续尝试" },
  { id: "review", label: "人工审核", detail: "选择与批准分离" },
];

const modes = [
  { id: "T2I", title: "文字生成图片", detail: "从镜头描述创建关键帧候选", icon: "文 / 图" },
  { id: "I2V", title: "图片生成视频", detail: "用已批准首帧生成视频候选", icon: "图 / 影" },
  { id: "T2V", title: "文字生成视频", detail: "不绑定首帧，探索动作和运镜", icon: "文 / 影" },
  { id: "R2V", title: "参考图生成视频", detail: "保持角色或风格参考的一致性", icon: "参 / 影" },
] as const;

type GenerationMode = (typeof modes)[number]["id"];

export const generationModeCapability: Record<GenerationMode, string> = {
  T2I: "IMAGE_CONCEPT",
  I2V: "VIDEO_I2V",
  T2V: "VIDEO_T2V",
  R2V: "VIDEO_REFERENCE",
};

export function eligibleGenerationProfiles(profiles: Profile[], mode: GenerationMode): Profile[] {
  const requiredCapability = generationModeCapability[mode];
  return profiles.filter((profile) => profile.capability === requiredCapability && profile.status === "PUBLISHED");
}

export function approvedKeyframeMediaIds(
  candidates: ReviewInboxItem[],
  shotId: string | null,
  gateApproved?: { media_version_id?: string; shot_id?: string } | null,
): string[] {
  const ids = candidates
    .filter((item) => item.shot_id === shotId && item.media_kind === "IMAGE" && item.stage === "KEYFRAME" && item.decision === "APPROVED" && !item.is_stale)
    .map((item) => item.media_version_id);
  if (shotId && gateApproved?.shot_id === shotId && gateApproved.media_version_id) ids.push(gateApproved.media_version_id);
  return [...new Set(ids.filter(Boolean))];
}

type FrameAction = "FIRST_FRAME" | "CURRENT_FRAME" | "LAST_FRAME";

function parseControlList(value: string, label: string): Array<Record<string, unknown>> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label} 必须是有效 JSON 数组`);
  }
  if (!Array.isArray(parsed) || parsed.some((item) => !item || typeof item !== "object" || Array.isArray(item))) {
    throw new Error(`${label} 必须是对象数组`);
  }
  return parsed as Array<Record<string, unknown>>;
}

function profileResourcePolicy(profile: Profile | undefined): Record<string, unknown> {
  const raw = profile?.resource_policy ?? profile?.resource_policy_json;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try {
      const parsed: unknown = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
    } catch {
      // Ignored
    }
  }
  return {};
}

function policyEstimate(policy: Record<string, unknown>, keys: string[]): number | null {
  const value = keys.map((key) => policy[key]).find((candidate) => typeof candidate === "number");
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function formatEstimate(value: number | null, count: number, unit = ""): string {
  if (value === null) return "未声明";
  const total = Math.round(value * count * 100) / 100;
  return `${total}${unit}`;
}

const frameActionLabels: Record<FrameAction, string> = {
  FIRST_FRAME: "首帧",
  CURRENT_FRAME: "当前帧",
  LAST_FRAME: "末帧",
};

const WORKBENCH_DRAFT_PREFIX = "local-drama:generation-workbench-draft:v1";

export function generationWorkbenchDraftKey(projectId: string | null, shotId: string | null): string {
  return `${WORKBENCH_DRAFT_PREFIX}:${projectId ?? "no-project"}:${shotId ?? "no-shot"}`;
}

/** Stable per-shot seed derivation so consecutive shots never collide by
 *  default (Z-04). Deterministic to keep reproducibility when revisiting a shot. */
export function deriveShotSeed(shotId: string | null, fallback = 42): number {
  if (!shotId) return fallback;
  let hash = 2166136261;
  for (let index = 0; index < shotId.length; index += 1) {
    hash ^= shotId.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const seed = (hash >>> 0) % 900_000 + 100_000;
  return Number.isFinite(seed) ? seed : fallback;
}

/** Compose a starting prompt from the structured shot intent so directors do
 *  not have to re-paste every take (Z-05). */
export function composePromptFromIntent(fields: Record<string, unknown>, shotCode?: string | null): string {
  const camera = fields.camera_plan && typeof fields.camera_plan === "object" ? fields.camera_plan as Record<string, unknown> : {};
  const performance = fields.performance && typeof fields.performance === "object" ? fields.performance as Record<string, unknown> : {};
  const parts: string[] = [];
  if (shotCode) parts.push(`镜头 ${shotCode}`);
  const action = typeof fields.subject_action === "string" ? fields.subject_action : "";
  if (action) parts.push(action);
  const intent = typeof fields.creative_intent === "string" ? fields.creative_intent : "";
  if (intent) parts.push(`情绪基调：${intent}`);
  const shotType = typeof camera.shot_type === "string" && camera.shot_type ? camera.shot_type : typeof fields.shot_type === "string" && fields.shot_type ? fields.shot_type : "";
  if (shotType) parts.push(`景别 ${shotType}`);
  const movement = typeof camera.movement === "string" && camera.movement && camera.movement !== "STATIC" ? camera.movement : "";
  if (movement) parts.push(`运镜 ${movement}`);
  const emotion = typeof performance.emotion === "string" && performance.emotion ? performance.emotion : "";
  if (emotion) parts.push(`情绪 ${emotion}`);
  const dialogue = Array.isArray(fields.dialogue)
    ? fields.dialogue.map((line) => typeof line === "string" ? line : typeof line === "object" && line ? String((line as Record<string, unknown>).text ?? (line as Record<string, unknown>).speaker ?? "") : "").filter(Boolean).join("；")
    : typeof fields.dialogue === "string" ? fields.dialogue : "";
  if (dialogue) parts.push(`对白：${dialogue}`);
  return parts.filter(Boolean).join("，");
}

function readWorkbenchDraft(projectId: string | null, shotId: string | null): Record<string, unknown> | null {
  try {
    const scopedKey = generationWorkbenchDraftKey(projectId, shotId);
    let raw = window.localStorage.getItem(scopedKey);
    if (!raw) {
      // One-time compatibility migration from the former global draft key.
      raw = window.localStorage.getItem(WORKBENCH_DRAFT_PREFIX);
      if (raw) {
        window.localStorage.setItem(scopedKey, raw);
        window.localStorage.removeItem(WORKBENCH_DRAFT_PREFIX);
      }
    }
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

const readinessLabels: Record<string, string> = {
  APPROVED_KEYFRAME: "批准一张真实关键帧",
  PUBLISHED_I2V_PROFILE: "用真实 I2V 成功证据发布能力",
  FOUR_REAL_PROXY_TAKES: "从同一批准关键帧生成 4 个真实代理",
  HUMAN_PROXY_WINNER: "由人工选择代理 winner",
  FORMAL_VIDEO: "从 winner 生成正式视频",
  FORMAL_MACHINE_QC: "正式视频通过机器 QC",
  FORMAL_HUMAN_APPROVAL: "由人工完成正式审核批准",
};

export function GenerationWorkbench({
  projectId,
  profiles,
  candidates,
  h3,
  g6Readiness,
  i2vProbePlan,
  shots,
  selectedShotId,
  initialSourceImageId,
  onSelectShot,
  onOpenProfiles,
  onOpenReviews,
  onOpenJobs,
  onSubmitted,
  aspectRatio,
  activeStep,
  onStepChange,
}: GenerationWorkbenchProps) {
  const [localStep, setLocalStep] = useState<GenerationStep>("setup");
  const currentStep = activeStep ?? localStep;
  const selectStep = (step: GenerationStep) => (onStepChange ?? setLocalStep)(step);

  const selectedShot = shots.find((shot) => String(shot.id) === selectedShotId);
  const revision = selectedShot?.current_revision && typeof selectedShot.current_revision === "object" ? (selectedShot.current_revision as Record<string, unknown>) : {};

  const [mode, setMode] = useState<(typeof modes)[number]["id"]>("I2V");
  const videos = useMemo(() => candidates.filter((item) => item.media_kind === "VIDEO"), [candidates]);
  const approvedKeyframeIds = useMemo(
    () => approvedKeyframeMediaIds(candidates, selectedShotId, i2vProbePlan?.snapshot.approved_keyframe),
    [candidates, i2vProbePlan, selectedShotId],
  );

  const eligibleProfiles = useMemo(
    () => eligibleGenerationProfiles(profiles, mode),
    [mode, profiles]
  );
  const [profileVersionId, setProfileVersionId] = useState("");
  const [sourceVideoId, setSourceVideoId] = useState("");
  const [sourceImageId, setSourceImageId] = useState("");
  const [currentTimeSeconds, setCurrentTimeSeconds] = useState("0");
  const [draftAnchor, setDraftAnchor] = useState<FrameAnchor | null>(null);
  const [frameAction, setFrameAction] = useState<FrameAction | null>(null);
  const [promptText, setPromptText] = useState("");
  const [timedDirectionsText, setTimedDirectionsText] = useState("[]");
  const [performanceBindingsText, setPerformanceBindingsText] = useState("[]");
  const [referenceBindingsText, setReferenceBindingsText] = useState("[]");
  const [motionMasksText, setMotionMasksText] = useState("[]");
  const [seedText, setSeedText] = useState("42");
  const [seedTouched, setSeedTouched] = useState(false);
  const [takeCountText, setTakeCountText] = useState("1");
  const [tier, setTier] = useState("");
  const [approvedKeyframeId, setApprovedKeyframeId] = useState("");
  const [prepared, setPrepared] = useState<{
    draft: GenerationVariantDraft;
    plan: GenerationVariantPlan;
    idempotencyKeys: string[];
  } | null>(null);
  const [preflightResourceEstimate, setPreflightResourceEstimate] = useState<GenerationResourceEstimate | null>(null);
  const [submitted, setSubmitted] = useState<Job | null>(null);
  const [submittedCount, setSubmittedCount] = useState(0);
  const [submittedVariantId, setSubmittedVariantId] = useState<string | null>(null);
  const [takeBatchReport, setTakeBatchReport] = useState<TakeBatchReport | null>(null);
  const [restoredIntentId, setRestoredIntentId] = useState<string | null>(null);
  const [restoredVariantId, setRestoredVariantId] = useState<string | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [seedBatchText, setSeedBatchText] = useState("43,44,45,46");
  const [seedBatchCount, setSeedBatchCount] = useState(4);
  const [seedBatchPlan, setSeedBatchPlan] = useState<Awaited<ReturnType<typeof deriveGenerationVariantSeedBatch>>["batch"] | null>(null);
  const [branchPlan, setBranchPlan] = useState<Awaited<ReturnType<typeof deriveGenerationVariantPlan>>["plan"] | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    setProfileVersionId(eligibleProfiles.find((item) => item.status === "PUBLISHED")?.version_id ?? eligibleProfiles[0]?.version_id ?? "");
  }, [eligibleProfiles]);

  useEffect(() => {
    if (!videos.some((item) => item.media_version_id === sourceVideoId)) {
      setSourceVideoId(videos[0]?.media_version_id ?? "");
    }
  }, [sourceVideoId, videos]);

  useEffect(() => {
    if (!approvedKeyframeIds.includes(approvedKeyframeId)) {
      setApprovedKeyframeId(approvedKeyframeIds[0] ?? "");
    }
  }, [approvedKeyframeId, approvedKeyframeIds]);

  useEffect(() => {
    // A locked Frame Bridge is the director's explicit source choice for this
    // shot.  Carry it into the source picker so users do not have to guess
    // which generated anchor belongs to the current boundary.  It is still
    // only a source image here: I2V remains blocked until a KEYFRAME review is
    // explicitly approved.
    setSourceImageId(initialSourceImageId ?? "");
    setDraftAnchor(null);
  }, [initialSourceImageId, selectedShotId]);

  useEffect(() => {
    setPrepared(null);
    setPreflightResourceEstimate(null);
    setSubmitted(null);
    setSubmittedCount(0);
    setSubmittedVariantId(null);
    setTakeBatchReport(null);
    setSeedBatchPlan(null);
    setBranchPlan(null);
    setConfirmOpen(false);
  }, [mode, profileVersionId, selectedShotId, promptText, timedDirectionsText, performanceBindingsText, referenceBindingsText, motionMasksText, seedText, approvedKeyframeId, takeCountText, tier]);

  // Derive a deterministic per-shot default seed (Z-04) unless the director
  // has already hand-edited it for this session.
  useEffect(() => {
    if (seedTouched) return;
    setSeedText(String(deriveShotSeed(selectedShotId)));
  }, [selectedShotId, seedTouched]);

  // Restore a project-and-shot scoped browser draft so switching shots never
  // leaks one shot's prompt or seed into another (L-01).
  useEffect(() => {
    const draft = readWorkbenchDraft(projectId, selectedShotId);
    if (!draft) {
      setPromptText("");
      setSeedText(String(deriveShotSeed(selectedShotId)));
      setSeedTouched(false);
      setTakeCountText("1");
      return;
    }
    if (typeof draft.promptText === "string") setPromptText(draft.promptText);
    if (typeof draft.seedText === "string") { setSeedText(draft.seedText); setSeedTouched(true); }
    if (typeof draft.takeCountText === "string") setTakeCountText(draft.takeCountText);
    if (typeof draft.mode === "string" && modes.some((item) => item.id === draft.mode)) setMode(draft.mode as GenerationMode);
  }, [projectId, selectedShotId]);

  const persistWorkbenchDraft = (
    patch: Partial<Record<"promptText" | "seedText" | "takeCountText" | "mode", string>>,
    draftProjectId = projectId,
    draftShotId = selectedShotId,
  ) => {
    try {
      window.localStorage.setItem(generationWorkbenchDraftKey(draftProjectId, draftShotId), JSON.stringify({
        promptText, seedText, takeCountText, mode, ...patch,
      }));
    } catch {
      // Non-fatal: draft buffer is best-effort only.
    }
  };

  const prefillPromptFromIntent = () => {
    if (!selectedShot) return;
    const fields = revision;
    const composed = composePromptFromIntent(fields, String(selectedShot.code ?? selectedShotId ?? ""));
    if (composed) setPromptText(composed);
  };

  // Debounced best-effort draft persistence + dirty guard on leave.
  useEffect(() => {
    const hasMeaningfulDraft = Boolean(promptText) || seedTouched || takeCountText !== "1" || mode !== "I2V";
    if (!hasMeaningfulDraft) return;
    const timer = window.setTimeout(() => persistWorkbenchDraft({}), 400);
    return () => {
      window.clearTimeout(timer);
      persistWorkbenchDraft({}, projectId, selectedShotId);
    };
  }, [mode, projectId, promptText, seedText, seedTouched, selectedShotId, takeCountText]);

  useEffect(() => {
    const hasMeaningfulDraft = Boolean(promptText) || seedTouched || takeCountText !== "1" || mode !== "I2V";
    if (!hasMeaningfulDraft) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [mode, promptText, seedTouched, takeCountText]);

  useEffect(() => {
    let cancelled = false;
    setRestoredIntentId(null);
    setRestoredVariantId(null);
    setRestoreError(null);
    if (!projectId || !selectedShotId) return () => { cancelled = true; };
    void (async () => {
      try {
        const purpose = mode === "I2V" ? "I2V_PROXY" : mode;
        const { items: intents } = await listGenerationIntents(projectId);
        const matching = intents
          .filter((item) => item.owner_type === "SHOT" && item.owner_id === selectedShotId && item.purpose === purpose)
          .reverse();
        for (let offset = 0; offset < matching.length; offset += 8) {
          const batch = matching.slice(offset, offset + 8);
          const results = await Promise.all(batch.map(async (intent) => ({
            intent,
            variants: (await listGenerationVariants(intent.id)).items,
          })));
          const restored = results.find((item) => item.variants.length > 0);
          if (!restored) continue;
          const latest = [...restored.variants].sort((left, right) => Number(right.variant_no) - Number(left.variant_no))[0];
          if (!cancelled) {
            setRestoredIntentId(restored.intent.id);
            setRestoredVariantId(latest.id);
          }
          return;
        }
      } catch (caught) {
        if (!cancelled) setRestoreError(String(caught));
      }
    })();
    return () => { cancelled = true; };
  }, [mode, projectId, selectedShotId]);

  const effectiveIntentId = prepared?.draft.intent_id ?? restoredIntentId;
  const effectiveVariantId = submittedVariantId ?? restoredVariantId;

  const selected = eligibleProfiles.find((profile) => profile.version_id === profileVersionId);
  const allowedTierCodes = useMemo(
    () =>
      selected?.dynamic_production_tiers || !selected?.workflow_tier
        ? undefined
        : [selected.workflow_tier],
    [selected?.dynamic_production_tiers, selected?.workflow_tier],
  );
  useEffect(() => {
    if (tier && allowedTierCodes && !allowedTierCodes.includes(tier)) {
      setTier("");
    }
  }, [allowedTierCodes, tier]);
  const resourcePolicy = profileResourcePolicy(selected);
  const cameraPlan = revision.camera_plan && typeof revision.camera_plan === "object" ? (revision.camera_plan as CameraPlan) : null;
  const requiresCamera = mode !== "T2I";
  const cameraReady = !requiresCamera || !cameraPlan || selected?.status === "PUBLISHED";
  const sourceReady = mode !== "I2V" || Boolean(approvedKeyframeId);
  const seed = Number(seedText);
  const takeCount = Number(takeCountText);
  const runnable =
    selected?.status === "PUBLISHED" &&
    Boolean(
      projectId &&
        selectedShotId &&
        promptText.trim() &&
        Number.isInteger(seed) &&
        Number.isInteger(takeCount) &&
        takeCount >= 1 &&
        takeCount <= 8 &&
        cameraReady &&
        sourceReady
    );

  const anchorMutation = useMutation({
    mutationFn: async (action: FrameAction) => {
      if (!sourceVideoId) throw new Error("当前项目没有可用视频");
      const seconds = Number(currentTimeSeconds);
      if (action === "CURRENT_FRAME" && (!Number.isFinite(seconds) || seconds < 0)) {
        throw new Error("当前时间必须是大于或等于 0 的秒数");
      }
      setFrameAction(action);
      return createFrameAnchor(
        sourceVideoId,
        action === "FIRST_FRAME"
          ? { position_mode: "FIRST_FRAME", role_hint: action }
          : action === "LAST_FRAME"
            ? { position_mode: "LAST_FRAME", role_hint: action }
            : { source_time_us: Math.round(seconds * 1_000_000), role_hint: action }
      );
    },
    onSuccess: ({ frame_anchor }) => setDraftAnchor(frame_anchor),
  });

  const keyframeMutation = useMutation({
    mutationFn: async () => {
      const sourceMediaVersionId = draftAnchor?.extracted_media_version_id ?? sourceImageId;
      if (!sourceMediaVersionId || !selectedShotId) throw new Error("必须先选择镜头和项目内真实图片，或从视频提取真实帧");
      return createKeyframeCandidate(sourceMediaVersionId, selectedShotId);
    },
    onSuccess: ({ media }) => onOpenReviews?.(media.id),
  });

  const preflightMutation = useMutation({
    mutationFn: async () => {
      if (!projectId || !selectedShotId || !selected) throw new Error("请先选择项目、镜头和可用生成能力");
      if (!promptText.trim()) throw new Error("镜头描述不能为空");
      if (!Number.isInteger(seed)) throw new Error("Seed 必须是整数");
      if (!Number.isInteger(takeCount) || takeCount < 1 || takeCount > 8) throw new Error("候选数量必须是 1—8");
      if (requiresCamera && !cameraReady) throw new Error("当前生成能力不支持这个运镜设置");
      if (mode === "I2V" && !approvedKeyframeId) throw new Error("图片生成视频前需要选择本镜已批准关键帧");

      const intent = await createGenerationIntent({
        project_id: projectId,
        owner_type: "SHOT",
        owner_id: selectedShotId,
        purpose: mode === "I2V" ? "I2V_PROXY" : mode,
        creative_goal: promptText.trim(),
      });
      let boundCameraPlan = cameraPlan
        ? {
            ...cameraPlan,
            profile_version_id: selected.version_id,
          }
        : null;
      if (cameraPlan?.mode === "UNSUPPORTED") {
        const { resolution } = await resolveProfileCameraPlan(selected.version_id, {
          shot_type: cameraPlan.shot_type,
          movement: cameraPlan.movement,
          direction: cameraPlan.direction,
          intensity: cameraPlan.intensity,
          curve: cameraPlan.curve,
          ...(cameraPlan.prompt_text ? { prompt_text: cameraPlan.prompt_text } : {}),
        });
        if (!resolution.submission_allowed) {
          throw new Error(`当前生成能力不支持这个运镜设置：${resolution.support}`);
        }
        boundCameraPlan = { ...resolution.camera_plan, profile_version_id: selected.version_id };
      }

      const prompt = await createPrompt({
        project_id: projectId,
        owner_type: "SHOT",
        owner_id: selectedShotId,
        purpose: mode,
        title: `${String(selectedShot?.code ?? selectedShotId)} ${mode}`,
        content_text: promptText.trim(),
        structured: { camera_plan: boundCameraPlan },
      });
      const semanticBindings = parseControlList(referenceBindingsText, "驱动与参考 MediaVersion");
      const bindings = (
        mode === "I2V"
          ? [{ role: "FIRST_FRAME", media_version_id: approvedKeyframeId, ordinal: 0 }, ...semanticBindings]
          : semanticBindings
      ) as GenerationVariantDraft["bindings"];

      const draft: GenerationVariantDraft = {
        intent_id: intent.intent.id,
        variant_type: "BASE",
        parent_variant_id: null,
        branch_reason: "UI_BASE_GENERATION",
        prompt_revision_id: prompt.revision.id,
        profile_version_id: selected.version_id,
        parameter_set: {
          PROMPT: promptText.trim(),
          SEED: seed,
          ...(boundCameraPlan ? { camera_plan: boundCameraPlan } : {}),
          timed_directions: parseControlList(timedDirectionsText, "TimedDirection"),
          performance_bindings: parseControlList(performanceBindingsText, "PerformanceBinding"),
          motion_masks: parseControlList(motionMasksText, "MotionMask"),
          ...(tier ? { tier } : {}),
        },
        seed_policy: "EXPLICIT",
        explicit_seed: seed,
        bindings,
      };
      const effective = await resolveEffectiveConfiguration({
        project_id: projectId,
        episode_id: null,
        shot_id: selectedShotId,
        capability_code: selected.capability,
        requested_profile_version_id: selected.version_id,
        run_overrides: {
          ...(tier ? { production_tier: tier } : {}),
          ...(mode !== "T2I" ? { take_count: takeCount } : {}),
        },
      });
      if (effective.configuration.blocking_errors.length) {
        throw new Error(effective.configuration.blocking_errors.map((item) => item.message).join("；"));
      }
      const draftWithConfiguration: GenerationVariantDraft = {
        ...draft,
        expected_effective_configuration_fingerprint: effective.configuration.fingerprint,
      };
      const planned = await planGenerationVariant(draftWithConfiguration);
      return {
        draft: draftWithConfiguration,
        plan: planned.plan,
        idempotencyKeys: Array.from({ length: takeCount }, () => crypto.randomUUID()),
      };
    },
    onSuccess: (result) => {
      setPreflightResourceEstimate(result.plan.resource_estimate ?? null);
      setPrepared(result);
      setConfirmOpen(true);
    },
  });

  const submitMutation = useMutation({
    mutationFn: async (indexes: number[]) => {
      if (!prepared) throw new Error("必须先完成当前输入的资源预检");
      const results: TakeSubmissionItem[] = [];
      for (const index of indexes) {
        const currentSeed = seed + index;
        const draft =
          index === 0
            ? prepared.draft
            : {
                ...prepared.draft,
                explicit_seed: currentSeed,
                parameter_set: { ...prepared.draft.parameter_set, SEED: currentSeed },
                branch_reason: "UI_MULTI_TAKE_GENERATION",
              };
        try {
          const plan = index === 0 ? prepared.plan : (await planGenerationVariant(draft)).plan;
          const committed = await submitGenerationVariant({
            ...draft,
            plan_hash: plan.plan_hash,
            idempotency_key: prepared.idempotencyKeys[index],
          });
          results.push({
            index,
            takeNumber: index + 1,
            seed: currentSeed,
            status: "SUCCEEDED",
            job: committed.job,
            variantId: committed.variant.id,
          });
        } catch (caught) {
          results.push({
            index,
            takeNumber: index + 1,
            seed: currentSeed,
            status: "FAILED",
            error: caught instanceof Error ? caught.message : String(caught),
          });
        }
      }
      return results;
    },
    onSuccess: (results) => {
      setTakeBatchReport((previous) => {
        const merged = mergeTakeBatchResults(previous, results, takeCount);
        setSubmittedCount(merged.results.filter((item) => item.status === "SUCCEEDED").length);
        return merged;
      });
      const lastSuccess = [...results].reverse().find((item) => item.status === "SUCCEEDED" && item.job && item.variantId);
      if (lastSuccess?.job && lastSuccess.variantId) {
        setSubmitted(lastSuccess.job);
        setSubmittedVariantId(lastSuccess.variantId);
        onSubmitted?.();
      }
      setConfirmOpen(false);
    },
  });

  const failedTakeIndexes = takeBatchReport?.results
    .filter((item) => item.status === "FAILED")
    .map((item) => item.index) ?? [];

  const seedBatchMutation = useMutation({
    mutationFn: async () => {
      if (!effectiveVariantId) throw new Error("请先提交一个生成 Variant");
      const seeds = seedBatchText
        .split(",")
        .map((value) => Number(value.trim()))
        .filter((value) => Number.isInteger(value));
      if (seeds.length === 0 || seeds.length > 24 || new Set(seeds).size !== seeds.length) {
        throw new Error("批量 seed 必须是 1—24 个不重复整数");
      }
      return deriveGenerationVariantSeedBatch(effectiveVariantId, { seeds, branch_reason: "UI_SEED_BATCH_EXPERIMENT" });
    },
    onSuccess: ({ batch }) => setSeedBatchPlan(batch),
  });

  const branchMutation = useMutation({
    mutationFn: async (operation: "RESAMPLE_NEW_SEED" | "RESUBMIT_PROVIDER_RANDOM" | "EXACT_REPLAY" | "PROMPT_BRANCH" | "SOURCE_IMAGE_BRANCH") => {
      if (!effectiveVariantId) throw new Error("请先提交一个基础 Variant");
      if (operation === "RESAMPLE_NEW_SEED") {
        return deriveGenerationVariantPlan(effectiveVariantId, { operation, explicit_seed: seed + 1, branch_reason: "UI_RESAMPLE_NEW_SEED" });
      }
      if (operation === "RESUBMIT_PROVIDER_RANDOM") {
        return deriveGenerationVariantPlan(effectiveVariantId, { operation, branch_reason: "UI_RESUBMIT_PROVIDER_RANDOM" });
      }
      if (operation === "EXACT_REPLAY") {
        return deriveGenerationVariantPlan(effectiveVariantId, { operation, branch_reason: "UI_EXACT_REPLAY" });
      }
      if (operation === "SOURCE_IMAGE_BRANCH") {
        if (!approvedKeyframeId) throw new Error("换图分支需要选择已批准关键帧");
        return deriveGenerationVariantPlan(effectiveVariantId, {
          operation,
          first_frame_media_version_id: approvedKeyframeId,
          branch_reason: "UI_SOURCE_IMAGE_BRANCH",
        });
      }
      if (!projectId || !selectedShotId || !promptText.trim()) throw new Error("改 Prompt 分支需要项目、镜头和非空 Prompt");
      const prompt = await createPrompt({
        project_id: projectId,
        owner_type: "SHOT",
        owner_id: selectedShotId,
        purpose: "PROMPT_BRANCH",
        title: `${String(selectedShot?.code ?? selectedShotId)} Prompt branch`,
        content_text: promptText.trim(),
        structured: { branch_from_variant_id: effectiveVariantId },
      });
      return deriveGenerationVariantPlan(effectiveVariantId, {
        operation,
        prompt_revision_id: prompt.revision.id,
        branch_reason: "UI_PROMPT_BRANCH",
      });
    },
    onSuccess: ({ plan }) => setBranchPlan(plan),
  });

  const branchSubmitMutation = useMutation({
    mutationFn: async () => {
      if (!branchPlan) throw new Error("请先生成分支计划");
      return submitGenerationVariant({ ...branchPlan.draft, plan_hash: branchPlan.plan_hash, idempotency_key: crypto.randomUUID() });
    },
    onSuccess: ({ job }) => {
      setSubmitted(job);
      setBranchPlan(null);
      onSubmitted?.();
    },
  });

  const extractedThumbnail = draftAnchor
    ? `/api/v1/media-versions/${encodeURIComponent(draftAnchor.extracted_media_version_id)}/thumbnail?size=small&frame=poster`
    : null;
  const draftRoleLabel = draftAnchor ? frameActionLabels[draftAnchor.role_hint as FrameAction] ?? "提取帧" : null;

  const policyPerTake = {
    duration_seconds: policyEstimate(resourcePolicy, ["estimated_duration_seconds_per_take", "duration_seconds_per_take", "estimated_time_seconds_per_take", "time_seconds_per_take"]),
    vram_bytes: policyEstimate(resourcePolicy, ["estimated_vram_bytes_per_take", "vram_bytes_per_take", "estimated_gpu_memory_bytes_per_take", "gpu_memory_bytes_per_take"]),
    disk_bytes: policyEstimate(resourcePolicy, ["estimated_disk_bytes_per_take", "disk_bytes_per_take", "estimated_output_bytes_per_take", "output_bytes_per_take"]),
  };
  const visiblePerTake = preflightResourceEstimate?.per_take ?? policyPerTake;
  const resourceStatus =
    preflightResourceEstimate?.status ??
    (Object.values(policyPerTake).every((value) => value === null)
      ? "UNKNOWN"
      : Object.values(policyPerTake).some((value) => value === null)
        ? "PARTIAL"
        : "DECLARED");

  return (
    <div className="generation-workbench" role="region" aria-labelledby="generation-workbench-title">
      <h2 id="generation-workbench-title" className="sr-only">
        生成工作台
      </h2>

      {/* Stage Rail Navigation */}
      <nav className="step-rail" aria-label="生成阶段导航">
        {generationSteps.map((step, idx) => (
          <button
            key={step.id}
            type="button"
            className={`step-btn${currentStep === step.id ? " active" : ""}`}
            onClick={() => selectStep(step.id)}
            aria-current={currentStep === step.id ? "step" : undefined}
          >
            <span className="step-badge">{idx + 1}</span>
            <strong>{step.label}</strong>
          </button>
        ))}
      </nav>

      {/* STAGE 1: SETUP */}
      {currentStep === "setup" && (
        <section className="stage-container" aria-labelledby="stage-setup-title">
          <div className="stage-header">
            <div>
              <h3 id="stage-setup-title">1. 选择镜头与生成方式</h3>
              <p>选择要制作的镜头和生成方式，系统会自动匹配可用能力</p>
            </div>
            <span className="status-pill neutral">步骤 1 / 5</span>
          </div>

          <div className="shot-context">
            <label htmlFor="generation-shot">当前镜头</label>
            <select
              id="generation-shot"
              value={selectedShotId ?? ""}
              onChange={(event) => onSelectShot(event.target.value)}
              disabled={!shots.length}
            >
              {!shots.length && <option value="">当前集没有可用镜头</option>}
              {!selectedShotId && shots.length > 0 && <option value="">请选择镜头</option>}
              {shots.map((shot) => (
                <option key={String(shot.id)} value={String(shot.id)}>
                  {String(shot.code ?? "镜头")} · {String(shot.title ?? shot.name ?? "未命名镜头")}
                </option>
              ))}
            </select>
          </div>

          <div className="section-heading">
            <p className="eyebrow">生成意图</p>
            <h4>你想为当前镜头做什么？</h4>
          </div>
          <div className="mode-grid" role="group" aria-label="生成方式">
            {modes.map((item) => (
              <button
                type="button"
                key={item.id}
                className={`mode-card${mode === item.id ? " selected" : ""}`}
                aria-pressed={mode === item.id}
                onClick={() => setMode(item.id)}
              >
                <span className="mode-icon" aria-hidden="true">
                  {item.icon}
                </span>
                <strong>{item.title}</strong>
                <small>{item.detail}</small>
                {mode === item.id && <span className="selected-mark">当前选中</span>}
              </button>
            ))}
          </div>

          <div className="capability-section">
            <div className="section-title">
              <label htmlFor="generation-profile">生成能力</label>
              <small>系统已按生成方式自动推荐；有特殊需要时再切换</small>
            </div>
            <select
              id="generation-profile"
              value={profileVersionId}
              onChange={(event) => setProfileVersionId(event.target.value)}
              disabled={!eligibleProfiles.length}
            >
              {!eligibleProfiles.length && <option value="">没有适合当前方式的可用能力</option>}
              {eligibleProfiles.map((profile) => (
                <option key={profile.version_id} value={profile.version_id}>
                  {creatorProfileTitle(profile.title)} · 第 {profile.version_no ?? "?"} 版
                </option>
              ))}
            </select>
            <ProfileExecutionDetailButton profileVersionId={profileVersionId} />
            {!eligibleProfiles.length && <p className="review-guidance" role="status">当前生成方式还没有可用能力。请先完成模型检查并发布对应能力，草稿或已停用能力不会被用于正式生成。</p>}
          </div>

          <div className="stage-nav-bar">
            <button type="button" className="secondary" onClick={onOpenProfiles}>
              查看全部模型与能力
            </button>
            <button type="button" className="primary-action" onClick={() => selectStep("inputs")} disabled={!selectedShotId || !profileVersionId}>
              下一步：输入与控制 →
            </button>
          </div>
        </section>
      )}

      {/* STAGE 2: INPUTS */}
      {currentStep === "inputs" && (
        <section className="stage-container" aria-labelledby="stage-inputs-title">
          <div className="stage-header">
            <div>
              <h3 id="stage-inputs-title">2. 输入与创作意图</h3>
              <p>完善镜头描述和参考画面；运镜、表演绑定等精细控制按需展开</p>
            </div>
            <span className="status-pill neutral">步骤 2 / 5</span>
          </div>

          <div className="input-slot-row">
            <div className={`media-slot${draftAnchor ? " filled" : ""}`}>
              {extractedThumbnail ? (
                <img src={extractedThumbnail} alt="提取帧海报" width="220" height="124" decoding="async" />
              ) : (
                <span aria-hidden="true">+</span>
              )}
              <strong>{draftAnchor ? `${draftRoleLabel}已填入草稿` : mode === "R2V" ? "选择参考图片" : "选择视频帧"}</strong>
            </div>

            <div className="prompt-field">
              <label htmlFor="generation-prompt">镜头描述</label>
              <textarea
                id="generation-prompt"
                value={promptText}
                onChange={(event) => setPromptText(event.target.value)}
                placeholder="描述主体动作、镜头运动、节奏与环境变化…"
              />
              <button type="button" className="secondary" style={{ marginTop: 6 }} onClick={prefillPromptFromIntent} disabled={!selectedShot}>
                从镜头信息生成描述
              </button>
            </div>
          </div>

          {videos.length > 0 && (
            <div className="frame-extract-section">
              <label htmlFor="frame-source-video">从已注册视频取帧</label>
              <select
                id="frame-source-video"
                value={sourceVideoId}
                onChange={(event) => {
                  setSourceVideoId(event.target.value);
                  setDraftAnchor(null);
                }}
              >
                {videos.map((item) => (
                  <option value={item.media_version_id} key={item.media_version_id}>
                    {optionLabel(MEDIA_STAGE_LABELS, item.stage)} · 版本 {item.media_version_id.slice(0, 12)}
                  </option>
                ))}
              </select>
              <div className="frame-actions" style={{ marginTop: 8, display: "flex", gap: 8 }}>
                <button type="button" onClick={() => anchorMutation.mutate("FIRST_FRAME")} disabled={anchorMutation.isPending}>
                  用作首帧
                </button>
                <input
                  id="frame-current-time"
                  type="number"
                  min="0"
                  step="0.001"
                  value={currentTimeSeconds}
                  onChange={(event) => setCurrentTimeSeconds(event.target.value)}
                  placeholder="当前秒"
                  aria-label="当前关键帧时间（秒）"
                  style={{ width: 90 }}
                />
                <button type="button" onClick={() => anchorMutation.mutate("CURRENT_FRAME")} disabled={anchorMutation.isPending}>
                  用作当前帧
                </button>
                <button type="button" onClick={() => anchorMutation.mutate("LAST_FRAME")} disabled={anchorMutation.isPending}>
                  用作末帧
                </button>
              </div>
              {draftAnchor && (
                <button
                  type="button"
                  className="secondary"
                  style={{ marginTop: 8 }}
                  onClick={() => keyframeMutation.mutate()}
                  disabled={!selectedShotId || keyframeMutation.isPending}
                >
                  创建关键帧候选并进入人工审核
                </button>
              )}
            </div>
          )}

          {(mode === "I2V" || mode === "R2V") && projectId && (
            <div className="generation-source-image">
              <MediaPicker
                projectId={projectId}
                value={sourceImageId}
                onChange={(mediaVersionId) => {
                  setSourceImageId(mediaVersionId);
                  setDraftAnchor(null);
                }}
                mediaKind="IMAGE"
                allowUpload
                label={mode === "I2V" ? "选择项目首帧图片" : "选择项目参考图片"}
              />
              {mode === "I2V" && initialSourceImageId && sourceImageId === initialSourceImageId && !approvedKeyframeIds.includes(initialSourceImageId) && (
                <p className="review-guidance" role="status">
                  {approvedKeyframeId
                    ? `已带入导演台 Frame Bridge 的锁定首帧，并匹配本镜头已批准 KEYFRAME ${approvedKeyframeId.slice(0, 12)}；I2V 将使用已批准版本。`
                    : "已带入导演台 Frame Bridge 的锁定首帧；请先创建关键帧候选并完成人工批准，批准前不会提交 I2V。"}
                </p>
              )}
              {sourceImageId && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => keyframeMutation.mutate()}
                  disabled={!selectedShotId || keyframeMutation.isPending}
                >
                  {keyframeMutation.isPending ? "正在创建关键帧候选…" : "创建关键帧候选并进入人工审核"}
                </button>
              )}
              {keyframeMutation.error && <p className="inline-error" role="alert">{keyframeMutation.error.message}</p>}
            </div>
          )}

          <details className="generation-advanced-controls">
            <summary><span>高级导演控制</span><small>运镜时序、表演绑定、参考输入、遮罩与生产档位</small></summary>
            <GenerationControlPanel
              projectId={projectId ?? undefined}
              timedDirections={timedDirectionsText}
              performanceBindings={performanceBindingsText}
              referenceBindings={referenceBindingsText}
              motionMasks={motionMasksText}
              onTimedDirectionsChange={setTimedDirectionsText}
              onPerformanceBindingsChange={setPerformanceBindingsText}
              onReferenceBindingsChange={setReferenceBindingsText}
              onMotionMasksChange={setMotionMasksText}
              tier={tier}
              onTierChange={setTier}
              profileVersionId={selected?.version_id}
              profileLabel={selected?.title ?? selected?.code}
              aspectRatio={aspectRatio}
              allowedTierCodes={allowedTierCodes}
            />

            {approvedKeyframeId && profileVersionId && (
              <MotionControlPanel projectId={projectId ?? undefined} sourceMediaVersionId={approvedKeyframeId} profileVersionId={profileVersionId} />
            )}
          </details>

          <div className="stage-nav-bar">
            <button type="button" className="secondary" onClick={() => selectStep("setup")}>
              ← 上一步：方式与镜头
            </button>
            <button type="button" className="primary-action" onClick={() => selectStep("preflight")} disabled={!promptText.trim()}>
              下一步：检查并启动 →
            </button>
          </div>
        </section>
      )}

      {/* STAGE 3: PREFLIGHT */}
      {currentStep === "preflight" && (
        <section className="stage-container" aria-labelledby="stage-preflight-title">
          <div className="stage-header">
            <div>
              <h3 id="stage-preflight-title">3. 检查资源并启动生成</h3>
              <p>系统会在后台核对能力、输入与资源上限，实际占用生成资源前再请你确认</p>
            </div>
            <span className="status-pill neutral">步骤 3 / 5</span>
          </div>

          <div className="generation-submit-panel">
            <div className="generation-submit-fields">
              <label htmlFor="generation-take-count">
                候选数量
                <select
                  id="generation-take-count"
                  value={takeCountText}
                  onChange={(event) => setTakeCountText(event.target.value)}
                >
                  {[1, 2, 3, 4, 6, 8].map((count) => <option key={count} value={String(count)}>{count} 个候选</option>)}
                </select>
              </label>
              {mode === "I2V" && (
                <label htmlFor="generation-keyframe">
                  已批准关键帧
                  <select
                    id="generation-keyframe"
                    value={approvedKeyframeId}
                    onChange={(event) => setApprovedKeyframeId(event.target.value)}
                    disabled={approvedKeyframeIds.length === 0}
                  >
                    <option value="">{approvedKeyframeIds.length === 0 ? "暂无已批准关键帧" : "请选择"}</option>
                    {approvedKeyframeIds.map((mediaVersionId, index) => (
                      <option key={mediaVersionId} value={mediaVersionId}>
                        已批准关键帧 {index + 1}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>

            <details className="generation-expert-inputs">
              <summary>专家：复现参数</summary>
              <label htmlFor="generation-seed">
                固定随机种子
                <input id="generation-seed" type="number" step="1" value={seedText} onChange={(event) => { setSeedText(event.target.value); setSeedTouched(true); }} />
                <small>系统已按镜头自动生成稳定值；仅在需要精确复现实验时调整。</small>
              </label>
            </details>

            <div className="generation-batch-estimate" aria-label="批量资源估算">
              <strong>提交前资源估算</strong>
              <span>生成能力：{selected ? creatorProfileTitle(selected.title) : "未选择"}</span>
              <span>本机运行环境：{h3?.status === "READY" ? "可用" : h3?.status ?? "未检查"}</span>
              <span>资源信息：{resourceStatus === "DECLARED" ? "完整" : resourceStatus === "PARTIAL" ? "部分可估算" : "由运行时检查"}</span>
              <span>预计时长：{formatEstimate(visiblePerTake.duration_seconds, takeCount, " 秒")}</span>
              <span>预计显存：{formatEstimate(visiblePerTake.vram_bytes, takeCount, " bytes")}</span>
              <span>预计磁盘：{formatEstimate(visiblePerTake.disk_bytes, takeCount, " bytes")}</span>
            </div>

            <div className="generation-submit-actions">
              <button
                type="button"
                className="primary-action"
                disabled={!runnable || preflightMutation.isPending || Boolean(takeBatchReport)}
                onClick={() => prepared ? setConfirmOpen(true) : preflightMutation.mutate()}
              >
                {preflightMutation.isPending ? "正在后台检查…" : takeBatchReport ? "生成任务已启动" : prepared ? "查看检查结果并启动" : "检查并准备生成"}
              </button>
            </div>

            {prepared && !takeBatchReport && (
              <p className="frame-feedback success" role="status">
                <strong>检查已通过，尚未占用生成资源。</strong> 点击上方按钮可再次查看并启动。
              </p>
            )}
            {takeBatchReport && (
              <section
                className={`take-batch-report${failedTakeIndexes.length ? " has-failures" : ""}`}
                aria-label="候选生成结果"
                role="status"
              >
                <div className="take-batch-report-heading">
                  <div>
                    <strong>
                      {failedTakeIndexes.length
                        ? `已创建 ${submittedCount}/${takeBatchReport.requested} 个候选`
                        : `${submittedCount} 个候选全部创建成功`}
                    </strong>
                    <span>
                      {failedTakeIndexes.length
                        ? `失败 ${failedTakeIndexes.length} 个；成功项不会重复提交。`
                        : "每个候选都有独立的生成记录，不会相互覆盖。"}
                    </span>
                  </div>
                  {failedTakeIndexes.length > 0 && (
                    <button
                      type="button"
                      className="primary-action"
                      disabled={submitMutation.isPending}
                      onClick={() => submitMutation.mutate(failedTakeIndexes)}
                    >
                      {submitMutation.isPending ? "正在重试失败候选…" : `仅重试失败的 ${failedTakeIndexes.length} 个候选`}
                    </button>
                  )}
                </div>
                <ol className="take-batch-results">
                  {takeBatchReport.results.map((item) => (
                    <li key={item.index} className={item.status === "SUCCEEDED" ? "succeeded" : "failed"}>
                      <span className="status-pill">候选 {item.takeNumber}</span>
                      {item.status === "SUCCEEDED" && item.job ? (
                        <>
                          <span>后台任务已创建</span>
                          {onOpenJobs ? <button type="button" className="secondary" onClick={() => onOpenJobs(item.job!.id)}>查看进度</button> : null}
                        </>
                      ) : (
                        <span className="inline-error">失败：{item.error ?? "未知错误"}</span>
                      )}
                    </li>
                  ))}
                </ol>
                {submitted && onOpenReviews ? (
                  <button type="button" className="secondary" onClick={() => onOpenReviews()}>前往审核工作区</button>
                ) : null}
              </section>
            )}
            {(preflightMutation.error || submitMutation.error) && (
              <p className="inline-error" role="alert">
                {(preflightMutation.error ?? submitMutation.error)?.message}
              </p>
            )}
          </div>

          <div className="stage-nav-bar">
            <button type="button" className="secondary" onClick={() => selectStep("inputs")}>
              ← 上一步：输入与控制
            </button>
            <button type="button" className="secondary" onClick={() => selectStep("variants")}>
              下一步：创作分支 →
            </button>
          </div>
        </section>
      )}

      {/* STAGE 4: VARIANTS */}
      {currentStep === "variants" && (
        <section className="stage-container" aria-labelledby="stage-variants-title">
          <div className="stage-header">
            <div>
              <h3 id="stage-variants-title">4. 保留原结果，继续尝试</h3>
              <p>从已提交结果派生新的创作方向，原候选和历史不会被覆盖</p>
            </div>
            <span className="status-pill neutral">步骤 4 / 5</span>
          </div>

          <div className="variant-strip">
            <strong>创作分支：</strong>
            <span className="chip active">基础生成</span>
            <button type="button" className="chip" disabled={!effectiveVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("RESAMPLE_NEW_SEED")}>
              相同输入，再试一次
            </button>
            <button type="button" className="chip" disabled={!effectiveVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("EXACT_REPLAY")}>
              精确重现这次结果
            </button>
            <button type="button" className="chip" disabled={!effectiveVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("RESUBMIT_PROVIDER_RANDOM")}>
              由模型重新随机
            </button>
            <button type="button" className="chip" disabled={!effectiveVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("PROMPT_BRANCH")}>
              调整镜头描述
            </button>
            <button type="button" className="chip" disabled={!effectiveVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("SOURCE_IMAGE_BRANCH")}>
              更换参考图
            </button>
          </div>

          {branchPlan && (
            <div className="branch-plan-panel" style={{ padding: 12, background: "var(--surface-subtle)", borderRadius: 6 }}>
              <strong>新分支已检查，可以提交。</strong>
              <button type="button"
                className="secondary"
                style={{ marginLeft: 12 }}
                onClick={() => branchSubmitMutation.mutate()}
                disabled={branchSubmitMutation.isPending}
              >
                {branchSubmitMutation.isPending ? "提交中…" : "确认创建新候选"}
              </button>
            </div>
          )}

          {effectiveVariantId && (
            <details className="seed-batch-panel generation-expert-inputs">
              <summary>专家：批量随机种子实验</summary>
              <div>
              <div className="section-title">
                <span>自定义可复现随机种子</span>
              </div>
              <label>候选数量<select value={seedBatchCount} onChange={(event) => { const count = Number(event.target.value); setSeedBatchCount(count); setSeedBatchText(Array.from({ length: count }, () => Math.floor(Math.random() * 900_000) + 100_000).join(",")); }}>{[2, 4, 6, 8].map((count) => <option key={count} value={count}>{count} 个可复现候选</option>)}</select></label>
              <button type="button" className="secondary" onClick={() => setSeedBatchText(Array.from({ length: seedBatchCount }, () => Math.floor(Math.random() * 900_000) + 100_000).join(","))}>换一组随机 Seed</button>
              <details><summary>查看本组复现参数</summary><code>{seedBatchText.split(",").join(" · ")}</code></details>
              <button type="button" className="secondary" onClick={() => seedBatchMutation.mutate()} disabled={seedBatchMutation.isPending} style={{ marginLeft: 8 }}>
                生成批量实验矩阵
              </button>
              {seedBatchPlan && <p className="frame-feedback success">{seedBatchPlan.count} 个分支计划已生成</p>}
              </div>
            </details>
          )}

          {restoredVariantId && !submittedVariantId && <p className="review-success" role="status">已恢复上次保存的生成上下文；可以继续创建分支。</p>}
          {restoreError && <p className="inline-error" role="alert">恢复已持久化生成上下文失败：{restoreError}</p>}
          <GenerationExperimentPanel intentId={effectiveIntentId} />

          <div className="stage-nav-bar">
            <button type="button" className="secondary" onClick={() => selectStep("preflight")}>
              ← 上一步：预检与确认
            </button>
            <button type="button" className="primary-action" onClick={() => selectStep("review")}>
              下一步：候选审核 →
            </button>
          </div>
        </section>
      )}

      {/* STAGE 5: REVIEW */}
      {currentStep === "review" && (
        <section className="stage-container" aria-labelledby="stage-review-title">
          <div className="stage-header">
            <div>
              <h3 id="stage-review-title">5. 候选对比与人工审核</h3>
              <p>查看真实代理视频候选，选择 winner 与人工批准严格分离</p>
            </div>
            <span className="status-pill neutral">步骤 5 / 5</span>
          </div>

          {g6Readiness && (
            <div className="panel gate-readiness">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">退出就绪门禁</p>
                  <h4>真实生成闭环门禁</h4>
                </div>
                <span className={`status-pill${g6Readiness.status === "PASS" ? "" : " neutral"}`}>{g6Readiness.status}</span>
              </div>
              <ol className="gate-checks">
                {g6Readiness.checks.map((check) => (
                  <li className={check.passed ? "passed" : "blocked"} key={check.code}>
                    <GateStatusIcon passed={check.passed} />
                    <strong>{readinessLabels[check.code] ?? check.code}</strong>
                  </li>
                ))}
              </ol>
            </div>
          )}

          <div className="candidate-shelf">
            <div className="candidate-shelf-heading">
              <h4>视频候选（{videos.length} 个待处理）</h4>
            </div>
            {videos.length ? (
              <div className="candidate-grid">
                {videos.slice(0, 8).map((item, index) => (
                  <article className="candidate-card" key={item.media_version_id}>
                    <div className="candidate-poster">
                      <img
                        src={`/api/v1/media-versions/${encodeURIComponent(item.media_version_id)}/thumbnail?size=small&frame=poster`}
                        alt={`代理候选 ${index + 1}`}
                        loading="lazy"
                        decoding="async"
                      />
                      <span>候选 {index + 1}</span>
                    </div>
                    <div className="candidate-card-body">
                      <strong>视频候选 {index + 1}</strong>
                      <div className="candidate-state">
                        <span className={item.decision ? "status-pill" : "status-pill neutral"}>{item.decision ?? "待人工审核"}</span>
                      </div>
                      <button type="button" className="secondary" onClick={() => onOpenReviews?.(item.media_version_id)}>
                        打开审核工作区
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="empty-state">当前项目还没有真实视频候选。</p>
            )}
          </div>

          <div className="stage-nav-bar">
            <button type="button" className="secondary" onClick={() => selectStep("variants")}>
              ← 上一步：创作分支
            </button>
            <button type="button" className="secondary" onClick={() => onOpenReviews?.()}>
              进入整集评审工作区
            </button>
          </div>
        </section>
      )}

      {/* CONFIRMATION DIALOG */}
      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="确认启动生成"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <p>
            即将为镜头 <strong>{String(selectedShot?.code ?? selectedShotId)}</strong> 生成 <strong>{takeCount}</strong> 个候选。
          </p>
          <div style={{ background: "var(--surface-subtle, #14171f)", padding: 12, borderRadius: 6, fontSize: 13 }}>
            <div><strong>生成方式：</strong>{mode}</div>
            <div><strong>生成能力：</strong>{selected ? creatorProfileTitle(selected.title) : "—"}</div>
            <div><strong>预计磁盘：</strong>{formatEstimate(visiblePerTake.disk_bytes, takeCount, " bytes")}</div>
          </div>
          <p className="muted" style={{ fontSize: 12 }}>
            确认后任务会在本机后台执行；关闭页面不会中断，已有候选和历史记录不会被覆盖。
          </p>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button type="button" className="secondary" onClick={() => setConfirmOpen(false)}>
              取消
            </button>
            <button
              type="button"
              className="primary-action"
              disabled={submitMutation.isPending}
              onClick={() => submitMutation.mutate(Array.from({ length: takeCount }, (_, index) => index))}
            >
              {submitMutation.isPending ? "正在启动…" : "确认启动生成"}
            </button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}

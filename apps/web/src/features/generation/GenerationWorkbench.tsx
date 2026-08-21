import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  createFrameAnchor,
  createGenerationIntent,
  createKeyframeCandidate,
  createPrompt,
  deriveGenerationVariantPlan,
  deriveGenerationVariantSeedBatch,
  planGenerationVariant,
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
import "./generation-workbench.css";

type Shot = Record<string, unknown>;
export type GenerationStep = "setup" | "inputs" | "preflight" | "variants" | "review";

export interface GenerationWorkbenchProps {
  projectId: string | null;
  profiles: Profile[];
  candidates: ReviewInboxItem[];
  h3?: { status: string; release_root?: string; missing_sidecars?: string[] };
  g6Readiness?: G6Readiness;
  i2vProbePlan?: I2VProbePlan;
  shots: Shot[];
  selectedShotId: string | null;
  onSelectShot: (id: string) => void;
  onOpenProfiles: () => void;
  onOpenReviews?: (mediaVersionId?: string) => void;
  onSubmitted?: () => void;
  aspectRatio?: "9:16" | "16:9";
  activeStep?: GenerationStep;
  onStepChange?: (step: GenerationStep) => void;
}

export const generationSteps: Array<{ id: GenerationStep; label: string; detail: string }> = [
  { id: "setup", label: "方式与镜头", detail: "绑定生产上下文" },
  { id: "inputs", label: "输入与控制", detail: "冻结创作参数" },
  { id: "preflight", label: "预检与确认", detail: "先只读，再提交" },
  { id: "variants", label: "Variant 分支", detail: "显式派生，不覆盖" },
  { id: "review", label: "人工审核", detail: "选择与批准分离" },
];

const modes = [
  { id: "T2I", title: "文字生成图片", detail: "从镜头描述创建关键帧候选", icon: "文 / 图" },
  { id: "I2V", title: "图片生成视频", detail: "用已注册首帧生成代理 take", icon: "图 / 影" },
  { id: "T2V", title: "文字生成视频", detail: "不绑定首帧，探索动作和运镜", icon: "文 / 影" },
  { id: "R2V", title: "参考图生成视频", detail: "保持角色或风格参考的一致性", icon: "参 / 影" },
] as const;

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
  onSelectShot,
  onOpenProfiles,
  onOpenReviews,
  onSubmitted,
  aspectRatio,
  activeStep,
  onStepChange,
}: GenerationWorkbenchProps) {
  const [localStep, setLocalStep] = useState<GenerationStep>("setup");
  const currentStep = activeStep ?? localStep;
  const selectStep = (step: GenerationStep) => (onStepChange ?? setLocalStep)(step);

  const [mode, setMode] = useState<(typeof modes)[number]["id"]>("I2V");
  const videos = useMemo(() => candidates.filter((item) => item.media_kind === "VIDEO"), [candidates]);
  const approvedKeyframeIds = useMemo(() => {
    const ids = candidates
      .filter((item) => item.media_kind === "IMAGE" && item.stage === "KEYFRAME" && item.decision === "APPROVED" && !item.is_stale)
      .map((item) => item.media_version_id);
    const gateApproved = i2vProbePlan?.snapshot.approved_keyframe?.media_version_id;
    if (gateApproved) ids.push(gateApproved);
    return [...new Set(ids)];
  }, [candidates, i2vProbePlan]);

  const eligibleProfiles = useMemo(() => profiles.filter((profile) => profile.capability === mode), [mode, profiles]);
  const [profileVersionId, setProfileVersionId] = useState("");
  const [sourceVideoId, setSourceVideoId] = useState("");
  const [currentTimeSeconds, setCurrentTimeSeconds] = useState("0");
  const [draftAnchor, setDraftAnchor] = useState<FrameAnchor | null>(null);
  const [frameAction, setFrameAction] = useState<FrameAction | null>(null);
  const [promptText, setPromptText] = useState("");
  const [timedDirectionsText, setTimedDirectionsText] = useState("[]");
  const [performanceBindingsText, setPerformanceBindingsText] = useState("[]");
  const [referenceBindingsText, setReferenceBindingsText] = useState("[]");
  const [motionMasksText, setMotionMasksText] = useState("[]");
  const [seedText, setSeedText] = useState("42");
  const [takeCountText, setTakeCountText] = useState("1");
  const [tier, setTier] = useState("");
  const [approvedKeyframeId, setApprovedKeyframeId] = useState("");
  const [prepared, setPrepared] = useState<{
    draft: GenerationVariantDraft;
    plan: GenerationVariantPlan;
    idempotencyKey: string;
  } | null>(null);
  const [preflightResourceEstimate, setPreflightResourceEstimate] = useState<GenerationResourceEstimate | null>(null);
  const [submitted, setSubmitted] = useState<Job | null>(null);
  const [submittedCount, setSubmittedCount] = useState(0);
  const [submittedVariantId, setSubmittedVariantId] = useState<string | null>(null);
  const [seedBatchText, setSeedBatchText] = useState("43,44,45,46");
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
    setPrepared(null);
    setPreflightResourceEstimate(null);
    setSubmitted(null);
    setSubmittedCount(0);
    setSubmittedVariantId(null);
    setSeedBatchPlan(null);
    setBranchPlan(null);
    setConfirmOpen(false);
  }, [mode, profileVersionId, selectedShotId, promptText, timedDirectionsText, performanceBindingsText, referenceBindingsText, motionMasksText, seedText, approvedKeyframeId, takeCountText, tier]);

  const selected = eligibleProfiles.find((profile) => profile.version_id === profileVersionId);
  const resourcePolicy = profileResourcePolicy(selected);
  const selectedShot = shots.find((shot) => String(shot.id) === selectedShotId);
  const revision = selectedShot?.current_revision && typeof selectedShot.current_revision === "object" ? (selectedShot.current_revision as Record<string, unknown>) : {};
  const cameraPlan = revision.camera_plan && typeof revision.camera_plan === "object" ? (revision.camera_plan as CameraPlan) : null;
  const requiresCamera = mode !== "T2I";
  const cameraReady = !requiresCamera || Boolean(cameraPlan && cameraPlan.mode !== "UNSUPPORTED" && cameraPlan.profile_version_id === profileVersionId);
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
      if (!draftAnchor || !selectedShotId) throw new Error("必须先选择镜头并提取真实帧");
      return createKeyframeCandidate(draftAnchor.extracted_media_version_id, selectedShotId);
    },
    onSuccess: ({ media }) => onOpenReviews?.(media.id),
  });

  const preflightMutation = useMutation({
    mutationFn: async () => {
      if (!projectId || !selectedShotId || !selected) throw new Error("必须先选择项目、镜头和已发布 Profile");
      if (!promptText.trim()) throw new Error("Prompt 不能为空");
      if (!Number.isInteger(seed)) throw new Error("Seed 必须是整数");
      if (!Number.isInteger(takeCount) || takeCount < 1 || takeCount > 8) throw new Error("代理 take 数量必须是 1—8");
      if (requiresCamera && !cameraReady) throw new Error("结构化运镜必须由当前同一 Profile 裁决为可执行");
      if (mode === "I2V" && !approvedKeyframeId) throw new Error("I2V 代理必须选择当前已批准关键帧");

      const intent = await createGenerationIntent({
        project_id: projectId,
        owner_type: "SHOT",
        owner_id: selectedShotId,
        purpose: mode === "I2V" ? "I2V_PROXY" : mode,
        creative_goal: promptText.trim(),
      });
      const prompt = await createPrompt({
        project_id: projectId,
        owner_type: "SHOT",
        owner_id: selectedShotId,
        purpose: mode,
        title: `${String(selectedShot?.code ?? selectedShotId)} ${mode}`,
        content_text: promptText.trim(),
        structured: { camera_plan: cameraPlan },
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
          ...(cameraPlan ? { camera_plan: cameraPlan } : {}),
          timed_directions: parseControlList(timedDirectionsText, "TimedDirection"),
          performance_bindings: parseControlList(performanceBindingsText, "PerformanceBinding"),
          motion_masks: parseControlList(motionMasksText, "MotionMask"),
          ...(tier ? { tier } : {}),
        },
        seed_policy: "EXPLICIT",
        explicit_seed: seed,
        bindings,
      };
      const planned = await planGenerationVariant(draft);
      return { draft, plan: planned.plan, idempotencyKey: crypto.randomUUID() };
    },
    onSuccess: (result) => {
      setPreflightResourceEstimate(result.plan.resource_estimate ?? null);
      setPrepared(result);
    },
  });

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!prepared) throw new Error("必须先完成当前输入的资源预检");
      let last: Awaited<ReturnType<typeof submitGenerationVariant>> | null = null;
      for (let index = 0; index < takeCount; index += 1) {
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
        const plan = index === 0 ? prepared.plan : (await planGenerationVariant(draft)).plan;
        last = await submitGenerationVariant({
          ...draft,
          plan_hash: plan.plan_hash,
          idempotency_key: index === 0 ? prepared.idempotencyKey : crypto.randomUUID(),
        });
      }
      if (!last) throw new Error("没有可提交的代理 take");
      return { ...last, takeCount };
    },
    onSuccess: ({ job, variant, takeCount: committedTakeCount }) => {
      setSubmitted(job);
      setSubmittedVariantId(variant.id);
      setSubmittedCount(committedTakeCount);
      setConfirmOpen(false);
      onSubmitted?.();
    },
  });

  const seedBatchMutation = useMutation({
    mutationFn: async () => {
      if (!submittedVariantId) throw new Error("请先提交一个生成 Variant");
      const seeds = seedBatchText
        .split(",")
        .map((value) => Number(value.trim()))
        .filter((value) => Number.isInteger(value));
      if (seeds.length === 0 || seeds.length > 24 || new Set(seeds).size !== seeds.length) {
        throw new Error("批量 seed 必须是 1—24 个不重复整数");
      }
      return deriveGenerationVariantSeedBatch(submittedVariantId, { seeds, branch_reason: "UI_SEED_BATCH_EXPERIMENT" });
    },
    onSuccess: ({ batch }) => setSeedBatchPlan(batch),
  });

  const branchMutation = useMutation({
    mutationFn: async (operation: "RESAMPLE_NEW_SEED" | "RESUBMIT_PROVIDER_RANDOM" | "EXACT_REPLAY" | "PROMPT_BRANCH" | "SOURCE_IMAGE_BRANCH") => {
      if (!submittedVariantId) throw new Error("请先提交一个基础 Variant");
      if (operation === "RESAMPLE_NEW_SEED") {
        return deriveGenerationVariantPlan(submittedVariantId, { operation, explicit_seed: seed + 1, branch_reason: "UI_RESAMPLE_NEW_SEED" });
      }
      if (operation === "RESUBMIT_PROVIDER_RANDOM") {
        return deriveGenerationVariantPlan(submittedVariantId, { operation, branch_reason: "UI_RESUBMIT_PROVIDER_RANDOM" });
      }
      if (operation === "EXACT_REPLAY") {
        return deriveGenerationVariantPlan(submittedVariantId, { operation, branch_reason: "UI_EXACT_REPLAY" });
      }
      if (operation === "SOURCE_IMAGE_BRANCH") {
        if (!approvedKeyframeId) throw new Error("换图分支需要选择已批准关键帧");
        return deriveGenerationVariantPlan(submittedVariantId, {
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
        structured: { branch_from_variant_id: submittedVariantId },
      });
      return deriveGenerationVariantPlan(submittedVariantId, {
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
              <p>锁定当前镜头生产上下文与模型能力 Profile</p>
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
                  {String(shot.code ?? shot.id)} · {String(shot.status ?? "UNKNOWN")}
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
              <span>本地能力 Profile</span>
              <small>由已发布的不可变 Profile 决定</small>
            </div>
            <select
              id="generation-profile"
              value={profileVersionId}
              onChange={(event) => setProfileVersionId(event.target.value)}
              disabled={!eligibleProfiles.length}
            >
              {!eligibleProfiles.length && <option value="">没有匹配的本地 Profile</option>}
              {eligibleProfiles.map((profile) => (
                <option key={profile.version_id} value={profile.version_id}>
                  {profile.title} · {profile.status}
                </option>
              ))}
            </select>
          </div>

          <div className="stage-nav-bar">
            <button type="button" className="secondary" onClick={onOpenProfiles}>
              查看全部模型与能力
            </button>
            <button type="button" className="primary-action" onClick={() => selectStep("inputs")} disabled={!selectedShotId}>
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
              <p>输入 Prompt、提取参考视频帧、配置运镜与高级控制参数</p>
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
              <label htmlFor="generation-prompt">镜头 Prompt</label>
              <textarea
                id="generation-prompt"
                value={promptText}
                onChange={(event) => setPromptText(event.target.value)}
                placeholder="描述主体动作、镜头运动、节奏与环境变化…"
              />
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
                    {item.stage} · {item.media_version_id.slice(0, 12)}
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

          <GenerationControlPanel
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
          />

          {approvedKeyframeId && profileVersionId && (
            <MotionControlPanel sourceMediaVersionId={approvedKeyframeId} profileVersionId={profileVersionId} />
          )}

          <div className="stage-nav-bar">
            <button type="button" className="secondary" onClick={() => selectStep("setup")}>
              ← 上一步：方式与镜头
            </button>
            <button type="button" className="primary-action" onClick={() => selectStep("preflight")} disabled={!promptText.trim()}>
              下一步：预检与确认 →
            </button>
          </div>
        </section>
      )}

      {/* STAGE 3: PREFLIGHT */}
      {currentStep === "preflight" && (
        <section className="stage-container" aria-labelledby="stage-preflight-title">
          <div className="stage-header">
            <div>
              <h3 id="stage-preflight-title">3. 资源预检与显式确认</h3>
              <p>在创建 Job 前完成不可变计划哈希计算与显式确认</p>
            </div>
            <span className="status-pill neutral">步骤 3 / 5</span>
          </div>

          <div className="generation-submit-panel">
            <div className="generation-submit-fields">
              <label htmlFor="generation-seed">
                首个显式 Seed
                <input id="generation-seed" type="number" step="1" value={seedText} onChange={(event) => setSeedText(event.target.value)} />
              </label>
              <label htmlFor="generation-take-count">
                代理 take 数量
                <input
                  id="generation-take-count"
                  type="number"
                  min="1"
                  max="8"
                  step="1"
                  value={takeCountText}
                  onChange={(event) => setTakeCountText(event.target.value)}
                />
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
                    {approvedKeyframeIds.map((mediaVersionId) => (
                      <option key={mediaVersionId} value={mediaVersionId}>
                        APPROVED KEYFRAME · {mediaVersionId.slice(0, 12)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>

            <div className="generation-batch-estimate" aria-label="批量资源估算">
              <strong>提交前资源估算</strong>
              <span>Profile：{selected?.code ?? "未选择"}</span>
              <span>本地 Runtime：{h3?.status ?? "未读取"}</span>
              <span>资源声明：{resourceStatus}</span>
              <span>预计时长：{formatEstimate(visiblePerTake.duration_seconds, takeCount, " 秒")}</span>
              <span>预计显存：{formatEstimate(visiblePerTake.vram_bytes, takeCount, " bytes")}</span>
              <span>预计磁盘：{formatEstimate(visiblePerTake.disk_bytes, takeCount, " bytes")}</span>
            </div>

            <div className="generation-submit-actions" style={{ display: "flex", gap: 12 }}>
              <button
                type="button"
                className="secondary"
                disabled={!runnable || preflightMutation.isPending}
                onClick={() => preflightMutation.mutate()}
              >
                {preflightMutation.isPending ? "正在建立意图并预检…" : "建立意图并执行只读预检"}
              </button>

              <button
                type="button"
                className="primary-action"
                disabled={!prepared || submitMutation.isPending || Boolean(submitted)}
                onClick={() => setConfirmOpen(true)}
              >
                {submitMutation.isPending ? "提交中…" : "确认创建 Variant 与 Job"}
              </button>
            </div>

            {prepared && !submitted && (
              <p className="frame-feedback success" role="status">
                <strong>预检 READY，尚未创建 Job。</strong> 计划哈希 <code>{prepared.plan.plan_hash.slice(0, 16)}</code>
              </p>
            )}
            {submitted && (
              <p className="frame-feedback success" role="status">
                <strong>{submittedCount > 1 ? `${submittedCount} 个代理 take 已创建` : "真实任务已持久化"}</strong> Job{" "}
                <code>{submitted.id}</code>
              </p>
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
              下一步：Variant 分支 →
            </button>
          </div>
        </section>
      )}

      {/* STAGE 4: VARIANTS */}
      {currentStep === "variants" && (
        <section className="stage-container" aria-labelledby="stage-variants-title">
          <div className="stage-header">
            <div>
              <h3 id="stage-variants-title">4. Variant 分支与批量实验</h3>
              <p>基于已提交 Variant 进行可复现分支衍生与 Seed 批量实验</p>
            </div>
            <span className="status-pill neutral">步骤 4 / 5</span>
          </div>

          <div className="variant-strip">
            <strong>创作分支：</strong>
            <span className="chip active">基础生成</span>
            <button type="button" className="chip" disabled={!submittedVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("RESAMPLE_NEW_SEED")}>
              同图同词 · 新 seed
            </button>
            <button type="button" className="chip" disabled={!submittedVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("EXACT_REPLAY")}>
              固定 seed · 精确重放
            </button>
            <button type="button" className="chip" disabled={!submittedVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("RESUBMIT_PROVIDER_RANDOM")}>
              Provider random
            </button>
            <button type="button" className="chip" disabled={!submittedVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("PROMPT_BRANCH")}>
              改 Prompt
            </button>
            <button type="button" className="chip" disabled={!submittedVariantId || branchMutation.isPending} onClick={() => branchMutation.mutate("SOURCE_IMAGE_BRANCH")}>
              换图
            </button>
          </div>

          {branchPlan && (
            <div className="branch-plan-panel" style={{ padding: 12, background: "var(--surface-subtle)", borderRadius: 6 }}>
              <strong>分支计划 READY：</strong>
              <span>计划哈希 <code>{branchPlan.plan_hash.slice(0, 16)}</code></span>
              <button type="button"
                className="secondary"
                style={{ marginLeft: 12 }}
                onClick={() => branchSubmitMutation.mutate()}
                disabled={branchSubmitMutation.isPending}
              >
                {branchSubmitMutation.isPending ? "提交中…" : "确认创建分支 Job"}
              </button>
            </div>
          )}

          {submittedVariantId && (
            <div className="seed-batch-panel" style={{ marginTop: 12 }}>
              <div className="section-title">
                <span>Seed 批量实验</span>
              </div>
              <input
                id="seed-batch-input"
                value={seedBatchText}
                onChange={(event) => setSeedBatchText(event.target.value)}
                aria-label="Seed 批量输入列表"
                placeholder="42, 43, 44..."
              />
              <button type="button" className="secondary" onClick={() => seedBatchMutation.mutate()} disabled={seedBatchMutation.isPending} style={{ marginLeft: 8 }}>
                生成批量实验矩阵
              </button>
              {seedBatchPlan && <p className="frame-feedback success">{seedBatchPlan.count} 个分支计划已生成</p>}
            </div>
          )}

          <GenerationExperimentPanel intentId={prepared?.draft.intent_id ?? null} />

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
                  <p className="eyebrow">G6 退出就绪门禁</p>
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
              <h4>真实代理候选 ({videos.length} 个待处理视频)</h4>
            </div>
            {videos.length ? (
              <div className="candidate-grid">
                {videos.slice(0, 8).map((item, index) => (
                  <article className="candidate-card" key={item.media_version_id}>
                    <div className="candidate-poster">
                      <img
                        src={`/api/v1/media-versions/${encodeURIComponent(item.media_version_id)}/thumbnail?size=small&frame=poster`}
                        alt={`代理候选 ${index + 1}`}
                        width="320"
                        height="180"
                        loading="lazy"
                        decoding="async"
                      />
                      <span>TAKE {index + 1}</span>
                    </div>
                    <div className="candidate-card-body">
                      <strong>{item.stage} · {item.media_kind}</strong>
                      <code>{item.media_version_id.slice(0, 12)}</code>
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
              ← 上一步：Variant 分支
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
        title="确认创建生成 Variant 与 Job"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <p>
            即将为镜头 <strong>{String(selectedShot?.code ?? selectedShotId)}</strong> 提交 <strong>{takeCount}</strong> 个代理 take。
          </p>
          <div style={{ background: "var(--surface-subtle, #14171f)", padding: 12, borderRadius: 6, fontSize: 13 }}>
            <div><strong>生成方式：</strong>{mode}</div>
            <div><strong>能力 Profile：</strong>{selected?.code ?? "—"}</div>
            <div><strong>起始 Seed：</strong>{seed}</div>
            <div><strong>计划哈希：</strong><code>{prepared?.plan.plan_hash.slice(0, 16) ?? "—"}</code></div>
          </div>
          <p className="muted" style={{ fontSize: 12 }}>
            确认后将持久化写入 SQLite 任务队列并由本地 Worker 异步执行；关闭浏览器不会中断任务，历史记录不会覆盖。
          </p>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
            <button type="button" className="secondary" onClick={() => setConfirmOpen(false)}>
              取消
            </button>
            <button
              type="button"
              className="primary-action"
              disabled={submitMutation.isPending}
              onClick={() => submitMutation.mutate()}
            >
              {submitMutation.isPending ? "正在提交…" : "确认提交任务"}
            </button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}

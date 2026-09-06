import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { Dialog } from "../../components/ui";
import {
  ApiRequestError, applyEpisodeProductionReplanV2, getEpisodeProductionOverviewV2,
  getEpisodeProductionReplanV2, listEpisodeProductionShotsV2,
  markEpisodeProductionShotsReadyV2,
  prepareEpisodeProductionV2, requestEpisodeProductionReplanV2,
  startEpisodeProductionRunV2, transitionEpisodeProductionRunV2,
  type EpisodeProductionBlocker, type EpisodeProductionMode,
  type EpisodeProductionRunStartCommand, type EpisodeProductionShot,
  type EpisodeProductionStage, type ProductionState,
} from "../../generated/api";
import { useProjectEventInvalidation } from "../events/useProjectEventInvalidation";
import { AssetProposalReviewPanel } from "../episode-plan-v2/AssetProposalReviewPanel";
import { syncEpisodeCharacterPacks } from "../asset-bible-v2/identityPackClient";
import "./episode-production.css";

type RunAction = "pause" | "resume" | "cancel" | "recover";
type StageCode = EpisodeProductionStage["stage_code"];
type RunDiagnosticSource = {
  pending_gate?: Record<string, unknown>;
  machine_context?: Record<string, unknown>;
};
const ATTENTION = new Set<ProductionState>(["BLOCKED", "FAILED", "NEEDS_REVIEW", "STALE"]);
const HUMAN_BLOCKING_STATES = new Set<ProductionState>(["BLOCKED", "FAILED", "NEEDS_REVIEW"]);
const AUTO_REGENERATED_BLOCKERS = new Set(["WORKING_MEDIA_STALE"]);
const REPLAN_DURATION_TOLERANCE_MS = 1000;
const STAGES: Array<{ code: StageCode; label: string; detail: string }> = [
  { code: "SHOT_PLANNING", label: "方案与设定", detail: "本集剧本、角色场景增量与分镜" },
  { code: "SHOT_IMAGE", label: "关键画面", detail: "关键帧、首尾帧与连续性基准" },
  { code: "VIDEO", label: "动态视频", detail: "逐镜视频候选与工作版本" },
  { code: "AUDIO_SUBTITLE", label: "声音字幕", detail: "对白、人声、字幕与同步" },
  { code: "COMPOSE_QC", label: "合成质检", detail: "时间线、整集渲染与检查" },
];
const STATE_LABELS: Record<ProductionState, string> = {
  EMPTY: "未开始", READY: "已完成", RUNNING: "生成中", NEEDS_REVIEW: "待确认",
  BLOCKED: "受阻", FAILED: "失败", STALE: "需更新", CANCELLED: "已取消",
};
const BLOCKER_LABELS: Record<string, string> = {
  SHOT_INTENT_INCOMPLETE: "分镜方案需要确认",
  MACHINE_QC_REQUIRES_ATTENTION: "自动质检没有通过",
  CONTINUITY_CONFLICT: "相邻镜头连续性冲突",
  CONTINUITY_STALE: "连续性基准已经变化",
  WORKING_MEDIA_STALE: "工作版本已经过期",
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function affectedShotCount(machineCheck: Record<string, unknown>) {
  const ids = new Set<string>();
  for (const field of ["missing_shots", "blocked_shots", "attention_shots", "incomplete_shots", "missing_required_references"]) {
    const values = machineCheck[field];
    if (!Array.isArray(values)) continue;
    for (const value of values) {
      const item = record(value);
      const id = textValue(item.shot_id) ?? textValue(item.shot_code) ?? textValue(value);
      if (id) ids.add(id);
    }
  }
  return ids.size;
}

function runGate(run: RunDiagnosticSource | null | undefined) {
  if (!run) return null;
  const pendingGate = record(run.pending_gate);
  const machineContext = record(run.machine_context);
  const machineCheck = record(machineContext.machine_check);
  const status = (textValue(machineCheck.status) ?? textValue(machineContext.status) ?? "").toUpperCase();
  const reason = textValue(pendingGate.reason);
  const configuredCheckpoint = reason === "CONFIGURED_CREATOR_CHECKPOINT" || reason === "BEFORE_RUN_APPROVAL";
  const isMachine = !configuredCheckpoint && (reason === "MACHINE_CHECK_REQUIRES_HITL" || ["NEEDS_HITL", "FAIL", "FAILED", "BLOCKED"].includes(status));
  const code = textValue(machineCheck.code) ?? textValue(machineCheck.reason_code) ?? (isMachine ? reason : null) ?? "AUTOMATION_HITL_REQUIRED";
  const detail = textValue(machineCheck.detail) ?? textValue(machineCheck.message) ?? (isMachine ? "机器检查发现当前生产依赖尚未满足，请处理后重新继续。" : "这是项目配置的人工确认点，确认后会继续执行下一节点。");
  return {
    isMachine,
    code,
    detail,
    affectedShotCount: affectedShotCount(machineCheck),
    nextAction: textValue(pendingGate.next_action),
  };
}

function blockerRoute(blocker: EpisodeProductionBlocker, projectId: string, episodeId: string, shotId: string) {
  if (blocker.owner_route === "SHOT_STUDIO") return routes.shotStudio(projectId, episodeId, shotId);
  if (blocker.owner_route === "REVIEW") return routes.postReview(projectId, episodeId);
  if (blocker.owner_route === "POST_AUDIO") return routes.postAudio(projectId, episodeId);
  if (blocker.owner_route === "POST_EDIT") return routes.postEdit(projectId, episodeId);
  return routes.systemJobs(projectId);
}

function errorText(error: unknown) {
  if (error instanceof ApiRequestError && error.code === "EPISODE_IDENTITY_PACK_SYNC_BLOCKED") {
    const missingAssets = Array.isArray(error.details?.missing_assets) ? error.details.missing_assets : [];
    const ambiguousAssets = Array.isArray(error.details?.ambiguous_assets) ? error.details.ambiguous_assets : [];
    const labels = (items: unknown[]) => items.map((item) => {
      if (!item || typeof item !== "object") return null;
      const record = item as Record<string, unknown>;
      const name = typeof record.asset_name === "string" ? record.asset_name : "";
      const code = typeof record.asset_code === "string" ? record.asset_code : "";
      return name || code || null;
    }).filter((value): value is string => Boolean(value));
    const missing = labels(missingAssets);
    const ambiguous = labels(ambiguousAssets);
    const reasons = [
      missing.length ? `尚未批准项目角色参考：${missing.join("、")}` : "",
      ambiguous.length ? `存在多个可用造型，需逐镜选择：${ambiguous.join("、")}` : "",
    ].filter(Boolean);
    const requestSuffix = error.requestId ? ` · 请求 ID ${error.requestId}` : "";
    return `${reasons.join("；") || error.message}。请先在项目资产中完成角色参考，再回到本集同步${requestSuffix}`;
  }
  if (error instanceof ApiRequestError && error.code === "EPISODE_PRODUCTION_PREFLIGHT_BLOCKED") {
    const blockerCodes = Array.isArray(error.details?.blocker_codes)
      ? error.details.blocker_codes.filter((code): code is string => typeof code === "string" && code.length > 0)
      : [];
    const blockers = blockerCodes.length ? ` 阻塞项：${blockerCodes.join("、")}。` : "";
    const preflight = error.details?.preflight;
    const preflightRecord = preflight && typeof preflight === "object" ? preflight as Record<string, unknown> : null;
    const checkItems = Array.isArray(preflightRecord?.blockers) ? preflightRecord.blockers : [];
    const assetCheck = checkItems.find((item) => item && typeof item === "object" && (item as Record<string, unknown>).code === "ASSET_COMPLETION_REQUIRED") as Record<string, unknown> | undefined;
    const evidence = assetCheck?.evidence && typeof assetCheck.evidence === "object" ? assetCheck.evidence as Record<string, unknown> : null;
    const invalidBindings = Array.isArray(evidence?.invalid_shot_bindings) ? evidence.invalid_shot_bindings : [];
    const invalidAssetCodes = [...new Set(invalidBindings.map((item) => item && typeof item === "object" ? (item as Record<string, unknown>).asset_code : null).filter((value): value is string => typeof value === "string" && value.length > 0))];
    const missingAssetIds = Array.isArray(evidence?.missing_asset_ids) ? evidence.missing_asset_ids.filter((value): value is string => typeof value === "string" && value.length > 0) : [];
    const pendingProposalIds = Array.isArray(evidence?.pending_proposal_ids) ? evidence.pending_proposal_ids.filter((value): value is string => typeof value === "string" && value.length > 0) : [];
    const nestedCode = typeof evidence?.code === "string" ? evidence.code : "";
    const nestedDetail = typeof evidence?.detail === "string" ? evidence.detail : "";
    const assetDetails = [
      nestedCode && nestedCode !== "ASSET_COMPLETION_REQUIRED" ? `资产前置检查：${nestedCode}${nestedDetail ? `（${nestedDetail}）` : ""}` : "",
      pendingProposalIds.length ? `待处理资产身份建议：${pendingProposalIds.map((value) => value.slice(0, 8)).join("、")}` : "",
      invalidAssetCodes.length ? `镜头绑定未更新：${invalidAssetCodes.join("、")}` : "",
      missingAssetIds.length ? `缺少完整身份包的资产：${missingAssetIds.map((value) => value.slice(0, 8)).join("、")}` : "",
    ].filter(Boolean);
    const detail = assetDetails.length ? ` ${assetDetails.join("；")}。` : "";
    return `还不能开始自动制作。${blockers}${detail}预检失败没有创建任何任务。`;
  }
  return error instanceof Error ? error.message : String(error);
}

function needsAssetIdentityReview(error: unknown) {
  if (!(error instanceof ApiRequestError) || error.code !== "EPISODE_PRODUCTION_PREFLIGHT_BLOCKED") return false;
  const preflight = error.details?.preflight;
  if (!preflight || typeof preflight !== "object") return false;
  const blockers = Array.isArray((preflight as Record<string, unknown>).blockers) ? (preflight as Record<string, unknown>).blockers as unknown[] : [];
  return blockers.some((item) => {
    if (!item || typeof item !== "object") return false;
    const blocker = item as Record<string, unknown>;
    const evidence = blocker.evidence && typeof blocker.evidence === "object" ? blocker.evidence as Record<string, unknown> : null;
    return blocker.code === "ASSET_COMPLETION_REQUIRED" && evidence?.code === "ASSET_IDENTITY_DECISION_REQUIRED";
  });
}

function needsEpisodeIdentityPackSync(error: unknown) {
  if (!(error instanceof ApiRequestError) || error.code !== "EPISODE_PRODUCTION_PREFLIGHT_BLOCKED") return false;
  const preflight = error.details?.preflight;
  if (!preflight || typeof preflight !== "object") return false;
  const blockers = Array.isArray((preflight as Record<string, unknown>).blockers) ? (preflight as Record<string, unknown>).blockers as unknown[] : [];
  return blockers.some((item) => {
    if (!item || typeof item !== "object") return false;
    const blocker = item as Record<string, unknown>;
    const evidence = blocker.evidence && typeof blocker.evidence === "object" ? blocker.evidence as Record<string, unknown> : null;
    return blocker.code === "ASSET_COMPLETION_REQUIRED"
      && Array.isArray(evidence?.invalid_shot_bindings)
      && evidence.invalid_shot_bindings.length > 0;
  });
}

function aggregate(items: EpisodeProductionShot[], code: StageCode) {
  const facts = items.map((item) => item.stages.find((stage) => stage.stage_code === code)).filter(Boolean) as EpisodeProductionStage[];
  const completed = facts.filter((fact) => fact.state === "READY").length;
  const attention = items.filter((item) => item.stages.find((stage) => ATTENTION.has(stage.state))?.stage_code === code).length;
  const stale = facts.filter((fact) => fact.state === "STALE").length;
  const requiresConfirmation = facts.filter((fact) => HUMAN_BLOCKING_STATES.has(fact.state)).length;
  const running = facts.some((fact) => fact.state === "RUNNING");
  const state = attention ? "attention" : running ? "running" : facts.length > 0 && completed === facts.length ? "complete" : "pending";
  return { completed, total: facts.length, attention, stale, requiresConfirmation, state };
}

type AttentionGroup = { key: string; items: EpisodeProductionShot[]; blocker?: EpisodeProductionBlocker; fallback?: EpisodeProductionStage };

function groupAttention(items: EpisodeProductionShot[]): AttentionGroup[] {
  const groups = new Map<string, AttentionGroup>();
  for (const item of items) {
    const blocker = item.blockers[0];
    const fallback = item.stages.find((stage) => ATTENTION.has(stage.state));
    const key = blocker ? `${blocker.owner_route}:${blocker.code}:${blocker.message}` : `${fallback?.stage_code}:${fallback?.reason_code}`;
    const existing = groups.get(key);
    if (existing) existing.items.push(item);
    else groups.set(key, { key, items: [item], blocker, fallback });
  }
  return [...groups.values()];
}

function AttentionCard({ group, projectId, episodeId }: { group: AttentionGroup; projectId: string; episodeId: string }) {
  const { blocker, fallback, items } = group;
  const sample = items.slice(0, 3).map((item) => item.shot_code).join("、");
  const target = blocker?.owner_route === "SHOT_STUDIO"
    ? routes.shotStudio(projectId, episodeId)
    : blocker ? blockerRoute(blocker, projectId, episodeId, items[0].shot_id) : routes.shotStudio(projectId, episodeId);
  const autoRegenerated = Boolean(blocker && AUTO_REGENERATED_BLOCKERS.has(blocker.code));
  return <article className={`episode-agent-attention-card${autoRegenerated ? " is-recoverable" : ""}`}>
    <div>
      <span>{items.length} 镜 · {sample}{items.length > 3 ? " 等" : ""}</span>
      <strong>{blocker ? BLOCKER_LABELS[blocker.code] ?? "这个镜头需要确认" : "这个镜头需要确认"}</strong>
      <p>{blocker?.message ?? (fallback ? `${STATE_LABELS[fallback.state]}，Agent 已暂停自动推进。` : "请检查后继续。")}</p>
    </div>
    {autoRegenerated ? <span className="episode-agent-recovery-label">随本集重生成</span> : <Link to={target}>{items.length > 1 ? "批量处理" : "处理这一项"}</Link>}
  </article>;
}

export function EpisodeProductionWorkspace({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const retryKeys = useRef(new Map<string, string>());
  const [mode, setMode] = useState<EpisodeProductionMode>("BALANCED");
  const [pauseBeforeMedia, setPauseBeforeMedia] = useState(false);
  const [tts, setTts] = useState(true);
  const [confirmReplan, setConfirmReplan] = useState(false);
  const [confirmShotsReady, setConfirmShotsReady] = useState(false);
  const [cancelRunConfirmOpen, setCancelRunConfirmOpen] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const overviewKey = ["episode-production-v2", episodeId, "overview"] as const;
  const shotsKey = ["episode-production-v2", episodeId, "shots", "all"] as const;
  const replanKey = ["episode-production-v2", episodeId, "replan"] as const;
  const overview = useQuery({
    queryKey: overviewKey,
    queryFn: () => getEpisodeProductionOverviewV2(episodeId),
    refetchInterval: (query) => (query.state.data?.overview.active_job_count ?? 0) > 0 ? 3000 : false,
  });
  const shots = useQuery({ queryKey: shotsKey, queryFn: () => listEpisodeProductionShotsV2(episodeId, { cursor: 0, limit: 100 }) });
  const replan = useQuery({
    queryKey: replanKey,
    queryFn: () => getEpisodeProductionReplanV2(episodeId),
    enabled: Boolean(overview.data?.overview.replan_required),
    refetchInterval: (query) => {
      const result = query.state.data?.replan;
      return result?.status === "NOT_READY" && result.job ? 3000 : false;
    },
  });
  useProjectEventInvalidation(projectId, ["EpisodeProductionRunChanged", "JOB_QUEUED", "JOB_FINISHED", "SHOT_REVISION_CREATED", "AudioWorkingCandidateChanged", "FrameBridgeChanged"], [overviewKey, shotsKey, replanKey]);

  const keyFor = (identity: string) => {
    const existing = retryKeys.current.get(identity);
    if (existing) return existing;
    const created = crypto.randomUUID();
    retryKeys.current.set(identity, created);
    return created;
  };
  const refresh = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["episode-production-v2", episodeId, "overview"] }),
    queryClient.invalidateQueries({ queryKey: ["episode-production-v2", episodeId, "shots"] }),
    queryClient.invalidateQueries({ queryKey: ["episode-production-v2", episodeId, "replan"] }),
  ]);
  const start = useMutation({
    mutationFn: () => {
      const checkpoint = pauseBeforeMedia ? "BEFORE_VIDEO" : "AUTO_CONTINUE";
      const identity = `start:${episodeId}:${mode}:${tts}:${checkpoint}`;
      const payload: EpisodeProductionRunStartCommand = { production_mode: mode, tts_enabled: tts, checkpoint_policy: checkpoint, idempotency_key: keyFor(identity) };
      return startEpisodeProductionRunV2(episodeId, payload).then((result) => ({ result, identity }));
    },
    onSuccess: async ({ identity }) => {
      retryKeys.current.delete(identity);
      setFeedback("Agent 已开始制作本集。你可以离开此页，命中确认点或异常时会暂停。");
      await refresh();
    },
  });
  const syncIdentityPacks = useMutation({
    mutationFn: () => syncEpisodeCharacterPacks(episodeId),
    onSuccess: async ({ sync }) => {
      start.reset();
      setFeedback(`已将 ${sync.character_count} 个角色的当前批准造型同步到本集，更新 ${sync.updated_binding_count} 条镜头引用。`);
      await refresh();
    },
  });
  const prepare = useMutation({
    mutationFn: () => {
      // A transport retry reuses its key; a new attempt after a terminal job
      // gets a new identity instead of replaying the failed submission.
      const identity = `prepare:${episodeId}:${overview.data?.overview.planning_job?.id ?? "initial"}`;
      return prepareEpisodeProductionV2(episodeId, { idempotency_key: keyFor(identity) })
        .then((result) => ({ result, identity }));
    },
    onSuccess: async ({ result, identity }) => {
      if (result.preparation.status !== "QUEUED") retryKeys.current.delete(identity);
      setFeedback(result.preparation.status === "QUEUED"
        ? null
        : "本集方案已生成，正在更新制作阶段。");
      await refresh();
    },
  });
  const requestReplan = useMutation({
    mutationFn: () => {
      const current = overview.data?.overview;
      const currentDraftId = replan.data?.replan?.draft_id ?? current?.replan_draft?.draft_id ?? "none";
      const identity = `replan:${episodeId}:${current?.target_duration_ms ?? 0}:${currentDraftId}`;
      return requestEpisodeProductionReplanV2(episodeId, { idempotency_key: keyFor(identity) }).then((result) => ({ result, identity }));
    },
    onSuccess: async ({ result, identity }) => {
      if (result.replan.status !== "NOT_READY") retryKeys.current.delete(identity);
      setFeedback(result.replan.idempotent_replay ? "本集重规划任务已在生成中。" : "已提交本集 AI 重规划，草稿就绪后会显示完整差异。请稍候刷新。");
      await refresh();
    },
  });
  const applyReplan = useMutation({
    mutationFn: () => {
      const plan = replan.data?.replan;
      if (!plan?.expected_episode_revision || !plan.plan_hash) throw new Error("重规划差异尚未就绪，请刷新后再试。");
      return applyEpisodeProductionReplanV2(episodeId, {
        expected_episode_revision: plan.expected_episode_revision,
        expected_plan_hash: plan.plan_hash,
        idempotency_key: keyFor(`replan-apply:${episodeId}:${plan.plan_hash}`),
      });
    },
    onSuccess: async () => {
      setConfirmReplan(false);
      setFeedback("本集重规划已应用；历史媒体与时间线已保留为过期版本，当前计划可继续生成。");
      await refresh();
    },
  });
  const markShotsReady = useMutation({
    mutationFn: () => {
      const revision = overview.data?.overview.episode_revision;
      if (!revision) throw new Error("本集版本尚未就绪，请刷新后再试。");
      const identity = `shots-ready:${episodeId}:${revision}`;
      return markEpisodeProductionShotsReadyV2(episodeId, {
        expected_episode_revision: revision,
        idempotency_key: keyFor(identity),
      }).then((result) => ({ result, identity }));
    },
    onSuccess: async ({ result, identity }) => {
      retryKeys.current.delete(identity);
      setConfirmShotsReady(false);
      setFeedback(`已确认本集 ${result.ready.ready_shot_count} 个分镜；全部按当前项目 Profile 校验并进入可生产状态。`);
      await refresh();
    },
  });
  const transition = useMutation({
    mutationFn: ({ action, revision }: { action: RunAction; revision: number }) => {
      const runId = overview.data?.overview.active_run?.id;
      if (!runId) throw new Error("当前没有可控制的本集运行。");
      const identity = `${action}:${runId}:${revision}`;
      const extra = action === "pause" ? { reason: "CREATOR_PAUSE" } : action === "resume" ? { note: "创作者确认后继续" } : {};
      return transitionEpisodeProductionRunV2(runId, action, { expected_revision: revision, idempotency_key: keyFor(identity), ...extra }).then((result) => ({ result, identity, action }));
    },
    onSuccess: async ({ identity, action }) => {
      retryKeys.current.delete(identity);
      if (action === "cancel") setCancelRunConfirmOpen(false);
      setFeedback({ pause: "本集制作已暂停。", resume: "确认完成，Agent 将继续制作。", cancel: "本次制作已取消。", recover: "运行恢复检查已完成。" }[action]);
      await refresh();
    },
  });

  const allShots = shots.data?.items ?? [];
  const attentionShots = useMemo(() => allShots.filter((item) => ATTENTION.has(item.overall_state)), [allShots]);
  const attentionGroups = useMemo(() => groupAttention(attentionShots), [attentionShots]);
  if (overview.isPending || shots.isPending) return <section className="episode-agent-loading" role="status">正在整理本集方案与生产进度…</section>;
  if (overview.error || shots.error) return <section className="episode-agent-error" role="alert"><strong>暂时无法读取本集</strong><span>{String(overview.error ?? shots.error)}</span><button type="button" onClick={() => { void overview.refetch(); void shots.refetch(); }}>重试</button></section>;

  const summary = overview.data.overview;
  const planningJob = summary.shot_count === 0 ? summary.planning_job : null;
  const planningFailure = planningJob && ["FAILED", "DEAD", "ORPHANED", "NEEDS_ATTENTION", "CANCELLED"].includes(planningJob.state) ? planningJob : null;
  const planningRunning = planningJob && ["QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"].includes(planningJob.state);
  const stageFacts = STAGES.map((stage) => ({ ...stage, ...aggregate(allShots, stage.code),
    ...(stage.code === "SHOT_PLANNING" && planningJob ? {
      state: planningFailure ? "attention" : planningRunning ? "running" : "pending",
      statusLabel: planningFailure ? "未完成" : planningRunning ? "生成中" : "等待方案应用",
    } : { statusLabel: null }),
  }));
  const run = summary.active_run;
  const complete = summary.shot_count > 0 && summary.state_counts.READY === summary.shot_count;
  const mutationError = syncIdentityPacks.error ?? prepare.error ?? requestReplan.error ?? applyReplan.error ?? markShotsReady.error ?? start.error ?? transition.error;
  const showAssetIdentityReview = needsAssetIdentityReview(mutationError);
  const showIdentityPackSync = needsEpisodeIdentityPackSync(start.error);
  const shotIntentGroup = attentionGroups.find((group) => group.blocker?.code === "SHOT_INTENT_INCOMPLETE");
  const blockingAttentionGroups = attentionGroups.filter((group) => !group.blocker || !AUTO_REGENERATED_BLOCKERS.has(group.blocker.code));
  const recoverableAttentionGroups = attentionGroups.filter((group) => group.blocker && AUTO_REGENERATED_BLOCKERS.has(group.blocker.code));
  const canRegenerateKeyframes = !run && summary.active_job_count === 0 && blockingAttentionGroups.length > 0
    && blockingAttentionGroups.every((group) => !group.blocker && group.fallback?.stage_code === "SHOT_IMAGE"
      && ["NEEDS_REVIEW", "STALE"].includes(group.fallback.state));
  const episodeHealthLabel = planningFailure ? "方案生成未完成"
    : planningRunning ? "方案生成中"
    : summary.shot_count === 0 ? "待生成方案"
    : blockingAttentionGroups.length ? `${blockingAttentionGroups.length} 类待确认`
    : recoverableAttentionGroups.length ? `${recoverableAttentionGroups.length} 类将自动更新`
    : "当前无异常";
  const replanPlan = replan.data?.replan?.status === "DRAFT_READY" ? replan.data.replan : null;
  const replanJob = summary.replan_job ?? (replan.data?.replan?.status === "NOT_READY" ? replan.data.replan.job : null);
  const replanDurationDeltaMs = replanPlan && typeof summary.target_duration_ms === "number" && typeof replanPlan.planned_duration_ms === "number"
    ? Math.abs(replanPlan.planned_duration_ms - summary.target_duration_ms)
    : null;
  const replanPlanNeedsRefresh = replanDurationDeltaMs !== null && replanDurationDeltaMs > REPLAN_DURATION_TOLERANCE_MS;
  const resolvedPresentation = summary.resolved_presentation;
  const gate = runGate(run);
  const fpsValue: string = resolvedPresentation && typeof resolvedPresentation.fps === "object" && resolvedPresentation.fps !== null
    ? `${String((resolvedPresentation.fps as { numerator?: number }).numerator ?? "")}/${String((resolvedPresentation.fps as { denominator?: number }).denominator ?? "")}`
    : typeof resolvedPresentation?.fps === "string" || typeof resolvedPresentation?.fps === "number"
      ? String(resolvedPresentation.fps)
      : "—";
  const formatSeconds = (ms?: number | null) => ms == null ? "—" : `${Math.round(ms / 1000)} 秒`;
  const replanPrimary = summary.replan_required
    ? replanPlan
      ? replanPlanNeedsRefresh
        ? <button className="primary-action" type="button" disabled={requestReplan.isPending} onClick={() => requestReplan.mutate()}>{requestReplan.isPending ? "重新生成中…" : "重新生成重规划草稿"}</button>
        : <button className="primary-action" type="button" onClick={() => document.getElementById("episode-agent-replan")?.scrollIntoView({ behavior: "smooth", block: "start" })}>审核重规划差异</button>
      : replanJob
        ? <button className="primary-action" type="button" disabled>重规划生成中…</button>
        : <button className="primary-action" type="button" disabled={requestReplan.isPending} onClick={() => requestReplan.mutate()}>{requestReplan.isPending ? "提交中…" : "生成本集重规划"}</button>
    : null;
  const primaryAction = run?.status === "PAUSED_HITL"
    ? <button className="primary-action" type="button" disabled={transition.isPending} onClick={() => transition.mutate({ action: "resume", revision: run.revision })}>{transition.isPending ? "正在继续…" : gate?.isMachine ? "问题处理后继续" : "确认并继续"}</button>
    : run?.status === "RUNNING"
      ? <Link className="primary-action v2-inline-link" to={routes.systemJobs(projectId)}>查看生成进度</Link>
      : replanPrimary
        ? replanPrimary
        : summary.shot_count === 0
        ? summary.active_job_count > 0
          ? <Link className="primary-action v2-inline-link" to={routes.systemJobs(projectId)}>正在生成本集方案</Link>
          : <button className="primary-action" type="button" disabled={prepare.isPending} onClick={() => prepare.mutate()}>{prepare.isPending ? "正在提交…" : planningFailure ? "重新生成本集方案" : "生成本集方案"}</button>
        : canRegenerateKeyframes
          ? <button className="primary-action" type="button" disabled={start.isPending} onClick={() => start.mutate()}>{start.isPending ? "正在检查…" : "重新生成本集关键帧"}</button>
        : blockingAttentionGroups.length
          ? <button className="primary-action" type="button" onClick={() => document.getElementById("episode-agent-attention")?.scrollIntoView({ behavior: "smooth", block: "start" })}>处理 {blockingAttentionGroups.length} 类待确认</button>
          : complete
            ? <Link className="primary-action v2-inline-link" to={routes.postEdit(projectId, episodeId)}>预览本集</Link>
            : <button className="primary-action" type="button" disabled={start.isPending} onClick={() => start.mutate()}>{start.isPending ? "正在检查…" : "开始本集"}</button>;

  return <section className="episode-agent-workspace" aria-label="本集 Agent 制作">
    <header className="episode-agent-hero">
      <div className="episode-agent-identity"><p className="eyebrow">本集制作</p><h2>{summary.episode_code}{summary.episode_title ? ` · ${summary.episode_title}` : ""}</h2><p>{summary.episode_summary || (summary.shot_count ? "Agent 已根据本集分镜整理制作任务；剧情摘要会随已确认方案更新。" : "本集方案尚未生成。先从已确认原稿生成本集分场和分镜。")}</p></div>
      <div className="episode-agent-primary">
        {primaryAction}
        {canRegenerateKeyframes && <p>按当前配置重新生成尚未批准的关键帧，再逐镜审核。旧候选与审核记录保留。</p>}
        {run && (run.status === "RUNNING" || run.status === "PAUSED_HITL") && <button
          className="secondary danger-outline"
          type="button"
          disabled={transition.isPending}
          onClick={() => setCancelRunConfirmOpen(true)}
        >取消本次制作</button>}
      </div>
    </header>

    <section className="episode-agent-brief" aria-label="本集创作摘要">
      <div><span>关键角色</span><p>{summary.key_characters.length ? summary.key_characters.join("、") : "由 Agent 在方案阶段提取"}</p></div>
      <div><span>关键场景</span><p>{summary.key_scenes.length ? summary.key_scenes.join("、") : "由 Agent 在方案阶段提取"}</p></div>
      <div><span>镜头</span><p><strong>{summary.shot_count}</strong> 镜 · {episodeHealthLabel}</p></div>
    </section>

    <section className="episode-agent-contract" aria-label="本集规格与计划">
      <div><span>本集目标</span><strong>{formatSeconds(summary.target_duration_ms)}</strong></div>
      <div><span>当前分镜</span><strong>{formatSeconds(summary.planned_duration_ms)}</strong><small>{!summary.shot_count ? "方案生成后核对时长" : summary.replan_required ? "与目标不一致，需审核重规划" : "与本集目标一致"}</small></div>
      <div><span>解析后交付规格</span><strong>{resolvedPresentation && resolvedPresentation.width != null && resolvedPresentation.height != null ? `${String(resolvedPresentation.width)}×${String(resolvedPresentation.height)}` : "未配置"}</strong><small>{fpsValue} fps · 计划 v{summary.production_plan_version_no ?? "—"}</small></div>
    </section>

    {summary.replan_required && <section className="episode-agent-replan" id="episode-agent-replan" aria-label="本集重规划审核">
      <div className="episode-agent-section-heading"><div><h3>本集计划需要更新</h3><p>AI 只读取本集已确认原文范围和项目故事拆解能力；旧镜头、媒体和时间线不会被删除。</p></div><span>{summary.replan_reasons?.length ?? 0}</span></div>
      <ul className="episode-agent-replan-reasons">{(summary.replan_reasons ?? []).map((reason) => <li key={String(reason.code)}><strong>{String(reason.code)}</strong><span>{String(reason.message)}</span></li>)}</ul>
      {!replanPlan && <div className="episode-agent-replan-empty"><strong>{replanJob ? "AI 重规划草稿生成中" : "尚未生成当前目标的重规划草稿"}</strong><span>{replanJob ? `任务 ${replanJob.id.slice(0, 8)} · 页面会自动刷新` : "点击上方按钮提交；生成完成后将在这里显示全本集差异。"}</span></div>}
      {replanPlan && <>
        <div className="episode-agent-replan-summary"><span>草稿 {replanPlan.draft_id?.slice(0, 8) ?? "—"}</span><span>建议总时长 {formatSeconds(replanPlan.planned_duration_ms)}</span><span>差异 {Object.entries(replanPlan.summary ?? {}).filter(([, count]) => count > 0).map(([key, count]) => `${key} ${count}`).join(" · ") || "无"}</span></div>
        {replanPlanNeedsRefresh && <div className="episode-agent-replan-issues" role="alert"><strong>这份重规划草稿已不符合当前目标</strong><span>草稿建议 {formatSeconds(replanPlan.planned_duration_ms)}，当前目标 {formatSeconds(summary.target_duration_ms)}，相差 {formatSeconds(replanDurationDeltaMs)}，超过技术容差 {formatSeconds(REPLAN_DURATION_TOLERANCE_MS)}。</span><button className="secondary" type="button" disabled={requestReplan.isPending} onClick={() => requestReplan.mutate()}>{requestReplan.isPending ? "重新生成中…" : "重新生成重规划草稿"}</button></div>}
        {replanPlan.valid === false && <div className="episode-agent-replan-issues" role="alert"><strong>这份草稿暂不能应用</strong><ul>{(replanPlan.issues ?? []).map((issue, index) => <li key={`${String(issue.code)}-${index}`}><span>{String(issue.code ?? "PLAN_INVALID")}</span><small>{String(issue.message ?? "请重新生成或调整本集方案")}</small></li>)}</ul></div>}
        <ul className="episode-agent-replan-diff">{(replanPlan.diff ?? []).map((item, index) => {
          const after = item.after as { target_duration_ms?: number } | undefined;
          return <li key={`${String(item.action)}-${String(item.shot_id ?? index)}`}><strong>{String(item.action)}</strong><span>{item.shot_id ? `现有镜头 ${String(item.shot_id).slice(0, 8)}` : "新增镜头"}</span><small>{item.reason ? String(item.reason) : `建议 ${formatSeconds(after?.target_duration_ms)}`}</small></li>;
        })}</ul>
        <label className="episode-agent-replan-confirm"><input type="checkbox" checked={confirmReplan} onChange={(event) => setConfirmReplan(event.target.checked)} />我已审核本集全部差异，确认应用这份重规划</label>
        <button className="primary-action" type="button" disabled={!confirmReplan || replanPlan.valid === false || replanPlanNeedsRefresh || applyReplan.isPending} onClick={() => applyReplan.mutate()}>{applyReplan.isPending ? "应用中…" : "确认应用并继续生成"}</button>
      </>}
    </section>}

    <ol className="episode-agent-stages" aria-label="本集制作阶段">
      {stageFacts.map((stage, index) => <li key={stage.code} className={`is-${stage.state}`}><span className="episode-agent-stage-index">{index + 1}</span><div><strong>{stage.label}</strong><small>{stage.detail}</small></div><span className="episode-agent-stage-state">{stage.statusLabel ?? (stage.requiresConfirmation ? `${stage.requiresConfirmation} 项待确认` : stage.stale ? `${stage.stale} 项待更新` : stage.state === "running" ? "生成中" : stage.state === "complete" ? "已完成" : stage.total ? `${stage.completed}/${stage.total}` : "等待方案")}</span></li>)}
    </ol>

    <section className="episode-agent-run" aria-label="Agent 运行">
      <div><span className={`episode-agent-run-state is-${run?.status.toLowerCase() ?? "idle"}`}>{run?.status === "RUNNING" ? "自动制作中" : run?.status === "PAUSED_HITL" ? "等待确认" : complete ? "本集已完成" : "尚未开始新的制作"}</span><p>{run ? `运行 ${run.id.slice(0, 8)} · 仅在异常或所选关键节点暂停` : "开始后按当前方案与项目设置推进，角色、场景和历史素材仍保留。"}</p></div>
      {run?.status === "RUNNING" && <button type="button" className="secondary" disabled={transition.isPending} onClick={() => transition.mutate({ action: "pause", revision: run.revision })}>暂停</button>}
      {!run && !complete && <details className="episode-agent-options"><summary>本集生成设置</summary><div><label><span>质量</span><select aria-label="本集质量" value={mode} onChange={(event) => setMode(event.target.value as EpisodeProductionMode)}><option value="DRAFT">预览</option><option value="BALANCED">标准</option><option value="QUALITY">精品</option></select></label><label><input type="checkbox" checked={tts} onChange={(event) => setTts(event.target.checked)} />生成对白与字幕</label><label><input type="checkbox" checked={pauseBeforeMedia} onChange={(event) => setPauseBeforeMedia(event.target.checked)} />批量生成视频前暂停确认</label><Link to={routes.settings(projectId)}>画幅、分辨率和模型使用项目设置</Link></div></details>}
    </section>
    {run?.status === "PAUSED_HITL" && gate && <section className={`episode-agent-run-gate${gate.isMachine ? " is-machine" : ""}`} aria-label="本次暂停原因">
      <div className="episode-agent-run-gate-copy">
        <span>{gate.isMachine ? "机器检查已暂停" : "配置确认点已暂停"}</span>
        <strong>{gate.isMachine ? "需要先处理机器检查结果" : "这是项目配置的人工确认点"}</strong>
        <p>{gate.detail}{gate.nextAction ? ` 下一节点：${gate.nextAction}。` : ""}</p>
      </div>
      {gate.isMachine && <dl className="episode-agent-run-gate-facts">
        <div><dt>机器检查 code</dt><dd><code>{gate.code}</code></dd></div>
        <div><dt>受影响镜头</dt><dd>{gate.affectedShotCount} 镜</dd></div>
      </dl>}
      {gate.isMachine && ["APPROVED_KEYFRAME_REQUIRED", "SHOT_KEYFRAME_BATCH_BLOCKED", "SHOT_KEYFRAME_GENERATION_FAILED"].includes(gate.code) && <Link className="secondary v2-inline-link" to={routes.shotStudio(projectId, episodeId)}>检查并审核本集关键帧</Link>}
      {gate.isMachine && <Link className="secondary v2-inline-link" to={routes.systemJobs(projectId)}>打开项目任务中心</Link>}
    </section>}
    {mutationError && <p className="inline-error" role="alert">{errorText(mutationError)}</p>}
    {feedback && !planningFailure && <p className="episode-agent-feedback" role="status">{feedback}</p>}
    {summary.shot_count === 0 && summary.active_job_count > 0 && <p className="episode-agent-feedback" role="status">Agent 正在根据本集原文生成方案，完成后会自动应用到本集。</p>}
    {showAssetIdentityReview && <AssetProposalReviewPanel projectId={projectId} />}
    {showIdentityPackSync && <article className="episode-agent-ready-confirm" aria-label="同步本集角色身份包">
      <div><strong>把当前批准造型应用到本集</strong><p>只同步本集已经引用的角色。每个角色只有一个有效造型时可一次完成；存在多个造型时会停止并要求逐镜选择，不会替你猜。</p></div>
      <button className="primary-action" type="button" disabled={syncIdentityPacks.isPending} onClick={() => syncIdentityPacks.mutate()}>{syncIdentityPacks.isPending ? "同步中…" : "同步并重新检查"}</button>
    </article>}

    <section className="episode-agent-attention" id="episode-agent-attention" aria-label="本集待确认项">
      <div className="episode-agent-section-heading"><div><h3>{planningFailure ? "方案生成未完成" : blockingAttentionGroups.length ? "需要你确认" : recoverableAttentionGroups.length ? "开始后自动更新" : "当前状态"}</h3><p>{planningFailure ? "请处理本次任务的失败原因，再重新生成本集方案。" : blockingAttentionGroups.length ? "同一根因会合并处理，不要求逐镜重复确认。" : recoverableAttentionGroups.length ? "旧工作版本保留用于审计；开始本集后按当前方案和项目配置重新生成。" : "本集没有需要人工处理的事项。"}</p></div><span>{attentionGroups.length + (planningFailure ? 1 : 0)}</span></div>
      {planningFailure && <article className="episode-agent-error" role="alert">
        <strong>{planningFailure.state === "CANCELLED" ? "本集方案任务已取消" : "本集方案生成失败"}</strong>
        <span>{planningFailure.error_message || "本次任务未完成，尚未生成可用分镜。请查看任务详情。"}</span>
        {planningFailure.error_code && <code>{planningFailure.error_code}</code>}
        <Link className="secondary v2-inline-link" to={`${routes.systemJobs(projectId)}&job=${encodeURIComponent(planningFailure.id)}`}>查看本次失败详情</Link>
        <Link className="secondary v2-inline-link" to={routes.settings(projectId)}>检查项目生成设置</Link>
      </article>}
      {shotIntentGroup && <article className="episode-agent-ready-confirm">
        <div><strong>一次确认本集 {shotIntentGroup.items.length} 个分镜</strong><p>系统会使用当前项目自己的已发布视频 Profile 逐镜校验运镜；任一镜失败则整批回滚，不会跨项目借用配置。</p></div>
        <label><input type="checkbox" checked={confirmShotsReady} onChange={(event) => setConfirmShotsReady(event.target.checked)} />我已审核当前本集分镜方案，确认全部进入生产</label>
        <button className="primary-action" type="button" disabled={!confirmShotsReady || markShotsReady.isPending} onClick={() => markShotsReady.mutate()}>{markShotsReady.isPending ? "整集校验中…" : "确认本集分镜并就绪"}</button>
      </article>}
      {attentionGroups.length ? <div className="episode-agent-attention-list">{attentionGroups.filter((group) => group !== shotIntentGroup).map((group) => <AttentionCard key={group.key} group={group} projectId={projectId} episodeId={episodeId} />)}</div> : !planningFailure && <div className="episode-agent-clear"><strong>当前没有需要处理的事项</strong><span>{run ? "Agent 会继续推进，并在新的确认点出现时更新这里。" : "准备完成后可以开始本集制作。"}</span></div>}
    </section>

    <footer className="episode-agent-footer"><Link to={routes.shotStudio(projectId, episodeId)}>打开镜头修正</Link><Link to={routes.storyWorkspace(projectId)}>查看故事与原文</Link><Link to={routes.settings(projectId)}>项目生成设置</Link><button type="button" className="quiet-action" disabled={overview.isFetching || shots.isFetching} onClick={() => { void overview.refetch(); void shots.refetch(); }}>{overview.isFetching || shots.isFetching ? "刷新中…" : "刷新状态"}</button></footer>
    <Dialog
      open={cancelRunConfirmOpen}
      title="取消本次制作？"
      onClose={() => !transition.isPending && setCancelRunConfirmOpen(false)}
      footer={<><button type="button" className="secondary" disabled={transition.isPending} onClick={() => setCancelRunConfirmOpen(false)}>返回</button><button type="button" className="secondary danger-outline" disabled={transition.isPending} onClick={() => { if (run) transition.mutate({ action: "cancel", revision: run.revision }); }}>{transition.isPending ? "正在取消…" : "确认取消本次制作"}</button></>}
    >取消只会停止当前本集 Agent 运行，已生成的历史素材、审核记录和失败证据都会保留。确认后如需继续，请按当前项目与分集配置重新开始。</Dialog>
  </section>;
}

import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  createShotGenerationIntentV2,
  markShotReadyV2,
  preflightShotGenerationV2,
  resolveProfileCameraPlan,
  submitShotGenerationV2,
  type ShotBaseGenerationPreflightCommand,
  type ShotStudio,
} from "../../generated/api";
import { composePromptFromIntent, deriveShotSeed } from "../generation/generationDefaults";
import {
  planShotKeyframeBatch,
  submitShotKeyframeBatch,
  type ShotKeyframeBatchPlan,
  type ShotPromptBundleRequest,
  type ShotFrameStrategy,
} from "./shotKeyframeBatchApi";

type CurrentShot = ShotStudio["current_shot"];

interface ShotGenerationInspectorProps {
  episodeId: string;
  shotId: string;
  shotCode: string;
  shotRevision: number;
  fields: Record<string, unknown>;
  currentShot: CurrentShot;
  canGenerate: boolean;
  reviewHref: string;
  onSubmitted: (message: string) => Promise<void> | void;
}

function commandKey(prefix: string): string {
  return globalThis.crypto?.randomUUID?.() ?? `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function approvedStartFrames(currentShot: CurrentShot) {
  const eligible = (candidate: CurrentShot["current_media"] | CurrentShot["candidates"][number]) => Boolean(
    candidate
    && candidate.media_kind === "IMAGE"
    && candidate.stage === "KEYFRAME"
    && candidate.frame_role !== "END_FRAME"
    && candidate.approved
    && !candidate.is_stale
    && candidate.integrity_status === "VERIFIED",
  );
  const frames = currentShot.candidates.filter(eligible);
  const current = currentShot.current_media;
  if (eligible(current) && current && !frames.some((item) => item.media_version_id === current.media_version_id)) frames.unshift(current as CurrentShot["candidates"][number]);
  return frames;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const DEFAULT_SHOT_NEGATIVE_PROMPT = "multi-panel, triptych, contact sheet, storyboard, split-screen, collage, multi-panel layout, text layout, captions, subtitles, watermark";
type FrameReframeMode = "NONE" | "SINGLE_MOMENT";
export const DEFAULT_FRAME_REFRAME_MODE: FrameReframeMode = "SINGLE_MOMENT";
type ActionFeedback = { kind: "pending" | "success" | "error"; message: string };

function compilePromptPreview(basePrompt: string, positiveOverride: string, _negativePrompt: string): string {
  // This is intentionally labelled as an input draft in the UI.  The
  // executable positive/negative split is only authoritative after the
  // server-side plan has resolved the published App Contract.
  return [basePrompt.trim(), positiveOverride.trim()].filter(Boolean).join("\n\n");
}

function frameMomentPreview(fields: Record<string, unknown>, role: "FIRST_FRAME" | "END_FRAME"): string {
  const action = typeof fields.subject_action === "string" ? fields.subject_action.trim() : "";
  const segments = action
    .split(/(?:切至|转至|随后|继而|最后|最终|镜头转向)|[。！？!?；;]+/)
    .map((segment) => segment.trim().replace(/^，+|，+$/g, ""))
    .filter(Boolean);
  const selected = segments[role === "FIRST_FRAME" ? 0 : Math.max(segments.length - 1, 0)] ?? "";
  const moment = selected.replace(/^(?:镜头\s*)?(?:扫过|掠过|推进到|推近至|拉近至|转向|对准|跟随|特写|切至|转至)\s*/, "").trim() || selected;
  const environment = typeof fields.environment === "string" ? fields.environment.trim() : "";
  return moment || environment || "当前镜头主体与空间关系";
}

function compileFramePromptPreview(
  fields: Record<string, unknown>,
  shotCode: string,
  role: "FIRST_FRAME" | "END_FRAME",
  positiveOverride: string,
  negativePrompt: string,
): string {
  const label = role === "FIRST_FRAME" ? "首帧只呈现第一个视觉瞬间" : "尾帧只呈现最后一个视觉瞬间";
  const base = `镜头 ${shotCode}；单一画面重构；${label}：${frameMomentPreview(fields, role)}；只呈现一个连续瞬间和一个空间位置`;
  return compilePromptPreview(base, positiveOverride, negativePrompt);
}

function hasShotFactsPrompt(prompt: string, shotCode: string): boolean {
  const normalized = prompt.trim();
  return Boolean(normalized) && normalized !== `镜头 ${shotCode}`;
}

type PlannedFrameDraw = { plan: ShotKeyframeBatchPlan; formFingerprint: string };

function promptFormFingerprint(shotRevision: number, promptBundle: ShotPromptBundleRequest, frameStrategy: ShotFrameStrategy, candidateCount: number): string {
  return JSON.stringify({ shotRevision, promptBundle, frameStrategy, candidateCount });
}

function executionContractForPlan(plan: ShotKeyframeBatchPlan): NonNullable<ShotKeyframeBatchPlan["execution_contract"]> {
  if (plan.execution_contract && Object.keys(plan.execution_contract).length > 0) return plan.execution_contract;
  const item = (Array.isArray(plan.items) ? plan.items : [])[0];
  const route = item?.shot_keyframe_route ?? {};
  const bindings = (item?.workflow_bindings ?? {}) as NonNullable<ShotKeyframeBatchPlan["execution_contract"]>["workflow_bindings"];
  return {
    source: typeof route.source === "string" ? route.source : null,
    contract_id: typeof route.contract_id === "string" ? route.contract_id : null,
    runtime_environment_version_id: typeof route.runtime_environment_version_id === "string" ? route.runtime_environment_version_id : null,
    published_contract_bound: route.published_contract_bound === true,
    compiler_mode: item?.prompt_bundle?.compiler_mode ?? null,
    semantic_roles: Object.keys(bindings ?? {}),
    workflow_bindings: bindings ?? {},
    seed_policy: null,
  };
}

function planHasExecutableContract(plan: ShotKeyframeBatchPlan): boolean {
  if (!plan.valid || !Array.isArray(plan.items) || plan.items.length === 0) return false;
  const contract = executionContractForPlan(plan);
  const roles = new Set((contract.semantic_roles ?? []).map((role) => String(role)));
  return contract.source === "APP_CONTRACT"
    && Boolean(contract.contract_id)
    && Boolean(contract.runtime_environment_version_id)
    && contract.published_contract_bound === true
    && roles.has("PROMPT")
    && roles.has("NEGATIVE_PROMPT")
    && roles.has("SEED");
}

export function ShotGenerationInspector({ episodeId, shotId, shotCode, shotRevision, fields, currentShot, canGenerate, reviewHref, onSubmitted }: ShotGenerationInspectorProps) {
  const videoResolution = currentShot.generation_preferences.resolutions.find((item) => item.capability === "VIDEO_I2V");
  const profileVersionId = videoResolution?.profile_version_id ?? "";
  const defaultPrompt = useMemo(() => composePromptFromIntent(fields, shotCode), [fields, shotCode]);
  const [positiveOverride, setPositiveOverride] = useState("");
  const [negativePrompt, setNegativePrompt] = useState(DEFAULT_SHOT_NEGATIVE_PROMPT);
  const [promptProvenance, setPromptProvenance] = useState<ShotPromptBundleRequest["provenance"]>("AI_GENERATED");
  const [frameReframeMode, setFrameReframeMode] = useState<FrameReframeMode>(DEFAULT_FRAME_REFRAME_MODE);
  const [frameStrategy, setFrameStrategy] = useState<ShotFrameStrategy>("FIRST_AND_LAST");
  const [candidateCount, setCandidateCount] = useState(4);
  const [actionFeedback, setActionFeedback] = useState<ActionFeedback | null>(null);
  const [plannedFrameDraw, setPlannedFrameDraw] = useState<PlannedFrameDraw | null>(null);
  const prompt = useMemo(() => compilePromptPreview(defaultPrompt, positiveOverride, negativePrompt), [defaultPrompt, negativePrompt, positiveOverride]);
  const promptBundle = useMemo<ShotPromptBundleRequest>(() => ({
    base_prompt: defaultPrompt,
    positive_override: positiveOverride,
    negative_prompt: negativePrompt,
    provenance: promptProvenance,
    frame_reframe_mode: frameReframeMode,
  }), [defaultPrompt, frameReframeMode, negativePrompt, positiveOverride, promptProvenance]);
  const firstFrame = useMemo(() => approvedStartFrames(currentShot)[0] ?? null, [currentShot]);
  const endFrame = currentShot.frame_bridge?.current_end ?? null;
  const profileOption = currentShot.capability_options?.find((item) => item.version_id === profileVersionId);
  const supportsEndFrame = profileOption?.capability === "VIDEO_FIRST_LAST_FRAME";
  const currentFrameFormFingerprint = useMemo(
    () => promptFormFingerprint(shotRevision, promptBundle, frameStrategy, candidateCount),
    [promptBundle, shotRevision, frameStrategy, candidateCount],
  );
  const plannedContract = plannedFrameDraw ? executionContractForPlan(plannedFrameDraw.plan) : null;
  const plannedFrameItem = plannedFrameDraw?.plan.items?.[0] ?? null;
  const plannedFrameIsFresh = plannedFrameDraw?.formFingerprint === currentFrameFormFingerprint;
  const framePlanCanSubmit = Boolean(
    plannedFrameDraw
    && plannedFrameIsFresh
    && planHasExecutableContract(plannedFrameDraw.plan),
  );

  const readyMutation = useMutation({
    onMutate: () => setActionFeedback({ kind: "pending", message: "正在校验并提交镜头就绪状态…" }),
    mutationFn: async () => {
      let draftPayload: Record<string, unknown> | undefined = undefined;
      const currentCamera = fields.camera_plan as Record<string, unknown> | undefined;
      if (profileVersionId) {
        try {
          const resolved = await resolveProfileCameraPlan(profileVersionId, {
            shot_type: (fields.shot_type as string) || (currentShot.shot.shot_type as string) || "OTHER",
            movement: (currentCamera?.movement as string) || "STATIC",
            direction: (currentCamera?.direction as string) || "UNSPECIFIED",
            intensity: typeof currentCamera?.intensity === "number" ? currentCamera.intensity : 0.5,
            curve: (currentCamera?.curve as string) || "LINEAR",
            prompt_text: (currentCamera?.prompt_text as string) || "",
          });
          draftPayload = {
            ...fields,
            camera_plan: resolved.resolution.camera_plan,
          };
        } catch {
          // If resolution fails, pass raw fields
        }
      }
      return markShotReadyV2(shotId, {
        draft: draftPayload,
        expected_revision_no: shotRevision,
      });
    },
    onSuccess: async () => {
      const message = "镜头已标记为可进入生产。";
      setActionFeedback({ kind: "success", message });
      await onSubmitted(message);
    },
    onError: (error) => setActionFeedback({ kind: "error", message: errorText(error) }),
  });

  const framePlan = useMutation({
    onMutate: () => {
      setPlannedFrameDraw(null);
      setActionFeedback({ kind: "pending", message: "正在验证生成计划（只读）…" });
    },
    mutationFn: async () => {
      const target = [{ shot_id: shotId, expected_revision: shotRevision }];
      if (!hasShotFactsPrompt(defaultPrompt, shotCode)) throw new Error("当前镜头缺少 AI 基础提示词，不能生成首尾帧");
      const formFingerprint = promptFormFingerprint(shotRevision, promptBundle, frameStrategy, candidateCount);
      const result = await planShotKeyframeBatch(episodeId, target, frameStrategy, candidateCount, promptBundle);
      return { ...result, formFingerprint };
    },
    onSuccess: ({ plan, formFingerprint }) => {
      setPlannedFrameDraw({ plan, formFingerprint });
      if (plan.valid && planHasExecutableContract(plan)) {
        setActionFeedback({ kind: "success", message: "生成计划已验证；请核对服务端契约证据后提交。" });
      } else {
        setActionFeedback({ kind: "error", message: plan.issues.map((item) => item.message).join("；") || "生成计划存在阻塞，不能提交" });
      }
    },
    onError: (error) => setActionFeedback({ kind: "error", message: errorText(error) }),
  });

  const frameDraw = useMutation({
    onMutate: () => setActionFeedback({ kind: "pending", message: "正在提交已验证的首尾帧生成计划…" }),
    mutationFn: async () => {
      if (!plannedFrameDraw || !plannedFrameIsFresh) throw new Error("当前表单已变化，请重新验证生成计划");
      if (!planHasExecutableContract(plannedFrameDraw.plan)) throw new Error("服务端契约未就绪，不能提交首尾帧生成");
      const target = [{ shot_id: shotId, expected_revision: shotRevision }];
      return submitShotKeyframeBatch(
        episodeId,
        target,
        plannedFrameDraw.plan.frame_strategy,
        plannedFrameDraw.plan.candidate_count,
        plannedFrameDraw.plan.plan_hash,
        commandKey("shot-frame-draw"),
        promptBundle,
      );
    },
    onSuccess: async ({ batch }) => {
      const message = `已排队 ${batch.summary.total} 张首尾帧候选；系统会推荐一个，需要时再替换。`;
      setActionFeedback({ kind: "success", message });
      await onSubmitted(message);
    },
    onError: (error) => setActionFeedback({ kind: "error", message: errorText(error) }),
  });

  const videoDraw = useMutation({
    onMutate: () => setActionFeedback({ kind: "pending", message: "正在校验并提交视频候选…" }),
    mutationFn: async () => {
      if (!profileVersionId) throw new Error(videoResolution?.blocked_reason || "项目尚未配置视频生成能力");
      if (!firstFrame) throw new Error("请先生成并批准一组首尾帧");
      if (!hasShotFactsPrompt(defaultPrompt, shotCode)) throw new Error("当前镜头缺少 AI 基础提示词，不能生成视频");
      const matchingIntent = currentShot.generation_intents.find((item) => item.purpose === "I2V_PROXY" && item.creative_goal === prompt);
      const intent = matchingIntent ?? (await createShotGenerationIntentV2(shotId, { purpose: "I2V_PROXY", creative_goal: prompt, idempotency_key: commandKey("shot-intent") })).intent;
      const seed = deriveShotSeed(shotId);
      const cameraPlan = fields.camera_plan && typeof fields.camera_plan === "object" ? fields.camera_plan as Record<string, unknown> : null;
      const bindings: NonNullable<ShotBaseGenerationPreflightCommand["bindings"]> = [{ role: "FIRST_FRAME", media_version_id: firstFrame.media_version_id, ordinal: 0 }];
      if (supportsEndFrame && endFrame) bindings.push({ role: "END_FRAME", media_version_id: endFrame.media_version_id, ordinal: 0 });
      const command: ShotBaseGenerationPreflightCommand = {
        operation: "BASE", stage_code: "VIDEO", intent_id: intent.id, variant_type: "BASE", parent_variant_id: null,
        branch_reason: "SHOT_STUDIO_BASE", profile_version_id: profileVersionId,
        parameter_set: { PROMPT: prompt, SEED: seed, ...(cameraPlan ? { camera_plan: cameraPlan } : {}) },
        seed_policy: "EXPLICIT", explicit_seed: seed, bindings, expected_shot_revision: shotRevision,
        prompt_bundle: promptBundle,
      };
      const { preflight } = await preflightShotGenerationV2(shotId, command);
      if (preflight.status !== "READY") throw new Error(preflight.blockers?.map((item) => String(item.message ?? item.code)).join("；") || "视频生成条件未满足");
      return submitShotGenerationV2(shotId, { ...command, plan_hash: preflight.plan_hash, idempotency_key: commandKey("shot-video-draw") });
    },
    onSuccess: async (result) => {
      const message = `视频候选已排队（任务 ${result.job.id}）。`;
      setActionFeedback({ kind: "success", message });
      await onSubmitted(message);
    },
    onError: (error) => setActionFeedback({ kind: "error", message: errorText(error) }),
  });

  const busy = framePlan.isPending || frameDraw.isPending || videoDraw.isPending || readyMutation.isPending;
  const error = framePlan.error ?? frameDraw.error ?? videoDraw.error ?? readyMutation.error;
  return <section className="shot-draw-panel" aria-labelledby="shot-draw-title">
    <header><div><span>异常镜头局部修正</span><h3 id="shot-draw-title">根据首尾帧生成候选</h3></div><small>{videoResolution?.profile?.title ?? videoResolution?.profile?.code ?? "能力待配置"}</small></header>
    <div className="shot-draw-description">
      <div className="shot-draw-description-header"><span>AI 基础提示词（镜头事实）</span></div>
      <textarea className="shot-draw-prompt-input" value={defaultPrompt} rows={3} readOnly aria-label="AI 基础提示词" />
      <label className="shot-draw-field-label" htmlFor="shot-positive-override">正向提示词补充（页面编辑）</label>
      <textarea
        id="shot-positive-override"
        className="shot-draw-prompt-input"
        value={positiveOverride}
        rows={2}
        onChange={(e) => { setPositiveOverride(e.target.value); setPromptProvenance("PAGE_USER_EDIT"); }}
        placeholder="补充构图、表演或氛围要求…"
        aria-label="正向提示词补充"
      />
      <label className="shot-draw-field-label" htmlFor="shot-negative-prompt">反向提示词（页面编辑）</label>
      <textarea
        id="shot-negative-prompt"
        className="shot-draw-prompt-input"
        value={negativePrompt}
        rows={2}
        onChange={(e) => { setNegativePrompt(e.target.value); setPromptProvenance("PAGE_USER_EDIT"); }}
        placeholder="输入必须避免的画面问题…"
        aria-label="反向提示词"
      />
      <div className="shot-draw-reframe-policy" aria-label="单一画面重构策略">
        <label className="shot-draw-field-label" htmlFor="shot-frame-reframe-mode">
          <input
            id="shot-frame-reframe-mode"
            type="checkbox"
            checked={frameReframeMode === "SINGLE_MOMENT"}
            onChange={(e) => {
              setFrameReframeMode(e.target.checked ? "SINGLE_MOMENT" : "NONE");
              setPromptProvenance("PAGE_USER_EDIT");
            }}
          />
          单一画面重构（首尾分别取一个视觉瞬间）
        </label>
        <small>
          {frameReframeMode === "SINGLE_MOMENT"
            ? "已启用：首帧只编译第一个视觉瞬间，尾帧只编译最后一个视觉瞬间；原 AI 基础提示词仍保留在审计快照。"
            : "关闭：保留完整镜头叙事；仅适用于明确需要连续叙事输入的旧工作流。"}
        </small>
      </div>
      {(positiveOverride || negativePrompt !== DEFAULT_SHOT_NEGATIVE_PROMPT) && (
        <button type="button" className="shot-draw-reset-btn" onClick={() => { setPositiveOverride(""); setNegativePrompt(DEFAULT_SHOT_NEGATIVE_PROMPT); setFrameReframeMode(DEFAULT_FRAME_REFRAME_MODE); setPromptProvenance("AI_GENERATED"); }}>
          恢复默认提示词
        </button>
      )}
      <details className="shot-draw-debug-details">
        <summary><span>查看底模编译草稿与计划证据</span></summary>
        <div className="shot-draw-prompt-preview" aria-label="输入草稿预览">
          <span>输入草稿预览（未验证）</span>
          <small>这里仅显示页面输入草稿；服务端验证计划后才会显示实际下发的正向/反向语义输入。</small>
          <label>正向草稿</label>
          <pre>{prompt || "（缺少 AI 基础提示词）"}</pre>
          <label>反向草稿</label>
          <pre>{negativePrompt || "（未填写反向约束）"}</pre>
        </div>
        {frameReframeMode === "SINGLE_MOMENT" && <div className="shot-draw-frame-previews" aria-label="首尾帧输入草稿预览">
          <span>首尾帧输入草稿预览（未验证）</span>
          <small>首尾帧分别收窄到镜头事实的第一个/最后一个视觉瞬间；点击“验证生成计划”后以服务端快照为准。</small>
          <label>首帧</label>
          <pre>{compileFramePromptPreview(fields, shotCode, "FIRST_FRAME", positiveOverride, negativePrompt)}</pre>
          <label>尾帧</label>
          <pre>{compileFramePromptPreview(fields, shotCode, "END_FRAME", positiveOverride, negativePrompt)}</pre>
        </div>}
        {plannedFrameDraw && <div className="shot-draw-plan-evidence" aria-label="服务端生成计划">
          <span>服务端生成计划（只读证据）</span>
          <small>{plannedFrameIsFresh ? "计划与当前表单一致，可继续核对后提交。" : "当前表单已变化；该计划失效，请重新验证。"}</small>
          <dl>
            <div><dt>状态</dt><dd>{plannedFrameDraw.plan.valid && planHasExecutableContract(plannedFrameDraw.plan) ? "READY" : "BLOCKED"}</dd></div>
            <div><dt>契约来源</dt><dd>{String(plannedContract?.source ?? "未解析")}</dd></div>
            <div><dt>契约 ID</dt><dd>{String(plannedContract?.contract_id ?? "未绑定")}</dd></div>
            <div><dt>运行环境版本</dt><dd>{String(plannedContract?.runtime_environment_version_id ?? "未绑定")}</dd></div>
            <div><dt>编译模式</dt><dd>{String(plannedContract?.compiler_mode ?? "未解析")}</dd></div>
          </dl>
          {plannedFrameItem && <>
            {!!plannedFrameItem.identity_inputs?.references?.length && <div aria-label="实际人物参考输入">
              <strong>将传给模型的人物参考图</strong>
              {plannedFrameItem.identity_inputs.references.map((ref) => <p key={ref.role}>{ref.character_name} · {ref.role} · 已批准身份包正面图</p>)}
            </div>}
            <label>服务端 PROMPT（{plannedFrameItem.frame_role}）</label>
            <pre>{String(plannedFrameItem.semantic_inputs?.PROMPT ?? "")}</pre>
            <label>服务端 NEGATIVE_PROMPT</label>
            <pre>{String(plannedFrameItem.semantic_inputs?.NEGATIVE_PROMPT ?? "未绑定")}</pre>
            <label>服务端 SEED</label>
            <pre>{plannedFrameItem.semantic_inputs?.SEED == null ? "显式 seed 将在提交时冻结" : String(plannedFrameItem.semantic_inputs?.SEED)}</pre>
          </>}
          <label>语义绑定</label>
          <pre>{Object.entries(plannedContract?.workflow_bindings ?? {}).map(([role, binding]) => `${role} → ${String(binding.node_id ?? "?")}.${String(binding.input ?? "?")}`).join("\n") || "未绑定"}</pre>
          {!plannedFrameDraw.plan.valid && <p className="shot-draw-error">{plannedFrameDraw.plan.issues.map((item) => item.message).join("；") || "生成计划存在阻塞"}</p>}
        </div>}
      </details>
    </div>
    <div className="shot-draw-frames" aria-label="本镜首尾帧状态">
      <div className={firstFrame ? "ready" : "missing"}><span>首帧</span><strong>{firstFrame ? "已批准" : "待生成"}</strong></div>
      <span aria-hidden="true">→</span>
      <div className={endFrame ? "ready" : "inferred"}><span>尾帧</span><strong>{endFrame ? (supportsEndFrame ? "已接入" : "连续性参考") : "AI 推演"}</strong></div>
    </div>
    <div className="shot-draw-frame-options" aria-label="关键帧生成数量">
      <label>帧策略<select aria-label="关键帧策略" value={frameStrategy} disabled={busy} onChange={(event) => setFrameStrategy(event.target.value as ShotFrameStrategy)}><option value="FIRST_ONLY">只生成首帧</option><option value="FIRST_AND_LAST">生成首帧和尾帧</option></select></label>
      <label>每种帧的候选数<select aria-label="每种帧候选数" value={candidateCount} disabled={busy} onChange={(event) => setCandidateCount(Number(event.target.value))}>{[1, 2, 3, 4].map((count) => <option key={count} value={count}>{count} 张</option>)}</select></label>
      <p>本次共生成 {candidateCount * (frameStrategy === "FIRST_AND_LAST" ? 2 : 1)} 张图片。局部修正可只生成 1 张首帧，审核后用于图生视频。</p>
    </div>
    <div className="shot-draw-actions-sticky" aria-label="镜头生成操作">
      <div className="shot-draw-action">
        <button type="button" className="director-button secondary wide" disabled={busy} onClick={() => framePlan.mutate()}>{framePlan.isPending ? "正在验证生成计划…" : "验证生成计划"}</button>
        <button type="button" className="director-button primary wide" disabled={!framePlanCanSubmit || busy} onClick={() => frameDraw.mutate()}>{frameDraw.isPending ? "正在重新生成首尾帧…" : "重新生成首尾帧"}</button>
        <p>先验证服务端计划，再按所选帧策略和数量生成候选并逐图审核；已有工作帧保留。</p><Link to={reviewHref}>查看或替换推荐结果</Link>
      </div>
      {firstFrame ? <div className="shot-draw-action">
        {!canGenerate && (
          <button
            type="button"
            className="director-button secondary wide"
            disabled={busy}
            onClick={() => readyMutation.mutate()}
          >
            {readyMutation.isPending ? "正在标记就绪…" : "标记镜头就绪并允许生成"}
          </button>
        )}
        <button type="button" className="director-button primary wide" disabled={!canGenerate || busy} onClick={() => videoDraw.mutate()}>{videoDraw.isPending ? "正在生成候选…" : "生成视频候选"}</button>
        <p>页面正/反提示词会随本次提交冻结；视频只能使用已通过人工审核且技术完整性合格的首帧。</p>
      </div> : null}
    </div>
    {actionFeedback && <p className={`shot-draw-feedback ${actionFeedback.kind}`} role={actionFeedback.kind === "error" ? "alert" : "status"} aria-live="polite">{actionFeedback.message}</p>}
    {!actionFeedback && error && <p className="shot-draw-error" role="alert">{errorText(error)}</p>}
    {!canGenerate && <p className="shot-draw-error">当前镜头尚未达到可生成状态。</p>}
  </section>;
}

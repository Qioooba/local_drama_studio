import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  createShotGenerationIntentV2,
  preflightShotGenerationV2,
  submitShotGenerationV2,
  type ShotBaseGenerationPreflightCommand,
  type ShotGenerationPreflight,
  type ShotStudio,
} from "../../generated/api";
import { composePromptFromIntent, deriveShotSeed } from "../generation/generationDefaults";

type CurrentShot = ShotStudio["current_shot"];
type ApprovedFrame = { media_version_id: string; take_no?: number | null };

interface ShotGenerationInspectorProps {
  shotId: string;
  shotCode: string;
  shotRevision: number;
  fields: Record<string, unknown>;
  currentShot: CurrentShot;
  canGenerate: boolean;
  reviewHref: string;
  onOpenKeyframePicker: () => void;
  onSubmitted: (message: string) => Promise<void> | void;
}

function commandKey(prefix: string): string {
  return globalThis.crypto?.randomUUID?.() ?? `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function bytesLabel(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未声明";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  if (value >= 1024 ** 2) return `${Math.round(value / 1024 ** 2)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KB`;
  return `${Math.round(value)} 字节`;
}

function secondsLabel(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 10) / 10} 秒` : "未声明";
}

export function ShotGenerationInspector({
  shotId,
  shotCode,
  shotRevision,
  fields,
  currentShot,
  canGenerate,
  reviewHref,
  onOpenKeyframePicker,
  onSubmitted,
}: ShotGenerationInspectorProps) {
  const videoResolution = currentShot.generation_preferences.resolutions.find((item) => item.capability === "VIDEO_I2V");
  const profileVersionId = videoResolution?.profile_version_id ?? "";
  const approvedFrames = useMemo(() => {
    const eligible = (candidate: CurrentShot["current_media"] | CurrentShot["candidates"][number]) => Boolean(
      candidate
      && candidate.media_kind === "IMAGE"
      && candidate.stage === "KEYFRAME"
      && candidate.approved
      && !candidate.is_stale
      && candidate.integrity_status === "VERIFIED",
    );
    const frames: ApprovedFrame[] = currentShot.candidates.filter((candidate) => eligible(candidate));
    const currentMedia = currentShot.current_media;
    if (eligible(currentMedia) && currentMedia && !frames.some((item) => item.media_version_id === currentMedia.media_version_id)) {
      frames.unshift(currentMedia);
    }
    return frames;
  }, [currentShot.candidates, currentShot.current_media]);
  const [sourceMediaVersionId, setSourceMediaVersionId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [seed, setSeed] = useState("");
  const [prepared, setPrepared] = useState<{ command: ShotBaseGenerationPreflightCommand; plan: ShotGenerationPreflight; submitKey: string } | null>(null);

  useEffect(() => {
    setPrompt(composePromptFromIntent(fields, shotCode));
    setSeed(String(deriveShotSeed(shotId)));
    setPrepared(null);
  }, [fields, shotCode, shotId]);

  useEffect(() => {
    if (!approvedFrames.some((item) => item.media_version_id === sourceMediaVersionId)) {
      setSourceMediaVersionId(approvedFrames[0]?.media_version_id ?? "");
    }
  }, [approvedFrames, sourceMediaVersionId]);

  const invalidatePlan = () => setPrepared(null);
  const preflightMutation = useMutation({
    mutationFn: async () => {
      const normalizedPrompt = prompt.trim();
      const explicitSeed = Number(seed);
      if (!normalizedPrompt) throw new Error("请填写本镜生成描述");
      if (!Number.isInteger(explicitSeed)) throw new Error("seed 必须是整数");
      if (!profileVersionId) throw new Error(videoResolution?.blocked_reason || "项目尚未配置可用的视频生成能力");
      if (!sourceMediaVersionId) throw new Error("图片生成视频需要一张已批准、已验证的关键帧");
      const matchingIntent = currentShot.generation_intents.find((item) => (
        item.purpose === "I2V_PROXY" && item.creative_goal === normalizedPrompt
      ));
      const intent = matchingIntent ?? (await createShotGenerationIntentV2(shotId, {
        purpose: "I2V_PROXY",
        creative_goal: normalizedPrompt,
        idempotency_key: commandKey("shot-intent"),
      })).intent;
      const cameraPlan = fields.camera_plan && typeof fields.camera_plan === "object"
        ? fields.camera_plan as Record<string, unknown>
        : null;
      const command: ShotBaseGenerationPreflightCommand = {
        operation: "BASE",
        stage_code: "VIDEO",
        intent_id: intent.id,
        variant_type: "BASE",
        parent_variant_id: null,
        branch_reason: "SHOT_STUDIO_BASE",
        profile_version_id: profileVersionId,
        parameter_set: {
          PROMPT: normalizedPrompt,
          SEED: explicitSeed,
          ...(cameraPlan ? { camera_plan: cameraPlan } : {}),
        },
        seed_policy: "EXPLICIT",
        explicit_seed: explicitSeed,
        bindings: [{ role: "FIRST_FRAME", media_version_id: sourceMediaVersionId, ordinal: 0 }],
        expected_shot_revision: shotRevision,
      };
      const { preflight } = await preflightShotGenerationV2(shotId, command);
      return { command, plan: preflight, submitKey: commandKey("shot-base") };
    },
    onSuccess: setPrepared,
  });
  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!prepared) throw new Error("请先完成当前输入的预检");
      if (prepared.plan.status !== "READY") throw new Error("预检仍有阻塞项，不能提交");
      return submitShotGenerationV2(shotId, {
        ...prepared.command,
        plan_hash: prepared.plan.plan_hash,
        idempotency_key: prepared.submitKey,
      });
    },
    onSuccess: async (result) => {
      await onSubmitted(`首个视频候选已排队（任务 ${result.job.id}）。`);
      setPrepared(null);
    },
  });
  const error = preflightMutation.error ?? submitMutation.error;
  const perTake = prepared?.plan.resource_estimate?.per_take;
  const diskGate = prepared?.plan.disk_gate;

  return <div className="shot-generation-form">
    <div className="director-section-head">
      <strong>本镜视频候选</strong>
      <span>{videoResolution?.profile?.title ?? videoResolution?.profile?.code ?? "未配置生成能力"}</span>
    </div>
    <label>
      <span>已批准首帧</span>
      <select value={sourceMediaVersionId} onChange={(event) => { setSourceMediaVersionId(event.target.value); invalidatePlan(); }}>
        <option value="">请选择</option>
        {approvedFrames.map((candidate, index) => <option key={candidate.media_version_id} value={candidate.media_version_id}>关键帧候选 {candidate.take_no ?? index + 1}</option>)}
      </select>
    </label>
    {approvedFrames.length === 0 && <div className="shot-generation-missing">
      <strong>缺少已批准关键帧</strong>
      <span>先创建关键帧候选，再到正式审核批准；工作采用不能代替批准。</span>
      <div><button type="button" className="director-button ghost" onClick={onOpenKeyframePicker}>从项目图片创建关键帧候选</button><Link className="director-button ghost" to={reviewHref}>前往审核</Link></div>
    </div>}
    <label>
      <span>镜头生成描述</span>
      <textarea rows={5} value={prompt} onChange={(event) => { setPrompt(event.target.value); invalidatePlan(); }} placeholder="描述主体动作、情绪、构图和运镜" />
    </label>
    <label>
      <span>可复现 seed</span>
      <input inputMode="numeric" value={seed} onChange={(event) => { setSeed(event.target.value); invalidatePlan(); }} />
    </label>
    <div className="shot-generation-actions">
      <button type="button" className="director-button ghost" disabled={!canGenerate || preflightMutation.isPending || submitMutation.isPending} onClick={() => preflightMutation.mutate()}>
        {preflightMutation.isPending ? "检查中…" : "检查生成方案"}
      </button>
      <button type="button" className="director-button primary" disabled={prepared?.plan.status !== "READY" || submitMutation.isPending} onClick={() => submitMutation.mutate()}>
        {submitMutation.isPending ? "正在入队…" : "确认并生成"}
      </button>
    </div>
    {prepared && <section className={`shot-generation-preflight ${prepared.plan.status === "READY" ? "ready" : "blocked"}`} aria-live="polite">
      <header><strong>{prepared.plan.status === "READY" ? "方案可以提交" : "方案仍有阻塞"}</strong><span>预检不会创建候选或任务</span></header>
      <dl>
        <div><dt>预计耗时</dt><dd>{secondsLabel(perTake?.duration_seconds)}</dd></div>
        <div><dt>预计显存</dt><dd>{bytesLabel(perTake?.vram_bytes)}</dd></div>
        <div><dt>预计磁盘</dt><dd>{bytesLabel(perTake?.disk_bytes)}</dd></div>
        <div><dt>磁盘空间</dt><dd>{diskGate?.blocking === true ? "不足" : "充足"}</dd></div>
      </dl>
      {prepared.plan.blockers?.length ? <ul>{prepared.plan.blockers.map((item, index) => <li key={`${String(item.code ?? "blocker")}-${index}`}>{String(item.message ?? item.code ?? "生成条件未满足")}</li>)}</ul> : null}
    </section>}
    {error && <p className="director-error" role="alert">{error instanceof Error ? error.message : String(error)}</p>}
    {!canGenerate && <p className="director-help">当前镜头尚未标记为可生成，或项目生成能力仍有阻塞。</p>}
    <p className="director-help">运行失败请到任务详情重试；创意重抽会从已有候选创建新分支，不覆盖当前结果。</p>
  </div>;
}

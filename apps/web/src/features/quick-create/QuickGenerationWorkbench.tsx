import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { getProfileVersion, type GenerationModel, type GenerationModelRoute } from "../../generated/api";
import {
  getModelPlatformQuickCreateV2DirectImageStatus,
  getModelPlatformQuickCreateV2Run,
  listModelPlatformQuickCreateV2Readiness,
  previewModelPlatformQuickCreateV2DirectImage,
  previewModelPlatformQuickCreateV2ImageCandidates,
  previewModelPlatformQuickCreateV2ImageToVideo,
  selectModelPlatformQuickCreateV2ImageCandidate,
  submitModelPlatformQuickCreateV2DirectImage,
  submitModelPlatformQuickCreateV2ImageCandidates,
  submitModelPlatformQuickCreateV2ImageToVideo,
  type ModelPlatformQuickCreateV2DirectImagePreview,
  type ModelPlatformQuickCreateV2DirectImageStatus,
  type ModelPlatformQuickCreateV2CandidatePlan,
  type ModelPlatformQuickCreateV2ImageToVideoPreview,
  type ModelPlatformQuickCreateV2Readiness,
  type ModelPlatformQuickCreateV2Run,
} from "../model-platform-v2/api";
import { QuickGenerationModelSettings } from "./QuickGenerationModelSettings";
import { LocalArtifactReference } from "../shared/LocalArtifactReference";
import {
  cancelQuickGeneration,
  commitQuickGeneration,
  getQuickGeneration,
  listQuickGenerations,
  planQuickGeneration,
  regenerateQuickGenerationPrompt,
  rerollQuickGenerationImages,
  resumeQuickGeneration,
  retryQuickGeneration,
  selectQuickGenerationCandidate,
  type QuickGenerationCandidate,
  type QuickGenerationMode,
  type QuickGenerationParameters,
  type QuickGenerationPlan,
  type QuickGenerationRun,
} from "./quickGenerationClient";

type Props = { models: GenerationModel[] };
const TERMINAL_STATES = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
const STATE_LABELS: Record<string, string> = {
  PLANNING: "正在生成规划", PLANNED: "等待确认规划", COMMITTING: "正在提交任务", GENERATING: "正在生成",
  AWAITING_SELECTION: "等待选择图片", CANCELLING: "正在取消", SUCCEEDED: "已完成", FAILED: "需要处理",
  CANCELLED: "已取消", QUEUED: "排队中", CLAIMED: "准备中", RUNNING: "生成中", READY: "可选择",
  PROCESSING: "正在准备预览",
};
const PARAMETER_LABELS: Record<string, string> = {
  temperature: "创造性", top_p: "候选词范围", max_tokens: "最大输出长度",
  width: "宽度", height: "高度", frame_count: "帧数", fps: "帧率",
  steps: "采样步数", sigma_points: "生成步数", cfg: "CFG", sampler_name: "采样器",
  scheduler: "调度器", denoise: "降噪强度", production_tier: "生产档位",
  acceleration: "加速模式", lora_strength: "LoRA 强度", native_audio: "原生音频",
};

function executableRoutes(models: GenerationModel[], action: GenerationModelRoute["action"]) {
  return models.flatMap((model) => model.routes.filter((route) => route.action === action && route.executable));
}

function routeIsExecutable(models: GenerationModel[], action: GenerationModelRoute["action"], profileVersionId: string) {
  return models.some((model) => model.routes.some((route) => route.action === action && route.profile_version_id === profileVersionId && route.executable));
}

function usesImage(mode: QuickGenerationMode) {
  return mode === "TEXT_TO_IMAGE" || mode === "TEXT_TO_IMAGE_TO_VIDEO";
}

function usesVideo(mode: QuickGenerationMode) {
  return mode === "TEXT_TO_VIDEO" || mode === "TEXT_TO_IMAGE_TO_VIDEO";
}

function routeLabel(mode: QuickGenerationMode) {
  if (mode === "TEXT_TO_IMAGE") return "文生图";
  if (mode === "TEXT_TO_VIDEO") return "文生视频";
  return "文生图 → 图生视频";
}

function isRemoteProfile(profileVersion: Awaited<ReturnType<typeof getProfileVersion>>["profile_version"] | null) {
  if (!profileVersion) return false;
  const execution = profileVersion.execution as Record<string, unknown> | null;
  const contract = profileVersion.capability_contract as Record<string, unknown> | null;
  const runtime = execution?.runtime as Record<string, unknown> | undefined;
  const provider = String(execution?.provider ?? contract?.provider ?? "").toUpperCase();
  const baseUrl = String(runtime?.base_url ?? contract?.base_url ?? "");
  return provider !== "OLLAMA" && provider !== "OLLAMA_LOOPBACK"
    && !/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::|\/|$)/i.test(baseUrl);
}

const newCommandKey = () => globalThis.crypto?.randomUUID?.() ?? `one-sentence-${Date.now()}-${Math.random().toString(16).slice(2)}`;

function describeFailure(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error);
  if (/failed to fetch/i.test(raw)) return "无法连接本机服务：请确认 API 与本机 Worker 正在运行，然后重试。";
  if (/HTTP 5\d\d/.test(raw)) return `本机服务暂时不可用（${raw}）。任务检查点已保留，可直接重试。`;
  return raw;
}

function normalizeProgress(percent: unknown) {
  if (typeof percent !== "number" || !Number.isFinite(percent)) return null;
  const scaled = percent > 0 && percent <= 1 ? percent * 100 : percent;
  return Math.max(0, Math.min(100, Math.round(scaled)));
}

function progressOf(candidate: QuickGenerationCandidate) {
  return normalizeProgress(candidate.job?.progress?.percent);
}

function stepIndex(run: QuickGenerationRun | null, hasPlan: boolean, mode: QuickGenerationMode) {
  if (!run) return hasPlan ? 1 : 0;
  if (run.state === "PLANNED") return 1;
  if (mode === "TEXT_TO_VIDEO") {
    if (run.state === "SUCCEEDED") return 4;
    return run.job_id ? 3 : 2;
  }
  if (mode === "TEXT_TO_IMAGE") {
    if (run.state === "SUCCEEDED") return 4;
    if (run.state === "AWAITING_SELECTION") return 3;
    return run.candidates.length ? 2 : 1;
  }
  if (run.state === "SUCCEEDED") return 5;
  if (run.selected_candidate_id || run.stage === "VIDEO_GENERATING") return 4;
  if (run.state === "AWAITING_SELECTION") return 3;
  return run.candidates.length ? 2 : 1;
}

function PlanConfirmation({ run, plan, busy, onCommit, onAbandon, onRegenerate }: { run: QuickGenerationRun; plan: QuickGenerationPlan; busy: string; onCommit: () => void; onAbandon: () => void; onRegenerate: (target: "KEYFRAME" | "VIDEO") => void }) {
  const parameterCount = Object.values(plan.model_parameters ?? {}).reduce((total, item) => total + Object.keys(item).length, 0);
  return <section className="one-sentence-video-confirmation" aria-labelledby="one-sentence-confirm-title">
    <div><p className="eyebrow">只读预检完成</p><h4 id="one-sentence-confirm-title">{plan.video_plan.title}</h4></div>
    <dl className="one-sentence-video-specs">
      {plan.image_spec && <div><dt>{plan.result_kind === "IMAGE" ? "图片候选" : "首帧候选"}</dt><dd>{plan.image_candidate_count} 张 · {plan.image_spec.width}×{plan.image_spec.height}</dd></div>}
      <div><dt>{plan.result_kind === "IMAGE" ? "图片输出" : "视频输出"}</dt><dd>{plan.output_spec.width}×{plan.output_spec.height}{"fps" in plan.output_spec ? ` · ${plan.output_spec.fps} fps` : ""}</dd></div>
      {"duration_seconds" in plan.output_spec && <div><dt>时长</dt><dd>{plan.output_spec.duration_seconds} 秒 · {plan.output_spec.frame_count} 帧</dd></div>}
      <div><dt>生成路线</dt><dd>{routeLabel(plan.mode)}</dd></div>
      <div><dt>本次参数</dt><dd>{parameterCount ? `自定义 ${parameterCount} 项` : "全部使用模型默认"}</dd></div>
    </dl>
    {plan.image_spec && <div className="one-sentence-video-prompt-preview"><div className="one-sentence-video-prompt-heading"><strong>{plan.result_kind === "IMAGE" ? "图片提示词" : "首帧提示词"}</strong><button type="button" className="secondary" aria-label="重新生成图片提示词" disabled={Boolean(busy)} onClick={() => onRegenerate("KEYFRAME")}>{busy === "regenerate-keyframe" ? "正在重新生成…" : "重新生成"}</button></div><p>{plan.video_plan.keyframe_prompt}</p><small>文生图执行提示词使用 English，以匹配当前图片模型。</small></div>}
    {plan.video && <div className="one-sentence-video-prompt-preview"><div className="one-sentence-video-prompt-heading"><strong>视频提示词</strong><button type="button" className="secondary" aria-label="重新生成视频提示词" disabled={Boolean(busy)} onClick={() => onRegenerate("VIDEO")}>{busy === "regenerate-video" ? "正在重新生成…" : "重新生成"}</button></div><p>{plan.video_plan.video_prompt}</p></div>}
    {parameterCount > 0 && <details className="quick-plan-parameters"><summary>核对本次模型参数（{parameterCount} 项）</summary>{(["llm", "image", "video"] as const).map((stage) => Object.keys(plan.model_parameters?.[stage] ?? {}).length ? <section key={stage}><strong>{stage === "llm" ? "文字模型" : stage === "image" ? "图片模型" : "视频模型"}</strong><dl>{Object.entries(plan.model_parameters[stage]).map(([key, value]) => <div key={key}><dt>{PARAMETER_LABELS[key] ?? key}</dt><dd>{String(value)}</dd></div>)}</dl></section> : null)}</details>}
    <p className="muted">确认后只创建本次快速生成任务和独立作品记录，不会创建项目、分集或镜头。</p>
    {run.state === "PLANNED" && <div className="one-sentence-video-actions"><button type="button" disabled={Boolean(busy)} onClick={onCommit}>{busy === "commit" ? "正在提交…" : usesImage(plan.mode) ? `确认规划并生成${plan.result_kind === "IMAGE" ? "图片" : "首帧"}候选` : "确认规划并生成视频"}</button><button type="button" className="secondary" disabled={Boolean(busy)} onClick={onAbandon}>放弃此规划</button></div>}
  </section>;
}

function CandidateCard({ candidate, selected, locked, onSelect }: { candidate: QuickGenerationCandidate; selected: boolean; locked: boolean; onSelect: () => void }) {
  const progress = progressOf(candidate);
  const ready = candidate.state === "READY" && Boolean(candidate.output?.content_url);
  const displayOrdinal = candidate.ordinal + 1;
  return <article className={`one-sentence-image-card${selected ? " selected" : ""}${candidate.state === "FAILED" ? " failed" : ""}`}>
    <button type="button" className="one-sentence-image-choice" aria-pressed={selected} disabled={!ready || locked} onClick={onSelect}>
      <span className="one-sentence-image-frame">
        {ready && candidate.output
          ? <img src={candidate.output.thumbnail_url} alt={`图片候选 ${displayOrdinal}`} loading="lazy" decoding="async" />
          : <span className="one-sentence-image-placeholder" aria-hidden="true"><span>{progress === null ? "…" : `${progress}%`}</span></span>}
        {selected && <span className="one-sentence-image-selected-mark">已选中</span>}
      </span>
      <span className="one-sentence-image-meta"><strong>候选 {displayOrdinal}</strong><small>{STATE_LABELS[candidate.state] ?? candidate.state} · Seed {candidate.seed}</small></span>
    </button>
    {candidate.error?.message && <p className="inline-error">{candidate.error.message}</p>}
  </article>;
}

export function QuickGenerationWorkbench({ models }: Props) {
  const [v2Readiness, setV2Readiness] = useState<ModelPlatformQuickCreateV2Readiness[] | null>(null);
  const [v2DirectPreview, setV2DirectPreview] = useState<ModelPlatformQuickCreateV2DirectImagePreview | null>(null);
  const [v2DirectJobId, setV2DirectJobId] = useState<string | null>(null);
  const [v2DirectStatus, setV2DirectStatus] = useState<ModelPlatformQuickCreateV2DirectImageStatus | null>(null);
  const [v2DirectBusy, setV2DirectBusy] = useState(false);
  const [v2DirectError, setV2DirectError] = useState("");
  const [v2I2VPlan, setV2I2VPlan] = useState<ModelPlatformQuickCreateV2CandidatePlan | null>(null);
  const [v2I2VRun, setV2I2VRun] = useState<ModelPlatformQuickCreateV2Run | null>(null);
  const [v2I2VPreview, setV2I2VPreview] = useState<ModelPlatformQuickCreateV2ImageToVideoPreview | null>(null);
  const [v2I2VSelectedStepId, setV2I2VSelectedStepId] = useState("");
  const [v2I2VSelectionConfirmed, setV2I2VSelectionConfirmed] = useState(false);
  const [v2I2VBusy, setV2I2VBusy] = useState("");
  const [v2I2VError, setV2I2VError] = useState("");
  useEffect(() => { void listModelPlatformQuickCreateV2Readiness().then((result) => setV2Readiness(result.items)).catch(() => setV2Readiness(null)); }, []);
  useEffect(() => {
    if (!v2DirectJobId) { setV2DirectStatus(null); return undefined; }
    let disposed = false;
    const refresh = async () => {
      try {
        const result = await getModelPlatformQuickCreateV2DirectImageStatus(v2DirectJobId);
        if (!disposed) setV2DirectStatus(result.execution);
      } catch (error) {
        if (!disposed) setV2DirectError(describeFailure(error));
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [v2DirectJobId]);
  useEffect(() => {
    if (!v2I2VRun || ["SUCCEEDED", "FAILED", "CANCELLED"].includes(v2I2VRun.state)) return undefined;
    let disposed = false;
    const refresh = async () => {
      try {
        const result = await getModelPlatformQuickCreateV2Run(v2I2VRun.id);
        if (!disposed) setV2I2VRun(result.run);
      } catch (error) {
        if (!disposed) setV2I2VError(describeFailure(error));
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [v2I2VRun?.id, v2I2VRun?.state]);
  const llmRoutes = useMemo(() => executableRoutes(models, "TEXT_PLANNING"), [models]);
  const imageRoutes = useMemo(() => executableRoutes(models, "TEXT_TO_IMAGE"), [models]);
  const t2vRoutes = useMemo(() => executableRoutes(models, "TEXT_TO_VIDEO"), [models]);
  const i2vRoutes = useMemo(() => executableRoutes(models, "IMAGE_TO_VIDEO"), [models]);
  const imageAvailable = imageRoutes.length > 0;
  const directAvailable = t2vRoutes.length > 0;
  const imageFirstAvailable = imageAvailable && i2vRoutes.length > 0;
  const [story, setStory] = useState("");
  const [language, setLanguage] = useState<"zh-CN" | "en-US">("zh-CN");
  const [mode, setMode] = useState<QuickGenerationMode>(() => imageFirstAvailable ? "TEXT_TO_IMAGE_TO_VIDEO" : imageAvailable ? "TEXT_TO_IMAGE" : "TEXT_TO_VIDEO");
  const [llmId, setLlmId] = useState("");
  const [imageId, setImageId] = useState("");
  const [videoId, setVideoId] = useState("");
  const [llmParameters, setLlmParameters] = useState<QuickGenerationParameters>({});
  const [imageParameters, setImageParameters] = useState<QuickGenerationParameters>({});
  const [videoParameters, setVideoParameters] = useState<QuickGenerationParameters>({});
  const [candidateCount, setCandidateCount] = useState(4);
  const [remote, setRemote] = useState(false);
  const [remoteConfirmed, setRemoteConfirmed] = useState(false);
  const [run, setRun] = useState<QuickGenerationRun | null>(null);
  const [recentRuns, setRecentRuns] = useState<QuickGenerationRun[]>([]);
  const [chosenCandidateId, setChosenCandidateId] = useState("");
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [busy, setBusy] = useState("");
  const [observing, setObserving] = useState(true);
  const [elapsedSeconds, setElapsedSeconds] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const acceptRun = useCallback((nextRun: QuickGenerationRun) => {
    setRun(nextRun);
    setRecentRuns((items) => [nextRun, ...items.filter((item) => item.id !== nextRun.id)].slice(0, 8));
  }, []);

  useEffect(() => {
    setLlmId((current) => llmRoutes.some((route) => route.profile_version_id === current) ? current : llmRoutes[0]?.profile_version_id || "");
    setImageId((current) => imageRoutes.some((route) => route.profile_version_id === current) ? current : imageRoutes[0]?.profile_version_id || "");
  }, [imageRoutes, llmRoutes]);

  useEffect(() => {
    const choices = mode === "TEXT_TO_VIDEO" ? t2vRoutes : mode === "TEXT_TO_IMAGE_TO_VIDEO" ? i2vRoutes : [];
    setVideoId((current) => choices.some((route) => route.profile_version_id === current) ? current : choices[0]?.profile_version_id || "");
  }, [i2vRoutes, mode, t2vRoutes]);

  useEffect(() => {
    let active = true;
    setRemoteConfirmed(false);
    if (!llmId) { setRemote(false); return () => { active = false; }; }
    void getProfileVersion(llmId).then((result) => { if (active) setRemote(isRemoteProfile(result.profile_version)); }).catch(() => { if (active) setRemote(false); });
    return () => { active = false; };
  }, [llmId]);

  useEffect(() => {
    const controller = new AbortController();
    void listQuickGenerations(12, controller.signal).then((result) => setRecentRuns(result.items)).catch((error: unknown) => { if (!(error instanceof DOMException && error.name === "AbortError")) setRecentRuns([]); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!run || !observing || TERMINAL_STATES.has(run.state) || run.state === "PLANNED" || run.state === "AWAITING_SELECTION") return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void getQuickGeneration(run.id, controller.signal).then((result) => acceptRun(result.run)).catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) setMessage(`刷新执行状态失败：${describeFailure(error)}`);
      });
    }, 1500);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [acceptRun, observing, run?.id, run?.state]);

  useEffect(() => { if (run?.selected_candidate_id) setChosenCandidateId(run.selected_candidate_id); }, [run?.id, run?.selected_candidate_id]);
  // Local video generation offers no provider progress; expose elapsed time so
  // the wait is legible instead of a frozen "正在生成" label.
  useEffect(() => {
    if (run && (run.job_id || run.candidates.length) && !TERMINAL_STATES.has(run.state) && run.state !== "AWAITING_SELECTION" && run.state !== "PLANNED") {
      const startedAt = Date.now();
      setElapsedSeconds(0);
      const timer = window.setInterval(() => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000);
      return () => window.clearInterval(timer);
    }
    setElapsedSeconds(null);
    return undefined;
  }, [run?.id, run?.state, run?.job_id, run?.candidates.length]);
  useEffect(() => () => controllerRef.current?.abort(), []);

  async function execute(label: string, action: (signal: AbortSignal) => Promise<{ run: QuickGenerationRun }>) {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(label); setMessage("");
    try {
      const result = await action(controller.signal);
      acceptRun(result.run); setObserving(true);
      return result.run;
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) setMessage(describeFailure(error));
      return null;
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      setBusy("");
    }
  }

  async function previewV2DirectImage() {
    if (story.trim().length < 2) return;
    setV2DirectBusy(true); setV2DirectError(""); setV2DirectJobId(null); setV2DirectStatus(null);
    try {
      const result = await previewModelPlatformQuickCreateV2DirectImage({ prompt: story.trim(), run_overrides: {} });
      setV2DirectPreview(result.preview);
    } catch (error) {
      setV2DirectPreview(null);
      setV2DirectError(describeFailure(error));
    } finally { setV2DirectBusy(false); }
  }

  async function submitV2DirectImage() {
    if (!v2DirectPreview?.executable || story.trim().length < 2) return;
    setV2DirectBusy(true); setV2DirectError("");
    try {
      const result = await submitModelPlatformQuickCreateV2DirectImage(
        { prompt: story.trim(), expected_resolution_hash: v2DirectPreview.resolution_hash, run_overrides: {} },
        newCommandKey(),
      );
      setV2DirectJobId(result.execution.job_id);
    } catch (error) { setV2DirectError(describeFailure(error)); }
    finally { setV2DirectBusy(false); }
  }

  async function previewV2I2VCandidates() {
    if (story.trim().length < 2) return;
    setV2I2VBusy("candidate-preview"); setV2I2VError(""); setV2I2VRun(null); setV2I2VPreview(null); setV2I2VSelectedStepId(""); setV2I2VSelectionConfirmed(false);
    try {
      const result = await previewModelPlatformQuickCreateV2ImageCandidates({ prompt: story.trim(), candidate_count: candidateCount });
      setV2I2VPlan(result.plan);
    } catch (error) { setV2I2VPlan(null); setV2I2VError(describeFailure(error)); }
    finally { setV2I2VBusy(""); }
  }

  async function submitV2I2VCandidates() {
    if (!v2I2VPlan?.executable || story.trim().length < 2) return;
    setV2I2VBusy("candidate-submit"); setV2I2VError("");
    try {
      const result = await submitModelPlatformQuickCreateV2ImageCandidates({ prompt: story.trim(), candidates: v2I2VPlan.candidates }, newCommandKey());
      const aggregate = await getModelPlatformQuickCreateV2Run(result.run.id);
      setV2I2VRun(aggregate.run);
    } catch (error) { setV2I2VError(describeFailure(error)); }
    finally { setV2I2VBusy(""); }
  }

  async function selectV2I2VCandidate() {
    if (!v2I2VRun || !v2I2VSelectedStepId || !v2I2VSelectionConfirmed) return;
    setV2I2VBusy("candidate-select"); setV2I2VError("");
    try {
      const result = await selectModelPlatformQuickCreateV2ImageCandidate(v2I2VRun.id, v2I2VSelectedStepId);
      setV2I2VRun(result.run); setV2I2VPreview(null);
    } catch (error) { setV2I2VError(describeFailure(error)); }
    finally { setV2I2VBusy(""); }
  }

  async function previewV2I2VVideo() {
    if (!v2I2VRun) return;
    setV2I2VBusy("video-preview"); setV2I2VError("");
    try {
      const result = await previewModelPlatformQuickCreateV2ImageToVideo(v2I2VRun.id);
      setV2I2VPreview(result.preview);
    } catch (error) { setV2I2VError(describeFailure(error)); }
    finally { setV2I2VBusy(""); }
  }

  async function submitV2I2VVideo() {
    if (!v2I2VRun || !v2I2VPreview?.executable) return;
    setV2I2VBusy("video-submit"); setV2I2VError("");
    try {
      await submitModelPlatformQuickCreateV2ImageToVideo(v2I2VRun.id, v2I2VPreview.resolution_hash, newCommandKey());
      const aggregate = await getModelPlatformQuickCreateV2Run(v2I2VRun.id);
      setV2I2VRun(aggregate.run);
    } catch (error) { setV2I2VError(describeFailure(error)); }
    finally { setV2I2VBusy(""); }
  }

  function changeMode(nextMode: QuickGenerationMode) {
    if (nextMode === mode) return;
    setMode(nextMode); setVideoParameters({}); setRun(null); setChosenCandidateId(""); setReviewConfirmed(false); setMessage("");
  }

  function changeModel(setter: (value: string) => void, parameterSetter: (value: QuickGenerationParameters) => void, value: string) {
    setter(value); parameterSetter({}); dropStalePlan();
  }

  function changeParameters(setter: (value: QuickGenerationParameters) => void, value: QuickGenerationParameters) {
    setter(value); dropStalePlan();
  }

  // A PLANNED run is frozen against the exact inputs that produced it; editing
  // any of them silently invalidates the plan, so drop it instead of letting
  // the confirm button submit stale inputs.
  function dropStalePlan() {
    setRun((current) => (current && current.state === "PLANNED" ? null : current));
  }

  async function abandonPlan(runId: string) {
    const next = await execute("cancel", (signal) => cancelQuickGeneration(runId, signal));
    if (next) { setRun(null); setChosenCandidateId(""); setReviewConfirmed(false); }
  }

  function chooseCandidate(id: string) { setChosenCandidateId(id); setReviewConfirmed(false); }

  async function reroll(parentCandidateId?: string) {
    if (!run) return;
    const next = await execute("reroll-images", (signal) => rerollQuickGenerationImages(run.id, { count: parentCandidateId ? 1 : candidateCount, parent_candidate_id: parentCandidateId || null }, signal));
    if (next) { setChosenCandidateId(""); setReviewConfirmed(false); }
  }

  async function approveCandidate() {
    if (!run || !chosenCandidateId || !reviewConfirmed) return;
    await execute("select-image", (signal) => selectQuickGenerationCandidate(run.id, chosenCandidateId, signal));
  }

  async function regeneratePrompt(target: "KEYFRAME" | "VIDEO") {
    if (!run) return;
    const label = target === "KEYFRAME" ? "regenerate-keyframe" : "regenerate-video";
    const next = await execute(label, (signal) => regenerateQuickGenerationPrompt(run.id, target, newCommandKey(), signal));
    if (next) { setChosenCandidateId(""); setReviewConfirmed(false); }
  }

  const currentRouteAvailable = mode === "TEXT_TO_IMAGE" ? imageAvailable : mode === "TEXT_TO_VIDEO" ? directAvailable : imageFirstAvailable;
  const videoAction = mode === "TEXT_TO_VIDEO" ? "TEXT_TO_VIDEO" : "IMAGE_TO_VIDEO";
  const selectedRoutesReady = routeIsExecutable(models, "TEXT_PLANNING", llmId)
    && (!usesImage(mode) || routeIsExecutable(models, "TEXT_TO_IMAGE", imageId))
    && (!usesVideo(mode) || routeIsExecutable(models, videoAction, videoId));
  const canPlan = story.trim().length >= 2 && selectedRoutesReady && currentRouteAvailable && (!remote || remoteConfirmed) && !busy;
  const plan = run?.plan && "video_plan" in run.plan ? run.plan as QuickGenerationPlan : null;
  const steps = mode === "TEXT_TO_VIDEO" ? ["描述", "核对规划", "确认", "生成视频", "完成"] : mode === "TEXT_TO_IMAGE" ? ["描述", "核对规划", "生成候选图", "选择作品", "完成"] : ["描述", "核对规划", "生成候选图", "选择首帧", "生成视频", "完成"];
  const currentStep = stepIndex(run, Boolean(plan), mode);
  const latestBatchNo = Math.max(0, ...(run?.candidates ?? []).map((candidate) => candidate.batch_no));
  const visibleCandidates = (run?.candidates ?? []).filter((candidate) => candidate.batch_no === latestBatchNo);
  const chosenCandidate = visibleCandidates.find((candidate) => candidate.id === chosenCandidateId);
  const candidateJobsActive = visibleCandidates.some((candidate) => ["QUEUED", "CLAIMED", "RUNNING", "RETRY_WAIT", "PROCESSING"].includes(candidate.state));
  const videoProgress = normalizeProgress(run?.job?.progress?.percent);

  return <section className="panel one-sentence-video quick-generation-workbench" aria-labelledby="quick-generation-title">
    <div className="one-sentence-video-heading"><div><p className="eyebrow">快速生成</p><h2 id="quick-generation-title">描述一个画面，直接得到作品</h2><p className="muted">先选择要完成的生成动作，再为每个步骤选择支持该动作的模型。同一视频模型可以同时提供文生视频和图生视频路线。</p></div><span className="status-pill neutral">不创建项目</span></div>
    {v2Readiness ? <div className="quick-create-v2-readiness" role="status"><p className="muted">V2 迁移状态：{v2Readiness.every((item) => item.ready) ? "门禁已满足；V2 试运行可独立验证，不会改写 V1 记录。" : "部分能力尚未满足 V2 门禁；可查看阻塞原因。"}</p><details><summary>查看 V2 切流前置条件与试运行</summary><ul>{v2Readiness.map((item) => <li key={`${item.mode}:${item.capability_code}`}><strong>{routeLabel(item.mode as QuickGenerationMode)} · {item.capability_code}</strong><span>{item.ready ? "已满足 V2 合同" : item.blocker ?? "未满足 V2 合同"}</span></li>)}</ul>{mode === "TEXT_TO_IMAGE" ? <div className="quick-create-v2-readiness__direct"><strong>V2 单次文生图试运行</strong><p>只使用 V2 SYSTEM Assignment、冻结快照与 V2 Job；不会使用旧模型、旧参数或 V1 快速生成记录。</p>{!v2DirectPreview ? <button type="button" className="secondary" disabled={v2DirectBusy || story.trim().length < 2} onClick={() => void previewV2DirectImage()}>{v2DirectBusy ? "正在进行 V2 预检…" : "预检 V2 单次文生图"}</button> : <><p>{v2DirectPreview.executable ? "V2 合同已确认。提交后将在此处显示 V2 Job 状态与已验证制品。" : `V2 预检未通过：${v2DirectPreview.blockers.join("、") || "未满足合同"}`}</p>{v2DirectPreview.executable ? <button type="button" className="secondary" disabled={v2DirectBusy || Boolean(v2DirectJobId)} onClick={() => void submitV2DirectImage()}>{v2DirectBusy ? "正在提交 V2 Job…" : v2DirectJobId ? "V2 Job 已提交" : "确认提交 V2 单次文生图"}</button> : null}</>}{v2DirectJobId ? <div className="quick-create-v2-readiness__execution"><p>V2 Job：{v2DirectJobId}。它独立于当前 V1 快速生成记录。</p>{v2DirectStatus ? <><p><strong>{STATE_LABELS[v2DirectStatus.state] ?? v2DirectStatus.state}</strong>{typeof v2DirectStatus.progress.percent === "number" ? ` · ${v2DirectStatus.progress.percent}%` : ""}</p>{v2DirectStatus.artifacts.map((artifact) => <figure key={artifact.artifact_id}><img src={artifact.download_url} alt="V2 单次文生图结果" /><figcaption><a className="secondary" href={artifact.download_url} download>下载已验证图片</a></figcaption></figure>)}{v2DirectStatus.state === "SUCCEEDED" && v2DirectStatus.artifacts.length === 0 ? <p className="muted">任务已完成，正在等待已验证制品登记。</p> : null}{v2DirectStatus.error_code ? <p className="inline-error">{v2DirectStatus.error_code}{v2DirectStatus.error_detail_redacted ? `：${v2DirectStatus.error_detail_redacted}` : ""}</p> : null}</> : <p className="muted">正在读取 V2 Job 状态…</p>}</div> : null}{v2DirectError ? <p className="inline-error" role="alert">V2 试运行失败：{v2DirectError}</p> : null}</div> : null}{mode === "TEXT_TO_IMAGE_TO_VIDEO" ? <div className="quick-create-v2-readiness__direct"><strong>V2 候选图 → 图生视频试运行</strong><p>每张候选图、人工选择和视频任务都有独立 V2 快照；只能把本次已验证候选图交给图生视频。</p>{!v2I2VPlan ? <button type="button" className="secondary" disabled={Boolean(v2I2VBusy) || story.trim().length < 2} onClick={() => void previewV2I2VCandidates()}>{v2I2VBusy === "candidate-preview" ? "正在预检候选图…" : "预检 V2 候选图"}</button> : null}{v2I2VPlan && !v2I2VRun ? <div><p>{v2I2VPlan.executable ? `候选图合同已确认，将生成 ${v2I2VPlan.candidates.length} 张独立候选。` : `V2 候选图预检未通过：${v2I2VPlan.blockers.join("、") || "未满足合同"}`}</p>{v2I2VPlan.executable ? <button type="button" className="secondary" disabled={Boolean(v2I2VBusy)} onClick={() => void submitV2I2VCandidates()}>{v2I2VBusy === "candidate-submit" ? "正在提交候选图…" : "确认提交 V2 候选图"}</button> : null}</div> : null}{v2I2VRun ? <div className="quick-create-v2-readiness__execution"><p><strong>V2 聚合状态：{STATE_LABELS[v2I2VRun.state] ?? v2I2VRun.state}</strong> · {v2I2VRun.id}</p><div className="one-sentence-image-grid">{v2I2VRun.steps.filter((step) => step.kind === "IMAGE_CANDIDATE").map((step) => <article key={step.id} className={`one-sentence-image-card${step.id === v2I2VRun.selected_step_id ? " selected" : ""}`}><button type="button" className="one-sentence-image-choice" disabled={!step.output_artifact || Boolean(v2I2VRun.selected_step_id) || Boolean(v2I2VBusy)} aria-pressed={step.id === v2I2VSelectedStepId} onClick={() => { setV2I2VSelectedStepId(step.id); setV2I2VSelectionConfirmed(false); }}><span className="one-sentence-image-frame">{step.output_artifact ? <img src={step.output_artifact.download_url} alt={`V2 图片候选 ${step.step_no}`} loading="lazy" decoding="async" /> : <span className="one-sentence-image-placeholder">{STATE_LABELS[step.job_state] ?? step.job_state}</span>}</span><span className="one-sentence-image-meta"><strong>候选 {step.selection_rank ?? step.step_no}</strong><small>{STATE_LABELS[step.state] ?? step.state}</small></span></button>{step.error_code ? <p className="inline-error">{step.error_code}{step.error_detail_redacted ? `：${step.error_detail_redacted}` : ""}</p> : null}</article>)}</div>{v2I2VRun.state === "AWAITING_SELECTION" && !v2I2VRun.selected_step_id ? <div className="one-sentence-image-decision"><p>选择一张已验证图片后，才会开放图生视频预检。</p><label><input type="checkbox" checked={v2I2VSelectionConfirmed} onChange={(event) => setV2I2VSelectionConfirmed(event.target.checked)} />我已检查主体、构图、风格和画面质量，并确认将此图作为视频首帧。</label><button type="button" disabled={!v2I2VSelectedStepId || !v2I2VSelectionConfirmed || Boolean(v2I2VBusy)} onClick={() => void selectV2I2VCandidate()}>{v2I2VBusy === "candidate-select" ? "正在冻结选择…" : "确认选择首帧"}</button></div> : null}{v2I2VRun.state === "IMAGE_SELECTED" ? <div><p>{v2I2VPreview ? (v2I2VPreview.executable ? "图生视频合同已确认。" : `图生视频预检未通过：${v2I2VPreview.blockers.join("、") || "未满足合同"}`) : "首帧已冻结；请预检图生视频合同。"}</p>{!v2I2VPreview ? <button type="button" className="secondary" disabled={Boolean(v2I2VBusy)} onClick={() => void previewV2I2VVideo()}>{v2I2VBusy === "video-preview" ? "正在预检图生视频…" : "预检 V2 图生视频"}</button> : v2I2VPreview.executable ? <button type="button" disabled={Boolean(v2I2VBusy)} onClick={() => void submitV2I2VVideo()}>{v2I2VBusy === "video-submit" ? "正在提交图生视频…" : "确认提交 V2 图生视频"}</button> : null}</div> : null}{v2I2VRun.steps.filter((step) => step.kind === "VIDEO_I2V" && step.output_artifact).map((step) => <figure key={step.id}><video controls playsInline preload="none" src={step.output_artifact?.download_url} aria-label="V2 图生视频结果">浏览器不支持视频播放。</video><figcaption><a className="secondary" href={step.output_artifact?.download_url} download>下载已验证视频</a></figcaption></figure>)}</div> : null}{v2I2VError ? <p className="inline-error" role="alert">V2 图生视频试运行失败：{v2I2VError}</p> : null}</div> : null}</details></div> : null}
    <ol className="one-sentence-video-stepper" aria-label="生成进度" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>{steps.map((label, index) => <li key={label} className={index < currentStep ? "done" : index === currentStep ? "current" : ""} aria-current={index === currentStep ? "step" : undefined}><span>{index < currentStep ? "✓" : index + 1}</span>{label}</li>)}</ol>

    <fieldset className="one-sentence-video-mode"><legend>第一步：选择生成动作</legend>
      <label className={mode === "TEXT_TO_IMAGE" ? "selected" : ""}><input type="radio" name="one-sentence-mode" value="TEXT_TO_IMAGE" checked={mode === "TEXT_TO_IMAGE"} onChange={() => changeMode("TEXT_TO_IMAGE")} /><span><strong>文生图</strong><small>{imageRoutes.length ? `${imageRoutes.length} 个可执行图片模型` : "可选择此动作；当前模型路线尚未就绪"}</small></span></label>
      <label className={mode === "TEXT_TO_IMAGE_TO_VIDEO" ? "selected" : ""}><input type="radio" name="one-sentence-mode" value="TEXT_TO_IMAGE_TO_VIDEO" checked={mode === "TEXT_TO_IMAGE_TO_VIDEO"} onChange={() => changeMode("TEXT_TO_IMAGE_TO_VIDEO")} /><span><strong>文生图，再图生视频</strong><small>{imageFirstAvailable ? `${imageRoutes.length} 个图片模型 · ${i2vRoutes.length} 个图生视频模型` : "可选择此动作；缺少可执行的图片或图生视频路线"}</small></span></label>
      <label className={mode === "TEXT_TO_VIDEO" ? "selected" : ""}><input type="radio" name="one-sentence-mode" value="TEXT_TO_VIDEO" checked={mode === "TEXT_TO_VIDEO"} onChange={() => changeMode("TEXT_TO_VIDEO")} /><span><strong>文生视频</strong><small>{t2vRoutes.length ? `${t2vRoutes.length} 个可执行文生视频模型` : "可选择此动作；当前文生视频路线尚未就绪"}</small></span></label>
    </fieldset>
    {!currentRouteAvailable && <p className="inline-warning" role="status">你仍可浏览和配置当前步骤，但现在没有完整的可执行模型路线。模型支持的动作与实际工作流是分开管理的；请到 <Link to={routes.systemCapabilities()}>系统能力</Link> 补齐当前动作的工作流。</p>}

    <label className="one-sentence-video-prompt">你想看到什么？<textarea aria-label="你想看到什么？" value={story} maxLength={2000} rows={4} disabled={Boolean(busy)} placeholder="例如：雨夜霓虹灯下，一只橘猫撑着透明雨伞穿过街道，镜头缓慢推进。" onChange={(event) => { setStory(event.target.value); dropStalePlan(); }} /></label>

    <section className="quick-model-settings" aria-labelledby="quick-model-settings-title">
      <div className="quick-model-settings__heading"><div><p className="eyebrow">第二步：按步骤选择模型</p><h3 id="quick-model-settings-title">每一步只展示支持该动作的模型</h3><p>模型与执行路线分开：同一模型支持多个动作时，会出现在对应的多个步骤中；参数只影响本次任务。</p></div><label>提示词语言<select aria-label="提示词语言" value={language} onChange={(event) => { setLanguage(event.target.value as "zh-CN" | "en-US"); dropStalePlan(); }}><option value="zh-CN">简体中文</option><option value="en-US">English</option></select></label></div>
      <QuickGenerationModelSettings title="文字规划模型" description="负责理解一句话并生成当前动作所需的提示词。" action="TEXT_PLANNING" models={models} profileVersionId={llmId} parameters={llmParameters} disabled={Boolean(busy)} onProfileChange={(value) => changeModel(setLlmId, setLlmParameters, value)} onParametersChange={(value) => changeParameters(setLlmParameters, value)} />
      {usesImage(mode) && <QuickGenerationModelSettings title="文生图模型" description={mode === "TEXT_TO_IMAGE" ? "生成最终图片候选。" : "生成可挑选的视频首帧候选。"} action="TEXT_TO_IMAGE" models={models} profileVersionId={imageId} parameters={imageParameters} disabled={Boolean(busy)} onProfileChange={(value) => changeModel(setImageId, setImageParameters, value)} onParametersChange={(value) => changeParameters(setImageParameters, value)} />}
      {usesVideo(mode) && <QuickGenerationModelSettings title={mode === "TEXT_TO_VIDEO" ? "文生视频模型" : "图生视频模型"} description="只展示支持当前视频动作的模型；双能力模型会在两种视频动作中同时出现。" action={mode === "TEXT_TO_VIDEO" ? "TEXT_TO_VIDEO" : "IMAGE_TO_VIDEO"} models={models} profileVersionId={videoId} parameters={videoParameters} disabled={Boolean(busy)} onProfileChange={(value) => changeModel(setVideoId, setVideoParameters, value)} onParametersChange={(value) => changeParameters(setVideoParameters, value)} />}
      {usesImage(mode) && <label className="quick-model-settings__candidate-count">每批图片候选数<input aria-label="每批图片候选数" type="number" min={1} max={8} value={candidateCount} onChange={(event) => { setCandidateCount(Math.max(1, Math.min(8, Math.round(Number(event.target.value)) || 1))); dropStalePlan(); }} /><small>候选数属于本次生成动作，不会写入模型预设。</small></label>}
      <div className="one-sentence-video-credential"><strong>模型身份与执行路线分开管理</strong><span>这里按动作选择模型并编辑本次参数；模型接入、连接和工作流维护集中在系统能力。</span><Link to={routes.systemCapabilities()}>管理模型与路线</Link></div>
    </section>

    {remote && <label className="one-sentence-video-consent"><input type="checkbox" checked={remoteConfirmed} onChange={(event) => setRemoteConfirmed(event.target.checked)} />我确认本次文字描述会发送到所选远端故事规划服务；图片和视频仍由选定的本机工作流生成。</label>}
    <div className="one-sentence-video-actions"><button type="button" disabled={!canPlan} onClick={() => void execute("plan", (signal) => planQuickGeneration({ story: story.trim(), mode, language, llm_profile_version_id: llmId, image_profile_version_id: usesImage(mode) ? imageId : null, video_profile_version_id: usesVideo(mode) ? videoId : null, image_candidate_count: candidateCount, allow_remote_outbound: remoteConfirmed, llm_parameters: llmParameters, image_parameters: usesImage(mode) ? imageParameters : {}, video_parameters: usesVideo(mode) ? videoParameters : {} }, newCommandKey(), signal))}>{busy === "plan" ? "正在检查本机能力…" : "生成执行规划"}</button></div>

    {run && plan && <PlanConfirmation run={run} plan={plan} busy={busy} onCommit={() => void execute("commit", (signal) => commitQuickGeneration(run.id, signal))} onAbandon={() => void abandonPlan(run.id)} onRegenerate={(target) => void regeneratePrompt(target)} />}

    {run && usesImage(mode) && visibleCandidates.length > 0 && <section className="one-sentence-image-review" aria-labelledby="one-sentence-images-title">
      <div className="one-sentence-image-review-heading"><div><p className="eyebrow">图片工作台 · 批次 {latestBatchNo}</p><h4 id="one-sentence-images-title">{mode === "TEXT_TO_IMAGE" ? "选择最终图片作品" : "选择一张图片作为视频输入"}</h4><p className="muted">点选只会高亮候选，不会自动提交下一步。你可以整批重出，也可以基于选中的构图换一个 Seed。</p></div><span className="status-pill neutral">{visibleCandidates.filter((candidate) => candidate.state === "READY").length}/{visibleCandidates.length} 可选择</span></div>
      <div className="one-sentence-image-grid">{visibleCandidates.map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} selected={candidate.id === chosenCandidateId} locked={Boolean(run.selected_candidate_id) || Boolean(busy)} onSelect={() => chooseCandidate(candidate.id)} />)}</div>
      {candidateJobsActive && <div className="one-sentence-video-progress" role="status" aria-live="polite"><div><strong>本机正在生成图片候选</strong><span>各候选独立排队，失败的单张不会丢失其他结果。</span></div><button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void execute("cancel", (signal) => cancelQuickGeneration(run.id, signal))}>取消这一批任务</button></div>}
      {!run.selected_candidate_id && !candidateJobsActive && <div className="one-sentence-image-actions"><button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void reroll()}>整批重出 {candidateCount} 张</button><button type="button" className="secondary" disabled={!chosenCandidate || Boolean(busy)} onClick={() => void reroll(chosenCandidateId)}>基于选中构图换 Seed</button></div>}
      {chosenCandidate && !run.selected_candidate_id && <div className="one-sentence-image-decision"><div><strong>候选 {chosenCandidate.ordinal + 1} 已选中</strong><p>{mode === "TEXT_TO_IMAGE" ? "确认后将这张图片设为本次独立作品。" : "确认后只会把这张独立作品绑定为本次图生视频输入。"}不会产生项目审核记录。</p></div><label><input type="checkbox" checked={reviewConfirmed} onChange={(event) => setReviewConfirmed(event.target.checked)} />我已检查主体、构图、风格和画面质量，确认{mode === "TEXT_TO_IMAGE" ? "选择此图片作品" : "将此图作为视频首帧"}</label><button type="button" disabled={!reviewConfirmed || Boolean(busy)} onClick={() => void approveCandidate()}>{busy === "select-image" ? "正在确认…" : mode === "TEXT_TO_IMAGE" ? "选择此图片作品" : "使用此首帧生成视频"}</button></div>}
      {run.selected_candidate_id && mode === "TEXT_TO_IMAGE_TO_VIDEO" && <p className="notice">首帧已确认并锁定，图生视频任务已经提交。需要更换首帧时，请从原描述开始一次新的快速生成。</p>}
    </section>}

    {run && (run.job_id || run.candidates.length > 0) && <div className="one-sentence-video-progress" role="status" aria-live="polite"><div><strong>{STATE_LABELS[run.state] ?? run.state}</strong><span>{run.stage}{run.job_id ? ` · 任务 ${run.job_id.slice(0, 8)}` : ""}{videoProgress !== null ? ` · ${videoProgress}%` : ""}{elapsedSeconds !== null ? ` · 已等待 ${Math.floor(elapsedSeconds / 60)} 分 ${elapsedSeconds % 60} 秒` : ""}</span><small className="muted">{TERMINAL_STATES.has(run.state) ? "本次后台任务已结束，作品记录仍可从本页打开。" : "本机生成可能需要较长时间；可以离开页面，稍后从最近生成继续查看。"}</small></div>{!TERMINAL_STATES.has(run.state) && run.state !== "AWAITING_SELECTION" && run.state !== "PLANNED" && <div className="one-sentence-video-actions"><button type="button" className="secondary" onClick={() => setObserving((value) => !value)}>{observing ? "停止观察" : "继续观察"}</button><button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void execute("cancel", (signal) => cancelQuickGeneration(run.id, signal))}>取消生成</button></div>}</div>}
    {run?.state === "FAILED" && <section className="one-sentence-video-recovery"><strong>{run.error.message || "生成流程中断"}</strong><p className="muted">描述、规划、候选图、Seed 与检查点均已保留。</p><div className="one-sentence-video-actions"><button type="button" onClick={() => void execute("resume", (signal) => resumeQuickGeneration(run.id, signal))}>从检查点恢复</button><button type="button" className="secondary" onClick={() => void execute("retry", (signal) => retryQuickGeneration(run.id, "NEW_SEED", signal))}>使用新 Seed 重试</button></div></section>}
    {run?.state === "CANCELLED" && <p className="notice">生成已取消；已完成的候选图和执行记录仍会保留。</p>}
    {run?.state === "SUCCEEDED" && run.output && <section className="one-sentence-video-result">{run.output.media_kind === "IMAGE" ? <img src={run.output.thumbnail_url} alt="快速生成的图片作品" /> : <video controls playsInline preload="none" poster={run.selected_image_output?.thumbnail_url} src={run.output.content_url} aria-label="快速生成的视频预览">浏览器不支持视频播放。</video>}<div><strong>{run.output.media_kind === "IMAGE" ? "图片已生成" : "视频已生成"}</strong><p className="muted">这是独立作品，不会出现在项目列表中。规划、候选选择和模型执行快照均随本次生成保留。</p><LocalArtifactReference artifact={run.output.artifact} title="独立作品与保存位置" relativeLabel="数据目录相对路径" downloadLabel={`下载${run.output.media_kind === "IMAGE" ? "图片" : "视频"}`} /></div></section>}
    {run?.state === "SUCCEEDED" && !run.output && <p className="notice">生成任务已完成，作品登记仍在收口；可从最近生成继续查看。</p>}
    {message && <p role="alert" className="inline-error">{message}</p>}
    {recentRuns.length > 0 && <details className="one-sentence-video-recent"><summary>最近生成</summary><div>{recentRuns.map((item) => <button type="button" key={item.id} onClick={() => { setRun(item); setMode(item.mode); setStory(item.story.text); setLlmId(item.llm_profile_version_id); setImageId(item.image_profile_version_id ?? ""); setVideoId(item.video_profile_version_id ?? ""); setLlmParameters(item.model_parameters?.llm ?? {}); setImageParameters(item.model_parameters?.image ?? {}); setVideoParameters(item.model_parameters?.video ?? {}); setChosenCandidateId(item.selected_candidate_id ?? ""); setReviewConfirmed(false); }}><span><strong>{item.story.text}</strong><small>{routeLabel(item.mode)}</small></span><span>{STATE_LABELS[item.state] ?? item.state}</span></button>)}</div></details>}
  </section>;
}

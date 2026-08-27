import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { getProfileVersion, type Profile } from "../../generated/api";
import { ProfileExecutionDetailButton } from "../model-config/ProfileExecutionDetailButton";
import {
  cancelOneSentenceVideoRun,
  commitOneSentenceVideoRun,
  getOneSentenceVideoRun,
  listOneSentenceVideoRuns,
  planOneSentenceVideo,
  rerollOneSentenceImages,
  resumeOneSentenceVideoRun,
  retryOneSentenceVideoRun,
  selectOneSentenceImageCandidate,
  type OneSentenceImageCandidate,
  type OneSentenceMode,
  type OneSentenceVideoPlan,
  type OneSentenceVideoRun,
} from "./oneSentenceVideoClient";

type Props = { profiles: Profile[]; onProjectCreated: (projectId: string) => void };
const TERMINAL_STATES = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
const STATE_LABELS: Record<string, string> = {
  PLANNING: "正在生成规划", PLANNED: "等待确认规划", COMMITTING: "正在创建项目", GENERATING: "正在生成",
  AWAITING_SELECTION: "等待选择首帧", CANCELLING: "正在取消", SUCCEEDED: "已完成", FAILED: "需要处理",
  CANCELLED: "已取消", QUEUED: "排队中", CLAIMED: "准备中", RUNNING: "生成中", READY: "可选择",
  PROCESSING: "正在准备预览",
};

function published(profiles: Profile[], capability: string) {
  return profiles.filter((profile) => profile.status === "PUBLISHED" && profile.capability === capability);
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
const thumbnailUrl = (id: string) => `/api/v1/media-versions/${encodeURIComponent(id)}/thumbnail?size=small&frame=poster`;

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

function progressOf(candidate: OneSentenceImageCandidate) {
  return normalizeProgress(candidate.job?.progress?.percent);
}

function stepIndex(run: OneSentenceVideoRun | null, hasPlan: boolean, mode: OneSentenceMode) {
  if (!run) return hasPlan ? 1 : 0;
  if (run.state === "PLANNED") return 1;
  if (mode === "DIRECT_T2V") {
    if (run.state === "SUCCEEDED") return 4;
    return run.project_id ? 3 : 2;
  }
  if (run.state === "SUCCEEDED") return 5;
  if (run.selected_candidate_id || run.stage === "VIDEO_GENERATING") return 4;
  if (run.state === "AWAITING_SELECTION") return 3;
  return run.project_id ? 2 : 1;
}

function PlanConfirmation({ run, plan, busy, onCommit, onAbandon }: { run: OneSentenceVideoRun; plan: OneSentenceVideoPlan; busy: boolean; onCommit: () => void; onAbandon: () => void }) {
  return <section className="one-sentence-video-confirmation" aria-labelledby="one-sentence-confirm-title">
    <div><p className="eyebrow">只读预检完成</p><h4 id="one-sentence-confirm-title">{plan.video_plan.title}</h4></div>
    <dl className="one-sentence-video-specs">
      {plan.image_spec && <div><dt>首帧候选</dt><dd>{plan.image_candidate_count} 张 · {plan.image_spec.width}×{plan.image_spec.height}</dd></div>}
      <div><dt>视频输出</dt><dd>{plan.output_spec.width}×{plan.output_spec.height} · {plan.output_spec.fps} fps</dd></div>
      <div><dt>时长</dt><dd>{plan.output_spec.duration_seconds} 秒 · {plan.output_spec.frame_count} 帧</dd></div>
      <div><dt>生成路线</dt><dd>{plan.mode === "DIRECT_T2V" ? "文字 → 视频" : "文字 → 多图筛选 → 视频"}</dd></div>
    </dl>
    {plan.image_spec && <div className="one-sentence-video-prompt-preview"><strong>首帧提示词</strong><p>{plan.video_plan.keyframe_prompt}</p></div>}
    <div className="one-sentence-video-prompt-preview"><strong>视频提示词</strong><p>{plan.video_plan.video_prompt}</p></div>
    <p className="muted">确认后才会创建项目、镜头和不可变生成记录；本机 Worker 在后台继续运行。</p>
    {run.state === "PLANNED" && <div className="one-sentence-video-actions"><button type="button" disabled={busy} onClick={onCommit}>{busy ? "正在提交…" : plan.mode === "DIRECT_T2V" ? "确认规划并生成视频" : "确认规划并生成首帧候选"}</button><button type="button" className="secondary" disabled={busy} onClick={onAbandon}>放弃此规划</button></div>}
  </section>;
}

function CandidateCard({ candidate, selected, locked, onSelect }: { candidate: OneSentenceImageCandidate; selected: boolean; locked: boolean; onSelect: () => void }) {
  const progress = progressOf(candidate);
  const ready = candidate.state === "READY" && Boolean(candidate.media_version_id);
  const displayOrdinal = candidate.ordinal + 1;
  return <article className={`one-sentence-image-card${selected ? " selected" : ""}${candidate.state === "FAILED" ? " failed" : ""}`}>
    <button type="button" className="one-sentence-image-choice" aria-pressed={selected} disabled={!ready || locked} onClick={onSelect}>
      <span className="one-sentence-image-frame">
        {ready && candidate.media_version_id
          ? <img src={thumbnailUrl(candidate.media_version_id)} alt={`首帧候选 ${displayOrdinal}`} loading="lazy" decoding="async" />
          : <span className="one-sentence-image-placeholder" aria-hidden="true"><span>{progress === null ? "…" : `${progress}%`}</span></span>}
        {selected && <span className="one-sentence-image-selected-mark">已选中</span>}
      </span>
      <span className="one-sentence-image-meta"><strong>候选 {displayOrdinal}</strong><small>{STATE_LABELS[candidate.state] ?? candidate.state} · Seed {candidate.seed}</small></span>
    </button>
    {candidate.error?.message && <p className="inline-error">{candidate.error.message}</p>}
  </article>;
}

export function OneSentenceVideoWizard({ profiles, onProjectCreated }: Props) {
  const llmProfiles = useMemo(() => published(profiles, "LLM_STORY_PARSE"), [profiles]);
  const t2vProfiles = useMemo(() => published(profiles, "VIDEO_T2V"), [profiles]);
  const i2vProfiles = useMemo(() => published(profiles, "VIDEO_I2V"), [profiles]);
  const imageProfiles = useMemo(() => published(profiles, "IMAGE_CONCEPT"), [profiles]);
  const directAvailable = t2vProfiles.length > 0;
  const imageFirstAvailable = imageProfiles.length > 0 && i2vProfiles.length > 0;
  const [story, setStory] = useState("");
  const [language, setLanguage] = useState<"zh-CN" | "en-US">("zh-CN");
  const [mode, setMode] = useState<OneSentenceMode>(() => directAvailable || !imageFirstAvailable ? "DIRECT_T2V" : "KEYFRAME_I2V");
  const [llmId, setLlmId] = useState("");
  const [imageId, setImageId] = useState("");
  const [videoId, setVideoId] = useState("");
  const [candidateCount, setCandidateCount] = useState(4);
  const [remote, setRemote] = useState(false);
  const [remoteConfirmed, setRemoteConfirmed] = useState(false);
  const [run, setRun] = useState<OneSentenceVideoRun | null>(null);
  const [recentRuns, setRecentRuns] = useState<OneSentenceVideoRun[]>([]);
  const [chosenCandidateId, setChosenCandidateId] = useState("");
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [busy, setBusy] = useState("");
  const [observing, setObserving] = useState(true);
  const [elapsedSeconds, setElapsedSeconds] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const announcedProjectRef = useRef<string | null>(null);
  const acceptRun = useCallback((nextRun: OneSentenceVideoRun) => {
    setRun(nextRun);
    setRecentRuns((items) => [nextRun, ...items.filter((item) => item.id !== nextRun.id)].slice(0, 8));
  }, []);

  useEffect(() => {
    setLlmId((current) => current || llmProfiles[0]?.version_id || "");
    setImageId((current) => current || imageProfiles[0]?.version_id || "");
  }, [imageProfiles, llmProfiles]);

  useEffect(() => {
    const choices = mode === "DIRECT_T2V" ? t2vProfiles : i2vProfiles;
    setVideoId((current) => choices.some((profile) => profile.version_id === current) ? current : choices[0]?.version_id || "");
  }, [i2vProfiles, mode, t2vProfiles]);

  useEffect(() => {
    if (mode === "DIRECT_T2V" && !directAvailable && imageFirstAvailable) setMode("KEYFRAME_I2V");
    if (mode === "KEYFRAME_I2V" && !imageFirstAvailable && directAvailable) setMode("DIRECT_T2V");
  }, [directAvailable, imageFirstAvailable, mode]);

  useEffect(() => {
    let active = true;
    setRemoteConfirmed(false);
    if (!llmId) { setRemote(false); return () => { active = false; }; }
    void getProfileVersion(llmId).then((result) => { if (active) setRemote(isRemoteProfile(result.profile_version)); }).catch(() => { if (active) setRemote(false); });
    return () => { active = false; };
  }, [llmId]);

  useEffect(() => {
    const controller = new AbortController();
    void listOneSentenceVideoRuns(8, controller.signal).then((result) => setRecentRuns(result.items)).catch((error: unknown) => { if (!(error instanceof DOMException && error.name === "AbortError")) setRecentRuns([]); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!run?.project_id || announcedProjectRef.current === run.project_id) return;
    announcedProjectRef.current = run.project_id;
    onProjectCreated(run.project_id);
  }, [onProjectCreated, run?.project_id]);

  useEffect(() => {
    if (!run || !observing || TERMINAL_STATES.has(run.state) || run.state === "PLANNED" || run.state === "AWAITING_SELECTION") return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void getOneSentenceVideoRun(run.id, controller.signal).then((result) => acceptRun(result.run)).catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) setMessage(`刷新执行状态失败：${describeFailure(error)}`);
      });
    }, 1500);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [acceptRun, observing, run?.id, run?.state]);

  useEffect(() => { if (run?.selected_candidate_id) setChosenCandidateId(run.selected_candidate_id); }, [run?.id, run?.selected_candidate_id]);
  // Local video generation offers no provider progress; expose elapsed time so
  // the wait is legible instead of a frozen "正在生成" label.
  useEffect(() => {
    if (run && run.project_id && !TERMINAL_STATES.has(run.state) && run.state !== "AWAITING_SELECTION" && run.state !== "PLANNED") {
      const startedAt = Date.now();
      setElapsedSeconds(0);
      const timer = window.setInterval(() => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000);
      return () => window.clearInterval(timer);
    }
    setElapsedSeconds(null);
    return undefined;
  }, [run?.id, run?.state, run?.project_id]);
  useEffect(() => () => controllerRef.current?.abort(), []);

  async function execute(label: string, action: (signal: AbortSignal) => Promise<{ run: OneSentenceVideoRun }>) {
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

  function changeMode(nextMode: OneSentenceMode) {
    if (nextMode === mode) return;
    setMode(nextMode); setRun(null); setChosenCandidateId(""); setReviewConfirmed(false); setMessage("");
  }

  // A PLANNED run is frozen against the exact inputs that produced it; editing
  // any of them silently invalidates the plan, so drop it instead of letting
  // the confirm button submit stale inputs.
  function dropStalePlan() {
    setRun((current) => (current && current.state === "PLANNED" ? null : current));
  }

  async function abandonPlan(runId: string) {
    const next = await execute("cancel", (signal) => cancelOneSentenceVideoRun(runId, signal));
    if (next) { setRun(null); setChosenCandidateId(""); setReviewConfirmed(false); }
  }

  function chooseCandidate(id: string) { setChosenCandidateId(id); setReviewConfirmed(false); }

  async function reroll(parentCandidateId?: string) {
    if (!run) return;
    const next = await execute("reroll-images", (signal) => rerollOneSentenceImages(run.id, { count: parentCandidateId ? 1 : candidateCount, parent_candidate_id: parentCandidateId || null }, signal));
    if (next) { setChosenCandidateId(""); setReviewConfirmed(false); }
  }

  async function approveCandidate() {
    if (!run || !chosenCandidateId || !reviewConfirmed) return;
    await execute("select-image", (signal) => selectOneSentenceImageCandidate(run.id, chosenCandidateId, signal));
  }

  const canPlan = story.trim().length >= 2 && Boolean(llmId) && Boolean(videoId) && (mode === "DIRECT_T2V" || Boolean(imageId)) && (!remote || remoteConfirmed) && !busy;
  const plan = run?.plan && "video_plan" in run.plan ? run.plan as OneSentenceVideoPlan : null;
  const steps = mode === "DIRECT_T2V" ? ["描述", "核对规划", "确认", "生成视频", "完成"] : ["描述", "核对规划", "生成候选图", "选择并批准", "生成视频", "完成"];
  const currentStep = stepIndex(run, Boolean(plan), mode);
  const latestBatchNo = Math.max(0, ...(run?.candidates ?? []).map((candidate) => candidate.batch_no));
  const visibleCandidates = (run?.candidates ?? []).filter((candidate) => candidate.batch_no === latestBatchNo);
  const chosenCandidate = visibleCandidates.find((candidate) => candidate.id === chosenCandidateId);
  const candidateJobsActive = visibleCandidates.some((candidate) => ["QUEUED", "CLAIMED", "RUNNING", "RETRY_WAIT", "PROCESSING"].includes(candidate.state));
  const videoProgress = normalizeProgress(run?.job?.progress?.percent);

  return <section className="panel one-sentence-video" aria-labelledby="one-sentence-video-title">
    <div className="one-sentence-video-heading"><div><p className="eyebrow">快捷创作</p><h3 id="one-sentence-video-title">一句话生成画面或视频</h3><p className="muted">先核对规划。你可以直接生成视频，也可以先挑选首帧，再让它动起来。</p></div><span className="status-pill neutral">后台任务可恢复</span></div>
    <ol className="one-sentence-video-stepper" aria-label="生成进度" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}>{steps.map((label, index) => <li key={label} className={index < currentStep ? "done" : index === currentStep ? "current" : ""} aria-current={index === currentStep ? "step" : undefined}><span>{index < currentStep ? "✓" : index + 1}</span>{label}</li>)}</ol>

    <fieldset className="one-sentence-video-mode"><legend>选择生成路线</legend>
      <label className={mode === "DIRECT_T2V" ? "selected" : ""}><input type="radio" name="one-sentence-mode" value="DIRECT_T2V" checked={mode === "DIRECT_T2V"} disabled={!directAvailable} onChange={() => changeMode("DIRECT_T2V")} /><span><strong>直接生成视频</strong><small>{directAvailable ? "速度优先，文字直接进入文生视频模型" : "暂无已发布的文生视频模型"}</small></span></label>
      <label className={mode === "KEYFRAME_I2V" ? "selected" : ""}><input type="radio" name="one-sentence-mode" value="KEYFRAME_I2V" checked={mode === "KEYFRAME_I2V"} disabled={!imageFirstAvailable} onChange={() => changeMode("KEYFRAME_I2V")} /><span><strong>先选图，再生成视频</strong><small>{imageFirstAvailable ? "控制优先，先生成多张首帧，可筛选和重出" : "需要已发布的文生图与图生视频模型"}</small></span></label>
    </fieldset>
    {!directAvailable && !imageFirstAvailable && <p className="inline-warning" role="status">当前没有可执行的一句话视频路线。请先到 <Link to={routes.systemCapabilities()}>系统能力</Link> 发布所需能力。</p>}

    <label className="one-sentence-video-prompt">你想看到什么？<textarea aria-label="你想看到什么？" value={story} maxLength={2000} rows={4} disabled={Boolean(busy)} placeholder="例如：雨夜霓虹灯下，一只橘猫撑着透明雨伞穿过街道，镜头缓慢推进。" onChange={(event) => { setStory(event.target.value); dropStalePlan(); }} /></label>

    <details className="one-sentence-video-settings"><summary>模型、语言与候选数量</summary><div className="one-sentence-video-models">
      <label>故事规划模型<select aria-label="故事规划模型" value={llmId} onChange={(event) => { setLlmId(event.target.value); dropStalePlan(); }}>{llmProfiles.map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.title} · v{profile.version_no}</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={llmId} />
      {mode === "KEYFRAME_I2V" && <><label>本机文生图模型<select aria-label="本机文生图模型" value={imageId} onChange={(event) => { setImageId(event.target.value); dropStalePlan(); }}>{imageProfiles.map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.title} · v{profile.version_no}</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={imageId} /></>}
      <label>{mode === "DIRECT_T2V" ? "本机文生视频模型" : "本机图生视频模型"}<select aria-label={mode === "DIRECT_T2V" ? "本机文生视频模型" : "本机图生视频模型"} value={videoId} onChange={(event) => { setVideoId(event.target.value); dropStalePlan(); }}>{(mode === "DIRECT_T2V" ? t2vProfiles : i2vProfiles).map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.title} · v{profile.version_no}</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={videoId} />
      <label>提示词语言<select aria-label="提示词语言" value={language} onChange={(event) => { setLanguage(event.target.value as "zh-CN" | "en-US"); dropStalePlan(); }}><option value="zh-CN">简体中文</option><option value="en-US">English</option></select></label>
      {mode === "KEYFRAME_I2V" && <label>每批首帧候选数<input aria-label="每批首帧候选数" type="number" min={1} max={8} value={candidateCount} onChange={(event) => { setCandidateCount(Math.max(1, Math.min(8, Math.round(Number(event.target.value)) || 1))); dropStalePlan(); }} /></label>}
    </div><div className="one-sentence-video-credential"><strong>凭据由系统安全配置</strong><span>此处只选择已发布的执行配置，不会重复索要密钥。</span><Link to={routes.systemCapabilities()}>打开系统能力</Link></div></details>

    {remote && <label className="one-sentence-video-consent"><input type="checkbox" checked={remoteConfirmed} onChange={(event) => setRemoteConfirmed(event.target.checked)} />我确认本次文字描述会发送到所选远端故事规划服务；图片和视频仍由选定的本机工作流生成。</label>}
    <div className="one-sentence-video-actions"><button type="button" disabled={!canPlan} onClick={() => void execute("plan", (signal) => planOneSentenceVideo({ story: story.trim(), mode, language, llm_profile_version_id: llmId, image_profile_version_id: mode === "KEYFRAME_I2V" ? imageId : null, video_profile_version_id: videoId, image_candidate_count: candidateCount, allow_remote_outbound: remoteConfirmed }, newCommandKey(), signal))}>{busy === "plan" ? "正在检查本机能力…" : "检查并生成规划"}</button></div>

    {run && plan && <PlanConfirmation run={run} plan={plan} busy={Boolean(busy)} onCommit={() => void execute("commit", (signal) => commitOneSentenceVideoRun(run.id, signal))} onAbandon={() => void abandonPlan(run.id)} />}

    {run && mode === "KEYFRAME_I2V" && visibleCandidates.length > 0 && <section className="one-sentence-image-review" aria-labelledby="one-sentence-images-title">
      <div className="one-sentence-image-review-heading"><div><p className="eyebrow">首帧工作台 · 批次 {latestBatchNo}</p><h4 id="one-sentence-images-title">先选构图，再决定是否批准</h4><p className="muted">点选只会高亮候选，不会自动进入视频生成。你可以整批重出，也可以基于选中的构图换一个 Seed。</p></div><span className="status-pill neutral">{visibleCandidates.filter((candidate) => candidate.state === "READY").length}/{visibleCandidates.length} 可选择</span></div>
      <div className="one-sentence-image-grid">{visibleCandidates.map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} selected={candidate.id === chosenCandidateId} locked={Boolean(run.selected_candidate_id) || Boolean(busy)} onSelect={() => chooseCandidate(candidate.id)} />)}</div>
      {candidateJobsActive && <div className="one-sentence-video-progress" role="status" aria-live="polite"><div><strong>本机正在生成首帧候选</strong><span>各候选独立排队，失败的单张不会丢失其他结果。</span></div><button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void execute("cancel", (signal) => cancelOneSentenceVideoRun(run.id, signal))}>取消这一批任务</button></div>}
      {!run.selected_candidate_id && !candidateJobsActive && <div className="one-sentence-image-actions"><button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void reroll()}>整批重出 {candidateCount} 张</button><button type="button" className="secondary" disabled={!chosenCandidate || Boolean(busy)} onClick={() => void reroll(chosenCandidateId)}>基于选中构图换 Seed</button></div>}
      {chosenCandidate && !run.selected_candidate_id && <div className="one-sentence-image-decision"><div><strong>候选 {chosenCandidate.ordinal + 1} 已选中</strong><p>批准会写入正式 KEYFRAME 审核记录，并立即把这张图绑定为图生视频的首帧。</p></div><label><input type="checkbox" checked={reviewConfirmed} onChange={(event) => setReviewConfirmed(event.target.checked)} />我已检查主体、构图、风格和画面质量，确认将此图作为视频首帧</label><button type="button" disabled={!reviewConfirmed || Boolean(busy)} onClick={() => void approveCandidate()}>{busy === "select-image" ? "正在批准并提交视频…" : "批准此首帧并生成视频"}</button></div>}
      {run.selected_candidate_id && <p className="notice">首帧已批准并锁定，图生视频任务已经提交。需要更换首帧时，{run.links.generation ? <><Link to={run.links.generation}>打开镜头生成记录</Link>，</> : "从保留的执行记录，"}创建新分支。</p>}
    </section>}

    {run?.project_id && <div className="one-sentence-video-progress" role="status" aria-live="polite"><div><strong>{STATE_LABELS[run.state] ?? run.state}</strong><span>{run.stage}{run.job_id ? ` · 任务 ${run.job_id.slice(0, 8)}` : ""}{videoProgress !== null ? ` · ${videoProgress}%` : ""}{elapsedSeconds !== null ? ` · 已等待 ${Math.floor(elapsedSeconds / 60)} 分 ${elapsedSeconds % 60} 秒` : ""}</span><small className="muted">本机生成通常需要 10—25 分钟；期间可离开页面，稍后从"最近的一句话任务"继续观察。</small></div><div className="one-sentence-video-actions"><button type="button" className="secondary" onClick={() => setObserving((value) => !value)}>{observing ? "停止观察" : "继续观察"}</button>{!TERMINAL_STATES.has(run.state) && run.state !== "AWAITING_SELECTION" && <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void execute("cancel", (signal) => cancelOneSentenceVideoRun(run.id, signal))}>取消后台生成</button>}</div></div>}
    {run?.state === "FAILED" && <section className="one-sentence-video-recovery"><strong>{run.error.message || "生成流程中断"}</strong><p className="muted">项目、候选图、Seed 与检查点均已保留。</p><div className="one-sentence-video-actions"><button type="button" onClick={() => void execute("resume", (signal) => resumeOneSentenceVideoRun(run.id, signal))}>从检查点恢复</button><button type="button" className="secondary" onClick={() => void execute("retry", (signal) => retryOneSentenceVideoRun(run.id, "NEW_SEED", signal))}>使用新 Seed 重试</button></div></section>}
    {run?.state === "CANCELLED" && <p className="notice">生成已取消；已创建的项目、候选图与执行记录仍会保留。</p>}
    {run?.state === "SUCCEEDED" && run.media_version_id && <section className="one-sentence-video-result"><video controls playsInline preload="none" poster={thumbnailUrl(run.media_version_id)} src={`/api/v1/media-versions/${encodeURIComponent(run.media_version_id)}/content`} aria-label="一句话生成的视频预览">浏览器不支持视频播放。</video><div><strong>视频已生成并保存</strong><p className="muted">所有规划、候选图、审批与模型执行快照都保留在项目中。</p><div className="one-sentence-video-links">{run.links.project && <Link to={run.links.project}>打开项目</Link>}{run.links.generation && <Link to={run.links.generation}>查看生成记录</Link>}{run.links.review && <Link to={run.links.review}>进入审片</Link>}</div></div></section>}
    {run?.state === "SUCCEEDED" && !run.media_version_id && <p className="notice">视频任务已完成，媒体登记仍在收口；可从任务记录继续查看。</p>}
    {message && <p role="alert" className="inline-error">{message}</p>}
    {recentRuns.length > 0 && <details className="one-sentence-video-recent"><summary>最近的一句话任务</summary><div>{recentRuns.map((item) => <button type="button" key={item.id} onClick={() => { setRun(item); setMode(item.mode); setStory(item.story.text); setChosenCandidateId(item.selected_candidate_id ?? ""); setReviewConfirmed(false); }}><span><strong>{item.story.text}</strong><small>{item.mode === "DIRECT_T2V" ? "直接生成视频" : "先选图再生成视频"}</small></span><span>{STATE_LABELS[item.state] ?? item.state}</span></button>)}</div></details>}
  </section>;
}

import { useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getProjectOverviewV2, getStoryboardWorkspace } from "../generated/api";
import { routes } from "../app/routeRegistry";
import { planShotKeyframeBatch, submitShotKeyframeBatch, type ShotFrameStrategy } from "../features/director-v2/shotKeyframeBatchApi";
import { AssetProposalReviewPanel } from "../features/episode-plan-v2/AssetProposalReviewPanel";
import {
  confirmProductionEpisode,
  controlProductionSession,
  createProductionSession,
  extendProductionSessionBudget,
  getProductionSessionReview,
  listProductionSessions,
  planProductionSession,
  rerollProductionChoice,
  resolveSessionStart,
  retryProductionSessionItem,
  startProductionSession,
  type PlanCommand,
  type ProductionPlan,
  type ProductionSession,
} from "../features/production-sessions/client";
import { operationIdempotencyKey } from "../services/commandId";
import "../features/production-sessions/production-factory.css";

const statusLabel: Record<string, string> = {
  READY: "待启动", RUNNING: "持续生成中", PAUSED: "已暂停", WAITING_USER: "需要人工处理", WAITING_REVIEW: "等待人工审核",
  COMPLETED: "已完成", FAILED: "失败", CANCELLED: "已取消", GENERATING: "生成中",
  BLOCKED: "需要处理", READY_FOR_HUMAN_REVIEW: "可人工审核", REVIEWED: "已确认",
};

const choiceRoleLabel: Record<string, string> = {
  FIRST_FRAME: "首帧关键帧",
  LAST_FRAME: "尾帧关键帧",
  VIDEO: "视频",
  TTS_AUDIO: "对白配音",
};
const rerollableChoiceRoles = new Set(["FIRST_FRAME", "VIDEO"]);
const reviewTargetUrl = (projectId: string, episodeId: string, targetKind: "MEDIA_VERSION" | "EPISODE_RENDER_VERSION", targetId: string) =>
  `${routes.postReview(projectId, episodeId)}?targetKind=${targetKind}&targetId=${encodeURIComponent(targetId)}`;

export function ProductionFactoryPage() {
  const { projectId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const requestedEpisodeId = searchParams.get("episode")?.trim() || "";
  const overview = useQuery({ queryKey: ["production-factory-overview", projectId], queryFn: () => getProjectOverviewV2(projectId), enabled: Boolean(projectId) });
  const sessions = useInfiniteQuery({
    queryKey: ["production-sessions", projectId],
    queryFn: ({ pageParam }) => listProductionSessions(projectId, { cursor: pageParam, limit: 50 }),
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: Boolean(projectId),
    refetchInterval: (query) => query.state.data?.pages.some((page) => page.items.some((item) => item.status === "RUNNING")) ? 5000 : false,
  });
  const sessionItems = useMemo(() => (sessions.data?.pages ?? []).flatMap((page) => page.items), [sessions.data?.pages]);
  const sessionTotal = sessions.data?.pages[0]?.total ?? null;
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const selectedSession = sessionItems.find((item) => item.id === selectedSessionId) ?? sessionItems[0] ?? null;
  const review = useInfiniteQuery({
    queryKey: ["production-session-review", selectedSession?.id],
    queryFn: ({ pageParam }) => getProductionSessionReview(selectedSession!.id, { cursor: pageParam, limit: 100 }),
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: Boolean(selectedSession),
    refetchInterval: (query) => (selectedSession?.status === "RUNNING" || query.state.data?.pages.some((page) => page.items.some((item) => (item.asset_inputs ?? []).some((input) => input.review_status !== "CONFIRMED")))) ? 5000 : false,
  });
  const reviewItems = useMemo(() => (review.data?.pages ?? []).flatMap((page) => page.items), [review.data?.pages]);
  const reviewTotal = review.data?.pages[0]?.total ?? null;
  const episodes = useMemo(() => (overview.data?.seasons ?? []).flatMap((season) => season.episodes), [overview.data]);
  const [scope, setScope] = useState<"SINGLE_EPISODE" | "WHOLE_DRAMA">(requestedEpisodeId ? "SINGLE_EPISODE" : "WHOLE_DRAMA");
  const [episodeId, setEpisodeId] = useState(requestedEpisodeId);
  const [mode, setMode] = useState<"DRAFT" | "BALANCED" | "QUALITY">("BALANCED");
  const [frameStrategy, setFrameStrategy] = useState<ShotFrameStrategy>("FIRST_ONLY");
  const [parallel, setParallel] = useState(1);
  const [checkpoint, setCheckpoint] = useState<PlanCommand["checkpoint_policy"]>("ON_EXCEPTION");
  const [ttsEnabled, setTtsEnabled] = useState(true);
  const [durationHours, setDurationHours] = useState(24);
  const [maxJobs, setMaxJobs] = useState(600);
  const [maxAttempts, setMaxAttempts] = useState(1200);
  const [maxOutputGiB, setMaxOutputGiB] = useState(100);
  const [gpuQueue, setGpuQueue] = useState(8);
  const [dispatchShots, setDispatchShots] = useState(4);
  const [plan, setPlan] = useState<ProductionPlan | null>(null);
  /** Session that `create` already persisted, kept even when `start` failed. */
  const [createdSession, setCreatedSession] = useState<ProductionSession | null>(null);
  const [startState, setStartState] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { if (!episodeId && episodes[0]) setEpisodeId(String(episodes[0].id)); }, [episodeId, episodes]);
  useEffect(() => {
    if (!requestedEpisodeId) return;
    setScope("SINGLE_EPISODE");
    setEpisodeId(requestedEpisodeId);
    setPlan(null);
  }, [requestedEpisodeId]);
  useEffect(() => {
    if (requestedEpisodeId && episodes.length && !episodes.some((episode) => String(episode.id) === requestedEpisodeId)) {
      setError("链接中的分集不属于当前项目，请重新选择分集。");
    }
  }, [episodes, requestedEpisodeId]);

  const command = (): PlanCommand => ({
    scope_type: scope,
    episode_ids: scope === "SINGLE_EPISODE" ? [episodeId] : [],
    production_mode: mode,
    checkpoint_policy: checkpoint,
    tts_enabled: ttsEnabled,
    max_parallel_episodes: parallel,
    min_free_disk_bytes: 5 * 1024 * 1024 * 1024,
    max_duration_seconds: durationHours * 60 * 60,
    max_new_jobs: maxJobs,
    max_attempts_total: maxAttempts,
    max_output_bytes: maxOutputGiB * 1024 * 1024 * 1024,
    max_queued_gpu_jobs: gpuQueue,
    dispatch_shots_per_tick: dispatchShots,
  });
  const resetFeedback = () => { setError(""); setMessage(""); setStartState(""); };
  const refresh = async (sessionId?: string) => {
    const result = await sessions.refetch();
    if (sessionId) setSelectedSessionId(sessionId);
    await review.refetch();
    return result;
  };
  const runAction = async (name: string, action: () => Promise<void>) => {
    resetFeedback(); setBusy(name);
    try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(""); }
  };
  /** Operation-scoped key: `create` and its retry share one key, a new plan starts a new one. */
  const sessionOperationKey = (operation: string, payload: unknown) => operationIdempotencyKey(`production-session:${operation}:${projectId}`, payload);

  const planNow = () => runAction("plan", async () => {
    if (scope === "SINGLE_EPISODE" && !episodeId) throw new Error("请先选择一集");
    const result = await planProductionSession(projectId, command());
    setPlan(result.plan); setCreatedSession(null); setMessage("预检完成：这里只计算范围与资源，不会启动生成。");
  });
  const createAndStart = () => runAction("start", async () => {
    if (!plan) throw new Error("请先重新预检");
    const payload = { ...command(), expected_plan_hash: plan.plan_hash };
    const createKey = sessionOperationKey("create", payload);
    const created = await createProductionSession(projectId, payload, createKey);
    // Cache the session before starting: a later `start` failure must not hide it.
    setCreatedSession(created.session);
    setSelectedSessionId(created.session.id);
    setPlan(null);
    await refresh(created.session.id);
    const started = await startProductionSession(created.session, sessionOperationKey("start", { session_id: created.session.id, expected_revision: created.session.revision }));
    setCreatedSession(null);
    setSelectedSessionId(started.session.id);
    setStartState("");
    setMessage("生产会话已启动。关闭页面或重启应用后，Worker 仍会从持久状态继续。");
    await refresh(started.session.id);
  });
  /**
   * FE-12 retry: read the original session first and only then act, so an already
   * running session is never started twice and a terminal one is reported instead.
   */
  const retryStart = (session: ProductionSession) => runAction("start-retry", async () => {
    const key = sessionOperationKey("start", { session_id: session.id, expected_revision: session.revision });
    const outcome = await resolveSessionStart(session.id, key, session.revision);
    if (outcome.state === "STARTED") {
      setCreatedSession(null);
      setSelectedSessionId(outcome.session.id);
      setStartState(`会话已启动（状态 ${outcome.session.status}）。`);
      await refresh(outcome.session.id);
      return;
    }
    if (outcome.state === "MISSING") throw new Error("这条生产会话已经不存在，请重新预检并创建。");
    if (outcome.state === "TERMINAL") { setCreatedSession(null); setSelectedSessionId(outcome.session.id); throw new Error(`会话已处于终态 ${outcome.session.status}，不能再次启动。`); }
    if (outcome.state === "REVISION_CHANGED") { setCreatedSession(outcome.session); await refresh(outcome.session.id); throw new Error(`会话修订已变化（${session.revision} → ${outcome.session.revision}），请确认后再次启动此会话。`); }
    setCreatedSession(outcome.session);
    throw new Error(`会话当前状态为 ${outcome.session.status}，没有可用的 START 动作。`);
  });
  const control = (action: "pause" | "resume" | "cancel") => selectedSession && runAction(action, async () => {
    const result = await controlProductionSession(selectedSession, action);
    setMessage(action === "pause" ? "会话已暂停。" : action === "resume" ? "会话已恢复。" : "会话已取消。");
    await refresh(result.session.id);
  });
  const fillEpisodeKeyframes = () => runAction("keyframes", async () => {
    if (!episodeId) throw new Error("请先选择一集");
    const { storyboard } = await getStoryboardWorkspace(episodeId);
    const targets = storyboard.items.map((item) => ({ shot_id: item.id, expected_revision: item.revision }));
    if (!targets.length) throw new Error("本集还没有镜头，请先生成并确认本集方案");
    const candidateCount = { DRAFT: 1, BALANCED: 2, QUALITY: 4 }[mode];
    const { plan: keyframePlan } = await planShotKeyframeBatch(episodeId, targets, frameStrategy, candidateCount);
    if (!keyframePlan.summary.jobs) {
      if (!keyframePlan.summary.blocked) {
        setMessage(`本集每个镜头已经具备 ${candidateCount} 个所需关键帧候选，无需重复生成。`);
        return;
      }
      throw new Error(keyframePlan.issues.map((issue) => issue.message).filter(Boolean).slice(0, 3).join("；") || "关键帧生成条件尚未满足");
    }
    const { batch } = await submitShotKeyframeBatch(
      episodeId,
      targets,
      frameStrategy,
      candidateCount,
      keyframePlan.plan_hash,
      globalThis.crypto?.randomUUID?.() ?? `factory-keyframes-${episodeId}-${Date.now()}`,
    );
    setMessage(`已提交 ${batch.summary.total} 个关键帧缺口任务${keyframePlan.summary.blocked ? `；另有 ${keyframePlan.summary.blocked} 项需处理` : ""}。已有候选不会重复生成。`);
  });
  const extendBudget = () => selectedSession && runAction("extend-budget", async () => {
    const limits = selectedSession.budget?.limits ?? {};
    const extension: Parameters<typeof extendProductionSessionBudget>[1] = {};
    const maximums = {
      max_duration_seconds: 365 * 24 * 60 * 60,
      max_new_jobs: 1_000_000,
      max_attempts_total: 2_000_000,
      max_output_bytes: 2 ** 50,
    } as const;
    for (const blocker of selectedSession.budget?.hard_blockers ?? []) {
      const key = blocker.limit_key as keyof typeof maximums;
      if (!(key in maximums)) continue;
      const current = Number(limits[key] ?? blocker.limit);
      const next = Math.min(maximums[key], Math.max(1, current * 2));
      if (next > current) extension[key] = next;
    }
    if (!Object.keys(extension).length) throw new Error("已耗尽的生产预算已经达到系统上限");
    const result = await extendProductionSessionBudget(selectedSession, extension);
    setMessage("已耗尽的生产预算已提高，因预算停下的分集已重新进入调度。");
    await refresh(result.session.id);
  });
  const reviewWaiting = selectedSession
    ? selectedSession.counters.review_waiting ?? (selectedSession.status === "WAITING_REVIEW" ? selectedSession.counters.waiting ?? 0 : 0)
    : 0;
  const stageWaiting = selectedSession
    ? (selectedSession.counters.machine_waiting ?? 0) + (selectedSession.counters.gate_waiting ?? 0)
    : 0;

  return <div className="v2-page production-factory">
    <section className="panel factory-hero">
      <div className="factory-hero__copy"><p className="eyebrow">一键漫剧工厂</p><h2>让机器持续生产，最后集中人工审核</h2><p className="muted">选择单集或整部后，系统按资产、分镜、关键帧、视频、声音、时间线、预览成片顺序持续执行。机器选择始终标为临时，只有人工审核决定才能完成会话。</p></div>
      <Link className="secondary v2-inline-link" to={routes.systemJobs(projectId)}>查看机器任务</Link>
    </section>

    <section className="panel" aria-labelledby="factory-create-title">
      <div className="panel-heading"><div><p className="eyebrow">新生产会话</p><h3 id="factory-create-title">生产范围与速度</h3></div><span className="status-pill neutral">先预检，再启动</span></div>
      <div className="factory-config">
        <label>生产范围<select value={scope} onChange={(event) => { setScope(event.target.value as typeof scope); setPlan(null); }}><option value="WHOLE_DRAMA">整部连续生产</option><option value="SINGLE_EPISODE">只生产一集</option></select></label>
        <label>质量模式<select value={mode} onChange={(event) => { setMode(event.target.value as typeof mode); setPlan(null); }}><option value="DRAFT">草稿 · 每镜头 1 个候选</option><option value="BALANCED">平衡 · 每镜头 2 个候选</option><option value="QUALITY">精品 · 每镜头 4 个候选</option></select></label>
        {scope === "SINGLE_EPISODE" && <label className="factory-config__wide">分集<select value={episodeId} onChange={(event) => { setEpisodeId(event.target.value); setPlan(null); }}>{episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.code} · {episode.title}</option>)}</select></label>}
        <label>同时生产的分集数<input type="number" min={1} max={8} value={parallel} onChange={(event) => { setParallel(Math.max(1, Math.min(8, Number(event.target.value) || 1))); setPlan(null); }} /><small className="muted">显存有限时保持 1；多卡或充足显存再提高。</small></label>
        <label>停顿策略<select value={checkpoint} onChange={(event) => { setCheckpoint(event.target.value as typeof checkpoint); setPlan(null); }}><option value="ON_EXCEPTION">只在异常时停下</option><option value="AUTO_CONTINUE">尽量自动继续</option><option value="AFTER_ASSETS">资产完成后暂停</option><option value="AFTER_SHOT_PLAN">分镜完成后暂停</option><option value="BEFORE_VIDEO">生成视频前暂停</option></select></label>
        <label className="factory-config__wide"><span><input type="checkbox" checked={ttsEnabled} onChange={(event) => { setTtsEnabled(event.target.checked); setPlan(null); }} /> 自动生成配音与字幕</span></label>
      </div>
      <details className="factory-config__wide"><summary>持续运行预算（默认适合无人值守 24 小时）</summary><div className="factory-config">
        <label>最长运行小时<input type="number" min={1} max={8760} value={durationHours} onChange={(event) => { setDurationHours(Math.max(1, Math.min(8760, Number(event.target.value) || 24))); setPlan(null); }} /></label>
        <label>最多新建任务<input type="number" min={1} max={1000000} value={maxJobs} onChange={(event) => { setMaxJobs(Math.max(1, Math.min(1000000, Number(event.target.value) || 600))); setPlan(null); }} /></label>
        <label>最多执行尝试<input type="number" min={1} max={2000000} value={maxAttempts} onChange={(event) => { setMaxAttempts(Math.max(1, Math.min(2000000, Number(event.target.value) || 1200))); setPlan(null); }} /></label>
        <label>最多输出 GiB<input type="number" min={1} max={1048576} value={maxOutputGiB} onChange={(event) => { setMaxOutputGiB(Math.max(1, Math.min(1048576, Number(event.target.value) || 100))); setPlan(null); }} /></label>
        <label>GPU 队列上限<input type="number" min={1} max={128} value={gpuQueue} onChange={(event) => { setGpuQueue(Math.max(1, Math.min(128, Number(event.target.value) || 8))); setPlan(null); }} /></label>
        <label>每轮调度镜头<input type="number" min={1} max={100} value={dispatchShots} onChange={(event) => { setDispatchShots(Math.max(1, Math.min(100, Number(event.target.value) || 4))); setPlan(null); }} /></label>
      </div><p className="muted">达到总任务、尝试、时间或输出上限后会停在人工处理；GPU 队列暂满只会自动等待。</p></details>
      <div className="factory-actions"><button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void planNow()}>{busy === "plan" ? "正在预检…" : "预检生产计划"}</button>{plan && <button type="button" className="primary-action" disabled={Boolean(busy)} onClick={() => void createAndStart()}>{busy === "start" ? "正在启动…" : plan.scope_type === "WHOLE_DRAMA" ? "一键生成整部" : "一键生成本集"}</button>}</div>
      {plan && <><div className="factory-plan-summary"><div><strong>{plan.episode_count}</strong><span>分集</span></div><div><strong>{plan.total_shot_count}</strong><span>已有镜头</span></div><div><strong>{plan.estimated_candidate_count}</strong><span>预计画面候选</span></div></div>{plan.warnings.length > 0 && <ul className="muted">{plan.warnings.map((warning, index) => <li key={String(warning.code ?? index)}>{String(warning.message ?? warning.code)}</li>)}</ul>}</>}
      {message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">{error}</p>}
      {createdSession && <div className="factory-session-recovery" role="alert">
        <p><strong>会话 {createdSession.id}</strong> 已创建并保存在本机（状态 {createdSession.status}）。启动没有成功，可以直接启动这条原始会话；重新预检会另建一条会话。</p>
        <div className="factory-actions">
          {(createdSession.allowed_actions ?? []).includes("START") && <button type="button" className="primary-action" disabled={Boolean(busy)} onClick={() => void retryStart(createdSession)}>{busy === "start-retry" ? "正在启动原会话…" : "启动此会话"}</button>}
          {createdSession.allowed_actions.includes("PAUSE") && <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => { setSelectedSessionId(createdSession.id); void control("pause"); }}>暂停该会话</button>}
          {createdSession.allowed_actions.includes("RESUME") && <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => { setSelectedSessionId(createdSession.id); void control("resume"); }}>继续该会话</button>}
          <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => setCreatedSession(null)}>稍后处理</button>
        </div>
      </div>}
      {startState && <p className="review-success" role="status">{startState}</p>}
    </section>

    <section className="panel" aria-labelledby="factory-keyframes-title">
      <div className="panel-heading"><div><p className="eyebrow">独立快捷生产</p><h3 id="factory-keyframes-title">一键补齐本集关键帧</h3></div><span className="status-pill neutral">只补缺口</span></div>
      <p className="muted">读取本集全部镜头，保留已有成功候选，只为缺少的首帧或尾帧建立可恢复任务。候选数量沿用上方质量模式。</p>
      <div className="factory-config">
        <label className="factory-config__wide">分集<select value={episodeId} onChange={(event) => setEpisodeId(event.target.value)}>{episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.code} · {episode.title}</option>)}</select></label>
        <label>画面策略<select value={frameStrategy} onChange={(event) => setFrameStrategy(event.target.value as ShotFrameStrategy)}><option value="FIRST_ONLY">只补首帧</option><option value="FIRST_AND_LAST">补首帧和尾帧</option></select></label>
      </div>
      <div className="factory-actions"><button type="button" className="primary-action" disabled={Boolean(busy) || !episodeId} onClick={() => void fillEpisodeKeyframes()}>{busy === "keyframes" ? "正在检查并提交…" : "一键补齐关键帧"}</button><Link className="secondary v2-inline-link" to={episodeId ? routes.shotStudio(projectId, episodeId) : routes.projectHome(projectId)}>查看镜头候选</Link></div>
    </section>

    <section className="panel" aria-labelledby="factory-sessions-title">
      <div className="panel-heading"><div><p className="eyebrow">持续运行</p><h3 id="factory-sessions-title">最近生产会话</h3></div><button type="button" className="secondary" onClick={() => void refresh()}>刷新</button></div>
      {sessions.isPending ? <p role="status">正在读取会话…</p> : sessionItems.length ? <>
        <p className="muted" role="status" aria-live="polite">已加载 {sessionItems.length} 条会话{sessionTotal !== null ? ` / 共 ${sessionTotal} 条` : ""}{sessions.hasNextPage ? "（还有更多）" : "（已到末页）"}</p>
        <div className="factory-session-list">{sessionItems.map((session) => <button key={session.id} type="button" className={`factory-session-row ${selectedSession?.id === session.id ? "is-active" : ""}`} onClick={() => setSelectedSessionId(session.id)}><strong>{session.scope_type === "WHOLE_DRAMA" ? "整部生产" : "单集生产"} · {session.production_mode}</strong><span className="status-pill">{statusLabel[session.status] ?? session.status}</span><small className="muted">{session.current_stage} · {new Date(session.updated_at).toLocaleString()}</small></button>)}</div>
        {sessions.hasNextPage && <button type="button" className="secondary list-more" disabled={sessions.isFetchingNextPage} onClick={() => void sessions.fetchNextPage()}>{sessions.isFetchingNextPage ? "读取中…" : `加载更早会话（已加载 ${sessionItems.length}）`}</button>}
      </> : <p className="empty-state">还没有生产会话。</p>}
      {selectedSession && <><div className="factory-progress"><div><strong>{selectedSession.counters.completed ?? 0}</strong><span>已确认</span></div><div><strong>{selectedSession.counters.running ?? 0}</strong><span>运行中</span></div><div><strong>{selectedSession.counters.pending ?? 0}</strong><span>待调度</span></div><div><strong>{reviewWaiting}</strong><span>待审核</span></div><div><strong>{stageWaiting}</strong><span>阶段等待</span></div><div><strong>{(selectedSession.counters.blocked ?? 0) + (selectedSession.counters.failed ?? 0)}</strong><span>需处理</span></div></div>
        <p className="muted">预算：任务 {selectedSession.budget?.usage?.new_jobs ?? 0}/{selectedSession.budget?.limits?.max_new_jobs ?? "—"}，尝试 {selectedSession.budget?.usage?.attempts_total ?? 0}/{selectedSession.budget?.limits?.max_attempts_total ?? "—"}，GPU 队列 {selectedSession.budget?.usage?.global_queued_gpu_jobs ?? 0}/{selectedSession.budget?.limits?.max_queued_gpu_jobs ?? "—"}</p>
        {selectedSession.budget?.resource_wait && <p className="muted" role="status">{selectedSession.budget.resource_wait.message}</p>}
        {Boolean(selectedSession.budget?.hard_blockers?.length) && <ul className="factory-blockers">{selectedSession.budget?.hard_blockers?.map((blocker) => <li key={blocker.code}>{blocker.message}（{blocker.usage}/{blocker.limit}）</li>)}</ul>}
        <div className="factory-actions">{selectedSession.allowed_actions.includes("START") && <button type="button" className="primary-action" disabled={Boolean(busy)} onClick={() => void retryStart(selectedSession)}>{busy === "start-retry" ? "正在启动…" : "启动此会话"}</button>}{selectedSession.allowed_actions.includes("PAUSE") && <button type="button" onClick={() => void control("pause")}>暂停</button>}{selectedSession.allowed_actions.includes("RESUME") && <button type="button" onClick={() => void control("resume")}>继续</button>}{Boolean(selectedSession.budget?.hard_blockers?.length) && <button type="button" className="primary-action" disabled={Boolean(busy)} onClick={() => void extendBudget()}>{busy === "extend-budget" ? "正在扩展…" : "提高已耗尽预算并继续"}</button>}{selectedSession.allowed_actions.includes("CANCEL") && <button type="button" className="danger" onClick={() => void control("cancel")}>取消</button>}</div></>}
    </section>

    {selectedSession && <section className="panel" aria-labelledby="factory-review-title">
      <div className="panel-heading"><div><p className="eyebrow">集中审核</p><h3 id="factory-review-title">逐集检查预览与机器临时选择</h3></div><span className="status-pill neutral">不会自动批准</span></div>
      {reviewItems.some((item) => (item.asset_inputs ?? []).some((input) => input.review_status !== "CONFIRMED")) && <><p className="muted">机器已用临时资产继续生产。请先核对下列身份建议；接受当前建议后，本页会自动解除资产审核阻塞。</p><AssetProposalReviewPanel projectId={projectId} /></>}
      {review.isPending ? <p role="status">正在汇总待审证据…</p> : <>
        <p className="muted" role="status" aria-live="polite">已加载 {reviewItems.length} 集待审证据{reviewTotal !== null ? ` / 共 ${reviewTotal} 集` : ""}{review.hasNextPage ? "（还有更多）" : "（已到末页）"}</p>
        <div className="factory-review-list">{reviewItems.map((item) => {
        const choiceApprovalsReady = item.choices.length > 0 && item.choices.every((choice) => Boolean(choice.available_human_approval_id));
        const renderApprovalReady = Boolean(item.preview_render?.human_approval_current);
        const approvalsReady = choiceApprovalsReady && renderApprovalReady;
        const isConfirmed = item.review_status === "REVIEWED" || item.item_state === "COMPLETED";
        const canConfirm = item.review_status === "READY_FOR_HUMAN_REVIEW" && approvalsReady && item.timeline_choice_consistency.status === "MATCH";
        const approvalHint = !choiceApprovalsReady
          ? "请先在正式审核中批准本集全部当前候选"
          : !renderApprovalReady
            ? "请先在正式审核中批准当前预览成片"
            : undefined;
        return <article className="factory-review-card" key={item.session_item_id}><header><div><strong>{item.episode_code} · {item.episode_title}</strong><p className="muted">{item.current_stage}</p></div><span className="status-pill">{statusLabel[item.review_status] ?? item.review_status}</span></header>
          {(item.asset_inputs ?? []).length > 0 && <div className="factory-choice-list">{(item.asset_inputs ?? []).map((input) => <div className="factory-choice" key={input.id}><div><strong>{input.name} · {input.asset_code}</strong><small className="muted"> {input.selection_authority} · {input.review_status === "CONFIRMED" ? "已人工确认" : input.review_status === "MISMATCH" ? "人工决定与生产输入不一致" : "等待人工确认"}</small></div></div>)}</div>}
          {item.preview_render?.id && <><video controls preload="none" poster={`/api/v1/episode-renders/${encodeURIComponent(item.preview_render.id)}/thumbnail?size=medium&frame=poster`} src={`/api/v1/episode-renders/${encodeURIComponent(item.preview_render.id)}/content`} aria-label={`${item.episode_code} 预览成片`} /><p className="muted">预览成片 · {item.preview_render.human_approval_current ? "已人工批准" : "等待人工批准"} {!item.preview_render.human_approval_current && <Link className="v2-inline-link" to={reviewTargetUrl(projectId, item.episode_id, "EPISODE_RENDER_VERSION", item.preview_render.id)}>审核当前预览</Link>}</p></>}
          <div className="factory-choice-list">{item.choices.map((choice) => <div className="factory-choice" key={choice.id}><div><strong>{choice.shot_code ?? choice.target_id} · {choiceRoleLabel[choice.slot_role] ?? choice.slot_role}</strong><small className="muted"> {choice.selection_authority} · {choice.media.integrity_status} · {choice.available_human_approval_id ? "已人工批准" : "等待人工批准"}</small></div><div className="factory-actions">{!choice.available_human_approval_id && <Link className="secondary v2-inline-link" to={reviewTargetUrl(projectId, item.episode_id, "MEDIA_VERSION", choice.candidate_id)}>审核此候选</Link>}{choice.selection_state === "TEMPORARY" && rerollableChoiceRoles.has(choice.slot_role) && <button type="button" className="secondary" disabled={Boolean(busy) || item.item_state !== "WAITING" || item.current_stage !== "WAITING_REVIEW" || !["WAITING_REVIEW", "WAITING_USER"].includes(selectedSession.status)} onClick={() => void runAction(`reroll-${choice.id}`, async () => { await rerollProductionChoice(selectedSession, choice); setMessage("已换到下一个已验证候选，系统正在重建本集预览。"); await refresh(selectedSession.id); })}>{busy === `reroll-${choice.id}` ? "换选中…" : "换一个"}</button>}</div></div>)}</div>
          {item.blockers.length > 0 && <ul className="factory-blockers">{item.blockers.map((blocker) => <li key={blocker.code}>{blocker.message}</li>)}</ul>}
          {item.review_status === "BLOCKED" && <p className="muted">返工建议：{item.repair_plan.summary}</p>}
          {item.repair_plan.prerequisites.length > 0 && <ul className="factory-blockers">{item.repair_plan.prerequisites.map((step) => <li key={step.action}>{step.message}</li>)}</ul>}
          <div className="factory-actions"><Link className="secondary v2-inline-link" to={routes.postReview(projectId, item.episode_id)}>打开正式审核</Link>{isConfirmed && <Link className="secondary v2-inline-link" to={routes.delivery(projectId, item.episode_id)}>进入本集交付</Link>}{item.review_status === "BLOCKED" && item.repair_plan.can_retry_now && <button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void runAction(`retry-${item.episode_id}`, async () => { await retryProductionSessionItem(selectedSession, item); setMessage(`${item.episode_code} 已按最小返工计划进入队列。`); await refresh(selectedSession.id); })}>{busy === `retry-${item.episode_id}` ? "重试中…" : item.repair_plan.recommended_strategy === "RECOMPOSE_ONLY" ? "只重建预览" : item.repair_plan.recommended_strategy === "FULL_EPISODE" ? "补齐本集缺口" : "从失败阶段继续"}</button>}<button type="button" className="primary-action" disabled={isConfirmed || !canConfirm || Boolean(busy)} title={isConfirmed ? undefined : approvalHint} onClick={() => void runAction(`confirm-${item.episode_id}`, async () => { await confirmProductionEpisode(selectedSession, item); setMessage(`${item.episode_code} 已确认。`); await refresh(selectedSession.id); })}>{isConfirmed ? "本集已确认" : busy === `confirm-${item.episode_id}` ? "确认中…" : approvalsReady ? "确认本集并锁定" : "等待正式审核批准"}</button></div>
        </article>;
      })}</div>
        {review.hasNextPage && <button type="button" className="secondary list-more" disabled={review.isFetchingNextPage} onClick={() => void review.fetchNextPage()}>{review.isFetchingNextPage ? "读取中…" : `加载更多待审证据（已加载 ${reviewItems.length}）`}</button>}
      </>}
    </section>}
  </div>;
}

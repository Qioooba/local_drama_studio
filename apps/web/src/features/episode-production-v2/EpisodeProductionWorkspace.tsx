import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Tabs, type TabItem } from "../../components/ui";
import { routes } from "../../app/routeRegistry";
import {
  getEpisodeProductionOverviewV2,
  listEpisodeProductionShotsV2,
  startEpisodeProductionRunV2,
  transitionEpisodeProductionRunV2,
  type EpisodeProductionBlocker,
  type EpisodeProductionRunStartCommand,
  type EpisodeProductionShot,
  type EpisodeProductionStage,
  type EpisodeProductionMode,
  type ProductionState,
} from "../../generated/api";
import { useProjectEventInvalidation } from "../events/useProjectEventInvalidation";
import "./episode-production.css";

const ATTENTION_STATES: ProductionState[] = ["BLOCKED", "FAILED", "NEEDS_REVIEW", "STALE"];
const VIEWS: TabItem[] = [{ id: "attention", label: "待处理" }, { id: "all", label: "全部镜头" }];
const STATE_LABELS: Record<ProductionState, string> = {
  EMPTY: "未开始", READY: "就绪", RUNNING: "进行中", NEEDS_REVIEW: "待确认",
  BLOCKED: "阻塞", FAILED: "失败", STALE: "已过期", CANCELLED: "已取消",
};
const STAGE_LABELS: Record<EpisodeProductionStage["stage_code"], string> = {
  SHOT_PLANNING: "镜头策划", SHOT_IMAGE: "镜头画面", VIDEO: "视频",
  AUDIO_SUBTITLE: "声音字幕", COMPOSE_QC: "合成质检",
};
const REASON_LABELS: Record<string, string> = {
  SHOT_REVISION_READY: "镜头意图已就绪", SHOT_INTENT_INCOMPLETE: "镜头意图需要补全",
  CANONICAL_JOB_ACTIVE: "后台任务正在执行", WORKING_SLOT_SELECTED: "已选工作版本",
  CANDIDATES_REQUIRE_WORKING_SELECTION: "已有候选，等待选择", NO_CANDIDATES: "尚无候选",
  SHOT_PLANNING_REQUIRED: "需要先完成镜头策划", ALL_LINES_HAVE_CURRENT_WORKING_AUDIO: "声音与最新台词一致",
  AUDIO_CANDIDATES_REQUIRE_SELECTION: "声音候选等待选择", TTS_CANDIDATES_REQUIRED: "需要生成声音候选",
  NO_DIALOGUE_LINES: "本镜没有对白", CURRENT_VIDEO_IN_TIMELINE: "当前视频已进入时间线",
  TIMELINE_MISSING_CURRENT_VIDEO: "时间线尚未引用当前视频", WORKING_VIDEO_REQUIRED: "需要先选择工作视频",
  MACHINE_QC_REQUIRES_ATTENTION: "机器质检需要处理",
};
const NEXT_ACTION_LABELS: Record<string, string> = {
  RESOLVE_ATTENTION: "先处理异常镜头",
  MONITOR_ACTIVE_JOBS: "查看正在执行的任务",
  OPEN_POST_EDIT: "进入后期合成",
  OPEN_SHOT_PLANNING: "从镜头策划开始",
};
const BLOCKER_LABELS: Record<string, string> = {
  SHOT_INTENT_INCOMPLETE: "镜头策划未完成",
  MACHINE_QC_REQUIRES_ATTENTION: "机器质检未通过",
  CONTINUITY_CONFLICT: "镜头衔接冲突",
  CONTINUITY_STALE: "镜头衔接需要刷新",
};

type View = "attention" | "all";
type RunAction = "pause" | "resume" | "cancel" | "recover";

function routeForBlocker(blocker: EpisodeProductionBlocker, projectId: string, episodeId: string, shotId: string) {
  if (blocker.owner_route === "SHOT_STUDIO") return routes.shotStudio(projectId, episodeId, shotId);
  if (blocker.owner_route === "REVIEW") return routes.postReview(projectId, episodeId);
  if (blocker.owner_route === "POST_AUDIO") return routes.postAudio(projectId, episodeId);
  if (blocker.owner_route === "POST_EDIT") return routes.postEdit(projectId, episodeId);
  return routes.systemJobs(projectId);
}

function StageFact({ stage }: { stage: EpisodeProductionStage }) {
  return <li className={`production-stage state-${stage.state.toLowerCase()}`}>
    <span className="production-stage__dot" aria-hidden="true" />
    <div><strong>{STAGE_LABELS[stage.stage_code]}</strong><small>{REASON_LABELS[stage.reason_code] ?? stage.reason_code}</small></div>
    <span>{STATE_LABELS[stage.state]}</span>
  </li>;
}

function ShotCard({ item, projectId, episodeId }: { item: EpisodeProductionShot; projectId: string; episodeId: string }) {
  const selected = item.material_slots.filter((slot) => slot.selected_version_id).length;
  const candidates = item.material_slots.reduce((sum, slot) => sum + slot.candidate_count, 0);
  return <article className={`production-shot state-${item.overall_state.toLowerCase()}`}>
    <header>
      <div><span className={`status-pill state-${item.overall_state.toLowerCase()}`}>{STATE_LABELS[item.overall_state]}</span><h3>{item.shot_code}</h3></div>
      <Link className="secondary" to={routes.shotStudio(projectId, episodeId, item.shot_id)}>打开镜头</Link>
    </header>
    <ul className="production-stage-rail" aria-label={`${item.shot_code} 五阶段状态`}>
      {item.stages.map((stage) => <StageFact key={stage.stage_code} stage={stage} />)}
    </ul>
    <div className="production-shot__facts">
      <span>候选 <strong>{candidates}</strong></span><span>工作版本 <strong>{selected}/3</strong></span>
      <span>新鲜度 <strong>{item.freshness_edges.filter((edge) => edge.state === "STALE").length ? "需刷新" : "当前"}</strong></span>
    </div>
    {item.blockers.length > 0 && <ul className="production-shot__blockers" aria-label={`${item.shot_code} 阻塞`}>
      {item.blockers.map((blocker) => <li key={`${blocker.code}:${blocker.repair_action}`}><div><strong>{BLOCKER_LABELS[blocker.code] ?? "需要处理"}</strong><span>{blocker.message}</span></div><Link to={routeForBlocker(blocker, projectId, episodeId, item.shot_id)}>处理</Link></li>)}
    </ul>}
  </article>;
}

export function EpisodeProductionWorkspace({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const retryKeys = useRef(new Map<string, string>());
  const [mode, setMode] = useState<EpisodeProductionMode>("BALANCED");
  const [tts, setTts] = useState(true);
  const [feedback, setFeedback] = useState<string | null>(null);
  const params = new URLSearchParams(location.search);
  const view: View = params.get("view") === "all" ? "all" : "attention";
  const cursor = Math.max(0, Number(params.get("cursor") ?? 0) || 0);
  const overviewKey = ["episode-production-v2", episodeId, "overview"] as const;
  const shotsKey = ["episode-production-v2", episodeId, "shots", view, cursor] as const;
  const overview = useQuery({ queryKey: overviewKey, queryFn: () => getEpisodeProductionOverviewV2(episodeId) });
  const shots = useQuery({ queryKey: shotsKey, queryFn: () => listEpisodeProductionShotsV2(episodeId, { cursor, limit: 50, states: view === "attention" ? ATTENTION_STATES : undefined }) });
  useProjectEventInvalidation(projectId, ["EpisodeProductionRunChanged", "JOB_QUEUED", "JOB_FINISHED", "SHOT_REVISION_CREATED", "AudioWorkingCandidateChanged", "FrameBridgeChanged"], [overviewKey, shotsKey]);

  const keyFor = (identity: string) => {
    const existing = retryKeys.current.get(identity);
    if (existing) return existing;
    const created = crypto.randomUUID();
    retryKeys.current.set(identity, created);
    return created;
  };
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["episode-production-v2", episodeId, "overview"] }),
      queryClient.invalidateQueries({ queryKey: ["episode-production-v2", episodeId, "shots"] }),
    ]);
  };
  const start = useMutation({
    mutationFn: () => {
      const identity = `start:${episodeId}:${mode}:${tts}`;
      const payload: EpisodeProductionRunStartCommand = { production_mode: mode, tts_enabled: tts, checkpoint_policy: "ON_EXCEPTION", idempotency_key: keyFor(identity) };
      return startEpisodeProductionRunV2(episodeId, payload).then((result) => ({ result, identity }));
    },
    onSuccess: async ({ identity }) => { retryKeys.current.delete(identity); setFeedback("整集生产已启动；页面会持续读取规范化阶段事实。"); await refresh(); },
  });
  const transition = useMutation({
    mutationFn: ({ action, revision }: { action: RunAction; revision: number }) => {
      const identity = `${action}:${overview.data?.overview.active_run?.id}:${revision}`;
      const runId = overview.data?.overview.active_run?.id;
      if (!runId) throw new Error("当前没有可控制的整集生产运行。");
      const extra = action === "pause" ? { reason: "CREATOR_PAUSE" } : action === "resume" ? { note: "创作者确认后继续" } : {};
      return transitionEpisodeProductionRunV2(runId, action, { expected_revision: revision, idempotency_key: keyFor(identity), ...extra }).then((result) => ({ result, identity, action }));
    },
    onSuccess: async ({ identity, action }) => { retryKeys.current.delete(identity); setFeedback({ pause: "生产已暂停。", resume: "生产已恢复。", cancel: "本次运行已安全取消。", recover: "租约恢复检查已完成。" }[action]); await refresh(); },
  });

  const selectView = (next: string) => {
    const query = new URLSearchParams(location.search);
    if (next === "all") query.set("view", "all"); else query.delete("view");
    query.delete("cursor");
    navigate({ pathname: location.pathname, search: query.toString() ? `?${query}` : "" }, { replace: true });
  };
  const setCursor = (next: number) => {
    const query = new URLSearchParams(location.search);
    if (next > 0) query.set("cursor", String(next)); else query.delete("cursor");
    navigate({ pathname: location.pathname, search: query.toString() ? `?${query}` : "" }, { replace: true });
  };

  if (overview.isPending || shots.isPending) return <section className="episode-production-loading" role="status">正在读取本集规范化生产事实…</section>;
  if (overview.error || shots.error) return <section className="episode-production-error" role="alert"><strong>无法读取本集生产状态</strong><span>{String(overview.error ?? shots.error)}</span><button type="button" onClick={() => { void overview.refetch(); void shots.refetch(); }}>重试</button></section>;
  const summary = overview.data.overview;
  const run = summary.active_run;
  const mutationError = start.error ?? transition.error;
  return <div className="episode-production-workspace">
    <header className="episode-production-hero">
      <div><p className="eyebrow">Episode Production</p><h2>{summary.episode_code} · 本集生产</h2><p>以镜头为单位查看五个规范阶段；默认只显示真正需要处理的事项。</p></div>
      <button className="secondary" type="button" onClick={() => { void overview.refetch(); void shots.refetch(); }} disabled={overview.isFetching || shots.isFetching}>{overview.isFetching || shots.isFetching ? "刷新中…" : "刷新事实"}</button>
    </header>

    <section className="episode-production-summary" aria-label="生产概览">
      <article><span>镜头</span><strong>{summary.shot_count}</strong></article>
      <article className={summary.attention_count ? "attention" : ""}><span>待处理</span><strong>{summary.attention_count}</strong></article>
      <article><span>活动任务</span><strong>{summary.active_job_count}</strong></article>
      <article><span>下一步</span><strong>{NEXT_ACTION_LABELS[summary.next_action] ?? "查看本集状态"}</strong></article>
    </section>

    <section className="episode-production-run" aria-label="整集生产运行">
      {run ? <>
        <div><span className={`status-pill state-${run.status.toLowerCase()}`}>{run.status}</span><strong>运行 {run.id.slice(0, 8)}</strong><small>revision {run.revision}{run.updated_at ? ` · ${new Date(run.updated_at).toLocaleString()}` : ""}</small></div>
        <div className="episode-production-run__actions">
          {run.status === "RUNNING" && <button type="button" onClick={() => transition.mutate({ action: "pause", revision: run.revision })} disabled={transition.isPending}>暂停</button>}
          {run.status === "PAUSED_HITL" && <button className="primary-action" type="button" onClick={() => transition.mutate({ action: "resume", revision: run.revision })} disabled={transition.isPending}>确认并继续</button>}
          {run.status === "RUNNING" && <button type="button" onClick={() => transition.mutate({ action: "recover", revision: run.revision })} disabled={transition.isPending}>恢复检查</button>}
          <button className="danger-action" type="button" onClick={() => transition.mutate({ action: "cancel", revision: run.revision })} disabled={transition.isPending}>取消运行</button>
        </div>
      </> : <>
        <div><strong>检查并启动整集生产</strong><small>启动命令会先执行真实预检；阻塞时不会创建任务。</small></div>
        <div className="episode-production-run__options">
          <label><span>质量</span><select aria-label="生产质量" value={mode} onChange={(event) => setMode(event.target.value as EpisodeProductionMode)}><option value="DRAFT">草稿</option><option value="BALANCED">平衡</option><option value="QUALITY">精品</option></select></label>
          <label className="episode-production-toggle"><input type="checkbox" checked={tts} onChange={(event) => setTts(event.target.checked)} />自动人声</label>
          <button className="primary-action" type="button" onClick={() => start.mutate()} disabled={start.isPending}>{start.isPending ? "检查中…" : "检查并开始"}</button>
        </div>
      </>}
    </section>
    {mutationError && <p className="inline-error" role="alert">{String(mutationError)}</p>}
    {feedback && <p className="episode-production-feedback" role="status">{feedback}</p>}

    <Tabs items={VIEWS} selectedId={view} onChange={selectView} ariaLabel="分集生产镜头视图" />
    <section className="episode-production-list" aria-label={view === "attention" ? "待处理镜头" : "全部镜头"}>
      <div className="episode-production-list__heading"><div><h3>{view === "attention" ? "待处理镜头" : "全部镜头"}</h3><p>{view === "attention" ? "阻塞、失败、待确认和已过期项目会出现在这里。" : "逐镜核对五阶段、工作版本与新鲜度。"}</p></div><span>{shots.data.total} 镜</span></div>
      {shots.data.items.length ? <div className="production-shot-list">{shots.data.items.map((item) => <ShotCard key={item.shot_id} item={item} projectId={projectId} episodeId={episodeId} />)}</div> : <div className="episode-production-empty"><strong>{view === "attention" ? "当前没有待处理镜头" : "本集尚无镜头"}</strong><span>{view === "attention" ? "仍可打开“全部镜头”核对每个阶段事实。" : "请先在策划页建立镜头。"}</span></div>}
      {(cursor > 0 || shots.data.next_cursor !== null) && <nav className="episode-production-pagination" aria-label="镜头分页"><button type="button" disabled={cursor === 0} onClick={() => setCursor(Math.max(0, cursor - 50))}>上一页</button><span>{cursor + 1}–{Math.min(cursor + shots.data.items.length, shots.data.total)} / {shots.data.total}</span><button type="button" disabled={shots.data.next_cursor === null} onClick={() => setCursor(shots.data.next_cursor ?? cursor)}>下一页</button></nav>}
    </section>
  </div>;
}

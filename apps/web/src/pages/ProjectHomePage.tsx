import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getProjectCreatorSetup, getProjectEpisodeCatalog, getProjectHealth, listProjects } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import { ProjectStructureAppendPanel } from "../features/projects/ProjectStructureAppendPanel";

type EpisodeCatalog = Awaited<ReturnType<typeof getProjectEpisodeCatalog>>["catalog"];
type SeasonItem = EpisodeCatalog["seasons"][number];
type EpisodeItem = SeasonItem["episodes"][number];
type EpisodeGroup = { season: Omit<SeasonItem, "episodes">; episodes: EpisodeItem[] };

const orderValue = (item: { number?: number; display_order?: number }) => item.number ?? item.display_order ?? Number.MAX_SAFE_INTEGER;
const isDeliveredOrApproved = (episode: EpisodeItem) => /DELIVERED|APPROVED/.test(episode.production_status.toUpperCase());
const episodeStatusLabel: Record<string, string> = { NOT_STARTED: "未开始", DRAFT: "草稿", DELIVERED: "已交付", APPROVED: "已批准", IN_PROGRESS: "制作中" };
const episodeStatusClass: Record<string, string> = { NOT_STARTED: "state-not_started", DRAFT: "state-draft", DELIVERED: "state-delivered", APPROVED: "state-approved", IN_PROGRESS: "state-running" };

/** Project home: bounded multi-season production entry points (story → assets → episodes). */
export function ProjectHomePage() {
  const { projectId } = useParams();
  const project = useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: () => listProjects({}),
    select: (data) => data.items.find((item) => item.id === projectId),
  });
  const health = useQuery({
    queryKey: queryKeys.projects.health(projectId as string),
    queryFn: () => getProjectHealth(projectId as string),
    enabled: Boolean(projectId),
  });
  const seasons = useQuery({
    queryKey: queryKeys.seasons.catalog(projectId as string),
    queryFn: () => getProjectEpisodeCatalog(projectId as string),
    enabled: Boolean(projectId),
  });
  const orderedSeasons = useMemo(() => (seasons.data?.catalog.seasons ?? [])
    .slice()
    .sort((left, right) => orderValue(left) - orderValue(right) || left.code.localeCompare(right.code)), [seasons.data?.catalog.seasons]);
  const groups = useMemo<EpisodeGroup[]>(() => orderedSeasons.map((season) => ({
    season: { id: season.id, code: season.code, title: season.title, number: season.number, display_order: season.display_order },
    episodes: season.episodes
      .slice()
      .sort((left, right) => orderValue(left) - orderValue(right) || left.code.localeCompare(right.code)),
  })), [orderedSeasons]);
  const creatorSetup = useQuery({
    queryKey: queryKeys.projects.creatorSetup(projectId as string),
    queryFn: () => getProjectCreatorSetup(projectId as string),
    enabled: Boolean(projectId),
  });
  const allEpisodes = groups.flatMap((group) => group.episodes);
  const targetEpisode = allEpisodes.find((episode) => !isDeliveredOrApproved(episode)) ?? allEpisodes[0];
  const allComplete = Boolean(allEpisodes.length) && allEpisodes.every(isDeliveredOrApproved);
  const episodesPending = seasons.isPending;
  const episodesError = seasons.error;
  const current = project.data;
  const targetLabel = targetEpisode ? `${targetEpisode.code} · ${targetEpisode.title}` : "尚无分集";
  const milestones = creatorSetup.data?.setup.milestones;
  const episodePath = targetEpisode ? `/projects/${projectId}/episodes/${targetEpisode.id}` : `/projects/${projectId}`;
  const directorPath = targetEpisode ? `${episodePath}/direct` : `/projects/${projectId}`;
  const generationPath = targetEpisode ? `${episodePath}/generation` : `/projects/${projectId}`;
  const setupChecks = [
    { id: "structure", label: "季度与分集已建立", ready: Boolean(milestones?.episode_count.ready), to: `/projects/${projectId}`, action: "查看分集" },
    { id: "story", label: "故事已完成一次可审阅拆解", ready: Boolean(milestones?.reviewable_story_draft_count.ready), to: `/projects/${projectId}/story#story-import`, action: "导入并拆解故事" },
    { id: "assets", label: "角色或场景资产已建档", ready: Boolean(milestones?.active_story_asset_count.ready), to: `/projects/${projectId}/assets`, action: "建立资产" },
    { id: "plan", label: "生产方案已绑定", ready: Boolean(milestones?.production_plan_count.ready), to: `/projects/${projectId}/production-settings?view=overview`, action: "配置生产方案" },
    { id: "profiles", label: "至少一项已发布模型能力已绑定", ready: Boolean(milestones?.published_profile_binding_count.ready), to: `/projects/${projectId}/production-settings?view=profiles`, action: "绑定模型能力" },
    { id: "intent", label: "第一镜创作意图已保存", ready: Boolean(milestones?.shot_intent_count.ready), to: directorPath, action: "进入导演台创建意图" },
    { id: "generation", label: "第一镜生成任务已提交", ready: Boolean(milestones?.shot_generation_job_count.ready), to: generationPath, action: "提交第一镜生成" },
  ];
  const setupPending = creatorSetup.isPending || episodesPending;
  const setupError = creatorSetup.error;
  const setupReadyCount = setupChecks.filter((item) => item.ready).length;
  const nextSetup = setupChecks.find((item) => !item.ready);

  return (
    <div className="v2-page">
      <div className="panel-heading"><div><p className="eyebrow">项目总览</p><h2>{current?.title ?? "项目加载中…"}</h2></div><span className="status-pill">{current?.code ?? ""}</span></div>
      <section className="panel onboarding-panel" aria-labelledby="onboarding-title">
        <div className="panel-heading"><div><p className="eyebrow">首次制作</p><h3 id="onboarding-title">从故事到第一镜</h3></div><span className="status-pill neutral">{setupPending ? "读取中" : `${setupReadyCount}/${setupChecks.length} 已就绪`}</span></div>
        <ol className="onboarding-checklist">
          {setupChecks.map((check) => <li key={check.id} className={check.ready ? "ready" : "pending"}>
            <span aria-hidden="true">{check.ready ? "✓" : "○"}</span><strong>{check.label}</strong><Link className="secondary v2-inline-link" to={check.to}>{check.ready ? "查看" : check.action}</Link>
          </li>)}
        </ol>
        {setupError && <p className="inline-error" role="alert">首次制作状态暂时无法读取：{setupError instanceof Error ? setupError.message : String(setupError)}</p>}
        {!setupPending && !setupError && nextSetup && <p className="onboarding-next">建议下一步：<Link to={nextSetup.to}>{nextSetup.action}</Link>。完成后回到这里继续，不需要记住系统路径。</p>}
        {!setupPending && !setupError && !nextSetup && <p className="review-success" role="status">第一镜已进入生成队列；可以在任务队列观察进度，或继续制作目标分集。</p>}
        {!setupPending && !setupError && !creatorSetup.data?.setup.operations.worker_ready && Boolean(milestones?.published_profile_binding_count.ready || milestones?.shot_generation_job_count.ready) && <p className="production-readiness-blocker" role="status">本机生成执行器尚未连接；已经保存的创作内容不受影响。<Link to={`/jobs?project=${encodeURIComponent(projectId ?? "")}`}>准备开始生成</Link></p>}
      </section>
      <section aria-labelledby="start-by-goal-title">
        <div className="panel-heading"><div><p className="eyebrow">按目标开始</p><h3 id="start-by-goal-title">你现在想从哪里继续？</h3></div><span className="status-pill neutral">目标分集：{targetLabel}</span></div>
        {allComplete && <p className="review-success" role="status">当前已加载分集均已交付或批准；入口定位到最早一集，便于复核历史。</p>}
        {(health.data?.blockers?.length ?? 0) > 0 && health.data && <div className="production-readiness-blocker" role="alert"><strong>生产准备度：{health.data.blockers.length} 项需处理</strong><ul>{health.data.blockers.slice(0, 6).map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></div>}
        <div className="v2-goal-grid">
          <article><span className="status-pill neutral">原文</span><h3>导入小说 / 剧本</h3><p>下一步：在故事工作区导入长文并预览 AI 拆解。</p><Link className="secondary v2-inline-link v2-goal-action" to={`/projects/${projectId}/story#story-import`}>进入故事工作区导入与拆解</Link></article>
          <article><span className="status-pill neutral">分集</span><h3>继续未完成分集</h3><p>下一步：从 {targetLabel} 的分集规划继续到导演、审核与交付。</p>{targetEpisode ? <Link className="secondary v2-inline-link v2-goal-action" to={`/projects/${projectId}/episodes/${targetEpisode.id}/plan`}>继续 {targetEpisode.code}</Link> : <small>当前项目还没有分集</small>}</article>
          <article><span className="status-pill neutral">资产</span><h3>从已有分镜 / 资产开始</h3><p>下一步：补齐 {targetLabel} 所需角色状态、场景参考和镜头使用关系。</p><Link className="secondary v2-inline-link v2-goal-action" to={`/projects/${projectId}/assets${targetEpisode ? `?episode=${encodeURIComponent(targetEpisode.id)}` : ""}`}>打开资产圣经</Link></article>
          <article><span className="status-pill neutral">高级</span><h3>自由创作</h3><p>下一步：围绕 {targetLabel} 检查依赖、分支实验与非线性工作流。</p><Link className="secondary v2-inline-link v2-goal-action" to={`/projects/${encodeURIComponent(projectId ?? "")}/canvas${targetEpisode ? `?episode=${encodeURIComponent(targetEpisode.id)}` : ""}`}>打开高级画布</Link></article>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading"><div><p className="eyebrow">分集</p><h3>选择一集进入生产流程</h3></div><span className="status-pill neutral"><span className="tabular-nums">{allEpisodes.length}</span> 集 · 单次只读目录</span></div>
        {episodesPending && <p className="empty-state" role="status">正在读取各季度分集…</p>}
        {episodesError && <p className="inline-error" role="alert">分集目录读取不完整：{episodesError instanceof Error ? episodesError.message : String(episodesError)}</p>}
        {!episodesPending && !episodesError && projectId ? <ProjectStructureAppendPanel projectId={projectId} seasons={groups.map((group) => ({ ...group.season, episodes: group.episodes }))} /> : null}
        {!episodesPending && groups.map((group) => <section className="v2-season-group" key={group.season.id} aria-labelledby={`season-${group.season.id}`}>
          <div className="panel-heading"><h4 id={`season-${group.season.id}`}>{group.season.code} · {group.season.title}</h4><span className="status-pill neutral">{group.episodes.length} 集</span></div>
          <div className="v2-episode-list">
            {group.episodes.map((episode) => { const status = episode.production_status.toUpperCase(); return <div className="v2-episode-row" key={episode.id}><strong>{episode.code} · {episode.title}</strong><span className={`status-pill ${episodeStatusClass[status] ?? "neutral"}`}>{episodeStatusLabel[status] ?? episode.production_status}</span><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episode.id}/plan`}>分集规划</Link><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episode.id}/direct`}>导演台</Link><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episode.id}/run`}>整集生产</Link></div>; })}
            {group.episodes.length === 0 && <p className="empty-state">本季度还没有分集。</p>}
          </div>
        </section>)}
      </section>
    </div>
  );
}

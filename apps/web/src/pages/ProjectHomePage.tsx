import { useMemo } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getProjectHealth, listEpisodes, listProjects, listSeasons } from "../generated/api";
import { queryKeys } from "../query/queryKeys";

const SEASON_LIMIT = 8;
const EPISODE_LIMIT_PER_SEASON = 100;
type SeasonItem = Awaited<ReturnType<typeof listSeasons>>["items"][number] & { number?: number; display_order?: number };
type EpisodeItem = Awaited<ReturnType<typeof listEpisodes>>["items"][number] & { number?: number; display_order?: number };
type EpisodeGroup = { season: SeasonItem; episodes: EpisodeItem[] };

const orderValue = (item: { number?: number; display_order?: number }) => item.number ?? item.display_order ?? Number.MAX_SAFE_INTEGER;
const isDeliveredOrApproved = (episode: EpisodeItem) => /DELIVERED|APPROVED/.test(episode.production_status.toUpperCase());

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
    queryKey: queryKeys.seasons.list(projectId as string),
    queryFn: () => listSeasons(projectId as string),
    enabled: Boolean(projectId),
  });
  const orderedSeasons = useMemo(() => ((seasons.data?.items ?? []) as SeasonItem[])
    .slice()
    .sort((left, right) => orderValue(left) - orderValue(right) || left.code.localeCompare(right.code))
    .slice(0, SEASON_LIMIT), [seasons.data?.items]);
  const episodeQueries = useQueries({
    queries: orderedSeasons.map((season) => ({
      queryKey: queryKeys.episodes.list(season.id, EPISODE_LIMIT_PER_SEASON),
      queryFn: () => listEpisodes(season.id),
      enabled: Boolean(projectId),
    })),
  });
  const groups = useMemo<EpisodeGroup[]>(() => orderedSeasons.map((season, index) => ({
    season,
    episodes: ((episodeQueries[index]?.data?.items ?? []) as EpisodeItem[])
      .slice()
      .sort((left, right) => orderValue(left) - orderValue(right) || left.code.localeCompare(right.code))
      .slice(0, EPISODE_LIMIT_PER_SEASON),
  })), [episodeQueries, orderedSeasons]);
  const allEpisodes = groups.flatMap((group) => group.episodes);
  const targetEpisode = allEpisodes.find((episode) => !isDeliveredOrApproved(episode)) ?? allEpisodes[0];
  const allComplete = Boolean(allEpisodes.length) && allEpisodes.every(isDeliveredOrApproved);
  const episodesPending = seasons.isPending || episodeQueries.some((query) => query.isPending);
  const episodesError = seasons.error ?? episodeQueries.find((query) => query.error)?.error;
  const current = project.data;
  const targetLabel = targetEpisode ? `${targetEpisode.code} · ${targetEpisode.title}` : "尚无分集";

  return (
    <div className="v2-page">
      <div className="panel-heading"><div><p className="eyebrow">项目总览</p><h2>{current?.title ?? "项目加载中…"}</h2></div><span className="status-pill">{current?.code ?? ""}</span></div>
      {health.data?.blockers && health.data.blockers.length > 0 && <p className="inline-error" role="alert">{health.data.blockers.join("；")}</p>}
      <section aria-labelledby="start-by-goal-title">
        <div className="panel-heading"><div><p className="eyebrow">按目标开始</p><h3 id="start-by-goal-title">你现在想从哪里继续？</h3></div><span className="status-pill neutral">目标分集：{targetLabel}</span></div>
        {allComplete && <p className="review-success" role="status">当前已加载分集均已交付或批准；入口定位到最早一集，便于复核历史。</p>}
        <div className="v2-goal-grid">
          <article><span className="status-pill neutral">原文</span><h3>导入小说 / 剧本</h3><p>下一步：为 {targetLabel} 核对原文权威并预览 AI 拆解。</p>{targetEpisode ? <Link to={`/projects/${projectId}/episodes/${targetEpisode.id}/plan`}>进入导入与策划</Link> : <small>先创建季度与分集后可用</small>}</article>
          <article><span className="status-pill neutral">分集</span><h3>继续未完成分集</h3><p>下一步：从 {targetLabel} 的分集规划继续到导演、审核与交付。</p>{targetEpisode ? <Link to={`/projects/${projectId}/episodes/${targetEpisode.id}/plan`}>继续 {targetEpisode.code}</Link> : <small>当前项目还没有分集</small>}</article>
          <article><span className="status-pill neutral">资产</span><h3>从已有分镜 / 资产开始</h3><p>下一步：补齐 {targetLabel} 所需角色状态、场景参考和镜头使用关系。</p><Link to={`/projects/${projectId}/assets${targetEpisode ? `?episode=${encodeURIComponent(targetEpisode.id)}` : ""}`}>打开资产圣经</Link></article>
          <article><span className="status-pill neutral">高级</span><h3>自由创作</h3><p>下一步：围绕 {targetLabel} 检查依赖、分支实验与非线性工作流。</p><Link to={`/projects/${encodeURIComponent(projectId ?? "")}/canvas${targetEpisode ? `?episode=${encodeURIComponent(targetEpisode.id)}` : ""}`}>打开高级画布</Link></article>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading"><div><p className="eyebrow">分集</p><h3>选择一集进入生产流程</h3></div><span className="status-pill">{allEpisodes.length} 集 · 最多读取 {SEASON_LIMIT} 季 / 每季 {EPISODE_LIMIT_PER_SEASON} 集</span></div>
        {episodesPending && <p className="empty-state" role="status">正在读取各季度分集…</p>}
        {episodesError && <p className="inline-error" role="alert">分集目录读取不完整：{episodesError instanceof Error ? episodesError.message : String(episodesError)}</p>}
        {!episodesPending && groups.length === 0 && <p className="empty-state">当前项目还没有季度；请先建立季度与分集。</p>}
        {!episodesPending && groups.map((group) => <section className="v2-season-group" key={group.season.id} aria-labelledby={`season-${group.season.id}`}>
          <div className="panel-heading"><h4 id={`season-${group.season.id}`}>{group.season.code} · {group.season.title}</h4><span className="status-pill neutral">{group.episodes.length} 集</span></div>
          <div className="v2-episode-list">
            {group.episodes.map((episode) => <div className="v2-episode-row" key={episode.id}><strong>{episode.code} · {episode.title}</strong><span className="muted">{episode.production_status}</span><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episode.id}/plan`}>分集规划</Link><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episode.id}/direct`}>导演台</Link><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episode.id}/run`}>整集生产</Link></div>)}
            {group.episodes.length === 0 && <p className="empty-state">本季度还没有分集。</p>}
          </div>
        </section>)}
      </section>
      <section className="panel"><div className="panel-heading"><div><p className="eyebrow">资产圣经</p><h3>角色 / 场景 / 道具 / 服装</h3></div></div><Link className="primary-action v2-inline-link" to={`/projects/${projectId}/assets`}>打开资产圣经</Link></section>
    </div>
  );
}

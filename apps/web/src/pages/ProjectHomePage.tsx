import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getProjectOverviewV2, type ProductRouteTarget, type ProjectOverviewV2 } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import { ProjectStructureAppendPanel } from "../features/projects/ProjectStructureAppendPanel";
import { routes } from "../app/routeRegistry";

type EpisodeCatalog = Pick<ProjectOverviewV2, "seasons">;
type SeasonItem = EpisodeCatalog["seasons"][number];
type EpisodeItem = SeasonItem["episodes"][number];
type EpisodeGroup = { season: Omit<SeasonItem, "episodes">; episodes: EpisodeItem[] };

const orderValue = (item: { number?: number; display_order?: number }) => item.number ?? item.display_order ?? Number.MAX_SAFE_INTEGER;
const isComplete = (episode: EpisodeItem) => /DELIVERED|APPROVED/.test(episode.production_status.toUpperCase());
const statusLabel: Record<string, string> = { NOT_STARTED: "未开始", DRAFT: "草稿", DELIVERED: "已交付", APPROVED: "已批准", IN_PROGRESS: "制作中" };
const statusClass: Record<string, string> = { NOT_STARTED: "state-not_started", DRAFT: "state-draft", DELIVERED: "state-delivered", APPROVED: "state-approved", IN_PROGRESS: "state-running" };

export function ProjectHomePage() {
  const { projectId = "" } = useParams();
  const overview = useQuery({ queryKey: queryKeys.projects.overview(projectId), queryFn: () => getProjectOverviewV2(projectId), enabled: Boolean(projectId) });
  const groups: EpisodeGroup[] = (overview.data?.seasons ?? []).slice().sort((a, b) => orderValue(a) - orderValue(b) || a.code.localeCompare(b.code)).map((season) => ({
    season: { id: season.id, code: season.code, title: season.title, number: season.number, display_order: season.display_order },
    episodes: season.episodes.slice().sort((a, b) => orderValue(a) - orderValue(b) || a.code.localeCompare(b.code)),
  }));
  const episodes = groups.flatMap((group) => group.episodes);
  const targetTo = (target: ProductRouteTarget) => {
    if (target.kind === "PROJECT_STRUCTURE") return "#project-structure";
    if (target.kind === "STORY") return `${routes.story(projectId)}#story-import`;
    if (target.kind === "ASSETS") return routes.assets(projectId);
    if (target.kind === "SETTINGS") return routes.settings(projectId, target.section ?? "production");
    if (target.kind === "SHOT_STUDIO" && target.episode_id) return `${routes.shotStudio(projectId, target.episode_id)}${target.focus ? `?focus=${encodeURIComponent(target.focus)}` : ""}`;
    if (target.kind === "EPISODE_PRODUCTION" && target.episode_id) return routes.episodeProduction(projectId, target.episode_id);
    return routes.projectHome(projectId);
  };
  const nextAction = overview.data?.next_action;

  return <div className="v2-page project-overview-page">
    <div className="panel-heading"><div><p className="eyebrow">项目首页</p><h2>{overview.data?.project.title ?? "项目加载中…"}</h2></div><span className="status-pill">{overview.data?.project.code ?? ""}</span></div>

    {overview.isPending ? <section className="panel project-next-step" role="status">正在计算项目下一步…</section> : overview.error ? <section className="panel"><p className="inline-error" role="alert">项目首页读取失败：{String(overview.error)}</p><button type="button" className="secondary" onClick={() => void overview.refetch()}>重新读取</button></section> : nextAction ? <section className="panel project-next-step" aria-labelledby="project-next-title">
      <div><p className="eyebrow">建议下一步</p><h3 id="project-next-title">{nextAction.title}</h3><p className="muted">{nextAction.description}</p></div>
      <Link className="primary-action v2-inline-link" to={targetTo(nextAction.target)}>{nextAction.label}</Link>
    </section> : null}

    {Boolean(overview.data?.blockers.length) && <section className="panel project-attention-summary" aria-labelledby="project-attention-title">
      <div className="panel-heading"><div><p className="eyebrow">待处理</p><h3 id="project-attention-title">项目阻塞</h3></div><span className="status-pill neutral">{overview.data?.blockers.length} 项</span></div>
      <details><summary>查看阻塞原因</summary><ul>{overview.data?.blockers.map((blocker) => <li key={blocker.code}><Link to={targetTo(blocker.owner)}>{blocker.label}</Link></li>)}</ul></details>
    </section>}

    <section className="panel" aria-labelledby="episode-list-title">
      <div className="panel-heading"><div><p className="eyebrow">分集</p><h3 id="episode-list-title">制作进度</h3></div><span className="status-pill neutral">{episodes.length} 集</span></div>
      {overview.isPending && <p className="empty-state" role="status">正在读取分集目录…</p>}
      {!overview.isPending && groups.map((group) => <section className="v2-season-group" key={group.season.id} aria-labelledby={`season-${group.season.id}`}>
        <div className="panel-heading"><h4 id={`season-${group.season.id}`}>{group.season.code} · {group.season.title}</h4><span className="status-pill neutral">{group.episodes.length} 集</span></div>
        <div className="v2-episode-list">{group.episodes.map((episode) => { const state = episode.production_status.toUpperCase(); return <div className="v2-episode-row" key={episode.id}><strong>{episode.code} · {episode.title}</strong><span className={`status-pill ${statusClass[state] ?? "neutral"}`}>{statusLabel[state] ?? episode.production_status}</span><Link className="secondary v2-inline-link" to={isComplete(episode) ? routes.delivery(projectId, episode.id) : state === "NOT_STARTED" || state === "DRAFT" ? routes.episodePlan(projectId, episode.id) : routes.episodeProduction(projectId, episode.id)}>{isComplete(episode) ? "查看交付" : "继续制作"}</Link></div>; })}{group.episodes.length === 0 && <p className="empty-state">本季度还没有分集。</p>}</div>
      </section>)}
    </section>

    {Boolean(overview.data?.recent_activity.length) && <section className="panel" aria-labelledby="recent-activity-title"><div className="panel-heading"><div><p className="eyebrow">最近活动</p><h3 id="recent-activity-title">项目变化</h3></div></div><ul>{overview.data?.recent_activity.map((item) => <li key={item.event_id}><strong>{item.type}</strong><span className="muted"> · {item.occurred_at}</span></li>)}</ul></section>}

    <section id="project-structure" className="panel" aria-labelledby="project-structure-title">
      <div className="panel-heading"><div><p className="eyebrow">系列结构</p><h3 id="project-structure-title">季度与分集</h3></div></div>
      <ProjectStructureAppendPanel projectId={projectId} seasons={groups.map((group) => ({ ...group.season, episodes: group.episodes }))} projectDefaultDurationMs={Number(episodes[0]?.target_duration_ms) || undefined} />
    </section>
  </div>;
}

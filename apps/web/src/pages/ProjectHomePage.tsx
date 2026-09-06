import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getProjectOverviewV2, type ProductRouteTarget } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import { EpisodeProgressLibrary, type EpisodeProgressGroup } from "../features/projects/EpisodeProgressLibrary";
import { ProjectStructureAppendPanel } from "../features/projects/ProjectStructureAppendPanel";
import { routes } from "../app/routeRegistry";
import "../features/projects/episode-progress-library.css";

type EpisodeGroup = EpisodeProgressGroup;

const orderValue = (item: { number?: number; display_order?: number }) => item.number ?? item.display_order ?? Number.MAX_SAFE_INTEGER;

export function ProjectHomePage() {
  const { projectId = "" } = useParams();
  const overview = useQuery({ queryKey: queryKeys.projects.overview(projectId), queryFn: () => getProjectOverviewV2(projectId), enabled: Boolean(projectId) });
  const groups: EpisodeGroup[] = (overview.data?.seasons ?? []).slice().sort((a, b) => orderValue(a) - orderValue(b) || a.code.localeCompare(b.code)).map((season) => ({
    season: { id: season.id, code: season.code, title: season.title, number: season.number, display_order: season.display_order },
    episodes: season.episodes.slice().sort((a, b) => orderValue(a) - orderValue(b) || a.code.localeCompare(b.code)),
  }));
  const targetTo = (target: ProductRouteTarget) => {
    if (target.kind === "PROJECT_STRUCTURE") return "#project-structure";
    if (target.kind === "STORY") return routes.story(projectId);
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

    <EpisodeProgressLibrary projectId={projectId} groups={groups} loading={overview.isPending} />

    {Boolean(overview.data?.recent_activity.length) && <section className="panel" aria-labelledby="recent-activity-title"><div className="panel-heading"><div><p className="eyebrow">最近活动</p><h3 id="recent-activity-title">项目变化</h3></div></div><ul>{overview.data?.recent_activity.map((item) => <li key={item.event_id}><strong>{item.type}</strong><span className="muted"> · {item.occurred_at}</span></li>)}</ul></section>}

    <section id="project-structure" className="panel" aria-labelledby="project-structure-title">
      <div className="panel-heading"><div><p className="eyebrow">系列结构</p><h3 id="project-structure-title">季度与分集</h3></div></div>
      <ProjectStructureAppendPanel
        projectId={projectId}
        seasons={groups.map((group) => ({ ...group.season, episodes: group.episodes }))}
        projectDefaultDurationMs={Number(overview.data?.project.target_duration_ms) || undefined}
      />
    </section>
  </div>;
}

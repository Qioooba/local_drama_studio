import { Link, useParams } from "react-router-dom";
import { EpisodeReviewWorkspace } from "../features/episode-review-v2/EpisodeReviewWorkspace";
import { routes } from "../app/routeRegistry";
import "./creative-workspaces.css";

/** Episode review: batch review of candidate media. */
export function EpisodeReviewPage() {
  const { projectId, episodeId } = useParams();
  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  return (
    <div className="v2-page creative-task-page episode-review-page-v2">
      <div className="panel-heading"><div><p className="eyebrow">人工权威</p><h3>从候选问题到整集批准</h3></div><div className="v2-actions"><Link className="secondary v2-inline-link" to={routes.shotStudio(projectId, episodeId)}>返回镜头</Link><Link className="secondary v2-inline-link" to={routes.postEdit(projectId, episodeId)}>查看编辑</Link></div></div>
      <p className="muted">集中审阅镜头媒体与整集成片，在播放器当前画面留下批注，并记录可撤回、可追溯的人工决定。工作采用仍由镜头工作台负责。</p>
      <EpisodeReviewWorkspace projectId={projectId} episodeId={episodeId} />
    </div>
  );
}

import { Link, useParams } from "react-router-dom";
import { EpisodeReviewWorkspace } from "../features/episode-review-v2/EpisodeReviewWorkspace";
import "./creative-workspaces.css";

/** Episode review: batch review of candidate media. */
export function EpisodeReviewPage() {
  const { projectId, episodeId } = useParams();
  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  return (
    <div className="v2-page creative-task-page episode-review-page-v2">
      <div className="panel-heading"><div><p className="eyebrow">本集审核</p><h2>从候选问题到整集批准</h2></div><div className="v2-actions"><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episodeId}/direct`}>返回导演台</Link><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episodeId}/timeline`}>查看时间线</Link></div></div>
      <p className="muted">集中处理本集未解决候选；采用候选、批准媒体与批准整集始终是三项可追溯的人工作业。</p>
      <EpisodeReviewWorkspace projectId={projectId} episodeId={episodeId} />
    </div>
  );
}

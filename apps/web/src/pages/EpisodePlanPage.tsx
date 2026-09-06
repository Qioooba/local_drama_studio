import { useParams } from "react-router-dom";
import { ErrorBoundary } from "../components/ui/ErrorBoundary";
import { EpisodeProductionWorkspace } from "../features/episode-production-v2/EpisodeProductionWorkspace";

/** One creator-facing owner for episode planning, generation and exceptions. */
export function EpisodePlanPage() {
  const { projectId = "", episodeId = "" } = useParams();
  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  return <ErrorBoundary projectId={projectId} fallbackTitle="本集制作工作区异常">
    <div className="v2-page"><EpisodeProductionWorkspace projectId={projectId} episodeId={episodeId} /></div>
  </ErrorBoundary>;
}

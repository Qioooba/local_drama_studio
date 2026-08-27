import { useParams } from "react-router-dom";
import { EpisodeProductionWorkspace } from "../features/episode-production-v2/EpisodeProductionWorkspace";
import "./creative-workspaces.css";

/** A single episode-production owner: exceptions first, full shot state on demand. */
export function EpisodeRunPage() {
  const { projectId, episodeId } = useParams();

  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  return <div className="v2-page creative-task-page episode-run-page-v2"><EpisodeProductionWorkspace projectId={projectId} episodeId={episodeId} /></div>;
}

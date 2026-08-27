import { Link, useParams } from "react-router-dom";
import { routes } from "../app/routeRegistry";
import { EpisodeAudioWorkspace } from "../features/audio-v2/EpisodeAudioWorkspace";
import "./creative-workspaces.css";

export function AudioPage() {
  const { projectId, episodeId } = useParams();
  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  return <div className="v2-page creative-task-page audio-workspace-v2">
    <div className="panel-heading"><div><p className="eyebrow">后期声音</p><h3>对白引用、音乐、音效与混音</h3></div><div className="action-row"><Link className="secondary v2-inline-link" to={routes.postReview(projectId, episodeId)}>声音审核</Link><Link className="primary-action v2-inline-link" to={routes.postEdit(projectId, episodeId)}>进入编辑</Link></div></div>
    <p className="muted">试听 Shot Studio 已采用的工作语音，并为本集安排背景音乐与音效。对白和 TTS 的编辑仍在镜头工作台，正式批准只在审核工作区。</p>
    <EpisodeAudioWorkspace projectId={projectId} episodeId={episodeId} />
  </div>;
}

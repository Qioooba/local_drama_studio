import { Link, useParams } from "react-router-dom";
import { routes } from "../app/routeRegistry";
import { EpisodeEditWorkspace } from "../features/edit-v2/EpisodeEditWorkspace";
import "../features/timeline-v2/timeline-v2.css";
import "./creative-workspaces.css";

export function TimelinePage() {
  const { projectId, episodeId } = useParams();
  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  return <div className="v2-page timeline-page-v2 creative-task-page">
    <div className="panel-heading"><div><p className="eyebrow">本集后期</p><h3>播放器、多轨编排与冻结</h3></div><div className="action-row"><Link className="secondary v2-inline-link" to={routes.postAudio(projectId, episodeId)}>返回声音</Link><Link className="secondary v2-inline-link" to={routes.delivery(projectId, episodeId)}>前往交付</Link></div></div>
    <p className="muted">在同一画布核对采用视频、对白、BGM/SFX 和字幕。每次保存与冻结都会创建新版本，不覆盖旧成片依据。</p>
    <EpisodeEditWorkspace projectId={projectId} episodeId={episodeId} />
  </div>;
}

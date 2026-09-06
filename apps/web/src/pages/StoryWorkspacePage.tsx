import { Link, useParams } from "react-router-dom";
import { OneClickPipelineWorkbench } from "../features/pipeline/OneClickPipelineWorkbench";
import "./story-workspace.css";

/**
 * The story route is the front door of the production pipeline. Detailed
 * import, breakdown and bible records remain internal project data; they are
 * not separate jobs a creator must complete before starting an episode.
 */
export function StoryWorkspacePage() {
  const { projectId } = useParams();

  if (!projectId) return <p className="inline-error" role="alert">缺少项目上下文。</p>;

  return (
    <div className="v2-page story-workspace story-workspace--simple">
      <header className="story-workspace-header">
        <div>
          <p className="eyebrow">AI 漫剧制作</p>
          <h2>上传原稿，AI 自动完成全剧规划</h2>
          <p className="muted">系统自动识别分集、核心人物与场景，再按集生成分镜和成片；只有关键冲突或异常才需要你确认。</p>
        </div>
        <Link className="secondary v2-inline-link" to={`/projects/${projectId}`}>返回项目</Link>
      </header>

      <OneClickPipelineWorkbench projectId={projectId} />
    </div>
  );
}

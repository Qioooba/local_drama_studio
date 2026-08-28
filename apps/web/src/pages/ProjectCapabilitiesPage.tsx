import { useParams } from "react-router-dom";
import { GenerationPreferencePanel } from "../features/preferences-v2/GenerationPreferencePanel";

/** Project-scoped defaults only; models and capabilities are global system resources. */
export function ProjectCapabilitiesPage() {
  const { projectId = "" } = useParams();
  if (!projectId) return <p className="inline-error" role="alert">缺少项目上下文。</p>;
  return <div className="v2-page settings-section-page project-capabilities-page">
    <div className="panel-heading"><div><p className="eyebrow">项目默认选择</p><h3>生成偏好</h3></div><span className="status-pill neutral">引用全局能力目录</span></div>
    <p className="muted">这里不创建项目模型，只决定本项目在图像、视频、声音和故事处理时优先使用哪个全局能力；未指定时由系统自动推荐。</p>
    <GenerationPreferencePanel initialProjectId={projectId} />
  </div>;
}

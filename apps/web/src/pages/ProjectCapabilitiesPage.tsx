import { useParams } from "react-router-dom";
import { GenerationPreferencePanel } from "../features/preferences-v2/GenerationPreferencePanel";

/** Project-scoped capability bindings only; publishing models and connections belongs to System Center. */
export function ProjectCapabilitiesPage() {
  const { projectId = "" } = useParams();
  if (!projectId) return <p className="inline-error" role="alert">缺少项目上下文。</p>;
  return <div className="v2-page settings-section-page project-capabilities-page">
    <div className="panel-heading"><div><p className="eyebrow">项目继承与覆盖</p><h3>创作能力绑定</h3></div><span className="status-pill neutral">仅绑定已发布能力</span></div>
    <p className="muted">按用途选择本项目使用的图像、视频、声音和故事拆解能力。模型文件、连接和运行契约由系统中心统一发布。</p>
    <GenerationPreferencePanel initialProjectId={projectId} />
  </div>;
}

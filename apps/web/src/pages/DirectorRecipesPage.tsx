import { useParams } from "react-router-dom";
import { DirectorRecipeManager } from "../features/recipes-v2/DirectorRecipeManager";

export function DirectorRecipesPage() {
  const { projectId = "" } = useParams();
  return <div className="v2-page"><div className="panel-heading"><div><p className="eyebrow">专业生产模板</p><h2>导演配方</h2></div><span className="status-pill">0046 · Declarative</span></div><p className="muted">把画幅、镜头规划、资产要求、生成能力与 QC 固定为可复现的不可变版本。项目只在你显式绑定时升级。</p><DirectorRecipeManager projectId={projectId} /></div>;
}


import { useParams } from "react-router-dom";
import { DirectorRecipeManager } from "../features/recipes-v2/DirectorRecipeManager";
import { ConceptGuide } from "../components/ui";

export function DirectorRecipesPage() {
  const { projectId = "" } = useParams();
  return <div className="v2-page"><div className="panel-heading"><div><p className="eyebrow">专业生产模板</p><h2>导演模板</h2></div><span className="status-pill">保留历史版本</span></div><p className="muted">把画幅、镜头规划、角色与场景要求、生成能力和自动质检规则保存成可重复使用的模板。只有你明确选择新版本时，项目才会更新。</p><ConceptGuide title="导演模板怎么用？" items={[{ term: "模板", description: "一组可复用的导演和生产规则，适合让同一系列作品保持一致。" }, { term: "不可变版本", description: "已保存的版本不会被覆盖；修改会创建新版本，因此旧项目仍可复现。" }, { term: "绑定", description: "明确指定项目使用某个模板版本，不会自动替换为最新版。" }]} /><DirectorRecipeManager projectId={projectId} /></div>;
}

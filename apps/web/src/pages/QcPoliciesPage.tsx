import { useParams } from "react-router-dom";
import { QcPolicyManager } from "../features/qc-policy-v2/QcPolicyManager";
import { ConceptGuide } from "../components/ui";

export function QcPoliciesPage() {
  const { projectId = "", episodeId = "", shotId = "" } = useParams();
  return <div className="v2-page"><div className="panel-heading"><div><p className="eyebrow">生产质量控制</p><h2>自动质检规则</h2></div><span className="status-pill">保留规则版本</span></div><p className="muted">设置机器要检查哪些问题、达到什么数值才算通过，以及失败后最多允许自动重试几次。机器检查通过不等于人工批准。</p><ConceptGuide title="质检规则名词说明" items={[{ term: "阈值", description: "判断通过或不通过的数值界线，例如清晰度不得低于某个值。" }, { term: "自动重试", description: "机器检查失败后，系统按相同要求再生成一次；会受次数上限保护。" }, { term: "机器通过", description: "只表示自动检查满足规则，最终是否采用仍由人工决定。" }]} /><QcPolicyManager projectId={projectId} initialEpisodeId={episodeId} initialShotId={shotId} /></div>;
}

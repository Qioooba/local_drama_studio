import { useParams } from "react-router-dom";
import { QcPolicyManager } from "../features/qc-policy-v2/QcPolicyManager";

export function QcPoliciesPage() {
  const { projectId = "", episodeId = "", shotId = "" } = useParams();
  return <div className="v2-page"><div className="panel-heading"><div><p className="eyebrow">生产治理</p><h2>QC 策略</h2></div><span className="status-pill">版本化门禁</span></div><p className="muted">定义检查类别、阈值和有限自动重抽。所有策略版本不可变；机器检查通过仍不会创建人工批准。</p><QcPolicyManager projectId={projectId} initialEpisodeId={episodeId} initialShotId={shotId} /></div>;
}


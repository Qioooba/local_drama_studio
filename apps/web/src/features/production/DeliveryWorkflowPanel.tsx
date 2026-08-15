import { useState } from "react";
import { buildDeliveryPackage, renderEpisode, verifyDeliveryPackage } from "../../generated/api";

export function DeliveryWorkflowPanel({ episodeId, timelineRevisionId, renderId, targetVersionId, deliveryId, onChanged }: { episodeId: string; timelineRevisionId: string | null; renderId: string | null; targetVersionId: string | null; deliveryId: string | null; onChanged?: () => void }) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const run = async (action: "render" | "build" | "verify") => {
    setPending(action); setError(null); setSuccess(null);
    try {
      if (action === "render") {
        if (!timelineRevisionId) throw new Error("请先创建并选择冻结 TimelineRevision");
        const result = await renderEpisode(timelineRevisionId);
        setSuccess(`整集渲染已登记：${result.render.id.slice(0, 12)} · ${result.render.integrity_status}`);
      } else if (action === "build") {
        if (!renderId || !targetVersionId) throw new Error("必须同时拥有整集 render 和项目显式交付目标");
        const result = await buildDeliveryPackage({ episode_render_version_id: renderId, target_version_id: targetVersionId });
        setSuccess(`交付候选已创建：${result.delivery.id.slice(0, 12)} · 仍需 verify`);
      } else {
        if (!deliveryId) throw new Error("请先创建交付候选");
        const result = await verifyDeliveryPackage(deliveryId);
        setSuccess(`交付 manifest 校验完成：${result.delivery.status}`);
      }
      onChanged?.();
    } catch (caught) { setError(`操作失败：${String(caught)}`); }
    finally { setPending(null); }
  };
  return <section className="panel delivery-workflow-panel" aria-labelledby="delivery-workflow-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-TML-002/DEL-001/002</p><h3 id="delivery-workflow-title">整集渲染与本地交付候选</h3></div><span className="status-pill neutral">NO OVERWRITE</span></div>
    <p className="muted">只读取冻结时间线和项目显式 DeliveryTarget；渲染、manifest、hash、verify 都保留独立版本，不覆盖输入。</p>
    <div className="action-row"><button className="secondary" onClick={() => void run("render")} disabled={pending !== null || !timelineRevisionId}>{pending === "render" ? "渲染登记中…" : "登记整集渲染"}</button><button className="secondary" onClick={() => void run("build")} disabled={pending !== null || !renderId || !targetVersionId}>{pending === "build" ? "创建交付中…" : "创建交付候选"}</button><button className="primary-action" onClick={() => void run("verify")} disabled={pending !== null || !deliveryId}>{pending === "verify" ? "校验中…" : "验证 manifest / SHA"}</button></div>
    <div className="review-meta"><span>episode：{episodeId.slice(0, 12)}</span><span>timeline：{timelineRevisionId?.slice(0, 12) ?? "缺失"}</span><span>render：{renderId?.slice(0, 12) ?? "缺失"}</span><span>target：{targetVersionId?.slice(0, 12) ?? "缺失"}</span><span>delivery：{deliveryId?.slice(0, 12) ?? "缺失"}</span></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

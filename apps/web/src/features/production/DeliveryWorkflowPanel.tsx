import { useState } from "react";
import { buildDeliveryPackage, renderEpisode, reviewDeliveryPackage, verifyDeliveryPackage, withdrawDeliveryPackage } from "../../generated/api";

export function DeliveryWorkflowPanel({ episodeId, timelineRevisionId, renderId, targetVersionId, deliveryId, onChanged }: { episodeId: string; timelineRevisionId: string | null; renderId: string | null; targetVersionId: string | null; deliveryId: string | null; onChanged?: () => void }) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const review = async (reviewerType: "HUMAN" | "PLATFORM") => {
    if (!deliveryId) { setError("请先创建交付候选"); return; }
    const note = window.prompt(`${reviewerType === "HUMAN" ? "人工" : "平台"}审核说明（不会由机器结果自动代填）`, "已完成本地复核")?.trim();
    if (!note) return;
    setPending(reviewerType); setError(null); setSuccess(null);
    try {
      const result = await reviewDeliveryPackage(deliveryId, { reviewer_type: reviewerType, decision: "APPROVED", note });
      setSuccess(`${reviewerType === "HUMAN" ? "人工" : "平台"}审核已记录：${String(result.delivery[reviewerType === "HUMAN" ? "human_review_status" : "platform_review_status"] ?? "APPROVED")}`);
      onChanged?.();
    } catch (caught) { setError(`审核记录失败：${String(caught)}`); }
    finally { setPending(null); }
  };
  const run = async (action: "render" | "build" | "verify" | "withdraw") => {
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
      } else if (action === "verify") {
        if (!deliveryId) throw new Error("请先创建交付候选");
        const result = await verifyDeliveryPackage(deliveryId);
        setSuccess(`交付 manifest 校验完成：${result.delivery.status}`);
      } else {
        if (!deliveryId) throw new Error("请先创建交付候选");
        const reason = window.prompt("请输入撤回原因（会写入交付事件历史）", "人工复核后撤回")?.trim();
        if (!reason) return;
        const result = await withdrawDeliveryPackage(deliveryId, reason);
        setSuccess(`交付包已撤回：${result.delivery.status} · ${reason}`);
      }
      onChanged?.();
    } catch (caught) { setError(`操作失败：${String(caught)}`); }
    finally { setPending(null); }
  };
  return <section className="panel delivery-workflow-panel" aria-labelledby="delivery-workflow-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-TML-002/DEL-001/002</p><h3 id="delivery-workflow-title">整集渲染与本地交付候选</h3></div><span className="status-pill neutral">NO OVERWRITE</span></div>
    <p className="muted">只读取冻结时间线和项目显式 DeliveryTarget；渲染、manifest、hash、verify 都保留独立版本，不覆盖输入。</p>
    <div className="action-row"><button className="secondary" onClick={() => void run("render")} disabled={pending !== null || !timelineRevisionId}>{pending === "render" ? "渲染登记中…" : "登记整集渲染"}</button><button className="secondary" onClick={() => void run("build")} disabled={pending !== null || !renderId || !targetVersionId}>{pending === "build" ? "创建交付中…" : "创建交付候选"}</button><button className="primary-action" onClick={() => void run("verify")} disabled={pending !== null || !deliveryId}>{pending === "verify" ? "校验中…" : "验证 manifest / SHA"}</button><button className="secondary" onClick={() => void review("HUMAN")} disabled={pending !== null || !deliveryId}>{pending === "HUMAN" ? "记录人工审核中…" : "记录人工批准"}</button><button className="secondary" onClick={() => void review("PLATFORM")} disabled={pending !== null || !deliveryId}>{pending === "PLATFORM" ? "记录平台审核中…" : "记录平台批准"}</button><button className="secondary" onClick={() => void run("withdraw")} disabled={pending !== null || !deliveryId}>{pending === "withdraw" ? "撤回中…" : "撤回交付包"}</button></div>
    <div className="review-meta"><span>episode：{episodeId.slice(0, 12)}</span><span>timeline：{timelineRevisionId?.slice(0, 12) ?? "缺失"}</span><span>render：{renderId?.slice(0, 12) ?? "缺失"}</span><span>target：{targetVersionId?.slice(0, 12) ?? "缺失"}</span><span>delivery：{deliveryId?.slice(0, 12) ?? "缺失"}</span><span>机器 PASS ≠ 人工/平台批准</span></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

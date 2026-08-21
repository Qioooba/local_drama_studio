import { useEffect, useState } from "react";
import { buildDeliveryPackage, listEpisodeDeliveryPackages, renderEpisode, reviewDeliveryPackage, verifyDeliveryPackage, withdrawDeliveryPackage, type DeliveryPackage } from "../../generated/api";

type DeliveryWorkflowFocus = "COMPOSE" | "REVIEW" | "PACKAGE";

export function DeliveryWorkflowPanel({ episodeId, timelineRevisionId, renderId, targetVersionId, deliveryId, onChanged, focus }: { episodeId: string; timelineRevisionId: string | null; renderId: string | null; targetVersionId: string | null; deliveryId: string | null; onChanged?: () => void; focus?: DeliveryWorkflowFocus }) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [history, setHistory] = useState<DeliveryPackage[]>([]);
  const [noteContext, setNoteContext] = useState<null | { purpose: "review"; reviewerType: "HUMAN" | "PLATFORM" } | { purpose: "withdraw" }>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const refreshHistory = async () => {
    try { setHistory((await listEpisodeDeliveryPackages(episodeId)).items); } catch (caught) { setError(`读取交付历史失败：${String(caught)}`); }
  };
  useEffect(() => {
    if (!focus || focus === "PACKAGE") void refreshHistory();
  }, [episodeId, focus]);
  const review = async (reviewerType: "HUMAN" | "PLATFORM") => {
    if (!deliveryId) { setError("请先创建交付候选"); return; }
    setNoteContext({ purpose: "review", reviewerType });
    setNoteDraft("");
  };
  const withdraw = async () => {
    if (!deliveryId) { setError("请先创建交付候选"); return; }
    setNoteContext({ purpose: "withdraw" });
    setNoteDraft("");
  };
  const confirmNote = async () => {
    if (!noteContext || !deliveryId) return;
    const note = noteDraft.trim();
    if (!note) return;
    setPending(noteContext.purpose === "review" ? noteContext.reviewerType : "withdraw");
    setError(null); setSuccess(null); setNoteContext(null); setNoteDraft("");
    try {
      if (noteContext.purpose === "review") {
        const result = await reviewDeliveryPackage(deliveryId, { reviewer_type: noteContext.reviewerType, decision: "APPROVED", note });
        setSuccess(`${noteContext.reviewerType === "HUMAN" ? "人工" : "平台"}审核已记录：${String(result.delivery[noteContext.reviewerType === "HUMAN" ? "human_review_status" : "platform_review_status"] ?? "APPROVED")}`);
      } else {
        const result = await withdrawDeliveryPackage(deliveryId, note);
        setSuccess(`交付包已撤回：${result.delivery.status} · ${note}`);
      }
      onChanged?.();
      void refreshHistory();
    } catch (caught) { setError(`操作失败：${String(caught)}`); }
    finally { setPending(null); }
  };
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
      void refreshHistory();
    } catch (caught) { setError(`操作失败：${String(caught)}`); }
    finally { setPending(null); }
  };
  const title = focus === "COMPOSE" ? "创建整集渲染与交付候选" : focus === "REVIEW" ? "校验并记录交付审核" : focus === "PACKAGE" ? "复验交付包与历史证据" : "整集渲染与本地交付候选";
  const showCompose = !focus || focus === "COMPOSE";
  const showReview = !focus || focus === "REVIEW";
  const showPackage = !focus || focus === "PACKAGE";
  return <section className="panel delivery-workflow-panel" aria-labelledby="delivery-workflow-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-TML-002/DEL-001/002</p><h3 id="delivery-workflow-title">{title}</h3></div><span className="status-pill neutral">不覆盖</span></div>
    <p className="muted">只读取冻结时间线和项目显式 DeliveryTarget；渲染、manifest、hash、verify 与人工决定都保留独立证据，不覆盖输入。</p>
    {noteContext && <div className="inline-note-box" role="dialog" aria-label={noteContext.purpose === "review" ? "填写审核说明" : "填写撤回原因"}><strong>{noteContext.purpose === "review" ? `${noteContext.reviewerType === "HUMAN" ? "人工" : "平台"}审核说明（不会由机器结果自动代填）` : "撤回原因（会写入交付事件历史）"}</strong><textarea autoFocus value={noteDraft} onChange={(event) => setNoteDraft(event.target.value)} placeholder={noteContext.purpose === "review" ? "说明审核依据与结论" : "说明撤回原因"} /><div className="action-row"><button className="primary-action" type="button" onClick={() => void confirmNote()} disabled={!noteDraft.trim() || pending !== null}>{pending === "withdraw" || (noteContext.purpose === "review" && pending === noteContext.reviewerType) ? "提交中…" : noteContext.purpose === "review" ? "记录审核" : "确认撤回"}</button><button className="secondary" type="button" onClick={() => { setNoteContext(null); setNoteDraft(""); }} disabled={pending !== null}>取消</button></div></div>}
    {showCompose && <div className="action-row" aria-label="合成候选操作"><button type="button" className="secondary" onClick={() => void run("render")} disabled={pending !== null || !timelineRevisionId}>{pending === "render" ? "渲染登记中…" : "登记整集渲染"}</button><button type="button" className="primary-action" onClick={() => void run("build")} disabled={pending !== null || !renderId || !targetVersionId}>{pending === "build" ? "创建交付中…" : "创建交付候选"}</button></div>}
    {showReview && <div className="action-row" aria-label="交付审核操作"><button type="button" className="primary-action" onClick={() => void run("verify")} disabled={pending !== null || !deliveryId}>{pending === "verify" ? "校验中…" : "验证 manifest / SHA"}</button><button type="button" className="secondary" onClick={() => void review("HUMAN")} disabled={pending !== null || !deliveryId}>{pending === "HUMAN" ? "记录人工审核中…" : "记录人工批准"}</button><button type="button" className="secondary" onClick={() => void review("PLATFORM")} disabled={pending !== null || !deliveryId}>{pending === "PLATFORM" ? "记录平台审核中…" : "记录平台批准"}</button><button type="button" className="secondary" onClick={() => void withdraw()} disabled={pending !== null || !deliveryId}>{pending === "withdraw" ? "撤回中…" : "撤回交付包"}</button></div>}
    {showPackage && <div className="action-row" aria-label="交付包复验操作"><button type="button" className="primary-action" onClick={() => void run("verify")} disabled={pending !== null || !deliveryId}>{pending === "verify" ? "校验中…" : "复验 manifest / SHA"}</button><button type="button" className="secondary" onClick={() => void withdraw()} disabled={pending !== null || !deliveryId}>{pending === "withdraw" ? "撤回中…" : "撤回交付包"}</button></div>}
    <div className="review-meta"><span>episode：{episodeId.slice(0, 12)}</span><span>timeline：{timelineRevisionId?.slice(0, 12) ?? "缺失"}</span><span>render：{renderId?.slice(0, 12) ?? "缺失"}</span><span>target：{targetVersionId?.slice(0, 12) ?? "缺失"}</span><span>delivery：{deliveryId?.slice(0, 12) ?? "缺失"}</span><span>机器 PASS ≠ 人工/平台批准</span><button className="secondary" type="button" onClick={() => void refreshHistory()} disabled={pending !== null}>刷新交付历史</button></div>
    {showPackage && history.length > 0 && <div className="table-wrap"><table><caption className="sr-only">交付包历史</caption><thead><tr><th>状态</th><th>目标版本</th><th>manifest SHA</th><th>路径</th><th>下载审计</th><th>撤回原因</th></tr></thead><tbody>{history.map((item) => { const downloadCount = (item.events ?? []).filter((event) => event.action === "DOWNLOAD").length; return <tr key={item.id}><td>{item.status}</td><td>{String(item.target_version_id).slice(0, 12)}</td><td><code>{String(item.manifest_sha256 ?? "").slice(0, 16)}…</code></td><td><code>{String(item.rel_path ?? "")}</code></td><td>{downloadCount > 0 ? `已下载 ${downloadCount} 次` : "未下载"}</td><td>{String(item.withdrawn_reason ?? "—")}</td></tr>; })}</tbody></table></div>}
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

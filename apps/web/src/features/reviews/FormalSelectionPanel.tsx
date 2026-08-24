import { useState } from "react";
import { commitFormalSelection, preflightFormalSelection, type FormalSelectionCandidate, type FormalSelectionPlan } from "../../generated/api";

function reviewState(candidate: FormalSelectionCandidate) {
  if (candidate.is_stale) return "上游已变更，需重新审核";
  if (candidate.decision !== "APPROVED") return "尚未人工批准";
  if (candidate.integrity_status !== "VERIFIED") return "文件完整性待校验";
  return "已批准且完整";
}

export function FormalSelectionPanel({ projectId, candidates, onChanged }: { projectId: string; candidates: FormalSelectionCandidate[]; onChanged?: () => void }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [plan, setPlan] = useState<FormalSelectionPlan | null>(null);
  const [busy, setBusy] = useState<"check" | "commit" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const ids = [...selected];

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    setPlan(null);
    setMessage(null);
  };

  const checkSelection = async () => {
    setBusy("check");
    setError(null);
    setMessage(null);
    try {
      setPlan((await preflightFormalSelection(projectId, ids)).plan);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(null);
    }
  };

  const commit = async () => {
    if (!plan || plan.status !== "READY") return;
    setBusy("commit");
    setError(null);
    setMessage(null);
    try {
      const result = await commitFormalSelection(projectId, ids, plan.plan_hash);
      setMessage(`已采用 ${result.result.items.length} 个正式成片；原采用记录仍可追溯。`);
      setSelected(new Set());
      setPlan(null);
      onChanged?.();
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(null);
    }
  };

  return <section className="panel formal-selection-panel" aria-labelledby="formal-selection-title">
    <div className="panel-heading">
      <div><p className="eyebrow">正式采用</p><h3 id="formal-selection-title">选择进入时间线的正式成片</h3></div>
      <span className="status-pill neutral">仅显示已审核候选</span>
    </div>
    <p className="muted">采用不会替代人工审核。系统会先检查批准状态、上游时效和文件完整性，再一次性更新所选镜头；有任何阻塞都不会部分提交。</p>
    {candidates.length === 0 ? <p className="empty-state">当前没有可采用的正式成片。</p> : <div className="configuration-table" role="table" aria-label="正式成片候选">
      <div className="configuration-row configuration-header" role="row"><strong>选择</strong><strong>成片</strong><strong>审核状态</strong><strong>采用状态</strong></div>
      {candidates.map((candidate, index) => <div className="configuration-row" role="row" key={candidate.media_version_id}>
        <label aria-label={`选择正式成片 ${index + 1}`}><input type="checkbox" checked={selected.has(candidate.media_version_id)} onChange={() => toggle(candidate.media_version_id)} /></label>
        <span><img className="thumbnail-inline" src={`/api/v1/media-versions/${encodeURIComponent(candidate.media_version_id)}/thumbnail?size=small&frame=poster`} alt={`正式成片 ${index + 1} 缩略图`} loading="lazy" decoding="async" /><strong>{String(candidate.shot_code ?? `正式成片 ${index + 1}`)}</strong></span>
        <span>{reviewState(candidate)}</span>
        <span>{candidate.approved_version_id === candidate.media_version_id ? "当前已采用" : "尚未采用"}</span>
      </div>)}
    </div>}
    <div className="action-row">
      <button className="secondary" type="button" onClick={() => void checkSelection()} disabled={busy !== null || ids.length === 0}>{busy === "check" ? "检查中…" : `检查所选 ${ids.length} 项`}</button>
      <button className="primary-action" type="button" onClick={() => void commit()} disabled={busy !== null || !plan || plan.status !== "READY"}>{busy === "commit" ? "采用中…" : `采用所选 ${ids.length} 项`}</button>
      {plan && <span className={plan.status === "READY" ? "ok-text" : "blocker-text"}>{plan.status === "READY" ? `检查通过：${plan.items.length} 项可以采用` : `${plan.items.filter((item) => item.status === "BLOCKED").length} 项需要先处理`}</span>}
    </div>
    {plan?.status === "BLOCKED" && <div className="review-guidance">{plan.items.filter((item) => item.status === "BLOCKED").map((item, index) => <p key={item.media_version_id}>成片 {index + 1}：{item.blockers.join("、")}</p>)}</div>}
    {message && <p className="review-success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">采用正式成片失败：{error}</p>}
  </section>;
}

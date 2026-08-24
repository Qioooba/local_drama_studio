import { useState } from "react";
import { authorizeWorkspaceAsset, revokeWorkspaceAssetAuthorization, type ReviewInboxItem, type WorkspaceAssetAuthorization } from "../../generated/api";

/** Project-scoped authorization is explicit and records the verified local hash. */
export function WorkspaceAssetAuthorizationPanel({ projectId, items, authorizations = [], onChanged }: { projectId: string; items: ReviewInboxItem[]; authorizations?: WorkspaceAssetAuthorization[]; onChanged?: () => void }) {
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [authorized, setAuthorized] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [revokeReason, setRevokeReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const candidates = items.filter((item) => item.project_id === projectId && ["IMAGE", "VIDEO", "AUDIO"].includes(item.media_kind) && item.integrity_status === "VERIFIED").slice(0, 12);
  const authorizationByMedia = new Map(authorizations.map((item) => [item.media_version_id, item]));
  const authorize = async (mediaVersionId: string) => {
    setPendingId(mediaVersionId); setError(null); setMessage(null);
    try {
      const result = await authorizeWorkspaceAsset(projectId, mediaVersionId);
      setAuthorized((current) => ({ ...current, [mediaVersionId]: String(result.authorization.id ?? "已授权") }));
      setMessage(result.authorization.reactivated === true ? "媒体完整性已复核，项目授权已重新激活；这不等于人工审核批准。" : "媒体完整性已复核并授权到项目；这不等于人工审核批准。");
      onChanged?.();
    } catch (caught) {
      setError(String(caught));
    } finally {
      setPendingId(null);
    }
  };
  const confirmRevoke = async (mediaVersionId: string) => {
    const reason = revokeReason.trim();
    if (!reason) return;
    setPendingId(mediaVersionId); setError(null); setMessage(null);
    try { await revokeWorkspaceAssetAuthorization(projectId, mediaVersionId, reason); setAuthorized((current) => { const next = { ...current }; delete next[mediaVersionId]; return next; }); setMessage("项目媒体授权已撤回；历史授权记录保留。"); onChanged?.(); }
    catch (caught) { setError(String(caught)); }
    finally { setPendingId(null); setRevokingId(null); setRevokeReason(""); }
  };
  return <section className="panel workspace-asset-panel" aria-labelledby="workspace-asset-title">
    <div className="panel-heading"><div><p className="eyebrow">项目授权</p><h3 id="workspace-asset-title">项目工作区资产授权</h3></div><span className="status-pill neutral">本地哈希</span></div>
    <p className="muted">只允许当前项目的 VERIFIED 媒体版本进入工作区；授权会复核本地路径、字节数和 SHA-256，不复制模型或媒体，也不等于人工审核批准。</p>
    {candidates.length === 0 ? <p className="empty-state">暂无完整性为 VERIFIED 的可授权媒体。</p> : <div className="configuration-table" role="table" aria-label="项目工作区资产候选"><div className="configuration-row configuration-header" role="row"><strong>所属镜头</strong><strong>类型 / 阶段</strong><strong>完整性 / 人审</strong><strong>操作</strong></div>{candidates.map((item) => { const existing = authorizationByMedia.get(item.media_version_id); const isAuthorized = existing?.authorization_status === "AUTHORIZED" || Boolean(authorized[item.media_version_id]); const humanReview = item.decision ?? "人审待定"; return <div className="configuration-row" role="row" key={item.media_version_id}><span><strong>{item.shot_code ?? item.episode_code ?? "项目级媒体"}</strong><details><summary>高级：版本标识</summary><code>{item.media_version_id}</code></details></span><span>{item.media_kind} · {item.stage}</span><span>{existing?.impact.length ? `影响：${existing.impact.join("、")}` : `${item.integrity_status} · ${humanReview}${isAuthorized ? " · 已授权" : ""}`}</span><span><button className="secondary" type="button" title="复核本地完整性并授权；不提交人工审核" onClick={() => void authorize(item.media_version_id)} disabled={pendingId !== null || isAuthorized}>{pendingId === item.media_version_id ? "校验中…" : isAuthorized ? "已授权" : "授权到项目"}</button>{isAuthorized && <button className="secondary" type="button" onClick={() => { setRevokingId(item.media_version_id); setRevokeReason(""); setError(null); setMessage(null); }} disabled={pendingId !== null}>撤回</button>}</span>{revokingId === item.media_version_id && <div className="revoke-inline" role="dialog" aria-label="填写撤回授权原因"><input autoFocus aria-label="撤回授权原因" value={revokeReason} onChange={(event) => setRevokeReason(event.target.value)} placeholder="撤回授权原因（必填）" /><button className="primary-action" type="button" onClick={() => void confirmRevoke(item.media_version_id)} disabled={!revokeReason.trim() || pendingId !== null}>{pendingId === item.media_version_id ? "撤回中…" : "确认撤回"}</button><button className="secondary" type="button" onClick={() => { setRevokingId(null); setRevokeReason(""); }} disabled={pendingId !== null}>取消</button></div>}</div>; })}</div>}
    {message && <p className="review-success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">授权失败：{error}</p>}
  </section>;
}

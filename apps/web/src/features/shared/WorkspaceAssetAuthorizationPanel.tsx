import { useState } from "react";
import { authorizeWorkspaceAsset, revokeWorkspaceAssetAuthorization, type ReviewInboxItem, type WorkspaceAssetAuthorization } from "../../generated/api";

/** Project-scoped authorization is explicit and records the verified local hash. */
export function WorkspaceAssetAuthorizationPanel({ projectId, items, authorizations = [], onChanged }: { projectId: string; items: ReviewInboxItem[]; authorizations?: WorkspaceAssetAuthorization[]; onChanged?: () => void }) {
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [authorized, setAuthorized] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const candidates = items.filter((item) => item.project_id === projectId && ["IMAGE", "VIDEO", "AUDIO"].includes(item.media_kind)).slice(0, 12);
  const authorizationByMedia = new Map(authorizations.map((item) => [item.media_version_id, item]));
  const authorize = async (mediaVersionId: string) => {
    setPendingId(mediaVersionId); setError(null);
    try {
      const result = await authorizeWorkspaceAsset(projectId, mediaVersionId);
      setAuthorized((current) => ({ ...current, [mediaVersionId]: String(result.authorization.id ?? "已授权") }));
      onChanged?.();
    } catch (caught) {
      setError(String(caught));
    } finally {
      setPendingId(null);
    }
  };
  const revoke = async (mediaVersionId: string) => {
    const reason = window.prompt("请输入撤回授权原因", "项目不再使用该工作区资产");
    if (!reason?.trim()) return;
    setPendingId(mediaVersionId); setError(null);
    try { await revokeWorkspaceAssetAuthorization(projectId, mediaVersionId, reason.trim()); setAuthorized((current) => { const next = { ...current }; delete next[mediaVersionId]; return next; }); onChanged?.(); }
    catch (caught) { setError(String(caught)); }
    finally { setPendingId(null); }
  };
  return <section className="panel workspace-asset-panel" aria-labelledby="workspace-asset-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-AST-001 · PROJECT AUTHORIZATION</p><h3 id="workspace-asset-title">项目工作区资产授权</h3></div><span className="status-pill neutral">LOCAL HASH</span></div>
    <p className="muted">只允许当前项目的 VERIFIED 媒体版本进入工作区；授权会复核本地路径、字节数和 SHA-256，不复制模型或媒体。</p>
    {candidates.length === 0 ? <p className="empty-state">暂无可授权的审核媒体候选。</p> : <div className="configuration-table" role="table" aria-label="项目工作区资产候选"><div className="configuration-row configuration-header" role="row"><strong>媒体版本</strong><strong>类型 / 阶段</strong><strong>审核状态</strong><strong>操作</strong></div>{candidates.map((item) => { const existing = authorizationByMedia.get(item.media_version_id); const isAuthorized = existing?.authorization_status === "AUTHORIZED" || Boolean(authorized[item.media_version_id]); return <div className="configuration-row" role="row" key={item.media_version_id}><span>{item.media_version_id.slice(0, 12)}…</span><span>{item.media_kind} · {item.stage}</span><span>{existing?.impact.length ? `影响：${existing.impact.join("、")}` : isAuthorized ? "已授权" : item.decision ?? "待审核"}</span><span><button className="secondary" type="button" onClick={() => void authorize(item.media_version_id)} disabled={pendingId !== null || isAuthorized}>{pendingId === item.media_version_id ? "校验中…" : isAuthorized ? "已授权" : "授权到项目"}</button>{isAuthorized && <button className="secondary" type="button" onClick={() => void revoke(item.media_version_id)} disabled={pendingId !== null}>撤回</button>}</span></div>; })}</div>}
    {error && <p className="inline-error" role="alert">授权失败：{error}</p>}
  </section>;
}

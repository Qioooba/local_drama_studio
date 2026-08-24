import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createProjectAssetGrant, listProjectAssetGrantCandidates, listProjectAssetGrants, revokeProjectAssetGrant, type ProjectAssetGrantCandidate } from "../../generated/api";
import { MEDIA_KIND_LABELS, optionLabel } from "../shared/optionLabels";

export function ProjectAssetGrantPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const candidates = useQuery({ queryKey: ["asset-grant-candidates", projectId], queryFn: () => listProjectAssetGrantCandidates(projectId) });
  const grants = useQuery({ queryKey: ["asset-grants", projectId], queryFn: () => listProjectAssetGrants(projectId) });
  const [selected, setSelected] = useState<ProjectAssetGrantCandidate | null>(null);
  const [mode, setMode] = useState<"READ_ONLY" | "DERIVED">("READ_ONLY");
  const [message, setMessage] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请选择一个已授权源资产");
      return createProjectAssetGrant(projectId, { authorization_id: selected.authorization_id, access_mode: mode });
    },
    onSuccess: async (result) => { setSelected(null); setMessage(result.grant.reactivated ? "项目 Grant 已重新激活；来源文件未复制。" : result.grant.duplicate ? "该项目 Grant 已存在，未重复创建。" : "项目 Grant 已创建；来源文件未复制。"); await queryClient.invalidateQueries({ queryKey: ["asset-grant-candidates", projectId] }); await queryClient.invalidateQueries({ queryKey: ["asset-grants", projectId] }); },
  });
  const revoke = useMutation({
    mutationFn: ({ grantId, withdrawalReason }: { grantId: string; withdrawalReason: string }) => revokeProjectAssetGrant(grantId, withdrawalReason),
    onSuccess: async () => { setRevokingGrantId(null); setRevokeReason(""); setMessage("项目 Grant 已撤回；历史记录保留。"); await queryClient.invalidateQueries({ queryKey: ["asset-grant-candidates", projectId] }); await queryClient.invalidateQueries({ queryKey: ["asset-grants", projectId] }); },
  });
  const [revokingGrantId, setRevokingGrantId] = useState<string | null>(null);
  const [revokeReason, setRevokeReason] = useState("");
  const available = useMemo(() => (candidates.data?.items ?? []).filter((item) => item.grantable), [candidates.data?.items]);
  const error = candidates.error ?? grants.error ?? create.error ?? revoke.error;
  return <section className="panel asset-grant-panel" aria-labelledby="asset-grant-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-AST-001 · 项目资产授权</p><h3 id="asset-grant-title">跨项目工作区资产复用</h3></div><span className="status-pill neutral">不复制源文件</span></div>
    <p className="muted">只显示其他项目已显式授权且完整性仍有效的媒体。Grant 只冻结来源项目、授权 revision、SHA-256 和用途；源授权撤回或内容变化会立即显示影响，不会静默变成可用资产。</p>
    <div className="asset-grant-toolbar"><label>候选源资产<select value={selected?.authorization_id ?? ""} onChange={(event) => { setSelected(available.find((item) => item.authorization_id === event.target.value) ?? null); setMessage(null); }} disabled={available.length === 0}><option value="">{available.length === 0 ? "暂无可授权源资产" : "选择已授权源资产"}</option>{available.map((item) => <option key={item.authorization_id} value={item.authorization_id}>{item.source_project_code} · {optionLabel(MEDIA_KIND_LABELS, item.asset_kind, "项目资产")} · {item.path_rel}</option>)}</select></label><label>授权用途<select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="READ_ONLY">只读复用（不能修改源资产）</option><option value="DERIVED">创建派生版本（保留来源关系）</option></select></label><button className="primary-action" type="button" onClick={() => { setMessage(null); create.mutate(); }} disabled={!selected || create.isPending}>{create.isPending ? "授权中…" : "创建项目授权"}</button></div>
    {selected && <p className="grant-fingerprint" role="status">来源 {selected.source_project_title} · v{selected.authorization_revision} · {selected.authorization_sha256.slice(0, 16)}… · {selected.license_status}</p>}
    {!available.length && <p className="empty-state">暂无可授权源资产；请先在来源项目对 VERIFIED 媒体执行“授权到工作区”。</p>}
    <div className="asset-grant-list"><strong>当前项目 Grant</strong>{(grants.data?.items ?? []).map((grant) => <article className="asset-grant-row" key={grant.id}><div><strong>{grant.source_project_code ?? grant.source_project_id}</strong><span>{grant.access_mode} · {grant.status} · {grant.media_version_id.slice(0, 12)}</span>{grant.impact?.length ? <small className="blocker-text">影响：{grant.impact.join("、")}</small> : <small className="ok-text">来源 revision/hash 当前一致 · {grant.usable ? "可用" : "不可用"}</small>}</div>{grant.status === "ACTIVE" && <button className="secondary" type="button" onClick={() => { setRevokingGrantId(grant.id); setRevokeReason(""); setMessage(null); }} disabled={revoke.isPending}>撤回 Grant</button>}{revokingGrantId === grant.id && <div className="revoke-inline" role="dialog" aria-label="填写撤回 Grant 原因"><input autoFocus aria-label="撤回 Grant 原因" value={revokeReason} onChange={(event) => setRevokeReason(event.target.value)} placeholder="撤回原因（必填）" /><button className="primary-action" type="button" onClick={() => { const value = revokeReason.trim(); if (value) revoke.mutate({ grantId: grant.id, withdrawalReason: value }); }} disabled={!revokeReason.trim() || revoke.isPending}>确认撤回</button><button className="secondary" type="button" onClick={() => { setRevokingGrantId(null); setRevokeReason(""); }} disabled={revoke.isPending}>取消</button></div>}</article>)}</div>
    {message && <p className="review-success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">资产 Grant 操作失败：{String(error)}</p>}
  </section>;
}

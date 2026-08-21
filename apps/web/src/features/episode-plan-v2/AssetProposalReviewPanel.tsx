import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { decideAssetProposal, listAssetProposals, type AssetProposal } from "./assetProposalsApi";

export function AssetProposalReviewPanel({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["asset-proposals", projectId];
  const query = useQuery({ queryKey: key, queryFn: () => listAssetProposals(projectId) });
  const [codes, setCodes] = useState<Record<string, string>>({});
  const mutation = useMutation({
    mutationFn: ({ proposal, action }: { proposal: AssetProposal; action: "CREATE_NEW" | "MERGE_EXISTING" | "REJECT" }) => decideAssetProposal(proposal, action, {
      targetAssetId: action === "MERGE_EXISTING" ? proposal.suggested_asset_id ?? undefined : undefined,
      newAssetCode: action === "CREATE_NEW" ? codes[proposal.id]?.trim().toUpperCase() : undefined,
    }),
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  });
  const pending = query.data?.filter((item) => item.status === "PENDING") ?? [];
  return <section className="subpanel" aria-labelledby="asset-proposal-title">
    <div className="panel-heading"><div><p className="eyebrow">AI Breakdown · 资产身份审核</p><h4 id="asset-proposal-title">角色资产建议</h4></div><span className="status-pill">{pending.length} 待处理</span></div>
    <p className="muted">AI 只创建建议，不自动合并身份。合并只把建议指向既有资产；“保留为独立角色”会显式创建新资产。</p>
    {pending.map((proposal) => <article className="ai-draft-list" key={proposal.id}>
      <div><strong>{proposal.name}</strong><span>{proposal.evidence.scene_count ?? 0} 个场次</span></div>
      {proposal.suggested_asset_id
        ? <p>可能与既有资产 <strong>{proposal.suggested_asset_code} · {proposal.suggested_asset_name}</strong> 相同，请人工裁决。</p>
        : <p>没有发现精确同名的既有角色资产。</p>}
      <div className="action-row">
        {proposal.suggested_asset_id && <button type="button" className="secondary" disabled={mutation.isPending} onClick={() => mutation.mutate({ proposal, action: "MERGE_EXISTING" })}>接受合并建议</button>}
        <label>独立资产 code<input value={codes[proposal.id] ?? ""} placeholder="CHAR_NAME" onChange={(event) => setCodes((current) => ({ ...current, [proposal.id]: event.target.value }))} /></label>
        <button type="button" disabled={!codes[proposal.id]?.trim() || mutation.isPending} onClick={() => mutation.mutate({ proposal, action: "CREATE_NEW" })}>保留为独立角色</button>
        <button type="button" className="secondary" disabled={mutation.isPending} onClick={() => mutation.mutate({ proposal, action: "REJECT" })}>拒绝建议</button>
      </div>
    </article>)}
    {query.isPending && <p className="empty-state">正在读取资产建议…</p>}
    {!query.isPending && !pending.length && <p className="empty-state">没有待处理的资产身份建议。</p>}
    {(query.error || mutation.error) && <p className="inline-error" role="alert">{String(query.error || mutation.error)}</p>}
  </section>;
}

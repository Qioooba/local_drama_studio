import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { decideAssetProposal, listAssetProposals, type AssetProposal } from "./assetProposalsApi";
import { generateAssetCode } from "../shared/autoCode";

export function AssetProposalReviewPanel({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["asset-proposals", projectId];
  const query = useQuery({ queryKey: key, queryFn: () => listAssetProposals(projectId) });
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchResult, setBatchResult] = useState<{ succeeded: string[]; failed: Array<{ name: string; reason: string }> } | null>(null);
  const mutation = useMutation({
    mutationFn: ({ proposal, action }: { proposal: AssetProposal; action: "CREATE_NEW" | "MERGE_EXISTING" | "REJECT" }) => decideAssetProposal(proposal, action, {
      targetAssetId: action === "MERGE_EXISTING" ? proposal.suggested_asset_id ?? undefined : undefined,
      newAssetCode: action === "CREATE_NEW" ? generateAssetCode("CHARACTER", proposal.name) : undefined,
    }),
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  });
  const pending = query.data?.filter((item) => item.status === "PENDING") ?? [];
  useEffect(() => {
    setSelectedIds((current) => new Set([...current].filter((id) => pending.some((proposal) => proposal.id === id))));
  }, [query.data]);
  const selected = pending.filter((proposal) => selectedIds.has(proposal.id));
  const batch = useMutation({
    mutationFn: async (proposals: AssetProposal[]) => {
      const succeeded: string[] = [];
      const failed: Array<{ name: string; reason: string }> = [];
      for (const proposal of proposals) {
        const action = proposal.suggested_asset_id ? "MERGE_EXISTING" : "CREATE_NEW";
        try {
          await decideAssetProposal(proposal, action, {
            targetAssetId: proposal.suggested_asset_id ?? undefined,
            newAssetCode: action === "CREATE_NEW" ? generateAssetCode("CHARACTER", proposal.name) : undefined,
            note: "人工确认按精确同名建议批量建档",
          });
          succeeded.push(proposal.name);
        } catch (reason) {
          failed.push({ name: proposal.name, reason: String(reason) });
        }
      }
      return { succeeded, failed };
    },
    onSuccess: (result) => {
      setBatchResult(result);
      void client.invalidateQueries({ queryKey: key });
    },
  });
  return <section className="subpanel" aria-labelledby="asset-proposal-title">
    <div className="panel-heading"><div><p className="eyebrow">AI Breakdown · 资产身份审核</p><h4 id="asset-proposal-title">角色资产建议</h4></div><span className="status-pill">{pending.length} 待处理</span></div>
    <p className="muted">AI 只创建建议，不自动合并身份。合并只把建议指向既有资产；“保留为独立角色”会显式创建新资产。</p>
    {pending.length > 0 && <div className="batch-review-panel" aria-label="角色建议批量建档">
      <div className="panel-heading"><div><strong>按安全建议批量处理</strong><p className="muted">精确同名资产合并，其余创建独立角色；逐条反馈，不隐藏部分失败。</p></div><span className="status-pill">已选 {selected.length}</span></div>
      <label><input type="checkbox" checked={selected.length === pending.length} onChange={(event) => setSelectedIds(event.target.checked ? new Set(pending.map((proposal) => proposal.id)) : new Set())} />选择全部 {pending.length} 条建议</label>
      {selected.length > 0 && <p className="muted">将合并 {selected.filter((proposal) => proposal.suggested_asset_id).length} 个精确同名角色，新建 {selected.filter((proposal) => !proposal.suggested_asset_id).length} 个独立角色。</p>}
      <button type="button" className="primary-action" disabled={selected.length === 0 || batch.isPending || mutation.isPending} onClick={() => batch.mutate(selected)}>{batch.isPending ? "批量处理中…" : `确认处理所选 ${selected.length} 项`}</button>
      {batchResult && <div className={batchResult.failed.length ? "review-guidance" : "frame-feedback success"} role="status"><p>批量结果：成功 {batchResult.succeeded.length} · 失败 {batchResult.failed.length}</p>{batchResult.failed.length > 0 && <ul>{batchResult.failed.map((item) => <li key={item.name}>{item.name}：{item.reason}</li>)}</ul>}</div>}
    </div>}
    {pending.map((proposal) => <article className="ai-draft-list" key={proposal.id}>
      <div><label><input type="checkbox" aria-label={`选择角色建议 ${proposal.name}`} checked={selectedIds.has(proposal.id)} onChange={(event) => setSelectedIds((current) => { const next = new Set(current); if (event.target.checked) next.add(proposal.id); else next.delete(proposal.id); return next; })} /><strong>{proposal.name}</strong></label><span>{proposal.evidence.scene_count ?? 0} 个场次</span></div>
      {proposal.suggested_asset_id
        ? <p>可能与既有资产 <strong>{proposal.suggested_asset_code} · {proposal.suggested_asset_name}</strong> 相同，请人工裁决。</p>
        : <p>没有发现精确同名的既有角色资产。</p>}
      <div className="action-row">
        {proposal.suggested_asset_id && <button type="button" className="secondary" disabled={mutation.isPending} onClick={() => mutation.mutate({ proposal, action: "MERGE_EXISTING" })}>接受合并建议</button>}
        <div className="field-fact"><span>独立资产编号</span><strong>{generateAssetCode("CHARACTER", proposal.name)}</strong><small>由角色名称自动生成</small></div>
        <button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate({ proposal, action: "CREATE_NEW" })}>保留为独立角色</button>
        <button type="button" className="secondary" disabled={mutation.isPending} onClick={() => mutation.mutate({ proposal, action: "REJECT" })}>拒绝建议</button>
      </div>
    </article>)}
    {query.isPending && <p className="empty-state">正在读取资产建议…</p>}
    {!query.isPending && !pending.length && <p className="empty-state">没有待处理的资产身份建议。</p>}
    {(query.error || mutation.error || batch.error) && <p className="inline-error" role="alert">{String(query.error || mutation.error || batch.error)}</p>}
  </section>;
}

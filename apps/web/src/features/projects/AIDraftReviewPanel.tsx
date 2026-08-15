import { useQuery } from "@tanstack/react-query";
import { listScriptBreakdownDrafts } from "../../generated/api";

export function AIDraftReviewPanel({ projectId }: { projectId: string }) {
  const drafts = useQuery({ queryKey: ["script-breakdown-drafts", projectId], queryFn: () => listScriptBreakdownDrafts(projectId) });
  return <section className="ai-draft-panel" aria-labelledby="ai-draft-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-WRT-007 · local LLM drafts</p><h3 id="ai-draft-title">AI 辅助提取草稿</h3></div><span className="status-pill">{drafts.data?.items.length ?? 0} 份</span></div>
    <p className="review-guidance">模型输出只保存为 DRAFT_READY；不会自动创建或覆盖母本场次、镜头或创作资料。采纳必须由后续显式人工流程完成。</p>
    {drafts.isPending && <p className="empty-state">正在读取本地草稿…</p>}
    {drafts.error && <p className="inline-error" role="alert">{String(drafts.error)}</p>}
    <div className="ai-draft-list">{drafts.data?.items.map((item) => {
      const scenes = Array.isArray(item.draft.scenes) ? item.draft.scenes : [];
      const shots = scenes.reduce((count, scene) => count + (Array.isArray(scene.shots) ? scene.shots.length : 0), 0);
      return <article key={item.id}><div><strong>{item.source_document_title}</strong><span>{item.status} · NOT_APPLIED</span></div><p>{scenes.length} 个建议场次 · {shots} 个建议镜头</p><small>{item.source_document_code} · requires_human_action=true · automatic_apply=false</small></article>;
    })}</div>
    {drafts.data?.items.length === 0 && <p className="empty-state">当前没有真实本地 LLM 拆解草稿；不会显示模拟建议。</p>}
  </section>;
}

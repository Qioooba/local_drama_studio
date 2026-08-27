import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { createVisualLab, listVisualLabs } from "./client";
import "./visual-lab.css";

export function VisualLabListPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [code, setCode] = useState("");
  const labs = useQuery({ queryKey: ["visual-labs", projectId], queryFn: () => listVisualLabs(projectId), enabled: Boolean(projectId), retry: false });
  const create = useMutation({ mutationFn: () => createVisualLab(projectId, { code, title }), onSuccess: ({ document }) => { void queryClient.invalidateQueries({ queryKey: ["visual-labs", projectId] }); navigate(`/projects/${projectId}/labs/${document.id}`); } });
  return <main className="visual-lab-list v2-page"><header className="visual-lab-list__header"><div><p className="eyebrow">Visual Lab</p><h2>视觉实验室</h2><p className="muted">用于参考、变体、比较和序列草稿。实验候选只有显式“采纳”后才会进入正式生产。</p></div><button className="primary-action" type="button" onClick={() => setCreating(true)}>新建实验画布</button></header>
    {creating && <form className="visual-lab-create panel" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><label>名称<input autoFocus value={title} onChange={(event) => { setTitle(event.target.value); if (!code) setCode(event.target.value.trim().replace(/[^A-Za-z0-9_-]/g, "-").replace(/-+/g, "-").slice(0, 80)); }} required /></label><label>代码<input value={code} onChange={(event) => setCode(event.target.value.replace(/[^A-Za-z0-9_-]/g, ""))} required /></label><div><button className="secondary" type="button" onClick={() => setCreating(false)}>取消</button><button className="primary-action" type="submit" disabled={create.isPending}>{create.isPending ? "创建中…" : "创建并打开"}</button></div>{create.error && <p role="alert">{create.error.message}</p>}</form>}
    {labs.isPending ? <section className="empty-state">正在读取 Visual Lab…</section> : labs.error ? <section className="workspace-error" role="alert"><div><strong>Visual Lab 读取失败</strong><p>{labs.error.message}</p></div></section> : labs.data?.items.length ? <section className="visual-lab-cards">{labs.data.items.map((lab) => <Link key={lab.id} className="visual-lab-card" to={`/projects/${projectId}/labs/${lab.id}`}><span className="visual-lab-card__diagram" aria-hidden="true"><i /><i /><i /></span><div><h3>{lab.title}</h3><p>{lab.code}</p><small>{lab.node_count ?? 0} 个节点 · {lab.edge_count ?? 0} 条连接</small></div><span>打开 →</span></Link>)}</section> : <section className="empty-state"><h3>还没有视觉实验</h3><p>创建一张空白画布，从参考图、文字或生成意图开始。</p><button className="primary-action" type="button" onClick={() => setCreating(true)}>创建第一张 Visual Lab</button></section>}
    <footer><Link to={`/projects/${projectId}`}>返回项目总览</Link></footer>
  </main>;
}


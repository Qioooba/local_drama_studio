import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { commitStoryboardBatch, getStoryboardWorkspace, planStoryboardBatch, type StoryboardBatchPayload, type StoryboardShot } from "../../generated/api";

type View = "TABLE" | "STORYBOARD" | "TIMELINE";
type Edit = { target_duration_ms: number; shot_type: string };

export function StoryboardBatchWorkbench({ episodeId }: { episodeId: string }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["storyboard", episodeId], queryFn: () => getStoryboardWorkspace(episodeId) });
  const source = query.data?.storyboard.items ?? [];
  const [view, setView] = useState<View>("TABLE");
  const [orderedIds, setOrderedIds] = useState<string[]>([]);
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [copySource, setCopySource] = useState("");
  const [copyCode, setCopyCode] = useState("");
  const [copies, setCopies] = useState<Array<{ source_shot_id: string; code: string }>>([]);
  const [plan, setPlan] = useState<Awaited<ReturnType<typeof planStoryboardBatch>>["plan"] | null>(null);
  useEffect(() => { setOrderedIds(source.map((item) => item.id)); setEdits({}); setCopies([]); setPlan(null); }, [query.dataUpdatedAt]);
  const byId = useMemo(() => new Map(source.map((item) => [item.id, item])), [source]);
  const ordered = orderedIds.map((id) => byId.get(id)).filter(Boolean) as StoryboardShot[];
  const payload = (): StoryboardBatchPayload => ({
    ordered_shot_ids: orderedIds,
    edits: Object.entries(edits).map(([shot_id, edit]) => ({ shot_id, expected_revision: Number(byId.get(shot_id)?.revision), ...edit })),
    copies,
  });
  const invalidatePlan = () => setPlan(null);
  const planning = useMutation({ mutationFn: () => planStoryboardBatch(episodeId, payload()), onSuccess: (data) => setPlan(data.plan) });
  const committing = useMutation({ mutationFn: () => commitStoryboardBatch(episodeId, payload(), String(plan?.plan_hash)), onSuccess: (data) => { queryClient.setQueryData(["storyboard", episodeId], { storyboard: data.result.storyboard }); setPlan(null); } });
  const move = (index: number, offset: number) => { const next = [...orderedIds]; const target = index + offset; if (target < 0 || target >= next.length) return; [next[index], next[target]] = [next[target], next[index]]; setOrderedIds(next); invalidatePlan(); };
  const update = (shot: StoryboardShot, field: keyof Edit, value: string) => { setEdits((current) => ({ ...current, [shot.id]: { target_duration_ms: current[shot.id]?.target_duration_ms ?? shot.target_duration_ms, shot_type: current[shot.id]?.shot_type ?? shot.shot_type, [field]: field === "target_duration_ms" ? Number(value) : value } })); invalidatePlan(); };
  const addCopy = () => { if (!copySource || !copyCode.trim()) return; setCopies((current) => [...current, { source_shot_id: copySource, code: copyCode.trim() }]); setCopyCode(""); invalidatePlan(); };
  if (query.isPending) return <section className="subpanel"><p className="empty-state">正在读取分镜批量台…</p></section>;
  if (query.error) return <section className="subpanel" role="alert">{String(query.error)}</section>;
  return <section className="subpanel storyboard-workbench" aria-labelledby="storyboard-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-ING-003 · 分镜批量台</p><h4 id="storyboard-title">身份稳定的排序、复制与批量字段编辑</h4></div><span className="status-pill">{source.length} 镜 · {(query.data!.storyboard.total_duration_ms / 1000).toFixed(1)} 秒</span></div>
    <p className="muted">镜头 id 与显示顺序分离；提交只改变 order_key，字段编辑派生新 revision，复制创建新身份。</p>
    <div className="storyboard-tabs" role="tablist" aria-label="分镜视图">{query.data!.storyboard.views.map((item) => <button role="tab" aria-selected={view === item} className={view === item ? "active" : "secondary"} onClick={() => setView(item)} key={item}>{item === "TABLE" ? "表格" : item === "STORYBOARD" ? "故事板" : "时间线"}</button>)}</div>
    {view === "TABLE" && <div className="storyboard-table" role="table" aria-label="分镜批量编辑表格">{ordered.map((shot, index) => <div className="storyboard-row" role="row" key={shot.id}><span>{index + 1}</span><strong>{shot.code}</strong><code title={shot.id}>{shot.id.slice(0, 8)}</code><label>时长 ms<input aria-label={`${shot.code} 时长`} type="number" min="1" value={edits[shot.id]?.target_duration_ms ?? shot.target_duration_ms} onChange={(event) => update(shot, "target_duration_ms", event.target.value)} /></label><label>类型<input aria-label={`${shot.code} 类型`} value={edits[shot.id]?.shot_type ?? shot.shot_type} onChange={(event) => update(shot, "shot_type", event.target.value)} /></label><span>r{shot.revision}</span><button className="secondary" aria-label={`${shot.code} 上移`} disabled={index === 0} onClick={() => move(index, -1)}>↑</button><button className="secondary" aria-label={`${shot.code} 下移`} disabled={index === ordered.length - 1} onClick={() => move(index, 1)}>↓</button></div>)}</div>}
    {view === "STORYBOARD" && <div className="storyboard-grid">{ordered.map((shot, index) => <article key={shot.id}><span className="status-pill">#{index + 1}</span><h5>{shot.code}</h5><p>{String(shot.fields.subject_action ?? shot.fields.action ?? "未填写动作")}</p><small>{shot.target_duration_ms} ms · r{shot.revision}</small></article>)}</div>}
    {view === "TIMELINE" && <div className="storyboard-timeline">{ordered.map((shot) => <div key={shot.id} style={{ flexGrow: Math.max(1, edits[shot.id]?.target_duration_ms ?? shot.target_duration_ms) }}><strong>{shot.code}</strong><span>{edits[shot.id]?.target_duration_ms ?? shot.target_duration_ms} ms</span></div>)}</div>}
    <div className="storyboard-copy"><label>复制来源<select aria-label="复制来源" value={copySource} onChange={(event) => { setCopySource(event.target.value); invalidatePlan(); }}><option value="">选择镜头</option>{source.map((shot) => <option key={shot.id} value={shot.id}>{shot.code}</option>)}</select></label><label>新编号<input aria-label="复制后的新编号" value={copyCode} onChange={(event) => setCopyCode(event.target.value)} placeholder="SH-024-COPY" /></label><button className="secondary" onClick={addCopy} disabled={!copySource || !copyCode.trim()}>加入复制计划</button><span className="muted">待复制 {copies.length} 项</span></div>
    <div className="action-row"><button className="secondary" onClick={() => planning.mutate()} disabled={planning.isPending || !source.length}>{planning.isPending ? "校验中…" : "校验批量计划"}</button><button onClick={() => committing.mutate()} disabled={!plan?.valid || committing.isPending}>{committing.isPending ? "提交中…" : "确认提交计划"}</button>{plan && <span className={plan.valid ? "ok-text" : "error-text"}>{plan.valid ? `可提交 · ${plan.plan_hash.slice(0, 12)}` : `${plan.issues.length} 个冲突`}</span>}</div>
    {plan?.issues.map((issue, index) => <p className="error-text" role="alert" key={`${issue.code}-${index}`}>{issue.code}：{issue.message}</p>)}
    {committing.isSuccess && <p className="ok-text">批量提交完成；镜头 identity 与既有历史均保留。</p>}
  </section>;
}

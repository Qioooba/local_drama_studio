import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { compareCreativeEntryRevisions, createCreativeEntry, createCreativeEntryRevision, getCreativeEntry, listCreativeEntries, restoreCreativeEntryRevision } from "../../generated/api";

const kinds = ["SERIES_BIBLE", "CHARACTER", "SCENE", "PROP", "COSTUME", "STYLE", "VOICE"];

function parseContent(raw: string): Record<string, unknown> | null {
  try { const value = JSON.parse(raw); return value && typeof value === "object" && !Array.isArray(value) ? value : null; } catch { return null; }
}

export function CreativeLibrary({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const entries = useQuery({ queryKey: ["creative-entries", projectId], queryFn: () => listCreativeEntries(projectId) });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = selectedId ?? entries.data?.items[0]?.id ?? null;
  const detail = useQuery({ queryKey: ["creative-entry", selected], queryFn: () => getCreativeEntry(selected as string), enabled: Boolean(selected) });
  const [kind, setKind] = useState(""); const [code, setCode] = useState(""); const [title, setTitle] = useState(""); const [initialContent, setInitialContent] = useState(""); const [initialNote, setInitialNote] = useState("");
  const [editContent, setEditContent] = useState(""); const [editNote, setEditNote] = useState("");
  const [leftId, setLeftId] = useState(""); const [rightId, setRightId] = useState(""); const [restoreNote, setRestoreNote] = useState("");
  useEffect(() => { if (detail.data) { setEditContent(JSON.stringify(detail.data.entry.content, null, 2)); setLeftId(detail.data.revisions[1]?.id ?? ""); setRightId(detail.data.revisions[0]?.id ?? ""); } }, [detail.data?.entry.current_revision_id]);
  const comparison = useQuery({ queryKey: ["creative-comparison", selected, leftId, rightId], queryFn: () => compareCreativeEntryRevisions(selected as string, leftId, rightId), enabled: Boolean(selected && leftId && rightId && leftId !== rightId) });
  const refresh = async () => { await client.invalidateQueries({ queryKey: ["creative-entries", projectId] }); await client.invalidateQueries({ queryKey: ["creative-entry", selected] }); };
  const create = useMutation({ mutationFn: () => createCreativeEntry({ project_id: projectId, kind, code, title, content: parseContent(initialContent) as Record<string, unknown>, change_note: initialNote }), onSuccess: async ({ entry }) => { setSelectedId(entry.id); setKind(""); setCode(""); setTitle(""); setInitialContent(""); setInitialNote(""); await refresh(); } });
  const save = useMutation({ mutationFn: () => createCreativeEntryRevision(selected as string, { content: parseContent(editContent) as Record<string, unknown>, change_note: editNote }), onSuccess: async () => { setEditNote(""); await refresh(); } });
  const restore = useMutation({ mutationFn: () => restoreCreativeEntryRevision(selected as string, leftId, restoreNote), onSuccess: async () => { setRestoreNote(""); await refresh(); } });
  const createValid = Boolean(kind && code && title.trim() && parseContent(initialContent) && initialNote.trim());
  const editValid = Boolean(selected && parseContent(editContent) && editNote.trim());
  const restoreValid = Boolean(selected && leftId && restoreNote.trim() && leftId !== detail.data?.entry.current_revision_id);
  const error = create.error ?? save.error ?? restore.error ?? entries.error ?? detail.error ?? comparison.error;
  return <section className="creative-library" aria-labelledby="creative-library-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-WRT-001 · 不可变历史</p><h3 id="creative-library-title">故事圣经与创作资料</h3></div><span className="status-pill">{entries.data?.items.length ?? 0} 项</span></div>
    <div className="creative-layout">
      <div className="creative-list">{entries.data?.items.map((entry) => <button type="button" key={entry.id} className={entry.id === selected ? "selected" : ""} onClick={() => setSelectedId(entry.id)}><strong>{entry.code}</strong><span>{entry.kind} · revision {entry.revision_no}</span></button>)}{entries.data?.items.length === 0 && <p className="empty-state">当前项目尚无创作资料。</p>}</div>
      <div className="creative-detail">{detail.data ? <>
        <div className="creative-title"><strong>{detail.data.entry.title}</strong><span>{detail.data.entry.kind} · current revision {detail.data.entry.revision_no}</span></div>
        <label>结构化内容 JSON<textarea value={editContent} onChange={(event) => setEditContent(event.target.value)} /></label>
        <label>变更说明<input value={editNote} onChange={(event) => setEditNote(event.target.value)} /></label>
        <button type="button" className="secondary" disabled={!editValid || save.isPending} onClick={() => save.mutate()}>保存新 revision</button>
        <div className="creative-compare"><label>比较旧版<select value={leftId} onChange={(event) => setLeftId(event.target.value)}><option value="">请选择</option>{detail.data.revisions.map((revision) => <option key={revision.id} value={revision.id}>revision {revision.revision_no}</option>)}</select></label><label>比较新版<select value={rightId} onChange={(event) => setRightId(event.target.value)}><option value="">请选择</option>{detail.data.revisions.map((revision) => <option key={revision.id} value={revision.id}>revision {revision.revision_no}</option>)}</select></label></div>
        {comparison.data && <div className="creative-diff">{comparison.data.comparison.changes.map((change) => <p key={change.field}><strong>{change.field}</strong><span>{JSON.stringify(change.before)} → {JSON.stringify(change.after)}</span></p>)}{comparison.data.comparison.changes.length === 0 && <p>两版结构化内容一致。</p>}</div>}
        <label>回退说明<input value={restoreNote} onChange={(event) => setRestoreNote(event.target.value)} placeholder="回退会派生新的 revision" /></label><button type="button" className="secondary" disabled={!restoreValid || restore.isPending} onClick={() => restore.mutate()}>从所选旧版派生回退 revision</button>
      </> : <p className="empty-state">选择资料查看 revision。</p>}</div>
    </div>
    <details className="creative-create"><summary>新建创作资料</summary><div className="creative-create-grid"><label>类型<select value={kind} onChange={(event) => setKind(event.target.value)}><option value="">请选择</option>{kinds.map((value) => <option key={value}>{value}</option>)}</select></label><label>代码<input value={code} onChange={(event) => setCode(event.target.value)} placeholder="CHAR_MOTHER" /></label><label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="wide">初始内容 JSON<textarea value={initialContent} onChange={(event) => setInitialContent(event.target.value)} /></label><label className="wide">建立说明<input value={initialNote} onChange={(event) => setInitialNote(event.target.value)} /></label><button type="button" className="primary-action" disabled={!createValid || create.isPending} onClick={() => create.mutate()}>建立并保存 revision 1</button></div></details>
    {error && <p className="inline-error" role="alert">{String(error)}</p>}
  </section>;
}

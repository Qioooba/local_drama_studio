import { useState } from "react";
import { createTimelineRevision } from "../../generated/api";
import { ProjectMediaVersionSelect } from "../media-picker/ProjectMediaVersionSelect";

type TimelineItemDraft = { track_type: string; media_version_id: string; start_us: number; end_us: number };

export function TimelineRevisionPanel({ episodeId, projectId, onCreated }: { episodeId: string; projectId?: string; onCreated?: () => void }) {
  const [items, setItems] = useState<TimelineItemDraft[]>([{ track_type: "VIDEO", media_version_id: "", start_us: 0, end_us: 2_000_000 }]);
  const [status, setStatus] = useState("DRAFT");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const submit = async () => {
    setPending(true); setError(null); setSuccess(null);
    try {
      if (!items.length) throw new Error("时间线至少需要一个 item");
      const normalizedItems = items.map((value) => {
        const startUs = Number(value.start_us), endUs = Number(value.end_us);
        if (!Number.isInteger(startUs) || !Number.isInteger(endUs) || endUs <= startUs) throw new Error("每个 item 必须有有效 start_us/end_us");
        return { track_type: value.track_type, media_version_id: value.media_version_id || undefined, start_us: startUs, end_us: endUs, parameters: {} };
      });
      const result = await createTimelineRevision(episodeId, { items: normalizedItems, input_snapshot: { source: "LOCAL_TIMELINE_EDITOR", item_count: normalizedItems.length }, status });
      setSuccess(`已创建 TimelineRevision v${result.timeline.revision_no} · ${result.timeline.status} · ${result.timeline.items.length} 个 item`);
      onCreated?.();
    } catch (caught) { setError(`时间线 revision 创建失败：${String(caught)}`); }
    finally { setPending(false); }
  };
  return <section className="panel timeline-revision-panel" aria-labelledby="timeline-revision-title">
    <div className="panel-heading"><div><p className="eyebrow">轻量时间线</p><h3 id="timeline-revision-title">轻量多轨时间线</h3></div><span className="status-pill neutral">版本化</span></div>
    <p className="muted">镜头顺序、入出点、轨道和参数保存为不可变 TimelineRevision；输入媒体版本只读引用，不覆盖源文件。</p>
    <fieldset className="subtitle-cues-field"><legend>时间线条目</legend><div className="structured-control-list">{items.map((item, index) => <div className="structured-control-row wide" key={index}><label>轨道类型<select value={item.track_type} onChange={(event) => setItems((entries) => entries.map((entry, itemIndex) => itemIndex === index ? { ...entry, track_type: event.target.value, media_version_id: "" } : entry))}><option value="VIDEO">视频</option><option value="AUDIO">音频</option><option value="SUBTITLE">字幕</option><option value="OVERLAY">叠加</option></select></label>{item.track_type === "SUBTITLE" ? <label>媒体引用<select disabled><option>由字幕版本自动绑定</option></select></label> : projectId ? <ProjectMediaVersionSelect projectId={projectId} value={item.media_version_id} onChange={(media_version_id) => setItems((entries) => entries.map((entry, itemIndex) => itemIndex === index ? { ...entry, media_version_id } : entry))} label="项目媒体版本" mediaKinds={item.track_type === "AUDIO" ? ["AUDIO"] : ["VIDEO", "IMAGE"]} /> : <label>项目媒体版本<select disabled><option>请从项目工作区进入</option></select></label>}<label>开始（微秒）<input type="number" min="0" step="1000" value={item.start_us} onChange={(event) => setItems((entries) => entries.map((entry, itemIndex) => itemIndex === index ? { ...entry, start_us: Number(event.target.value) } : entry))} /></label><label>结束（微秒）<input type="number" min="1" step="1000" value={item.end_us} onChange={(event) => setItems((entries) => entries.map((entry, itemIndex) => itemIndex === index ? { ...entry, end_us: Number(event.target.value) } : entry))} /></label><button type="button" className="secondary" disabled={items.length === 1} onClick={() => setItems((entries) => entries.filter((_, itemIndex) => itemIndex !== index))}>删除条目</button></div>)}</div><button type="button" className="secondary" onClick={() => setItems((entries) => [...entries, { track_type: "VIDEO", media_version_id: "", start_us: entries.at(-1)?.end_us ?? 0, end_us: (entries.at(-1)?.end_us ?? 0) + 2_000_000 }])}>添加时间线条目</button></fieldset>
    <div className="action-row"><label>保存状态<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="DRAFT">草稿（仍可修改）</option><option value="FROZEN">已冻结（不可再修改）</option></select></label><button type="button" className="primary-action" onClick={() => void submit()} disabled={pending}>{pending ? "校验并保存中…" : "创建时间线版本"}</button></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

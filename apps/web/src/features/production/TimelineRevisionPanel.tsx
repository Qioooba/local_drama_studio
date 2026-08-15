import { useState } from "react";
import { createTimelineRevision } from "../../generated/api";

const defaultItems = JSON.stringify([{ track_type: "VIDEO", media_version_id: "", start_us: 0, end_us: 2_000_000, parameters: {} }], null, 2);

export function TimelineRevisionPanel({ episodeId, onCreated }: { episodeId: string; onCreated?: () => void }) {
  const [itemsText, setItemsText] = useState(defaultItems);
  const [status, setStatus] = useState("DRAFT");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const submit = async () => {
    setPending(true); setError(null); setSuccess(null);
    try {
      const parsed: unknown = JSON.parse(itemsText);
      if (!Array.isArray(parsed) || parsed.length === 0) throw new Error("时间线 items 必须是非空 JSON 数组");
      const items = parsed.map((item) => {
        if (!item || typeof item !== "object") throw new Error("每个时间线 item 必须是 JSON object");
        const value = item as Record<string, unknown>;
        const startUs = Number(value.start_us), endUs = Number(value.end_us);
        if (!Number.isInteger(startUs) || !Number.isInteger(endUs) || endUs <= startUs) throw new Error("每个 item 必须有有效 start_us/end_us");
        return { track_type: String(value.track_type ?? "VIDEO"), media_version_id: value.media_version_id ? String(value.media_version_id) : undefined, start_us: startUs, end_us: endUs, parameters: value.parameters && typeof value.parameters === "object" ? value.parameters as Record<string, unknown> : {} };
      });
      const result = await createTimelineRevision(episodeId, { items, input_snapshot: { source: "LOCAL_TIMELINE_EDITOR", item_count: items.length }, status });
      setSuccess(`已创建 TimelineRevision v${result.timeline.revision_no} · ${result.timeline.status} · ${result.timeline.items.length} 个 item`);
      onCreated?.();
    } catch (caught) { setError(`时间线 revision 创建失败：${String(caught)}`); }
    finally { setPending(false); }
  };
  return <section className="panel timeline-revision-panel" aria-labelledby="timeline-revision-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-TML-001 · LIGHT TIMELINE</p><h3 id="timeline-revision-title">轻量多轨时间线</h3></div><span className="status-pill neutral">VERSIONED</span></div>
    <p className="muted">镜头顺序、入出点、轨道和参数保存为不可变 TimelineRevision；输入媒体版本只读引用，不覆盖源文件。</p>
    <label className="subtitle-cues-field">Timeline items JSON（track_type/media_version_id/start_us/end_us）<textarea value={itemsText} onChange={(event) => setItemsText(event.target.value)} rows={8} spellCheck={false} /></label>
    <div className="action-row"><label>保存状态<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="DRAFT">DRAFT</option><option value="FROZEN">FROZEN</option></select></label><button type="button" className="primary-action" onClick={() => void submit()} disabled={pending}>{pending ? "校验并保存中…" : "创建 TimelineRevision"}</button></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

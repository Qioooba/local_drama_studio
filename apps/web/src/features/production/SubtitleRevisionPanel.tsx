import { useState } from "react";
import { createSubtitleRevision } from "../../generated/api";

type CueDraft = { start_us: number; end_us: number; text: string; style?: Record<string, unknown> };

const defaultCues = JSON.stringify([{ start_us: 0, end_us: 2_000_000, text: "" }], null, 2);

export function SubtitleRevisionPanel({ episodeId, defaultSourceDocumentVersionId = "", onCreated }: { episodeId: string; defaultSourceDocumentVersionId?: string; onCreated?: () => void }) {
  const [format, setFormat] = useState("SRT");
  const [sourceDocumentVersionId, setSourceDocumentVersionId] = useState(defaultSourceDocumentVersionId);
  const [cuesText, setCuesText] = useState(defaultCues);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const submit = async () => {
    setPending(true); setError(null); setSuccess(null);
    try {
      if (!sourceDocumentVersionId.trim()) throw new Error("必须显式填写已解析的剧本文档版本 ID");
      const parsed: unknown = JSON.parse(cuesText);
      if (!Array.isArray(parsed) || parsed.length === 0) throw new Error("字幕 cues 必须是非空 JSON 数组");
      const cues = parsed.map((item) => {
        if (!item || typeof item !== "object") throw new Error("每个 cue 必须是 JSON object");
        const cue = item as Partial<CueDraft>;
        const startUs = cue.start_us, endUs = cue.end_us, text = cue.text?.trim();
        if (!Number.isInteger(startUs) || !Number.isInteger(endUs) || !text) throw new Error("cue 需要整数 start_us/end_us 和非空 text");
        return { start_us: startUs as number, end_us: endUs as number, text, style: cue.style ?? {} };
      });
      const result = await createSubtitleRevision(episodeId, { format, cues, authority: { text_authority: "SCRIPT", source_document_version_id: sourceDocumentVersionId.trim() } });
      setSuccess(`已创建字幕 revision v${result.subtitle.revision_no} · ${result.subtitle.format} · ${result.subtitle.cues.length} 条；文本权威 SCRIPT`);
      onCreated?.();
    } catch (caught) { setError(`字幕 revision 创建失败：${String(caught)}`); }
    finally { setPending(false); }
  };
  return <section className="panel subtitle-revision-panel" aria-labelledby="subtitle-revision-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-AUD-004 · SUBTITLE REVISION</p><h3 id="subtitle-revision-title">字幕生成、校对与格式导出</h3></div><span className="status-pill neutral">SCRIPT AUTHORITY</span></div>
    <p className="muted">字幕文本必须来自已解析的剧本文档版本；ASR 只能作为对齐辅助。每次保存都是不可变 revision，不覆盖历史。</p>
    <div className="field-grid subtitle-revision-fields"><label>源剧本文档版本 ID<input value={sourceDocumentVersionId} onChange={(event) => setSourceDocumentVersionId(event.target.value)} placeholder="source-document-version UUID" /></label><label>格式<select value={format} onChange={(event) => setFormat(event.target.value)}><option value="SRT">SRT</option><option value="VTT">WebVTT</option><option value="ASS">ASS</option></select></label></div>
    <label className="subtitle-cues-field">字幕 cues JSON（start_us/end_us/text）<textarea value={cuesText} onChange={(event) => setCuesText(event.target.value)} rows={8} spellCheck={false} /></label>
    <div className="action-row"><button className="primary-action" type="button" onClick={() => void submit()} disabled={pending}>{pending ? "校验并保存中…" : "创建字幕 revision"}</button><span className="muted">重叠、空文本、CPS 超限会被服务端拒绝</span></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

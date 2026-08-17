import { useEffect, useState } from "react";
import { createSubtitleRevision, listSubtitleStyleTemplates, saveSubtitleStyleTemplate } from "../../generated/api";

type CueDraft = { start_us: number; end_us: number; text: string; style?: Record<string, unknown> };

type SubtitleStyle = { font: string; size: number; color: string; position: "TOP" | "CENTER" | "BOTTOM"; outline: number };

const defaultStyle: SubtitleStyle = { font: "Microsoft YaHei", size: 48, color: "#FFFFFF", position: "BOTTOM", outline: 2 };

const defaultCues = JSON.stringify([{ start_us: 0, end_us: 2_000_000, text: "" }], null, 2);

export function SubtitleRevisionPanel({ episodeId, projectId = "", defaultSourceDocumentVersionId = "", onCreated }: { episodeId: string; projectId?: string; defaultSourceDocumentVersionId?: string; onCreated?: () => void }) {
  const [format, setFormat] = useState("SRT");
  const [sourceDocumentVersionId, setSourceDocumentVersionId] = useState(defaultSourceDocumentVersionId);
  const [cuesText, setCuesText] = useState(defaultCues);
  const [style, setStyle] = useState<SubtitleStyle>(defaultStyle);
  const [styleExpanded, setStyleExpanded] = useState(false);
  const [templates, setTemplates] = useState<Array<{ id: string; code: string; title: string }>>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    void listSubtitleStyleTemplates(projectId)
      .then((result) => setTemplates(result.items.map((item) => ({ id: item.id, code: item.code, title: item.title }))))
      .catch(() => setTemplates([]));
  }, [projectId]);

  const applyTemplate = async (entryId: string) => {
    if (!entryId) return;
    try {
      const { getSubtitleStyleTemplate } = await import("../../generated/api");
      const template = await getSubtitleStyleTemplate(entryId);
      const content = template.template.content as Record<string, unknown>;
      setStyle({
        font: String(content.font ?? defaultStyle.font),
        size: Number(content.size ?? defaultStyle.size),
        color: String(content.color ?? defaultStyle.color),
        position: (String(content.position ?? defaultStyle.position) as SubtitleStyle["position"]),
        outline: Number(content.outline ?? defaultStyle.outline),
      });
      setError(null);
    } catch (caught) {
      setError(`载入样式模板失败：${String(caught)}`);
    }
  };

  const saveTemplate = async () => {
    if (!projectId || !templateName.trim()) return setError("保存为模板需要填写模板名称。");
    const sanitized = templateName.toUpperCase().replace(/[^A-Z0-9_-]/g, "");
    const code = sanitized ? `STYLE_${sanitized}`.slice(0, 64) : `STYLE_${Date.now().toString(36).toUpperCase()}`;
    try {
      await saveSubtitleStyleTemplate(projectId, { code, title: templateName.trim(), style, change_note: "从字幕面板保存的样式模板" });
      setSuccess("样式已保存为项目模板。");
      const result = await listSubtitleStyleTemplates(projectId);
      setTemplates(result.items.map((item) => ({ id: item.id, code: item.code, title: item.title })));
    } catch (caught) {
      setError(`保存样式模板失败：${String(caught)}`);
    }
  };

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
      const result = await createSubtitleRevision(episodeId, { format, cues, style, authority: { text_authority: "SCRIPT", source_document_version_id: sourceDocumentVersionId.trim() } });
      setSuccess(`已创建字幕 revision v${result.subtitle.revision_no} · ${result.subtitle.format} · ${result.subtitle.cues.length} 条；样式 ${style.font}/${style.size}px`);
      onCreated?.();
    } catch (caught) { setError(`字幕 revision 创建失败：${String(caught)}`); }
    finally { setPending(false); }
  };
  return <section className="panel subtitle-revision-panel" aria-labelledby="subtitle-revision-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-AUD-004 · SUBTITLE REVISION</p><h3 id="subtitle-revision-title">字幕生成、校对与格式导出</h3></div><span className="status-pill neutral">SCRIPT AUTHORITY</span></div>
    <p className="muted">字幕文本必须来自已解析的剧本文档版本；ASR 只能作为对齐辅助。每次保存都是不可变 revision，不覆盖历史。</p>
    <div className="field-grid subtitle-revision-fields"><label>源剧本文档版本 ID<input value={sourceDocumentVersionId} onChange={(event) => setSourceDocumentVersionId(event.target.value)} placeholder="source-document-version UUID" /></label><label>格式<select value={format} onChange={(event) => setFormat(event.target.value)}><option value="SRT">SRT</option><option value="VTT">WebVTT</option><option value="ASS">ASS</option></select></label></div>
    <label className="subtitle-cues-field">字幕 cues JSON（start_us/end_us/text）<textarea value={cuesText} onChange={(event) => setCuesText(event.target.value)} rows={8} spellCheck={false} /></label>
    <button className="secondary" type="button" aria-expanded={styleExpanded} onClick={() => setStyleExpanded((value) => !value)}>{styleExpanded ? "收起字幕样式" : "字幕样式模板（字体/字号/颜色/位置/描边）"}</button>
    {styleExpanded && <div className="subtitle-style-editor">
      <div className="field-grid">
        <label>字体<input value={style.font} onChange={(event) => setStyle({ ...style, font: event.target.value })} /></label>
        <label>字号（8—160）<input type="number" min="8" max="160" value={style.size} onChange={(event) => setStyle({ ...style, size: Number(event.target.value) })} /></label>
        <label>颜色（#RRGGBB）<input value={style.color} onChange={(event) => setStyle({ ...style, color: event.target.value })} placeholder="#FFFFFF" /></label>
        <label>位置<select value={style.position} onChange={(event) => setStyle({ ...style, position: event.target.value as SubtitleStyle["position"] })}><option value="BOTTOM">底部</option><option value="CENTER">居中</option><option value="TOP">顶部</option></select></label>
        <label>描边（0—12）<input type="number" min="0" max="12" value={style.outline} onChange={(event) => setStyle({ ...style, outline: Number(event.target.value) })} /></label>
      </div>
      {projectId && <div className="action-row">
        <label>模板名称<input value={templateName} onChange={(event) => setTemplateName(event.target.value)} placeholder="例如 默认字幕" /></label>
        <button className="secondary" type="button" onClick={() => void saveTemplate()} disabled={pending}>保存为项目模板</button>
        <label>载入模板<select value={selectedTemplateId} onChange={(event) => { setSelectedTemplateId(event.target.value); void applyTemplate(event.target.value); }}><option value="">选择项目模板…</option>{templates.map((template) => <option key={template.id} value={template.id}>{template.title}（{template.code}）</option>)}</select></label>
      </div>}
      <p className="muted">ASS 输出包含 [V4+ Styles] 样式块；SRT 忽略样式，但样式对象会随每个 cue 持久化到 subtitle_cues.style_json。</p>
    </div>}
    <div className="action-row"><button className="primary-action" type="button" onClick={() => void submit()} disabled={pending}>{pending ? "校验并保存中…" : "创建字幕 revision"}</button><span className="muted">重叠、空文本、CPS 超限会被服务端拒绝</span></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

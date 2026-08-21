import { useEffect, useState } from "react";
import { createSubtitleRevision, listScriptBreakdownDrafts, listSubtitleStyleTemplates, saveSubtitleStyleTemplate } from "../../generated/api";

type CueDraft = { start_us: number; end_us: number; text: string; style?: Record<string, unknown> };

type SubtitleStyle = { font: string; size: number; color: string; position: "TOP" | "CENTER" | "BOTTOM"; outline: number };
type SourceDocumentChoice = { versionId: string; code: string; title: string; applied: boolean; currentEpisode: boolean };

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
  const [sourceDocuments, setSourceDocuments] = useState<SourceDocumentChoice[]>([]);
  const [sourceDocumentsState, setSourceDocumentsState] = useState<"loading" | "ready" | "error">(projectId ? "loading" : "ready");

  useEffect(() => {
    if (!projectId) return;
    void listSubtitleStyleTemplates(projectId)
      .then((result) => setTemplates(result.items.map((item) => ({ id: item.id, code: item.code, title: item.title }))))
      .catch(() => setTemplates([]));
  }, [projectId]);

  useEffect(() => {
    setSourceDocumentVersionId(defaultSourceDocumentVersionId);
  }, [defaultSourceDocumentVersionId, episodeId]);

  useEffect(() => {
    let cancelled = false;
    if (!projectId) {
      setSourceDocuments([]);
      setSourceDocumentsState("ready");
      return () => { cancelled = true; };
    }
    setSourceDocumentsState("loading");
    void listScriptBreakdownDrafts(projectId)
      .then(({ items }) => {
        if (cancelled) return;
        const seen = new Set<string>();
        const choices = items
          .filter((item) => {
            if (seen.has(item.source_document_version_id)) return false;
            seen.add(item.source_document_version_id);
            return true;
          })
          .map((item) => ({
            versionId: item.source_document_version_id,
            code: item.source_document_code,
            title: item.source_document_title,
            applied: item.application_status === "APPLIED",
            currentEpisode: item.source_document_version_id === defaultSourceDocumentVersionId,
          }))
          .sort((left, right) => Number(right.currentEpisode) - Number(left.currentEpisode) || Number(right.applied) - Number(left.applied) || left.title.localeCompare(right.title, "zh-CN"));
        setSourceDocuments(choices);
        setSourceDocumentVersionId((current) => current || choices.find((choice) => choice.applied)?.versionId || choices[0]?.versionId || "");
        setSourceDocumentsState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setSourceDocuments([]);
        setSourceDocumentsState("error");
      });
    return () => { cancelled = true; };
  }, [defaultSourceDocumentVersionId, projectId]);

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
      if (!sourceDocumentVersionId.trim()) throw new Error("必须先选择已解析的源剧本文档版本");
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
    <div className="panel-heading"><div><p className="eyebrow">FR-AUD-004 · 字幕修订</p><h3 id="subtitle-revision-title">字幕生成、校对与格式导出</h3></div><span className="status-pill neutral">剧本权威</span></div>
    <p className="muted">字幕文本必须来自已解析的剧本文档版本；ASR 只能作为对齐辅助。每次保存都是不可变 revision，不覆盖历史。</p>
    <div className="field-grid subtitle-revision-fields">
      <label>源剧本文档版本
        <select value={sourceDocumentVersionId} disabled={sourceDocumentsState === "loading"} aria-describedby="subtitle-source-document-help" onChange={(event) => setSourceDocumentVersionId(event.target.value)}>
          {!sourceDocumentVersionId && <option value="">选择已解析的源剧本文档…</option>}
          {defaultSourceDocumentVersionId && !sourceDocuments.some((choice) => choice.versionId === defaultSourceDocumentVersionId) && <option value={defaultSourceDocumentVersionId}>当前本集字幕权威版本</option>}
          {sourceDocuments.map((choice) => <option key={choice.versionId} value={choice.versionId}>{choice.title || choice.code} · {choice.code}{choice.currentEpisode ? " · 当前本集" : choice.applied ? " · 已应用" : ""}</option>)}
        </select>
        <small id="subtitle-source-document-help" className={sourceDocumentsState === "error" ? "inline-error" : "muted"} role={sourceDocumentsState === "loading" ? "status" : sourceDocumentsState === "error" ? "alert" : undefined}>
          {sourceDocumentsState === "loading" && "正在读取项目中已解析的剧本文档版本…"}
          {sourceDocumentsState === "error" && (defaultSourceDocumentVersionId ? "文档目录暂不可用；仍可沿用当前本集字幕权威版本。" : "文档目录暂不可用，暂时不能创建字幕 revision。")}
          {sourceDocumentsState === "ready" && sourceDocuments.length === 0 && !defaultSourceDocumentVersionId && "当前项目还没有可用的剧本解析结果，请先导入并解析剧本。"}
          {sourceDocumentsState === "ready" && (sourceDocuments.length > 0 || defaultSourceDocumentVersionId) && "选择会把不可变文档版本写入字幕 authority；ASR 不会替代剧本文本权威。"}
        </small>
      </label>
      <label>格式<select value={format} onChange={(event) => setFormat(event.target.value)}><option value="SRT">SRT</option><option value="VTT">WebVTT</option><option value="ASS">ASS</option></select></label>
    </div>
    <label className="subtitle-cues-field">字幕 cues JSON（start_us/end_us/text）<textarea value={cuesText} onChange={(event) => setCuesText(event.target.value)} rows={8} spellCheck={false} /></label>
    <button className="secondary" type="button" aria-expanded={styleExpanded} onClick={() => setStyleExpanded((value) => !value)}>{styleExpanded ? "收起字幕样式" : "字幕样式模板（字体/字号/颜色/位置/描边）"}</button>
    {styleExpanded && <div className="subtitle-style-editor">
      <div className="field-grid">
        <label>字体<input value={style.font} onChange={(event) => setStyle({ ...style, font: event.target.value })} /></label>
        <label>字号（8—160）<input type="number" min="8" max="160" value={style.size} onChange={(event) => { const raw = event.target.value; if (raw === "") return; const n = Number(raw.replace(/^(-?)0+(?=\d)/, "$1")); setStyle({ ...style, size: Number.isFinite(n) ? Math.min(160, Math.max(8, n)) : style.size }); }} /></label>
        <label>颜色（#RRGGBB）<input value={style.color} onChange={(event) => setStyle({ ...style, color: event.target.value })} placeholder="#FFFFFF" /></label>
        <label>位置<select value={style.position} onChange={(event) => setStyle({ ...style, position: event.target.value as SubtitleStyle["position"] })}><option value="BOTTOM">底部</option><option value="CENTER">居中</option><option value="TOP">顶部</option></select></label>
        <label>描边（0—12）<input type="number" min="0" max="12" value={style.outline} onChange={(event) => { const raw = event.target.value; if (raw === "") return; const n = Number(raw.replace(/^(-?)0+(?=\d)/, "$1")); setStyle({ ...style, outline: Number.isFinite(n) ? Math.min(12, Math.max(0, n)) : style.outline }); }} /></label>
      </div>
      {projectId && <div className="action-row">
        <label>模板名称<input value={templateName} onChange={(event) => setTemplateName(event.target.value)} placeholder="例如 默认字幕" /></label>
        <button className="secondary" type="button" onClick={() => void saveTemplate()} disabled={pending}>保存为项目模板</button>
        <label>载入模板<select value={selectedTemplateId} onChange={(event) => { setSelectedTemplateId(event.target.value); void applyTemplate(event.target.value); }}><option value="">选择项目模板…</option>{templates.map((template) => <option key={template.id} value={template.id}>{template.title}（{template.code}）</option>)}</select></label>
      </div>}
      <p className="muted">ASS 输出包含 [V4+ Styles] 样式块；SRT 忽略样式，但样式对象会随每个 cue 持久化到 subtitle_cues.style_json。</p>
    </div>}
    <div className="action-row"><button className="primary-action" type="button" onClick={() => void submit()} disabled={pending || !sourceDocumentVersionId} title={!sourceDocumentVersionId ? sourceDocumentsState === "loading" ? "正在读取可用的源剧本文档" : "请先选择已解析的源剧本文档" : undefined}>{pending ? "校验并保存中…" : "创建字幕 revision"}</button><span className="muted">{!sourceDocumentVersionId ? "需要先选择源剧本文档；" : ""}重叠、空文本、CPS 超限会被服务端拒绝</span></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

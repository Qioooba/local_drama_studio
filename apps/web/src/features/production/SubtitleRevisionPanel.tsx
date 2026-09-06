import { useCallback, useEffect, useRef, useState } from "react";
import { createSubtitleRevision, getEpisodeTTSSubtitleDraftPlan, getProjectConfiguration, listScriptBreakdownDrafts, listSubtitleStyleTemplates, saveSubtitleStyleTemplate, type TTSSubtitleDraftPlan } from "../../generated/api";
import { resolutionFromPlan } from "../shared/effectiveDefaults";

type CueDraft = { start_us: number; end_us: number; text: string; style?: Record<string, unknown> };

type SubtitleStyle = { font: string; size: number; color: string; position: "TOP" | "CENTER" | "BOTTOM"; outline: number };
type SourceDocumentChoice = { versionId: string; code: string; title: string; applied: boolean; currentEpisode: boolean };

const defaultStyle: SubtitleStyle = { font: "Microsoft YaHei", size: 48, color: "#FFFFFF", position: "BOTTOM", outline: 2 };

const defaultCues: CueDraft[] = [{ start_us: 0, end_us: 2_000_000, text: "" }];

function cueValidationIssue(cues: CueDraft[]) {
  if (cues.length === 0) return "请至少添加一条字幕";
  for (let index = 0; index < cues.length; index += 1) {
    const cue = cues[index];
    if (!Number.isInteger(cue.start_us) || !Number.isInteger(cue.end_us)) return `第 ${index + 1} 条字幕时间必须是整数微秒`;
    if (cue.end_us <= cue.start_us) return `第 ${index + 1} 条字幕结束时间必须晚于开始时间`;
    if (!cue.text.trim()) return `请填写第 ${index + 1} 条字幕文本`;
    if (index > 0 && cue.start_us < cues[index - 1].end_us) return `第 ${index}、${index + 1} 条字幕时间不能重叠`;
  }
  return null;
}

export function SubtitleRevisionPanel({ episodeId, projectId = "", defaultSourceDocumentVersionId = "", autoDeriveTTS = false, onCreated }: { episodeId: string; projectId?: string; defaultSourceDocumentVersionId?: string; autoDeriveTTS?: boolean; onCreated?: () => void }) {
  const [format, setFormat] = useState("SRT");
  const [sourceDocumentVersionId, setSourceDocumentVersionId] = useState(defaultSourceDocumentVersionId);
  const [cues, setCues] = useState<CueDraft[]>(defaultCues);
  const [style, setStyle] = useState<SubtitleStyle>(defaultStyle);
  const [styleExpanded, setStyleExpanded] = useState(false);
  const [templates, setTemplates] = useState<Array<{ id: string; code: string; title: string }>>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [availableFonts, setAvailableFonts] = useState<string[]>([defaultStyle.font]);
  const [templateName, setTemplateName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [sourceDocuments, setSourceDocuments] = useState<SourceDocumentChoice[]>([]);
  const [sourceDocumentsState, setSourceDocumentsState] = useState<"loading" | "ready" | "error">(projectId ? "loading" : "ready");
  const [ttsPlan, setTTSPlan] = useState<TTSSubtitleDraftPlan | null>(null);
  const [ttsPlanPending, setTTSPlanPending] = useState(false);
  const [overwriteArmed, setOverwriteArmed] = useState(false);
  const autoDeriveStarted = useRef(false);

  useEffect(() => {
    if (!projectId) return;
    void Promise.all([listSubtitleStyleTemplates(projectId), getProjectConfiguration(projectId)])
      .then(([result, configuration]) => {
        setTemplates(result.items.map((item) => ({ id: item.id, code: item.code, title: item.title })));
        const current = result.items[0];
        if (current) {
          const content = current.content;
          setSelectedTemplateId(current.id);
          setStyle({ font: String(content.font ?? defaultStyle.font), size: Number(content.size ?? defaultStyle.size), color: String(content.color ?? defaultStyle.color), position: String(content.position ?? defaultStyle.position) as SubtitleStyle["position"], outline: Number(content.outline ?? defaultStyle.outline) });
        } else {
          const resolution = resolutionFromPlan(configuration.configuration.production_plan?.plan);
          setStyle((old) => ({ ...old, size: Math.min(72, Math.max(28, Math.round(resolution.height * 0.035))) }));
        }
        const subtitleMode = String(configuration.configuration.production_plan?.plan?.subtitle_mode ?? "");
        if (["BURN_IN", "BOTH"].includes(subtitleMode)) setFormat("ASS");
      })
      .catch(() => setTemplates([]));
  }, [projectId]);

  useEffect(() => {
    const candidates = ["Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "SimHei", "SimSun", "Arial", "system-ui"];
    const fontSet = document.fonts;
    const detected = fontSet ? candidates.filter((font) => fontSet.check(`16px "${font}"`)) : candidates;
    setAvailableFonts(detected.length ? detected : ["system-ui"]);
    setStyle((old) => detected.includes(old.font) ? old : { ...old, font: detected[0] ?? "system-ui" });
  }, []);

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

  const loadTTSPlan = useCallback(async (forceOverwrite = false) => {
    const hasManualContent = cues.length > 1 || cues.some((cue) => Boolean(cue.text.trim()));
    if (hasManualContent && !forceOverwrite && !overwriteArmed) {
      setOverwriteArmed(true);
      setError("当前表单已有字幕内容。再次点击“确认覆盖并载入”才会以 TTS 草稿替换；尚未覆盖。 ");
      return;
    }
    setTTSPlanPending(true); setError(null); setSuccess(null);
    try {
      const result = await getEpisodeTTSSubtitleDraftPlan(episodeId, sourceDocumentVersionId || undefined);
      setTTSPlan(result.plan);
      if (!result.plan.ready_to_load) {
        throw new Error(result.plan.blockers.map((item) => item.message).join("；") || "当前采用结果不足以生成字幕草稿");
      }
      setCues(result.plan.cues.map((cue) => ({ ...cue, style: cue.style ?? {} })));
      if (result.plan.source_document_version_id) setSourceDocumentVersionId(result.plan.source_document_version_id);
      setOverwriteArmed(false);
      setSuccess(`已载入 ${result.plan.summary.cue_count} 条可审阅字幕草稿；${result.plan.summary.missing_count ? `${result.plan.summary.missing_count} 条未采用对白未载入。` : "当前对白采用结果已覆盖。"}尚未创建 revision。`);
    } catch (caught) {
      setError(`TTS 字幕草稿不可用：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setTTSPlanPending(false);
    }
  }, [cues, episodeId, overwriteArmed, sourceDocumentVersionId]);

  useEffect(() => {
    if (!autoDeriveTTS || autoDeriveStarted.current) return;
    autoDeriveStarted.current = true;
    void loadTTSPlan(true);
  }, [autoDeriveTTS, loadTTSPlan]);

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

  const cueIssue = cueValidationIssue(cues);
  const submit = async () => {
    setPending(true); setError(null); setSuccess(null);
    try {
      if (!sourceDocumentVersionId.trim()) throw new Error("必须先选择已解析的源剧本文档版本");
      if (cueIssue) throw new Error(cueIssue);
      const normalizedCues = cues.map((cue) => {
        const startUs = cue.start_us, endUs = cue.end_us, text = cue.text?.trim();
        if (!Number.isInteger(startUs) || !Number.isInteger(endUs) || !text) throw new Error("cue 需要整数 start_us/end_us 和非空 text");
        if (endUs <= startUs) throw new Error("每条 cue 的结束时间必须晚于开始时间");
        return { start_us: startUs as number, end_us: endUs as number, text, style: cue.style ?? {} };
      });
      const result = await createSubtitleRevision(episodeId, { format, cues: normalizedCues, style, authority: { text_authority: "SCRIPT", source_document_version_id: sourceDocumentVersionId.trim() } });
      setSuccess(`已创建字幕 revision v${result.subtitle.revision_no} · ${result.subtitle.format} · ${result.subtitle.cues.length} 条；样式 ${style.font}/${style.size}px`);
      onCreated?.();
    } catch (caught) { setError(`字幕 revision 创建失败：${caught instanceof Error ? caught.message : String(caught)}`); }
    finally { setPending(false); }
  };
  return <section className="panel subtitle-revision-panel" aria-labelledby="subtitle-revision-title">
    <div className="panel-heading"><div><p className="eyebrow">字幕修订</p><h3 id="subtitle-revision-title">字幕生成、校对与格式导出</h3></div><span className="status-pill neutral">剧本权威</span></div>
    <p className="muted">字幕文本必须来自已解析的剧本文档版本；ASR 只能作为对齐辅助。每次保存都是不可变 revision，不覆盖历史。</p>
    <div className="subtitle-tts-draft" role="region" aria-label="TTS 字幕草稿">
      <div><strong>从已采用 TTS 派生待审草稿</strong><span>对白最新文本 revision 提供文字，已采用且完整性通过的 TTS 媒体只提供时长；此操作不会保存字幕 revision。</span></div>
      <button className="secondary" type="button" disabled={ttsPlanPending} onClick={() => void loadTTSPlan(overwriteArmed)}>{ttsPlanPending ? "正在核对权威与时长…" : overwriteArmed ? "确认覆盖并载入" : "载入 TTS 字幕草稿"}</button>
    </div>
    {ttsPlan && <div className={`subtitle-tts-plan ${ttsPlan.status.toLowerCase()}`} role="status">
      <strong>{ttsPlan.status === "READY" ? "TTS 草稿齐全" : ttsPlan.status === "PARTIAL" ? "TTS 草稿部分齐全" : "TTS 草稿被阻塞"}</strong>
      <span>{ttsPlan.summary.cue_count} 条可载入 · {ttsPlan.summary.missing_count} 条缺失 · 文字权威 SCRIPT · 时间依据已采用 TTS</span>
      {ttsPlan.missing.length > 0 && <ul>{ttsPlan.missing.map((item) => <li key={item.line_id}>{item.code}：{item.message}</li>)}</ul>}
      {ttsPlan.blockers.length > 0 && <ul>{ttsPlan.blockers.map((item, index) => <li key={`${item.code}-${index}`}>{item.message}</li>)}</ul>}
    </div>}
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
      <label>字幕文件格式<select value={format} onChange={(event) => setFormat(event.target.value)}><option value="SRT">通用字幕（SRT）</option><option value="VTT">网页字幕（WebVTT）</option><option value="ASS">高级样式字幕（ASS）</option></select></label>
    </div>
    <fieldset className="subtitle-cues-field"><legend>字幕条目</legend>
      <div className="structured-control-list">
        {cues.map((cue, index) => <div className="structured-control-row wide" key={index}>
          <label>开始（微秒）<input aria-label={`字幕 ${index + 1} 开始（微秒）`} type="number" min="0" step="1000" value={cue.start_us} onChange={(event) => setCues((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, start_us: Number(event.target.value) } : item))} /></label>
          <label>结束（微秒）<input aria-label={`字幕 ${index + 1} 结束（微秒）`} type="number" min="1" step="1000" value={cue.end_us} onChange={(event) => setCues((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, end_us: Number(event.target.value) } : item))} /></label>
          <label className="wide">字幕文本<textarea aria-label={`字幕 ${index + 1} 文本`} value={cue.text} onChange={(event) => setCues((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, text: event.target.value } : item))} placeholder="输入这一时间段实际显示的字幕" /></label>
          <button type="button" className="secondary" aria-label={`删除字幕 ${index + 1}`} disabled={cues.length === 1} onClick={() => setCues((items) => items.filter((_, itemIndex) => itemIndex !== index))}>删除</button>
        </div>)}
      </div>
      <button type="button" className="secondary" onClick={() => setCues((items) => [...items, { start_us: items.at(-1)?.end_us ?? 0, end_us: (items.at(-1)?.end_us ?? 0) + 2_000_000, text: "" }])}>添加字幕条目</button>
    </fieldset>
    <button className="secondary" type="button" aria-expanded={styleExpanded} onClick={() => setStyleExpanded((value) => !value)}>{styleExpanded ? "收起字幕样式" : "字幕样式模板（字体/字号/颜色/位置/描边）"}</button>
    {styleExpanded && <div className="subtitle-style-editor">
      <div className="field-grid">
        <label>字体<select value={style.font} onChange={(event) => setStyle({ ...style, font: event.target.value })}>{availableFonts.map((font) => <option value={font} key={font}>{font}</option>)}</select><small>仅显示当前浏览器检测到的本机字体</small></label>
        <label>字号（8—160）<input type="number" min="8" max="160" value={style.size} onChange={(event) => { const raw = event.target.value; if (raw === "") return; const n = Number(raw.replace(/^(-?)0+(?=\d)/, "$1")); setStyle({ ...style, size: Number.isFinite(n) ? Math.min(160, Math.max(8, n)) : style.size }); }} /></label>
        <label>颜色<input type="color" value={style.color} onChange={(event) => setStyle({ ...style, color: event.target.value.toUpperCase() })} /><output>{style.color}</output></label>
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
    <div className="action-row"><button className="primary-action" type="button" onClick={() => void submit()} disabled={pending || !sourceDocumentVersionId || Boolean(cueIssue)} title={!sourceDocumentVersionId ? sourceDocumentsState === "loading" ? "正在读取可用的源剧本文档" : "请先选择已解析的源剧本文档" : cueIssue ?? undefined}>{pending ? "校验并保存中…" : "创建字幕 revision"}</button><span className="muted">{!sourceDocumentVersionId ? "需要先选择源剧本文档" : cueIssue ?? "提交时还会检查阅读速度和服务端契约"}</span></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

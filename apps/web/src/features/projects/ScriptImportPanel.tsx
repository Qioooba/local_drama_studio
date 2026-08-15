import { useState } from "react";
import { commitImportSession, importScriptDocument, pickLocalDocumentFile, type DocumentImport } from "../../generated/api";

export function ScriptImportPanel({ projectId }: { projectId: string }) {
  const [path, setPath] = useState("");
  const [prepared, setPrepared] = useState<DocumentImport | null>(null);
  const [committed, setCommitted] = useState(false);
  const [pending, setPending] = useState<"browse" | "preview" | "commit" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const changePath = (value: string) => {
    setPath(value);
    setPrepared(null);
    setCommitted(false);
    setError(null);
  };
  const browse = async () => {
    setPending("browse");
    setError(null);
    try {
      const result = await pickLocalDocumentFile();
      if (result.selection.selected && result.selection.path) changePath(result.selection.path);
    } catch (reason) {
      setError(`选择器失败：${String(reason)}。也可以粘贴绝对路径。`);
    } finally {
      setPending(null);
    }
  };
  const preview = async () => {
    setPending("preview");
    setError(null);
    try {
      const result = await importScriptDocument(projectId, path.trim());
      setPrepared(result.import);
      setCommitted(result.import.status === "COMMITTED");
    } catch (reason) {
      setError(`解析失败：${String(reason)}`);
    } finally {
      setPending(null);
    }
  };
  const commit = async () => {
    if (!prepared) return;
    setPending("commit");
    setError(null);
    try {
      const result = await commitImportSession(prepared.import_session_id, prepared.preview_hash);
      setCommitted(result.commit.status === "COMMITTED");
    } catch (reason) {
      setError(`提交失败：${String(reason)}`);
    } finally {
      setPending(null);
    }
  };

  return <section className="subpanel script-import-panel" aria-labelledby="script-import-title">
    <div className="section-title"><span id="script-import-title">剧本文档导入</span><small>FR-ING-001 · TXT / Markdown / DOCX</small></div>
    <label>电脑中的文档绝对路径<span className="inline-control"><input value={path} onChange={(event) => changePath(event.target.value)} placeholder="D:\\Scripts\\episode-01.docx" /><button type="button" className="secondary" onClick={() => { void browse(); }} disabled={pending !== null}>{pending === "browse" ? "选择中…" : "浏览…"}</button></span></label>
    <p className="muted">平台会复制并注册不可变源版本；不会修改原文档。解析预览和显式确认分为两步。</p>
    <div className="post-process-actions"><button type="button" className="secondary" onClick={() => { void preview(); }} disabled={!path.trim() || pending !== null}>{pending === "preview" ? "解析中…" : "建立源版本并解析预览"}</button><button type="button" className="primary-action" onClick={() => { void commit(); }} disabled={!prepared || committed || pending !== null}>{pending === "commit" ? "提交中…" : committed ? "已确认导入" : "确认 commit（不覆盖母本）"}</button></div>
    {prepared && <div className="import-preview" aria-label="剧本文档解析预览"><p><strong>{prepared.preview.paragraph_count} 段 · {prepared.preview.character_count} 字符</strong> · preview <code>{prepared.preview_hash.slice(0, 16)}</code></p><ol>{prepared.preview.paragraphs.map((paragraph, index) => <li key={`${index}-${paragraph.slice(0, 16)}`}>{paragraph}</li>)}</ol><p className={committed ? "frame-feedback success" : "frame-feedback"}>{committed ? "COMMITTED：源文档版本已保留，重复提交幂等。" : "PREVIEW_READY：尚未 commit，不会自动创建生产镜头。"}</p></div>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;
}

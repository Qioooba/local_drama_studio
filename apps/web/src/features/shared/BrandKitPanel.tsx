import { useState } from "react";
import { createBrandKit } from "../../generated/api";

export function BrandKitPanel({ projectId }: { projectId: string }) {
  const [code, setCode] = useState("series-brand");
  const [title, setTitle] = useState("系列视觉规范");
  const [tokens, setTokens] = useState('{"colors":{"primary":"#1f2937"},"typography":{"body":"system-ui"},"spacing":{"unit":4}}');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    setBusy(true); setMessage(null); setError(null);
    try {
      let parsed: unknown;
      try { parsed = JSON.parse(tokens); } catch { throw new Error("BrandKit tokens 必须是有效 JSON"); }
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("BrandKit tokens 必须是对象");
      const result = await createBrandKit(projectId, { code: code.trim(), title: title.trim(), tokens: parsed as Record<string, unknown> });
      setMessage(`BrandKit v${String(result.brand_kit.version_no ?? "?")} 已发布；旧版本保持 RETIRED 可追溯。`);
    } catch (caught) { setError(String(caught)); }
    finally { setBusy(false); }
  };
  return <section className="panel brand-kit-panel" aria-labelledby="brand-kit-title"><div className="panel-heading"><div><p className="eyebrow">FR-PST-003 · BRAND / COMPLIANCE</p><h3 id="brand-kit-title">BrandKit 版本</h3></div><span className="status-pill neutral">LOCAL VERSIONED</span></div><p className="muted">BrandKit 只保存项目内视觉 token；每次发布生成新不可变版本，旧版本不覆盖。Watermark/Compliance 执行仍需由具体后处理能力显式绑定。</p><div className="field-grid"><label>代码<input value={code} onChange={(event) => setCode(event.target.value)} /></label><label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label></div><label>Token JSON<textarea value={tokens} onChange={(event) => setTokens(event.target.value)} spellCheck={false} /></label><button className="primary-action" type="button" onClick={() => void create()} disabled={busy}>{busy ? "发布中…" : "发布 BrandKit 新版本"}</button>{message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">BrandKit 发布失败：{error}</p>}</section>;
}

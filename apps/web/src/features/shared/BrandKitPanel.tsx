import { useEffect, useState } from "react";
import { createBrandKit, createCompliancePolicy, createWatermarkProfile, listBrandControls } from "../../generated/api";

export function BrandKitPanel({ projectId }: { projectId: string }) {
  const [code, setCode] = useState("series-brand");
  const [title, setTitle] = useState("系列视觉规范");
  const [tokens, setTokens] = useState('{"colors":{"primary":"#1f2937"},"typography":{"body":"system-ui"},"spacing":{"unit":4}}');
  const [watermarkText, setWatermarkText] = useState("LOCAL STUDY");
  const [watermarkPosition, setWatermarkPosition] = useState("BOTTOM_RIGHT");
  const [maxDuration, setMaxDuration] = useState("600000");
  const [controls, setControls] = useState<{ watermark_profiles: Array<Record<string, unknown>>; compliance_policies: Array<Record<string, unknown>> }>({ watermark_profiles: [], compliance_policies: [] });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void listBrandControls(projectId).then((result) => setControls({ watermark_profiles: result.watermark_profiles, compliance_policies: result.compliance_policies })).catch(() => undefined); }, [projectId]);
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
  const publishWatermark = async () => {
    setBusy(true); setMessage(null); setError(null);
    try {
      const result = await createWatermarkProfile(projectId, { code: "series-watermark", title: "系列水印", config: { text: watermarkText.trim(), position: watermarkPosition, opacity: 0.8, font_size: 24, margin: 24, color: "white" } });
      setMessage(`Watermark v${String(result.watermark_profile.version_no ?? "?")} 已发布；新交付自动绑定 ACTIVE 版本。`);
      const refreshed = await listBrandControls(projectId); setControls({ watermark_profiles: refreshed.watermark_profiles, compliance_policies: refreshed.compliance_policies });
    } catch (caught) { setError(`Watermark 发布失败：${String(caught)}`); } finally { setBusy(false); }
  };
  const publishCompliance = async () => {
    setBusy(true); setMessage(null); setError(null);
    try {
      const result = await createCompliancePolicy(projectId, { code: "local-study", title: "本地学习交流预检", rules: { require_watermark: true, max_duration_ms: Number(maxDuration), require_human_review: true, require_platform_review: true } });
      setMessage(`CompliancePolicy v${String(result.compliance_policy.version_no ?? "?")} 已发布；机器预检不会替代人工/平台审核。`);
      const refreshed = await listBrandControls(projectId); setControls({ watermark_profiles: refreshed.watermark_profiles, compliance_policies: refreshed.compliance_policies });
    } catch (caught) { setError(`CompliancePolicy 发布失败：${String(caught)}`); } finally { setBusy(false); }
  };
  return <section className="panel brand-kit-panel" aria-labelledby="brand-kit-title"><div className="panel-heading"><div><p className="eyebrow">FR-PST-003 · 品牌 / 水印 / 合规</p><h3 id="brand-kit-title">本地品牌与合规版本</h3></div><span className="status-pill neutral">本地版本化</span></div><p className="muted">BrandKit、WatermarkProfile、CompliancePolicy 都是不可变版本。交付会读取当前 ACTIVE 版本并写入 manifest；机器预检只负责规则与文件完整性，人工/平台审核仍保持 PENDING。</p><div className="field-grid"><label>BrandKit 代码<input value={code} onChange={(event) => setCode(event.target.value)} /></label><label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label></div><label>BrandKit 令牌 JSON<textarea value={tokens} onChange={(event) => setTokens(event.target.value)} spellCheck={false} /></label><button className="primary-action brand-publish" type="button" onClick={() => void create()} disabled={busy}>{busy ? "发布中…" : "发布 BrandKit 新版本"}</button><div className="field-grid"><label>水印文字<input value={watermarkText} onChange={(event) => setWatermarkText(event.target.value)} /></label><label>位置<select value={watermarkPosition} onChange={(event) => setWatermarkPosition(event.target.value)}><option value="TOP_LEFT">左上</option><option value="TOP_RIGHT">右上</option><option value="BOTTOM_LEFT">左下</option><option value="BOTTOM_RIGHT">右下</option><option value="CENTER">居中</option></select></label></div><button className="primary-action brand-publish" type="button" onClick={() => void publishWatermark()} disabled={busy}>{busy ? "发布中…" : "发布 WatermarkProfile 新版本"}</button><div className="field-grid"><label>最长时长（毫秒）<input type="number" min="1" value={maxDuration} onChange={(event) => setMaxDuration(event.target.value)} /></label><span className="review-meta">新交付要求水印，机器预检通过后仍需人工/平台复核</span></div><button className="primary-action brand-publish" type="button" onClick={() => void publishCompliance()} disabled={busy}>{busy ? "发布中…" : "发布 CompliancePolicy 新版本"}</button><div className="review-meta"><span>Watermark ACTIVE：{controls.watermark_profiles.filter((item) => item.status === "ACTIVE").length}</span><span>Compliance ACTIVE：{controls.compliance_policies.filter((item) => item.status === "ACTIVE").length}</span></div>{message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">{error}</p>}</section>;
}

import { useEffect, useMemo, useState } from "react";
import { createBrandKit, createCompliancePolicy, createWatermarkProfile, listBrandControls } from "../../generated/api";
import { generateMachineCode } from "./autoCode";

const DURATION_OPTIONS = [
  { seconds: "60", label: "1 分钟" },
  { seconds: "180", label: "3 分钟" },
  { seconds: "300", label: "5 分钟" },
  { seconds: "600", label: "10 分钟" },
  { seconds: "1800", label: "30 分钟" },
] as const;

type BrandControls = {
  watermark_profiles: Array<Record<string, unknown>>;
  compliance_policies: Array<Record<string, unknown>>;
};

function activeCount(items: Array<Record<string, unknown>>): number {
  return items.filter((item) => item.status === "ACTIVE").length;
}

export function BrandKitPanel({ projectId }: { projectId: string }) {
  const [title, setTitle] = useState("系列视觉规范");
  const [primaryColor, setPrimaryColor] = useState("#1f2937");
  const [watermarkText, setWatermarkText] = useState("LOCAL STUDY");
  const [watermarkPosition, setWatermarkPosition] = useState("BOTTOM_RIGHT");
  const [maxDurationSeconds, setMaxDurationSeconds] = useState("600");
  const [controls, setControls] = useState<BrandControls>({ watermark_profiles: [], compliance_policies: [] });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const baseCode = useMemo(() => generateMachineCode("BRAND", title).toLowerCase().replace(/_/g, "-"), [title]);

  const refresh = async () => {
    const result = await listBrandControls(projectId);
    setControls({ watermark_profiles: result.watermark_profiles, compliance_policies: result.compliance_policies });
  };

  useEffect(() => { void refresh().catch(() => undefined); }, [projectId]);

  const savePublishingRules = async () => {
    if (!title.trim() || !watermarkText.trim() || !baseCode) return;
    setBusy(true); setMessage(null); setError(null);
    try {
      const brand = await createBrandKit(projectId, {
        code: baseCode,
        title: title.trim(),
        tokens: { colors: { primary: primaryColor }, typography: { body: "system-ui" }, spacing: { unit: 4 } },
      });
      const watermark = await createWatermarkProfile(projectId, {
        code: `${baseCode}-watermark`,
        title: `${title.trim()}水印`,
        config: { text: watermarkText.trim(), position: watermarkPosition, opacity: 0.8, font_size: 24, margin: 24, color: "white" },
      });
      const compliance = await createCompliancePolicy(projectId, {
        code: `${baseCode}-review`,
        title: `${title.trim()}发布检查`,
        rules: { require_watermark: true, max_duration_ms: Number(maxDurationSeconds) * 1000, require_human_review: true, require_platform_review: true },
      });
      setMessage(`发布规则已更新：视觉 v${String(brand.brand_kit.version_no ?? "?")}、水印 v${String(watermark.watermark_profile.version_no ?? "?")}、审核 v${String(compliance.compliance_policy.version_no ?? "?")}。`);
      await refresh();
    } catch (caught) {
      setError(`保存发布规则失败：${String(caught)}`);
    } finally {
      setBusy(false);
    }
  };

  return <section className="panel brand-kit-panel" aria-labelledby="brand-kit-title">
    <div className="panel-heading"><div><p className="eyebrow">品牌与水印</p><h3 id="brand-kit-title">发布时使用什么视觉标记？</h3></div><span className="status-pill neutral">自动保存为可追溯版本</span></div>
    <p className="muted">设置一次即可同时更新视觉规范、水印和发布检查；系统会生成内部代码与配置，不需要填写 JSON。</p>

    <div className="brand-rule-grid">
      <label>规范名称<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：第一季发布规范" /></label>
      <label>品牌主色<span className="brand-color-control"><input type="color" value={primaryColor} onChange={(event) => setPrimaryColor(event.target.value)} /><output>{primaryColor.toUpperCase()}</output></span></label>
      <label>水印文字<input value={watermarkText} onChange={(event) => setWatermarkText(event.target.value)} /></label>
      <label>水印位置<select value={watermarkPosition} onChange={(event) => setWatermarkPosition(event.target.value)}><option value="TOP_LEFT">左上</option><option value="TOP_RIGHT">右上</option><option value="BOTTOM_LEFT">左下</option><option value="BOTTOM_RIGHT">右下</option><option value="CENTER">居中</option></select></label>
      <label>单条作品最长时长<select value={maxDurationSeconds} onChange={(event) => setMaxDurationSeconds(event.target.value)}>{DURATION_OPTIONS.map((option) => <option key={option.seconds} value={option.seconds}>{option.label}</option>)}</select></label>
      <div className="brand-rule-note"><strong>发布前始终人工复核</strong><span>系统检查水印、文件和时长，但不会替你确认平台合规。</span></div>
    </div>

    <button className="primary-action brand-publish" type="button" onClick={() => void savePublishingRules()} disabled={busy || !title.trim() || !watermarkText.trim()}>{busy ? "正在保存三项规则…" : "保存发布规则"}</button>
    <details className="brand-rule-history"><summary>查看已生效规则</summary><div className="review-meta"><span>水印规则：{activeCount(controls.watermark_profiles) ? "已生效" : "尚未设置"}</span><span>发布检查：{activeCount(controls.compliance_policies) ? "已生效" : "尚未设置"}</span><span>系统标识：<code>{baseCode || "等待名称"}</code></span></div></details>
    {message && <p className="review-success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;
}

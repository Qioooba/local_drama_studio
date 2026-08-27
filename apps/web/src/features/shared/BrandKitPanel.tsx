import { useEffect, useMemo, useRef, useState } from "react";
import { createBrandKit, createCompliancePolicy, createWatermarkProfile, listBrandControls } from "../../generated/api";
import { generateMachineCode } from "./autoCode";

type BrandControls = {
  brand_kits: Array<Record<string, unknown>>;
  watermark_profiles: Array<Record<string, unknown>>;
  compliance_policies: Array<Record<string, unknown>>;
};

function activeCount(items: Array<Record<string, unknown>>): number {
  return items.filter((item) => item.status === "ACTIVE").length;
}

export function BrandKitPanel({ projectId }: { projectId: string }) {
  const [title, setTitle] = useState("系列视觉规范");
  const [primaryColor, setPrimaryColor] = useState("#1f2937");
  const [fontFamily, setFontFamily] = useState("system-ui");
  const [spacingUnit, setSpacingUnit] = useState(4);
  const [watermarkEnabled, setWatermarkEnabled] = useState(true);
  const [watermarkText, setWatermarkText] = useState("LOCAL STUDY");
  const [watermarkPosition, setWatermarkPosition] = useState("BOTTOM_RIGHT");
  const [watermarkColor, setWatermarkColor] = useState("#ffffff");
  const [watermarkOpacity, setWatermarkOpacity] = useState(0.8);
  const [watermarkFontSize, setWatermarkFontSize] = useState(24);
  const [watermarkMargin, setWatermarkMargin] = useState(24);
  const [maxDurationSeconds, setMaxDurationSeconds] = useState("600");
  const [requireHumanReview, setRequireHumanReview] = useState(true);
  const [requirePlatformReview, setRequirePlatformReview] = useState(true);
  const [controls, setControls] = useState<BrandControls>({ brand_kits: [], watermark_profiles: [], compliance_policies: [] });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hydrated = useRef(false);
  const baseCode = useMemo(() => generateMachineCode("BRAND", title).toLowerCase().replace(/_/g, "-"), [title]);

  const refresh = async () => {
    const result = await listBrandControls(projectId);
    setControls(result);
    if (!hydrated.current) {
      hydrated.current = true;
      const brand = result.brand_kits.find((item) => item.status === "ACTIVE") ?? result.brand_kits.at(-1);
      const watermark = result.watermark_profiles.find((item) => item.status === "ACTIVE") ?? result.watermark_profiles.at(-1);
      const compliance = result.compliance_policies.find((item) => item.status === "ACTIVE") ?? result.compliance_policies.at(-1);
      const tokens = (brand?.tokens ?? {}) as Record<string, Record<string, unknown>>;
      const config = (watermark?.config ?? {}) as Record<string, unknown>;
      const rules = (compliance?.rules ?? {}) as Record<string, unknown>;
      if (typeof brand?.title === "string") setTitle(brand.title);
      if (typeof tokens.colors?.primary === "string") setPrimaryColor(tokens.colors.primary);
      if (typeof tokens.typography?.body === "string") setFontFamily(tokens.typography.body);
      if (Number.isFinite(Number(tokens.spacing?.unit))) setSpacingUnit(Number(tokens.spacing.unit));
      if (typeof config.enabled === "boolean") setWatermarkEnabled(config.enabled);
      if (typeof config.text === "string") setWatermarkText(config.text);
      if (typeof config.position === "string") setWatermarkPosition(config.position);
      if (typeof config.color === "string") setWatermarkColor(config.color);
      if (Number.isFinite(Number(config.opacity))) setWatermarkOpacity(Number(config.opacity));
      if (Number.isFinite(Number(config.font_size))) setWatermarkFontSize(Number(config.font_size));
      if (Number.isFinite(Number(config.margin))) setWatermarkMargin(Number(config.margin));
      if (Number.isFinite(Number(rules.max_duration_ms))) setMaxDurationSeconds(String(Number(rules.max_duration_ms) / 1000));
      if (typeof rules.require_human_review === "boolean") setRequireHumanReview(rules.require_human_review);
      if (typeof rules.require_platform_review === "boolean") setRequirePlatformReview(rules.require_platform_review);
    }
  };

  useEffect(() => { void refresh().catch(() => undefined); }, [projectId]);

  const savePublishingRules = async () => {
    if (!title.trim() || (watermarkEnabled && !watermarkText.trim()) || !baseCode) return;
    setBusy(true); setMessage(null); setError(null);
    try {
      const brand = await createBrandKit(projectId, {
        code: baseCode,
        title: title.trim(),
        tokens: { colors: { primary: primaryColor }, typography: { body: fontFamily.trim() || "system-ui" }, spacing: { unit: spacingUnit } },
      });
      const watermark = await createWatermarkProfile(projectId, {
        code: `${baseCode}-watermark`,
        title: `${title.trim()}水印`,
        config: { enabled: watermarkEnabled, text: watermarkText.trim(), position: watermarkPosition, opacity: watermarkOpacity, font_size: watermarkFontSize, margin: watermarkMargin, color: watermarkColor },
      });
      const compliance = await createCompliancePolicy(projectId, {
        code: `${baseCode}-review`,
        title: `${title.trim()}发布检查`,
        rules: { require_watermark: watermarkEnabled, max_duration_ms: Number(maxDurationSeconds) * 1000, require_human_review: requireHumanReview, require_platform_review: requirePlatformReview },
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
      <label>正文字体<input value={fontFamily} onChange={(event) => setFontFamily(event.target.value)} placeholder="system-ui" /></label>
      <label>间距基准（px）<input type="number" min={1} max={32} value={spacingUnit} onChange={(event) => setSpacingUnit(Number(event.target.value))} /></label>
      <label><input type="checkbox" checked={watermarkEnabled} onChange={(event) => setWatermarkEnabled(event.target.checked)} />启用交付水印</label>
      <label>水印文字<input value={watermarkText} onChange={(event) => setWatermarkText(event.target.value)} disabled={!watermarkEnabled} /></label>
      <label>水印位置<select value={watermarkPosition} onChange={(event) => setWatermarkPosition(event.target.value)}><option value="TOP_LEFT">左上</option><option value="TOP_RIGHT">右上</option><option value="BOTTOM_LEFT">左下</option><option value="BOTTOM_RIGHT">右下</option><option value="CENTER">居中</option></select></label>
      <label>水印颜色<span className="brand-color-control"><input type="color" value={watermarkColor} onChange={(event) => setWatermarkColor(event.target.value)} /><output>{watermarkColor.toUpperCase()}</output></span></label>
      <label>水印透明度<input type="number" min={0} max={1} step={0.05} value={watermarkOpacity} onChange={(event) => setWatermarkOpacity(Number(event.target.value))} /></label>
      <label>水印字号（px）<input type="number" min={8} max={240} value={watermarkFontSize} onChange={(event) => setWatermarkFontSize(Number(event.target.value))} /></label>
      <label>水印边距（px）<input type="number" min={0} max={1000} value={watermarkMargin} onChange={(event) => setWatermarkMargin(Number(event.target.value))} /></label>
      <label>单条作品最长时长<input aria-label="单条作品最长时长" type="number" min={1} max={86400} value={maxDurationSeconds} onChange={(event) => setMaxDurationSeconds(event.target.value)} /><small>秒</small></label>
      <label><input type="checkbox" checked={requireHumanReview} onChange={(event) => setRequireHumanReview(event.target.checked)} />交付前必须人工复核</label>
      <label><input type="checkbox" checked={requirePlatformReview} onChange={(event) => setRequirePlatformReview(event.target.checked)} />必须完成平台规则复核</label>
      <div className="brand-rule-note"><strong>表单继承当前生效版本</strong><span>首次使用显示安全缺省值；保存后会形成新的可追溯版本。</span></div>
    </div>

    <button className="primary-action brand-publish" type="button" onClick={() => void savePublishingRules()} disabled={busy || !title.trim() || (watermarkEnabled && !watermarkText.trim())}>{busy ? "正在保存三项规则…" : "保存发布规则"}</button>
    <details className="brand-rule-history"><summary>查看已生效规则</summary><div className="review-meta"><span>视觉规范：{activeCount(controls.brand_kits) ? "已生效" : "尚未设置"}</span><span>水印规则：{activeCount(controls.watermark_profiles) ? "已生效" : "尚未设置"}</span><span>发布检查：{activeCount(controls.compliance_policies) ? "已生效" : "尚未设置"}</span><span>系统标识：<code>{baseCode || "等待名称"}</code></span></div></details>
    {message && <p className="review-success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;
}

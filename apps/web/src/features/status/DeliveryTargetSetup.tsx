import { useEffect, useMemo, useState } from "react";
import {
  createDeliveryTarget,
  createDeliveryTargetFromPreset,
  listDeliveryPresets,
  selectDeliveryTargetVersion,
  type DeliveryPreset,
  type ProjectConfiguration,
} from "../../generated/api";
import { generateMachineCode } from "../shared/autoCode";
import { resolutionFromPlan } from "../shared/effectiveDefaults";

const CUSTOM_FORMATS = [
  { id: "vertical-standard", title: "竖屏 1080P", width: 1080, height: 1920, fps: 30, bitrateKbps: 6000 },
  { id: "horizontal-standard", title: "横屏 1080P", width: 1920, height: 1080, fps: 30, bitrateKbps: 8000 },
  { id: "vertical-cinematic", title: "竖屏 24 帧", width: 1080, height: 1920, fps: 24, bitrateKbps: 6000 },
  { id: "horizontal-cinematic", title: "横屏 24 帧", width: 1920, height: 1080, fps: 24, bitrateKbps: 8000 },
] as const;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatSpec(spec: Record<string, unknown> | undefined): string {
  if (!spec) return "规格详情不可用";
  const width = Number(spec.width || 0);
  const height = Number(spec.height || 0);
  const fps = Number(spec.fps || 0);
  return width && height ? `${width} × ${height}${fps ? ` · ${fps} fps` : ""}` : "本地文件交付";
}

function generatedPath(code: string): string {
  return `06_delivery/${code.toLowerCase().replace(/[^a-z0-9_-]+/g, "_")}`;
}

export function DeliveryTargetSetup({ configuration, projectId, onChanged }: {
  configuration: ProjectConfiguration;
  projectId: string;
  onChanged?: () => void;
}) {
  const [presets, setPresets] = useState<DeliveryPreset[]>([]);
  const [presetCode, setPresetCode] = useState("");
  const [targetVersionId, setTargetVersionId] = useState(configuration.selected_delivery_target_version_id ?? "");
  const [customTitle, setCustomTitle] = useState("自定义本地交付");
  const projectResolution = resolutionFromPlan(configuration.production_plan?.plan);
  const [customFormatId, setCustomFormatId] = useState<string>("project");
  const [customWidth, setCustomWidth] = useState(projectResolution.width);
  const [customHeight, setCustomHeight] = useState(projectResolution.height);
  const [customFps, setCustomFps] = useState(projectResolution.fps);
  const [customBitrateKbps, setCustomBitrateKbps] = useState(6000);
  const [audioCodec, setAudioCodec] = useState("AAC");
  const [customPathOverride, setCustomPathOverride] = useState("");
  const [subtitleMode, setSubtitleMode] = useState("SIDECAR");
  const [pending, setPending] = useState<"preset" | "custom" | "select" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setTargetVersionId(configuration.selected_delivery_target_version_id ?? ""), [configuration.selected_delivery_target_version_id]);
  useEffect(() => {
    let cancelled = false;
    void listDeliveryPresets()
      .then(({ items }) => {
        if (cancelled) return;
        setPresets(items);
        setPresetCode((current) => current || items[0]?.code || "");
      })
      .catch((caught) => { if (!cancelled) setError(`读取发布规格失败：${errorMessage(caught)}`); });
    return () => { cancelled = true; };
  }, []);

  const selectedPreset = presets.find((preset) => preset.code === presetCode);
  const currentTarget = configuration.delivery_targets.find((target) => target.version_id === configuration.selected_delivery_target_version_id);
  const selectedExisting = configuration.delivery_targets.find((target) => target.version_id === targetVersionId);
  const customFormat = CUSTOM_FORMATS.find((item) => item.id === customFormatId);
  const customCode = useMemo(() => generateMachineCode("DELIVERY", customTitle).toLowerCase(), [customTitle]);
  const customPath = generatedPath(customCode || "delivery_custom");
  const effectiveCustomPath = customPathOverride.trim() || customPath;

  useEffect(() => {
    if (customFormatId === "project") {
      const inherited = resolutionFromPlan(configuration.production_plan?.plan);
      setCustomWidth(inherited.width); setCustomHeight(inherited.height); setCustomFps(inherited.fps);
      return;
    }
    const format = CUSTOM_FORMATS.find((item) => item.id === customFormatId);
    if (format) {
      setCustomWidth(format.width); setCustomHeight(format.height); setCustomFps(format.fps); setCustomBitrateKbps(format.bitrateKbps);
    }
  }, [configuration.production_plan?.version_id, customFormatId]);

  const createPreset = async () => {
    if (!selectedPreset) return;
    setPending("preset"); setMessage(null); setError(null);
    try {
      await createDeliveryTargetFromPreset(projectId, { preset_code: selectedPreset.code, title: selectedPreset.title });
      setMessage(`已创建并启用“${selectedPreset.title}”发布规格。`);
      onChanged?.();
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setPending(null); }
  };

  const createCustom = async () => {
    if (!customTitle.trim() || !customCode) return;
    setPending("custom"); setMessage(null); setError(null);
    try {
      await createDeliveryTarget(projectId, {
        code: customCode,
        title: customTitle.trim(),
        transport: "LOCAL_FILESYSTEM",
        spec: {
          path_rel: effectiveCustomPath,
          width: customWidth,
          height: customHeight,
          fps: customFps,
          bitrate_kbps: customBitrateKbps,
          audio: audioCodec,
          subtitles: subtitleMode,
        },
      });
      setMessage(`已创建并启用“${customTitle.trim()}”。`);
      onChanged?.();
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setPending(null); }
  };

  const selectExisting = async () => {
    if (!selectedExisting || selectedExisting.version_id === configuration.selected_delivery_target_version_id) return;
    setPending("select"); setMessage(null); setError(null);
    try {
      await selectDeliveryTargetVersion(projectId, selectedExisting.version_id);
      setMessage(`已切换到“${selectedExisting.title || selectedExisting.code}”。历史交付不会被改写。`);
      onChanged?.();
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setPending(null); }
  };

  return <section className="delivery-target-setup" aria-labelledby="delivery-target-setup-title">
    <header className="delivery-target-setup__header">
      <div><p className="eyebrow">发布规格</p><h3 id="delivery-target-setup-title">作品准备发布到哪里？</h3><p>选择平台即可启用完整规格；目录、代码、画面尺寸和帧率由系统管理。</p></div>
      <span className="status-pill neutral">仅保存到本机</span>
    </header>

    <div className="delivery-current" role="status">
      <small>当前发布规格</small>
      <strong>{currentTarget ? currentTarget.title || currentTarget.code : "尚未选择"}</strong>
      <span>{currentTarget ? formatSpec(currentTarget.spec) : "选择下方平台后会自动成为当前规格"}</span>
    </div>

    <div className="delivery-choice-grid">
      <label>发布平台
        <select aria-label="发布平台" value={presetCode} onChange={(event) => setPresetCode(event.target.value)} disabled={pending !== null || presets.length === 0}>
          {presets.length === 0 && <option value="">正在读取平台规格…</option>}
          {presets.map((preset) => <option key={preset.code} value={preset.code}>{preset.title}</option>)}
        </select>
      </label>
      {selectedPreset && <div className="delivery-preset-preview" aria-live="polite"><strong>{formatSpec(selectedPreset.spec)}</strong><span>{selectedPreset.description}</span><small>音频 {selectedPreset.spec.audio} · 字幕 {selectedPreset.spec.subtitles === "SIDECAR" ? "独立字幕文件" : selectedPreset.spec.subtitles}</small></div>}
      <button type="button" className="primary-action" disabled={!selectedPreset || pending !== null} onClick={() => void createPreset()}>{pending === "preset" ? "正在准备…" : "使用此发布规格"}</button>
    </div>

    {configuration.delivery_targets.length > 0 && <details className="delivery-existing-details"><summary>切换到以前使用过的规格</summary><div className="delivery-existing-controls"><label>已有规格<select aria-label="已有发布规格" value={targetVersionId} disabled={pending !== null} onChange={(event) => setTargetVersionId(event.target.value)}><option value="">请选择以前的规格</option>{configuration.delivery_targets.map((target) => <option key={target.version_id} value={target.version_id}>{target.title || target.code} · 第 {target.version_no} 版{target.version_id === configuration.selected_delivery_target_version_id ? "（当前）" : ""}</option>)}</select></label><button type="button" className="secondary" disabled={!selectedExisting || selectedExisting.version_id === configuration.selected_delivery_target_version_id || pending !== null} onClick={() => void selectExisting()}>{pending === "select" ? "正在切换…" : "切换规格"}</button></div></details>}

    <details className="delivery-custom-details"><summary>高级：创建非平台自定义规格</summary><div className="delivery-custom-form">
      <label>规格名称<input value={customTitle} onChange={(event) => setCustomTitle(event.target.value)} /></label>
      <label>画面规格来源<select value={customFormatId} onChange={(event) => setCustomFormatId(event.target.value)}><option value="project">继承当前项目方案</option><option value="custom">完全自定义</option>{CUSTOM_FORMATS.map((format) => <option key={format.id} value={format.id}>{format.title} · {format.width}×{format.height} · 每秒 {format.fps} 帧</option>)}</select></label>
      <div className="field-grid"><label>宽度<input type="number" min={64} max={8192} step={2} value={customWidth} onChange={(event) => { setCustomFormatId("custom"); setCustomWidth(Number(event.target.value)); }} /></label><label>高度<input type="number" min={64} max={8192} step={2} value={customHeight} onChange={(event) => { setCustomFormatId("custom"); setCustomHeight(Number(event.target.value)); }} /></label><label>帧率<input type="number" min={1} max={120} value={customFps} onChange={(event) => { setCustomFormatId("custom"); setCustomFps(Number(event.target.value)); }} /></label><label>视频码率（Kbps）<input type="number" min={100} max={200000} step={100} value={customBitrateKbps} onChange={(event) => { setCustomFormatId("custom"); setCustomBitrateKbps(Number(event.target.value)); }} /></label></div>
      <label>音频编码<select value={audioCodec} onChange={(event) => setAudioCodec(event.target.value)}><option value="AAC">AAC（兼容性优先）</option><option value="OPUS">Opus（体积优先）</option><option value="PCM_S16LE">PCM 无损</option><option value="NONE">不含音频</option></select></label>
      <label>字幕输出<select value={subtitleMode} onChange={(event) => setSubtitleMode(event.target.value)}><option value="SIDECAR">独立字幕文件</option><option value="BURN_IN">烧录到画面</option><option value="BOTH">画面 + 独立字幕</option><option value="NONE">无字幕</option></select></label>
      <label>本地保存目录<input value={customPathOverride} onChange={(event) => setCustomPathOverride(event.target.value)} placeholder={customPath} /></label>
      <div className="delivery-derived-facts"><span>系统代码 <code>{customCode || "等待名称"}</code></span><span>保存目录 <code>{effectiveCustomPath}</code></span><span>缺省来源 <strong>{customFormatId === "project" ? "当前项目方案" : customFormat ? "所选规格模板" : "手动输入"}</strong></span></div>
      <button type="button" className="secondary" disabled={!customTitle.trim() || pending !== null} onClick={() => void createCustom()}>{pending === "custom" ? "正在创建…" : "创建并启用自定义规格"}</button>
    </div></details>

    {message && <p className="review-success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">发布规格操作失败：{error}</p>}
  </section>;
}

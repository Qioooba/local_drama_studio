import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  createGlobalModelCompatibilityReport,
  getClientCapabilities,
  listModelLibraryRoots,
  pickLocalModelFile,
  registerGlobalModelReference,
  scanLocalModelRegistry,
  type ModelRegistryScanItem,
} from "../../generated/api";
import { generateMachineCode } from "../shared/autoCode";

type ModelUse = {
  kind: string;
  capability: "T2V" | "I2V" | "VIDEO" | "IMAGE" | "AUDIO" | "TTS" | "TEXT";
  label: string;
};

type ModelUseGroup = {
  label: string;
  items: readonly ModelUse[];
};

const MODEL_USE_GROUPS: readonly ModelUseGroup[] = [
  {
    label: "视频生成",
    items: [
      { kind: "T2V", capability: "T2V", label: "根据文字生成视频" },
      { kind: "I2V", capability: "I2V", label: "让图片动起来" },
    ],
  },
  {
    label: "声音与智能理解",
    items: [
      { kind: "TTS", capability: "TTS", label: "生成角色语音" },
      { kind: "LLM", capability: "TEXT", label: "理解和创作文字" },
      { kind: "CLIP", capability: "TEXT", label: "理解文字与画面" },
    ],
  },
  {
    label: "图像工作流组件",
    items: [
      { kind: "VAE", capability: "IMAGE", label: "图像或视频编解码（VAE）" },
      { kind: "CONTROLNET", capability: "IMAGE", label: "控制人物姿态或构图" },
      { kind: "UPSCALE", capability: "IMAGE", label: "放大和修复画面" },
    ],
  },
];

const MODEL_USES = MODEL_USE_GROUPS.flatMap((group) => group.items);

function modelStem(path: string): string {
  return path.replace(/\\/g, "/").split("/").pop()?.replace(/\.[^.]+$/, "") ?? "";
}

function inferModelKind(path: string): string {
  const value = path.toLowerCase().replace(/[\\/_.-]+/g, " ");
  if (/\b(controlnet|control net|openpose|depth control|canny control)\b/.test(value)) return "CONTROLNET";
  if (/\b(upscale|upscaler|esrgan|realesrgan|real esrgan|swinir|super resolution)\b/.test(value)) return "UPSCALE";
  if (/\b(vae|autoencoder)\b/.test(value)) return "VAE";
  if (/\b(i2v|image2video|image to video|fl2v|fl2va)\b/.test(value)) return "I2V";
  if (/\b(t2v|text2video|text to video)\b/.test(value)) return "T2V";
  if (/\b(tts|text to speech|voice|voxcpm|cosyvoice|fish speech|chattts)\b/.test(value)) return "TTS";
  if (/\b(clip|text encoder|vision encoder|siglip)\b/.test(value)) return "CLIP";
  if (/\b(llm|language model|llama|deepseek|qwen|mistral)\b/.test(value) || value.endsWith(" gguf")) return "LLM";
  return "";
}

function formatFileSize(byteSize: number): string {
  const gib = byteSize / 1024 / 1024 / 1024;
  if (gib >= 1) return `${gib.toFixed(gib >= 10 ? 0 : 1)} GB`;
  return `${Math.max(1, Math.round(byteSize / 1024 / 1024))} MB`;
}

function reportStatusLabel(value: string): string {
  if (value === "PASS") return "通过";
  if (value === "BLOCKED") return "需要处理";
  return value;
}

export function LocalModelReferenceForm({ onRegistered }: { onRegistered: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [kind, setKind] = useState("");
  const [path, setPath] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [scanDir, setScanDir] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanItems, setScanItems] = useState<ModelRegistryScanItem[]>([]);
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const capabilities = useQuery({
    queryKey: ["client-capabilities"],
    queryFn: () => getClientCapabilities(),
    staleTime: Infinity,
    enabled: expanded,
  });
  const modelRoots = useQuery({
    queryKey: ["model-library-roots"],
    queryFn: () => listModelLibraryRoots(),
    staleTime: 30_000,
    enabled: expanded,
  });

  useEffect(() => {
    if (!scanDir && modelRoots.data?.items[0]?.path) setScanDir(modelRoots.data.items[0].path);
  }, [modelRoots.data?.items, scanDir]);

  const selectedUse = MODEL_USES.find((item) => item.kind === kind);
  const suggestedKind = path ? inferModelKind(path) : "";
  const suggestedUse = MODEL_USES.find((item) => item.kind === suggestedKind);
  const selectedScanItem = scanItems.find((item) => item.path === path);
  const code = useMemo(() => generateMachineCode("MODEL", modelStem(path)), [path]);
  const visibleItems = useMemo(() => {
    const query = filter.trim().toLowerCase();
    if (!query) return scanItems;
    return scanItems.filter((item) => item.relative_path.toLowerCase().includes(query));
  }, [filter, scanItems]);

  const choosePath = (nextPath: string) => {
    setPath(nextPath);
    setKind(inferModelKind(nextPath));
    setMessage(null);
  };

  const submit = async () => {
    if (!path.trim() || !kind || !selectedUse || !code) return;
    setPending(true);
    setMessage(null);
    try {
      const registered = await registerGlobalModelReference({
        code,
        kind,
        machine_path_ref: path.trim(),
        license_note: "USER_SUPPLIED_LOCAL_MODEL",
      });
      const result = await createGlobalModelCompatibilityReport(registered.artifact.id, selectedUse.capability);
      setMessage(`已添加到模型库。离线兼容性检查：${reportStatusLabel(result.report.report_status)}。`);
      onRegistered();
    } catch (error) {
      setMessage(`添加失败：${String(error)}`);
    } finally {
      setPending(false);
    }
  };

  const browse = async () => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 65_000);
    setMessage(null);
    try {
      const result = await pickLocalModelFile("", controller.signal);
      if (result.selection.selected && result.selection.path) choosePath(result.selection.path);
    } catch (error) {
      setMessage(controller.signal.aborted ? "文件选择窗口没有响应，请重试。" : `无法打开文件选择窗口：${String(error)}`);
    } finally {
      window.clearTimeout(timeout);
    }
  };

  const readModelFolder = async () => {
    if (!scanDir.trim()) return;
    setScanning(true);
    setScanMessage(null);
    setFilter("");
    try {
      const result = await scanLocalModelRegistry(scanDir.trim());
      setScanItems(result.scan.items);
      setScanMessage(result.scan.scanned_count > 0
        ? `找到 ${result.scan.scanned_count} 个模型${result.scan.truncated ? `，仅显示前 ${result.scan.max_files} 个` : ""}`
        : "这个文件夹里没有找到支持的模型文件");
    } catch (error) {
      setScanItems([]);
      setScanMessage(`读取失败：${String(error)}`);
    } finally {
      setScanning(false);
    }
  };

  const roots = modelRoots.data?.items ?? [];
  const serverDialogs = capabilities.data?.capabilities.server_file_dialogs ?? false;

  return <div className="model-license-import local-model-reference-form">
    <button
      className={expanded ? "secondary" : "primary-action"}
      type="button"
      aria-expanded={expanded}
      onClick={() => setExpanded((value) => !value)}
    >
      {expanded ? "取消添加" : "添加本机模型"}
    </button>

    {expanded && <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div className="local-model-form-intro">
        <strong>添加电脑里已有的模型</strong>
        <span>选择文件并确认用途即可。系统保留原文件位置，只做离线检查，不会复制模型。</span>
      </div>

      <section className="local-model-step" aria-labelledby="local-model-source-title">
        <div className="local-model-step-heading">
          <span aria-hidden="true">1</span>
          <div><strong id="local-model-source-title">选择模型文件</strong><small>{serverDialogs ? "从常用模型文件夹查找，或直接选择一个文件。" : "从常用模型文件夹中查找并选择。"}</small></div>
        </div>

        {modelRoots.isPending || capabilities.isPending ? <p className="muted" role="status">正在读取模型文件夹…</p> : <div className="local-model-source-actions">
          {roots.length > 0 && <>
            <label htmlFor="local-model-root">模型文件夹</label>
            <select id="local-model-root" value={scanDir} onChange={(event) => setScanDir(event.target.value)}>
              {roots.map((root) => <option key={root.id} value={root.path}>{root.label}</option>)}
            </select>
            <button type="button" className="secondary" disabled={scanning || !scanDir} onClick={() => void readModelFolder()}>
              {scanning ? "正在读取…" : "读取模型文件夹"}
            </button>
          </>}
          {serverDialogs && <button type="button" className="secondary" onClick={() => void browse()}>{path ? "选择其他文件" : "选择单个模型文件"}</button>}
        </div>}

        {modelRoots.error && <p className="inline-error" role="alert">模型文件夹读取失败：{String(modelRoots.error)}</p>}
        {!modelRoots.isPending && roots.length === 0 && !serverDialogs && <div className="local-model-setup-guidance">
          <strong>还没有可读取的模型文件夹</strong>
          <span>在 Windows 服务配置的 <code>runtime.model_library_roots</code> 中添加模型目录并重启服务。</span>
        </div>}
        {!modelRoots.isPending && roots.length === 0 && serverDialogs && <p className="local-model-setup-hint">尚未设置常用模型文件夹，可以先选择单个模型文件。</p>}
        {scanMessage && <p className="local-model-scan-message" role="status">{scanMessage}</p>}

        {scanItems.length > 0 && <div className="local-model-results">
          <label htmlFor="local-model-filter">筛选找到的模型</label>
          <input id="local-model-filter" type="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="输入文件名" />
          <div className="local-model-scan-results" aria-label="找到的模型文件">
            {visibleItems.map((item) => {
              const itemUse = MODEL_USES.find((use) => use.kind === inferModelKind(item.path));
              const selected = item.path === path;
              return <button
                type="button"
                className={`local-model-scan-item${selected ? " selected" : ""}`}
                aria-pressed={selected}
                key={item.path}
                onClick={() => choosePath(item.path)}
              >
                <span><strong>{item.relative_path}</strong><small>{formatFileSize(item.byte_size)}{item.quantization_hint !== "UNKNOWN" ? ` · ${item.quantization_hint}` : ""}</small></span>
                <span className="local-model-suggestion">{itemUse?.label ?? "用途待确认"}</span>
              </button>;
            })}
            {visibleItems.length === 0 && <p className="empty-state">没有匹配的模型文件。</p>}
          </div>
        </div>}

        {path && <div className="local-model-selection" aria-live="polite">
          <div><small>已选择</small><strong>{modelStem(path)}</strong><span title={path}>{path}</span></div>
          {selectedScanItem && <span>{formatFileSize(selectedScanItem.byte_size)}{selectedScanItem.quantization_hint !== "UNKNOWN" ? ` · ${selectedScanItem.quantization_hint}` : ""}</span>}
        </div>}
      </section>

      <section className={`local-model-step${path ? "" : " disabled"}`} aria-labelledby="local-model-purpose-title">
        <div className="local-model-step-heading">
          <span aria-hidden="true">2</span>
          <div><strong id="local-model-purpose-title">确认模型用途</strong><small>{path ? "用途决定系统执行哪类兼容性检查。" : "选择模型文件后再确认。"}</small></div>
        </div>
        {path && <div className="local-model-purpose-field">
          <label htmlFor="local-model-kind">主要用途</label>
          <select id="local-model-kind" value={kind} onChange={(event) => setKind(event.target.value)} required>
            <option value="">请选择模型用途</option>
            {MODEL_USE_GROUPS.map((group) => <optgroup label={group.label} key={group.label}>{group.items.map((item) => <option key={item.kind} value={item.kind}>{item.label}</option>)}</optgroup>)}
          </select>
          <small>{suggestedUse ? `系统根据文件名建议“${suggestedUse.label}”，请按实际模型确认。` : "系统无法仅凭文件名判断，请选择最接近的用途。"}</small>
        </div>}
      </section>

      {path && <details className="local-model-technical-details"><summary>技术信息</summary><p>系统标识 <code>{code}</code></p><p>文件位置 <code>{path}</code></p></details>}

      <div className="local-model-submit-bar">
        <span>{path && kind ? `将把“${modelStem(path)}”添加到模型库` : "选择文件并确认用途后即可添加"}</span>
        <button className="primary-action" type="submit" disabled={pending || !path || !kind}>{pending ? "正在检查模型…" : "添加到模型库"}</button>
      </div>
      {message && <p className={message.startsWith("添加失败") || message.startsWith("无法") ? "inline-error" : "review-success"} role="status">{message}</p>}
    </form>}
  </div>;
}

import { useMemo, useState } from "react";
import { createModelCompatibilityReport, pickLocalModelFile, registerLocalModelReference, scanLocalModelRegistry, type ModelRegistryScanItem } from "../../generated/api";
import { generateMachineCode } from "../shared/autoCode";

const MODEL_USES = [
  { kind: "T2V", capability: "T2V", label: "根据文字生成视频" },
  { kind: "I2V", capability: "I2V", label: "让图片动起来" },
  { kind: "TTS", capability: "TTS", label: "生成角色语音" },
  { kind: "VAE", capability: "IMAGE", label: "图像编解码（VAE）" },
  { kind: "CONTROLNET", capability: "IMAGE", label: "控制人物姿态或构图" },
  { kind: "UPSCALE", capability: "IMAGE", label: "放大和修复画面" },
  { kind: "LLM", capability: "TEXT", label: "文本理解与创作" },
  { kind: "CLIP", capability: "TEXT", label: "理解文字与画面" },
] as const;

function modelStem(path: string): string {
  return path.replace(/\\/g, "/").split("/").pop()?.replace(/\.[^.]+$/, "") ?? "";
}

export function LocalModelReferenceForm({ projectId, onRegistered }: { projectId: string; onRegistered: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [kind, setKind] = useState("");
  const [path, setPath] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // Quick server model directory scan for LAN/remote users
  const [showScanner, setShowScanner] = useState(false);
  const [scanDir, setScanDir] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanItems, setScanItems] = useState<ModelRegistryScanItem[]>([]);
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const selectedUse = MODEL_USES.find((item) => item.kind === kind);
  const code = useMemo(() => generateMachineCode("MODEL", modelStem(path)), [path]);

  const submit = async () => {
    if (!path.trim() || !kind || !selectedUse || !code) return;
    setPending(true);
    setMessage(null);
    try {
      const registered = await registerLocalModelReference(projectId, {
        code, kind, machine_path_ref: path.trim(), license_note: "USER_SUPPLIED_LOCAL_MODEL",
      });
      const result = await createModelCompatibilityReport(projectId, registered.artifact.id, selectedUse.capability);
      const capabilityStatus = result.report.capability?.status ?? "NOT_REQUESTED";
      setMessage(`已引用本机模型，未复制或上传权重；用途检查：${capabilityStatus}；兼容性：${result.report.report_status}`);
      onRegistered();
    } catch (error) {
      setMessage(`登记失败：${String(error)}`);
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
      if (result.selection.selected && result.selection.path) setPath(result.selection.path);
    } catch (error) {
      setMessage(controller.signal.aborted
        ? "文件选择器长时间没有返回（若在远程/Mac访问，推荐使用下方“扫描服务端模型目录”直接勾选模型）。"
        : `选择器失败：${String(error)}。若从局域网访问，推荐使用下方“扫描服务端模型目录”。`);
    } finally {
      window.clearTimeout(timeout);
    }
  };

  const scanServerDirectory = async () => {
    if (!scanDir.trim()) return;
    setScanning(true);
    setScanMessage(null);
    try {
      const result = await scanLocalModelRegistry(scanDir.trim());
      setScanItems(result.scan.items);
      setScanMessage(`扫描完成：发现 ${result.scan.scanned_count} 个模型文件`);
    } catch (err) {
      setScanItems([]);
      setScanMessage(`扫描失败：${String(err)}`);
    } finally {
      setScanning(false);
    }
  };

  const selectScannedModel = (item: ModelRegistryScanItem) => {
    setPath(item.path);
    if (!kind) {
      const lower = item.path.toLowerCase();
      if (lower.includes("i2v") || lower.includes("image2video")) setKind("I2V");
      else if (lower.includes("t2v") || lower.includes("text2video")) setKind("T2V");
      else if (lower.includes("tts") || lower.includes("voice") || lower.includes("audio")) setKind("TTS");
      else if (lower.includes("vae")) setKind("VAE");
      else setKind("T2V");
    }
  };

  return <div className="model-license-import local-model-reference-form">
    <button className="primary-action" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "收起模型添加" : "添加电脑里的模型"}</button>
    {expanded && <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div className="local-model-primary-fields">
        <label>这个模型用来做什么？<select value={kind} onChange={(event) => setKind(event.target.value)} required><option value="">请选择用途</option>{MODEL_USES.map((item) => <option key={item.kind} value={item.kind}>{item.label}</option>)}</select></label>
        <div className="local-model-file-choice">
          <span>模型文件</span>
          <button className="secondary" type="button" onClick={() => { void browse(); }}>{path ? "重新选择文件" : "从电脑选择文件"}</button>
          <strong>{path ? modelStem(path) : "尚未选择"}</strong>
          {path && <small title={path}>{path}</small>}
        </div>
      </div>

      <details className="local-model-network-details" open={showScanner} onToggle={(event) => setShowScanner(event.currentTarget.open)}>
        <summary>通过局域网使用工作站上的模型</summary>
        <div className="local-model-network-scan">
          <p className="muted">只有在当前浏览器不在模型工作站上时才需要这里。填写工作站上的模型文件夹，系统会列出可选文件。</p>
          <div className="inline-control">
            <input
              aria-label="工作站模型文件夹"
              value={scanDir}
              onChange={(e) => setScanDir(e.target.value)}
              placeholder="例如 E:\\AI\\Models"
            />
            <button
              type="button"
              className="secondary"
              disabled={scanning || !scanDir.trim()}
              onClick={() => void scanServerDirectory()}
            >
              {scanning ? "扫描中…" : "扫描"}
            </button>
          </div>
          {scanMessage && <p className="muted">{scanMessage}</p>}
          {scanItems.length > 0 && (
            <div className="local-model-scan-results">
              {scanItems.map((item) => (
                <div className="local-model-scan-item" key={item.path}>
                  <div>
                    <strong>{item.relative_path}</strong>
                    <span className="muted">{Math.round(item.byte_size / 1024 / 1024)} MB · {item.quantization_hint}</span>
                  </div>
                  <button type="button" className="secondary btn-sm" onClick={() => selectScannedModel(item)}>选择此模型</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </details>

      {path && <details className="local-model-technical-details"><summary>技术信息</summary><p>系统标识 <code>{code}</code></p><p>将自动检查：{selectedUse?.label ?? "选择用途后显示"}</p></details>}
      <p className="muted">系统只读取文件信息和兼容性，不复制、不上传模型权重。用途选好后，类型和检查项会自动匹配。</p>
      <button className="primary-action" type="submit" disabled={pending || !path || !kind}>{pending ? "正在检查模型…" : "添加并检查模型"}</button>
      {message && <p className={message.startsWith("登记失败") ? "inline-error" : "review-success"} role="status">{message}</p>}
    </form>}
  </div>;
}

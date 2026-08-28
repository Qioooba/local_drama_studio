import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createGlobalModelCompatibilityReport, createModelCompatibilityReport, getClientCapabilities, listModelLibraryRoots, pickLocalModelFile, registerGlobalModelReference, registerLocalModelReference, scanLocalModelRegistry, type ModelRegistryScanItem } from "../../generated/api";
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

export function LocalModelReferenceForm({ projectId, onRegistered }: { projectId?: string; onRegistered: () => void }) {
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
  const capabilities = useQuery({ queryKey: ["client-capabilities"], queryFn: () => getClientCapabilities(), staleTime: Infinity });
  const modelRoots = useQuery({ queryKey: ["model-library-roots"], queryFn: () => listModelLibraryRoots(), staleTime: 30_000 });
  useEffect(() => { if (!scanDir && modelRoots.data?.items[0]?.path) setScanDir(modelRoots.data.items[0].path); }, [modelRoots.data?.items, scanDir]);
  const selectedUse = MODEL_USES.find((item) => item.kind === kind);
  const code = useMemo(() => generateMachineCode("MODEL", modelStem(path)), [path]);

  const submit = async () => {
    if (!path.trim() || !kind || !selectedUse || !code) return;
    setPending(true);
    setMessage(null);
    try {
      const payload = { code, kind, machine_path_ref: path.trim(), license_note: "USER_SUPPLIED_LOCAL_MODEL" };
      const registered = projectId
        ? await registerLocalModelReference(projectId, payload)
        : await registerGlobalModelReference(payload);
      const result = projectId
        ? await createModelCompatibilityReport(projectId, registered.artifact.id, selectedUse.capability)
        : await createGlobalModelCompatibilityReport(registered.artifact.id, selectedUse.capability);
      const capabilityStatus = result.report.capability?.status ?? "NOT_REQUESTED";
      setMessage(`已引用服务端模型，未复制或上传权重；用途检查：${capabilityStatus}；兼容性：${result.report.report_status}`);
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

  const serverDialogs = capabilities.data?.capabilities.server_file_dialogs ?? false;
  return <div className="model-license-import local-model-reference-form">
    <button className="primary-action" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "收起模型添加" : projectId ? "添加服务端模型" : "添加全局模型"}</button>
    {expanded && <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div className="local-model-primary-fields">
        <label>这个模型用来做什么？<select value={kind} onChange={(event) => setKind(event.target.value)} required><option value="">请选择用途</option>{MODEL_USES.map((item) => <option key={item.kind} value={item.kind}>{item.label}</option>)}</select></label>
        {serverDialogs && <div className="local-model-file-choice">
          <span>模型文件</span>
          <button className="secondary" type="button" onClick={() => { void browse(); }}>{path ? "重新选择文件" : "从服务器桌面选择"}</button>
          <strong>{path ? modelStem(path) : "尚未选择"}</strong>
          {path && <small title={path}>{path}</small>}
        </div>}
      </div>

      <details className="local-model-network-details" open={showScanner} onToggle={(event) => setShowScanner(event.currentTarget.open)}>
        <summary>从服务端模型库选择</summary>
        <div className="local-model-network-scan">
          <p className="muted">模型权重不经过浏览器传输。管理员先配置服务端模型库，任何电脑上的浏览器都只从受控目录选择。</p>
          <div className="inline-control">
            {modelRoots.data?.items.length ? <select aria-label="服务端模型库" value={scanDir} onChange={(event) => setScanDir(event.target.value)}>{modelRoots.data.items.map((root) => <option key={root.id} value={root.path}>{root.label}</option>)}</select> : serverDialogs ? <input aria-label="服务器模型文件夹" value={scanDir} onChange={(event) => setScanDir(event.target.value)} placeholder="例如 E:\\AI\\Models" /> : <span className="review-guidance">尚未配置服务端模型库。请管理员设置 <code>LOCAL_DRAMA_MODEL_LIBRARY_ROOTS</code> 后重启。</span>}
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
      <p className="muted">系统只在 Windows 服务端读取模型文件信息和兼容性，不把权重发送到浏览器；添加后可供所有项目选择。</p>
      <button className="primary-action" type="submit" disabled={pending || !path || !kind}>{pending ? "正在检查模型…" : "添加并检查模型"}</button>
      {message && <p className={message.startsWith("登记失败") ? "inline-error" : "review-success"} role="status">{message}</p>}
    </form>}
  </div>;
}

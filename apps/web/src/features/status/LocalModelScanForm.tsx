import { useState } from "react";
import { scanLocalModelRegistry, type ModelRegistryScan } from "../../generated/api";

export function LocalModelScanForm() {
  const [rootPath, setRootPath] = useState("");
  const [scan, setScan] = useState<ModelRegistryScan | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true); setMessage(null);
    try {
      const result = await scanLocalModelRegistry(rootPath.trim());
      setScan(result.scan);
      setMessage(`只读扫描完成：发现 ${result.scan.scanned_count} 个候选`);
    } catch (error) { setScan(null); setMessage(`扫描失败：${String(error)}`); }
    finally { setBusy(false); }
  };
  return <div className="model-license-import local-model-scan-form">
    <div className="workflow-history-heading"><div><p className="eyebrow">FR-OPS-002 · 离线扫描</p><h3>扫描电脑里的模型目录</h3></div><span className="status-pill neutral">只读 · 不复制权重</span></div>
    <p className="muted">输入本机绝对目录；平台只读取扩展名、大小、SHA-256 和文件名量化提示，不加载模型、不连接 Runtime、不上传。</p>
    <div className="field-grid"><label>模型目录绝对路径<input value={rootPath} onChange={(event) => setRootPath(event.target.value)} placeholder="E:\\AI\\ComfyUI-Models" required /></label></div>
    <button className="secondary" type="button" onClick={() => void submit()} disabled={busy || !rootPath.trim()}>{busy ? "扫描中…" : "扫描本机目录"}</button>
    {message && <p className={message.startsWith("扫描失败") ? "inline-error" : "review-success"} role="status">{message}</p>}
    {scan && <div className="configuration-table" role="table" aria-label="本机模型扫描结果"><div className="configuration-row configuration-header" role="row"><strong>文件</strong><strong>大小</strong><strong>Hash / 量化</strong></div>{scan.items.slice(0, 8).map((item) => <div className="configuration-row" role="row" key={item.path}><span title={item.relative_path}>{item.relative_path}</span><span>{Math.round(item.byte_size / 1024 / 1024)} MB</span><span title={`${item.sha256 ?? ""} · ${item.quantization_hint}`}>{String(item.sha256 ?? "").slice(0, 12) || "—"}… · {item.quantization_hint}</span></div>)}</div>}
  </div>;
}

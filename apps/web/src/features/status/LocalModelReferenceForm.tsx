import { useState } from "react";
import { createModelCompatibilityReport, pickLocalModelFile, registerLocalModelReference } from "../../generated/api";

export function LocalModelReferenceForm({ projectId, onRegistered }: { projectId: string; onRegistered: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [code, setCode] = useState("");
  const [kind, setKind] = useState("");
  const [path, setPath] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const submit = async () => {
    setPending(true);
    setMessage(null);
    try {
      const registered = await registerLocalModelReference(projectId, {
        code: code.trim(), kind: kind.trim(), machine_path_ref: path.trim(), license_note: "USER_SUPPLIED_LOCAL_MODEL",
      });
      const result = await createModelCompatibilityReport(projectId, registered.artifact.id);
      setMessage(`已引用本机模型，未复制或上传权重；兼容性：${result.report.report_status}`);
      onRegistered();
    } catch (error) {
      setMessage(`登记失败：${String(error)}`);
    } finally {
      setPending(false);
    }
  };

  const browse = async () => {
    setMessage(null);
    try {
      const result = await pickLocalModelFile();
      if (result.selection.selected && result.selection.path) setPath(result.selection.path);
    } catch (error) {
      setMessage(`选择器失败：${String(error)}。也可以直接粘贴绝对路径。`);
    }
  };

  return <div className="model-license-import local-model-reference-form">
    <button className="primary-action" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "收起本机模型登记" : "添加电脑里的模型"}</button>
    {expanded && <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div className="field-grid">
        <label>模型代码<input value={code} onChange={(event) => setCode(event.target.value)} placeholder="my-local-model" pattern="[a-zA-Z0-9][a-zA-Z0-9._-]*" required /></label>
        <label>模型类型<input value={kind} onChange={(event) => setKind(event.target.value)} placeholder="T2V / I2V / VAE / TTS" required /></label>
        <label>电脑中的模型绝对路径<span className="inline-control"><input value={path} onChange={(event) => setPath(event.target.value)} placeholder="E:\\AI\\Models\\model.safetensors" required /><button className="secondary" type="button" onClick={() => { void browse(); }}>浏览…</button></span></label>
      </div>
      <p className="muted">平台只保存路径引用并读取 hash/格式，不复制、不上传、不随安装包分发模型。路径失效时可用新代码重新登记；授权由用户自行确认。</p>
      <button className="primary-action" type="submit" disabled={pending}>{pending ? "正在读取本机模型…" : "引用并检查兼容性"}</button>
      {message && <p className={message.startsWith("登记失败") ? "inline-error" : "review-success"} role="status">{message}</p>}
    </form>}
  </div>;
}

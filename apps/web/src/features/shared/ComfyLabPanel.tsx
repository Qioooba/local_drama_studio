import { useEffect, useState } from "react";
import {
  captureComfyLabWorkflow,
  createComfyLabTestRun,
  getComfyLabStatus,
  type ComfyLabStatus,
  restartComfyLab,
  startComfyLab,
  stopComfyLab,
} from "../../generated/api";

const sampleWorkflow = { "1": { class_type: "SaveImage", inputs: { filename_prefix: "designer_capture" } } };

export function ComfyLabPanel() {
  const [status, setStatus] = useState<ComfyLabStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    const result = await getComfyLabStatus();
    setStatus(result.status);
  };
  useEffect(() => { void refresh().catch((caught) => setError(String(caught))); }, []);

  // 未配置 Designer 启动路径时，启动/重启按钮保持禁用并给出提示，避免无谓的后端拒绝。
  const canLaunch = status?.launch_configured === true;
  const launchDisabled = busy || status === null || !canLaunch;

  const execute = (action: () => Promise<{ status: ComfyLabStatus } | { capture: { sandbox_rel_path: string } } | { test_run: { status: string } }>) => {
    setBusy(true); setError(null); setMessage(null);
    void action().then((result) => {
      if ("status" in result) setStatus(result.status);
      if ("capture" in result) setMessage(`已捕获到隔离 Designer 沙盒：${result.capture.sandbox_rel_path}`);
      if ("test_run" in result) setMessage(`Designer test plan：${result.test_run.status}（不会写正式项目目录）`);
    }).catch((caught) => setError(String(caught))).finally(() => setBusy(false));
  };

  return <section className="panel comfy-lab-panel" aria-labelledby="comfy-lab-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-WFL-004 · Designer 沙盒</p><h3 id="comfy-lab-title">ComfyUI 实验室</h3></div><span className="status-pill neutral">仅本地 · 不写正式目录</span></div>
    <p className="muted">Designer 是独立的本地工作流调试进程。捕获和 test plan 只允许进入 <code>work/comfy-lab</code> 隔离沙盒；不会修改正式项目 workflow、媒体或批准结果。</p>
    <div className="review-meta"><span>状态：{status?.status ?? "读取中…"}</span><span>端口：{status?.endpoint ?? "未配置"}</span><span>启动配置：{status?.launch_configured ? "已配置" : "未配置"}</span><span>正式目录写入：否</span></div>
    <div className="button-row"><button className="secondary" type="button" onClick={() => execute(startComfyLab)} disabled={launchDisabled || status?.status === "RUNNING"} title={!canLaunch ? "未配置 Designer 启动路径，无法启动" : undefined}>{busy ? "处理中…" : "启动 Designer"}</button><button className="secondary" type="button" onClick={() => execute(stopComfyLab)} disabled={busy || status?.status !== "RUNNING"}>停止</button><button className="secondary" type="button" onClick={() => execute(restartComfyLab)} disabled={launchDisabled || status?.status !== "RUNNING"} title={!canLaunch ? "未配置 Designer 启动路径，无法重启" : undefined}>{busy ? "处理中…" : "重启"}</button><button className="secondary" type="button" onClick={() => execute(() => captureComfyLabWorkflow({ title: "安全 Designer 示例", workflow: sampleWorkflow }))} disabled={busy}>捕获到沙盒</button><button className="secondary" type="button" onClick={() => execute(() => createComfyLabTestRun({ workflow: sampleWorkflow, execute: false }))} disabled={busy}>生成 test plan</button></div>
    {!canLaunch && <p className="review-guidance">Designer 尚未配置启动路径；设置环境变量 <code>LOCAL_DRAMA_COMFY_DESIGNER_PYTHON</code> 与 <code>LOCAL_DRAMA_COMFY_DESIGNER_ROOT</code> 后可启动。捕获与 test plan 仍可在沙盒内使用。</p>}
    {status?.sandbox_root && <p className="muted"><code>{status.sandbox_root}</code> · runtime/network 由服务端明确记录为未接触（除非显式执行 test）。</p>}
    {message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">ComfyUI 实验室：{error}</p>}
  </section>;
}

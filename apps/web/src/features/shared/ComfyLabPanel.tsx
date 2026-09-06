import { useEffect, useRef, useState } from "react";
import {
  captureComfyLabWorkflow,
  createComfyLabTestRun,
  discoverComfyLab,
  getComfyLabCapture,
  getComfyLabStatus,
  listComfyLabCaptures,
  promoteComfyLabCapture,
  restartComfyLab,
  startComfyLab,
  stopComfyLab,
  type ComfyLabCapture,
  type ComfyLabStatus,
} from "../../generated/api";

const ACTION_TIMEOUT_MS = 270_000;

export function ComfyLabPanel() {
  const [status, setStatus] = useState<ComfyLabStatus | null>(null);
  const [captures, setCaptures] = useState<ComfyLabCapture[]>([]);
  const [selected, setSelected] = useState<ComfyLabCapture | null>(null);
  const [workflowJson, setWorkflowJson] = useState("{\n  \"1\": {\n    \"class_type\": \"SaveImage\",\n    \"inputs\": { \"filename_prefix\": \"designer_capture\" }\n  }\n}");
  const [captureTitle, setCaptureTitle] = useState("Designer 工作流");
  const [candidateCode, setCandidateCode] = useState("designer-candidate");
  const [candidateTitle, setCandidateTitle] = useState("Designer 候选工作流");
  const [capability, setCapability] = useState("CUSTOM_COMFY_CANDIDATE");
  const [bindingsJson, setBindingsJson] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchedRoots, setSearchedRoots] = useState<string[]>([]);
  const activeController = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  const refresh = async (signal?: AbortSignal) => {
    const [statusResult, captureResult] = await Promise.all([getComfyLabStatus("", signal), listComfyLabCaptures("", signal)]);
    if (!mounted.current) return;
    setStatus(statusResult.status);
    setCaptures(captureResult.items);
  };

  useEffect(() => {
    mounted.current = true;
    void refresh().catch((caught) => { if (mounted.current) setError(String(caught)); });
    return () => { mounted.current = false; activeController.current?.abort(); };
  }, []);

  const run = (action: (signal: AbortSignal) => Promise<void>) => {
    const controller = new AbortController();
    activeController.current = controller;
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, ACTION_TIMEOUT_MS);
    setBusy(true); setError(null); setMessage(null);
    void action(controller.signal).catch((caught) => {
      if (!mounted.current || (controller.signal.aborted && !timedOut)) return;
      setError(timedOut ? "动作等待超时；服务端可能仍在完成，请刷新状态后确认。" : String(caught));
    }).finally(() => {
      window.clearTimeout(timeout);
      if (activeController.current === controller) activeController.current = null;
      if (mounted.current) setBusy(false);
    });
  };

  const parseObject = (raw: string, label: string) => {
    let parsed: unknown;
    try { parsed = JSON.parse(raw); } catch { throw new Error(`${label}不是有效 JSON。`); }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(`${label}必须是 JSON 对象。`);
    if (Object.keys(parsed as object).length === 0 && label === "工作流") throw new Error("工作流 JSON 不能为空。");
    return parsed as Record<string, unknown>;
  };

  const selectCapture = (captureId: string) => run(async (signal) => {
    if (!captureId) { setSelected(null); return; }
    const result = await getComfyLabCapture(captureId, "", signal);
    setSelected(result.capture);
    if (result.capture.workflow) setWorkflowJson(JSON.stringify(result.capture.workflow, null, 2));
    setCandidateTitle(result.capture.title ? `${result.capture.title} · 候选` : "Designer 候选工作流");
  });

  const capture = () => run(async (signal) => {
    const result = await captureComfyLabWorkflow({ title: captureTitle.trim(), workflow: parseObject(workflowJson, "工作流") }, "", signal);
    const detail = await getComfyLabCapture(result.capture.capture_id, "", signal);
    setSelected(detail.capture);
    setMessage(`已捕获 ${result.capture.capture_id.slice(0, 8)}；下一步执行隔离测试。`);
    await refresh(signal);
  });

  const test = (execute: boolean) => run(async (signal) => {
    const payload = selected ? { capture_id: selected.capture_id, execute } : { workflow: parseObject(workflowJson, "工作流"), execute };
    const result = await createComfyLabTestRun(payload, "", signal);
    setMessage(execute ? `执行测试结果：${result.test_run.status}` : `测试计划：${result.test_run.status}`);
    if (selected) {
      const detail = await getComfyLabCapture(selected.capture_id, "", signal);
      setSelected(detail.capture);
    }
    await refresh(signal);
  });

  const promote = () => run(async (signal) => {
    if (!selected) throw new Error("请先选择已捕获工作流。");
    const result = await promoteComfyLabCapture(selected.capture_id, {
      code: candidateCode.trim(), title: candidateTitle.trim(),
      contract: { capability: capability.trim(), source_kind: "COMFY_LAB_CAPTURE" },
      node_bindings: parseObject(bindingsJson, "语义绑定"),
    }, "", signal);
    setMessage(`${result.workflow_version.code} v${result.workflow_version.version_no} 已提升为正式候选；仍需在工作流版本页完成兼容性验证和发布。`);
  });

  const autoConfigure = () => run(async (signal) => {
    const result = await discoverComfyLab({ apply: true }, "", signal);
    setSearchedRoots(result.discovery.searched_roots);
    setMessage("已检测并保存本机 ComfyUI 启动配置。");
    await refresh(signal);
  });

  const canLaunch = status?.launch_configured === true;
  const tested = selected?.test_evidence?.status === "PASS";

  return <section className="panel comfy-lab-panel" aria-labelledby="comfy-lab-title">
    <div className="panel-heading"><div><p className="eyebrow">设计器沙盒</p><h3 id="comfy-lab-title">ComfyUI 工作流实验室</h3></div><span className="status-pill neutral">捕获 → 执行测试 → 候选</span></div>
    <p className="muted">粘贴从 ComfyUI 导出的 API Format JSON。捕获只写入隔离沙盒；只有同一内容 hash 获得 PASS 执行证据后，才能提升为不可变候选版本。</p>
    <div className="review-meta"><span>状态：{status?.status ?? "读取中…"}</span><span>端点：{status?.endpoint ?? "未配置"}</span><span>捕获：{captures.length}</span><span>正式目录写入：仅显式提升时</span></div>
    <div className="button-row">
      {!canLaunch && <button className="primary-action" type="button" onClick={autoConfigure} disabled={busy}>{busy ? "检测中…" : "自动检测并配置"}</button>}
      <button className="secondary" type="button" onClick={() => run(async (signal) => { const result = await startComfyLab("", signal); setStatus(result.status); })} disabled={busy || !canLaunch || status?.status === "RUNNING"}>启动实验室</button>
      <button className="secondary" type="button" onClick={() => run(async (signal) => { const result = await stopComfyLab("", signal); setStatus(result.status); })} disabled={busy || status?.status !== "RUNNING"}>停止</button>
      <button className="secondary" type="button" onClick={() => run(async (signal) => { const result = await restartComfyLab("", signal); setStatus(result.status); })} disabled={busy || !canLaunch || status?.status !== "RUNNING"}>重启</button>
    </div>

    <div className="workflow-create-form workflow-capture-form">
      <label>捕获标题<input value={captureTitle} onChange={(event) => setCaptureTitle(event.target.value)} /></label>
      <label>已有捕获<select value={selected?.capture_id ?? ""} onChange={(event) => void selectCapture(event.target.value)}><option value="">新建捕获</option>{captures.map((item) => <option key={item.capture_id} value={item.capture_id}>{item.title ?? item.capture_id.slice(0, 8)} · {item.test_evidence?.status ?? "未执行"}</option>)}</select></label>
      <label className="workflow-create-form__wide">ComfyUI API Format JSON<textarea rows={14} value={workflowJson} onChange={(event) => setWorkflowJson(event.target.value)} spellCheck={false} /></label>
      <div className="workflow-create-form__wide button-row">
        <button className="secondary" type="button" onClick={capture} disabled={busy || !captureTitle.trim()}>捕获当前 JSON</button>
        <button className="secondary" type="button" onClick={() => test(false)} disabled={busy}>检查测试计划</button>
        <button className="primary-action" type="button" onClick={() => test(true)} disabled={busy || !selected || status?.status !== "RUNNING"}>执行隔离测试</button>
      </div>
    </div>

    {selected && <details className="automation-expert-tools" open={tested}>
      <summary><span>提升为正式候选</span><small>{tested ? "执行证据已通过" : "需先完成 PASS 执行测试"}</small></summary>
      <div className="workflow-create-form">
        <label>候选技术标识<input value={candidateCode} onChange={(event) => setCandidateCode(event.target.value)} /></label>
        <label>候选标题<input value={candidateTitle} onChange={(event) => setCandidateTitle(event.target.value)} /></label>
        <label>能力标识<input value={capability} onChange={(event) => setCapability(event.target.value)} /></label>
        <label className="workflow-create-form__wide">语义绑定 JSON<textarea rows={5} value={bindingsJson} onChange={(event) => setBindingsJson(event.target.value)} spellCheck={false} /><small>示例：{`{ "PROMPT": { "node_id": "2", "input": "text" } }`}</small></label>
        <button className="primary-action" type="button" onClick={promote} disabled={busy || !tested || !candidateCode.trim() || !candidateTitle.trim()}>提升为候选版本</button>
      </div>
    </details>}

    {!canLaunch && <p className="review-guidance">尚未配置本机 ComfyUI。自动检测只检查有限常见位置，不扫描整盘或访问公网。</p>}
    {searchedRoots.length > 0 && <details><summary>查看已检查位置</summary><ul>{searchedRoots.map((root) => <li key={root}><code>{root}</code></li>)}</ul></details>}
    {message && <p className="review-success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">ComfyUI 实验室：{error}</p>}
  </section>;
}

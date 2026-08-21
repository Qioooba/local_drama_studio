import { useEffect, useState } from "react";
import {
  cancelAutomationWorkflowRun,
  createAutomationWorkflow,
  createAutomationWorkflowFromTemplate,
  getAutomationWorkflowRun,
  listAutomationWorkflowTemplates,
  planAutomationWorkflow,
  resumeAutomationWorkflowRun,
  startAutomationWorkflowRun,
  stepAutomationWorkflowRun,
} from "../../generated/api";

type AutomationTemplate = { code: string; title: string; description: string };

/** A small operator surface for the same bounded workflow commands exposed by the API. */
export function AutomationWorkflowPanel({ projectId }: { projectId: string }) {
  const [code, setCode] = useState("bounded-local-loop");
  const [title, setTitle] = useState("有限批次与人工闸门");
  const [run, setRun] = useState<Awaited<ReturnType<typeof getAutomationWorkflowRun>>["run"] | null>(null);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [planHash, setPlanHash] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [templates, setTemplates] = useState<AutomationTemplate[]>([]);
  const [templateCode, setTemplateCode] = useState("WHOLE_DRAMA");
  const [templateTitle, setTemplateTitle] = useState("整剧一键编排");
  const [templateWorkflowId, setTemplateWorkflowId] = useState<string | null>(null);
  useEffect(() => {
    void listAutomationWorkflowTemplates()
      .then((result) => {
        setTemplates(result.items);
        if (result.items[0]) setTemplateCode(result.items[0].code);
      })
      .catch(() => setTemplates([]));
  }, []);
  // A BATCH_AUTOMATED run is driven by the local worker, not by panel clicks:
  // poll the durable run so HITL pauses surface in the UI for the operator.
  const runId = run?.id ?? null;
  useEffect(() => {
    if (!runId) return;
    const timer = window.setInterval(() => {
      void getAutomationWorkflowRun(runId)
        .then((result) => {
          setRun(result.run);
          if (["SUCCEEDED", "FAILED", "STOPPED", "CANCELLED", "LIMIT_REACHED"].includes(result.run.status)) {
            window.clearInterval(timer);
          }
        })
        .catch(() => { /* transient poll failure: keep the last known state */ });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [runId]);
  const execute = async (action: () => Promise<void>) => {
    setBusy(true); setError(null); setMessage(null);
    try { await action(); } catch (caught) { setError(String(caught)); } finally { setBusy(false); }
  };
  const create = () => void execute(async () => {
    const result = await createAutomationWorkflow(projectId, {
      code: code.trim(), title: title.trim(), mode: "ASSISTED",
      nodes: [{ id: "local-task", type: "LOCAL_TASK", requires_human_approval: true }],
      batch_items: [{ key: "current-project", payload: { project_id: projectId } }],
      conditions: [{ field: "machine_check.status", operator: "EQ", value: "FAIL", action: "PAUSE_HITL" }],
      max_iterations: 4, max_tasks: 4, max_disk_bytes: 1024 * 1024 * 1024,
      human_gate: "ON_CONDITION", repeat_batch: true,
    });
    setWorkflowId(result.workflow.id); setMessage("已保存有限 workflow；下一步先读取 plan，不会自动创建正式批准。");
  });
  const createFromTemplate = () => void execute(async () => {
    const result = await createAutomationWorkflowFromTemplate(projectId, {
      template_code: templateCode,
      title: templateTitle.trim(),
    });
    setWorkflowId(result.workflow.id);
    setTemplateWorkflowId(result.workflow.id);
    setMessage("整剧编排 workflow 已创建：按集顺序 关键帧确认→批量TTS→渲染→交付。请读取并冻结 plan 后启动 run；关键帧缺失或机器检查失败时 run 会自动暂停等待人工处理。");
  });
  const plan = () => void execute(async () => {
    if (!workflowId) return;
    const result = await planAutomationWorkflow(workflowId);
    setPlanHash(result.plan.plan_hash); setMessage(`计划已冻结：最多 ${result.plan.max_iterations} 次迭代 / ${result.plan.max_tasks} 个任务 / ${result.plan.max_disk_bytes} bytes。`);
  });
  const start = () => void execute(async () => {
    if (!workflowId || !planHash) return;
    const result = await startAutomationWorkflowRun(workflowId, planHash);
    setRun(result.run); setMessage("run 已启动；人工闸门和停止条件仍由服务端强制执行。");
  });
  const step = () => void execute(async () => {
    if (!run) return;
    const result = await stepAutomationWorkflowRun(run.id, { machine_context: { status: "PASS" }, ai_scores: {} });
    setRun(result.run); setMessage(`已执行一步：${result.run.status}。AI 分数不具备批准权限。`);
  });
  const resume = (decision: "HUMAN_APPROVED" | "HUMAN_REJECTED") => void execute(async () => {
    if (!run) return;
    const result = await resumeAutomationWorkflowRun(run.id, { decision, note: decision === "HUMAN_APPROVED" ? "操作员确认后继续" : "操作员拒绝继续" });
    setRun(result.run);
  });
  const cancel = () => void execute(async () => {
    if (!run) return;
    const result = await cancelAutomationWorkflowRun(run.id); setRun(result.run);
  });
  return <>
    <section className="panel automation-template-panel" aria-labelledby="automation-template-title">
      <div className="panel-heading"><div><p className="eyebrow">G11 · P0-4 整剧编排</p><h3 id="automation-template-title">整剧一键编排</h3></div><span className="status-pill neutral">批量自动化 · 仅本地</span></div>
      <p className="muted">内置流水线模板：按集顺序执行 关键帧确认 → 整集批量 TTS → 渲染成片 → 构建交付包。字幕不在 v1 模板内（自动化字幕无法保证逐字匹配剧本权威）。关键帧缺失或机器检查失败时 run 会自动暂停，等待人工处理后再继续。</p>
      <div className="field-grid">
        <label>模板
          <select value={templateCode} onChange={(event) => setTemplateCode(event.target.value)} aria-label="自动化模板选择">
            {templates.length === 0 && <option value="WHOLE_DRAMA">WHOLE_DRAMA · 整剧一键编排</option>}
            {templates.map((item) => <option key={item.code} value={item.code}>{item.code} · {item.title}</option>)}
          </select>
        </label>
        <label>编排标题<input value={templateTitle} onChange={(event) => setTemplateTitle(event.target.value)} aria-label="编排标题" /></label>
      </div>
      {templates[0] && <p className="muted" role="note">模板说明：{templates[0].description}</p>}
      <div className="button-row">
        <button className="primary-action" type="button" onClick={createFromTemplate} disabled={busy || !templateCode || !templateTitle.trim()}>创建整剧编排 workflow</button>
      </div>
      {templateWorkflowId && <p className="review-success" role="status">已创建整剧编排 workflow：{templateWorkflowId}；请使用下方按钮读取并冻结 plan 后启动 run。</p>}
    </section>
    <section className="panel automation-workflow-panel" aria-labelledby="automation-workflow-title">
      <div className="panel-heading"><div><p className="eyebrow">FR-AUT-001 · 声明式人工闸门</p><h3 id="automation-workflow-title">有限流程与人工暂停</h3></div><span className="status-pill neutral">有界 · 仅本地</span></div>
      <p className="muted">流程只接受声明式节点、结构化机器检查和有限批次。max_iterations、max_tasks、max_disk_bytes 在 SQLite run 中持久化；AI 评分永远不能替代正式人工批准。</p>
      <div className="field-grid"><label>工作流代码<input value={code} onChange={(event) => setCode(event.target.value)} /></label><label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label></div>
      <div className="button-row"><button className="secondary" type="button" onClick={create} disabled={busy || !code.trim() || !title.trim()}>创建声明式 workflow</button><button className="secondary" type="button" onClick={plan} disabled={busy || !workflowId}>读取并冻结 plan</button><button className="primary-action" type="button" onClick={start} disabled={busy || !workflowId || !planHash}>启动 run</button></div>
      {run && <div className="review-meta"><span>状态：{run.status}</span><span>迭代：{run.iteration_count}</span><span>任务：{run.task_count}</span><span>磁盘：{run.disk_bytes} / {run.limits.max_disk_bytes}</span><span>人工：{run.human_approval_status}</span></div>}
      {run && <div className="button-row"><button className="secondary" type="button" onClick={step} disabled={busy || run.status !== "RUNNING"}>执行一步</button><button className="secondary" type="button" onClick={() => resume("HUMAN_APPROVED")} disabled={busy || run.status !== "PAUSED_HITL"}>人工批准并继续</button><button className="secondary" type="button" onClick={() => resume("HUMAN_REJECTED")} disabled={busy || run.status !== "PAUSED_HITL"}>人工拒绝</button><button className="secondary" type="button" onClick={cancel} disabled={busy || !["RUNNING", "PAUSED_HITL"].includes(run.status)}>取消 run</button></div>}
      {message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">流程操作失败：{error}</p>}
    </section>
  </>;
}

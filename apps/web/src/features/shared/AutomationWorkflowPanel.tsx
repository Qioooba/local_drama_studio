import { useEffect, useMemo, useState } from "react";
import { Dialog } from "../../components/ui";
import {
  cancelAutomationWorkflowRun,
  createAutomationWorkflow,
  createAutomationWorkflowFromTemplate,
  getAutomationWorkflowRun,
  listAutomationWorkflowRuns,
  listAutomationWorkflowTemplates,
  listAutomationWorkflows,
  planAutomationWorkflow,
  resumeAutomationWorkflowRun,
  startAutomationWorkflowRun,
  stepAutomationWorkflowRun,
} from "../../generated/api";
import type { AutomationWorkflow, AutomationWorkflowRun } from "../../generated/api";
import { generateMachineCode } from "./autoCode";
import { WORKFLOW_MODE_LABELS, optionLabel } from "./optionLabels";

type AutomationTemplate = { code: string; title: string; description: string };
type WorkflowPlan = Awaited<ReturnType<typeof planAutomationWorkflow>>["plan"];

const TERMINAL_STATUSES = ["SUCCEEDED", "FAILED", "STOPPED", "CANCELLED", "LIMIT_REACHED"];
const ACTIVE_STATUSES = ["RUNNING", "PAUSED_HITL"];

const STATUS_LABELS: Record<string, string> = {
  RUNNING: "正在后台编排",
  PAUSED_HITL: "等待人工确认",
  SUCCEEDED: "已完成",
  STOPPED: "已停止",
  FAILED: "执行失败",
  CANCELLED: "已取消",
  LIMIT_REACHED: "已达到安全上限",
};

function formatBytes(value: number) {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KB`;
  return `${value} B`;
}

/** Creator-first whole-drama automation with bounded operator controls disclosed on demand. */
export function AutomationWorkflowPanel({ projectId }: { projectId: string }) {
  const [templates, setTemplates] = useState<AutomationTemplate[]>([]);
  const [templateCode, setTemplateCode] = useState("WHOLE_DRAMA");
  const [templateTitle, setTemplateTitle] = useState("整剧一键编排");
  const [workflows, setWorkflows] = useState<AutomationWorkflow[]>([]);
  const [knownRuns, setKnownRuns] = useState<AutomationWorkflowRun[]>([]);
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [run, setRun] = useState<AutomationWorkflowRun | null>(null);
  const [preparedPlan, setPreparedPlan] = useState<WorkflowPlan | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [customTitle, setCustomTitle] = useState("有限批次与人工闸门");
  const [expertOpen, setExpertOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedTemplate = templates.find((item) => item.code === templateCode);
  const templateWorkflow = workflows.find((item) => item.code === templateCode);
  const templateRun = run?.workflow_id === templateWorkflow?.id
    ? run
    : knownRuns.find((item) => item.workflow_id === templateWorkflow?.id) ?? null;
  const hasActiveTemplateRun = Boolean(templateRun && ACTIVE_STATUSES.includes(templateRun.status));
  const customCode = useMemo(
    () => generateMachineCode("workflow", customTitle).toLowerCase().replace(/_/g, "-"),
    [customTitle],
  );

  const execute = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  };

  const selectExistingWorkflow = (id: string, items = workflows, runs = knownRuns, announce = true) => {
    const selected = items.find((item) => item.id === id);
    setWorkflowId(id || null);
    setPreparedPlan(null);
    setRun(runs.find((item) => item.workflow_id === id) ?? null);
    if (selected && announce) setMessage(`已选择“${selected.title}”。`);
  };

  const refreshExistingWorkflows = async () => {
    const [workflowResult, runResult] = await Promise.all([
      listAutomationWorkflows(projectId),
      listAutomationWorkflowRuns(projectId, { limit: 100 }),
    ]);
    setWorkflows(workflowResult.items);
    setKnownRuns(runResult.items);
    const selectedId = workflowResult.items.some((item) => item.id === workflowId)
      ? workflowId
      : workflowResult.items.find((item) => item.code === "WHOLE_DRAMA")?.id ?? workflowResult.items[0]?.id ?? null;
    if (selectedId) selectExistingWorkflow(selectedId, workflowResult.items, runResult.items, false);
  };

  useEffect(() => {
    void listAutomationWorkflowTemplates()
      .then((result) => {
        setTemplates(result.items);
        if (result.items[0]) setTemplateCode(result.items[0].code);
      })
      .catch(() => setTemplates([]));
    void refreshExistingWorkflows().catch(() => {
      setWorkflows([]);
      setKnownRuns([]);
    });
  }, [projectId]);

  // Runs are worker-driven. Poll only while the run can still change so a human gate appears without refreshes.
  const runId = run?.id ?? templateRun?.id ?? null;
  useEffect(() => {
    if (!runId) return;
    const timer = window.setInterval(() => {
      void getAutomationWorkflowRun(runId)
        .then((result) => {
          setRun(result.run);
          setKnownRuns((items) => [result.run, ...items.filter((item) => item.id !== result.run.id)]);
          if (TERMINAL_STATUSES.includes(result.run.status)) window.clearInterval(timer);
        })
        .catch(() => { /* Keep the last durable state during a transient local failure. */ });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [runId]);

  const prepareTemplate = () => void execute(async () => {
    if (templateWorkflow && templateRun && ACTIVE_STATUSES.includes(templateRun.status)) {
      setWorkflowId(templateWorkflow.id);
      setRun(templateRun);
      setMessage(templateRun.status === "PAUSED_HITL" ? "整剧编排正在等待你的确认。" : "整剧编排已在后台运行，无需重复启动。");
      return;
    }

    let targetWorkflow = templateWorkflow;
    if (!targetWorkflow) {
      const created = await createAutomationWorkflowFromTemplate(projectId, {
        template_code: templateCode,
        title: templateTitle.trim(),
      });
      targetWorkflow = created.workflow;
      setWorkflows((items) => [created.workflow, ...items.filter((item) => item.id !== created.workflow.id)]);
    }

    const planned = await planAutomationWorkflow(targetWorkflow.id);
    setWorkflowId(targetWorkflow.id);
    setPreparedPlan(planned.plan);
    setConfirmOpen(true);
  });

  const confirmStart = () => void execute(async () => {
    if (!workflowId || !preparedPlan) return;
    const result = await startAutomationWorkflowRun(workflowId, preparedPlan.plan_hash);
    setRun(result.run);
    setKnownRuns((items) => [result.run, ...items.filter((item) => item.id !== result.run.id)]);
    setConfirmOpen(false);
    setMessage("整剧编排已在后台启动；到达关键帧或机器检查闸门时会自动暂停并通知你处理。");
  });

  const resume = (decision: "HUMAN_APPROVED" | "HUMAN_REJECTED") => void execute(async () => {
    if (!run) return;
    const result = await resumeAutomationWorkflowRun(run.id, {
      decision,
      note: decision === "HUMAN_APPROVED" ? "创作者确认后继续" : "创作者拒绝继续",
    });
    setRun(result.run);
    setMessage(decision === "HUMAN_APPROVED" ? "已确认，后台将继续编排。" : "已拒绝，本次编排不会越过人工闸门。");
  });

  const cancel = () => void execute(async () => {
    if (!run) return;
    const result = await cancelAutomationWorkflowRun(run.id);
    setRun(result.run);
    setMessage("整剧编排已停止。已完成的阶段结果会保留。");
  });

  const createCustomWorkflow = () => void execute(async () => {
    const result = await createAutomationWorkflow(projectId, {
      code: customCode,
      title: customTitle.trim(),
      mode: "ASSISTED",
      nodes: [{ id: "local-task", type: "LOCAL_TASK", requires_human_approval: true }],
      batch_items: [{ key: "current-project", payload: { project_id: projectId } }],
      conditions: [{ field: "machine_check.status", operator: "EQ", value: "FAIL", action: "PAUSE_HITL" }],
      max_iterations: 4,
      max_tasks: 4,
      max_disk_bytes: 1024 * 1024 * 1024,
      human_gate: "ON_CONDITION",
      repeat_batch: true,
    });
    setWorkflows((items) => [result.workflow, ...items.filter((item) => item.id !== result.workflow.id)]);
    setWorkflowId(result.workflow.id);
    setPreparedPlan(null);
    setRun(null);
    setMessage("有限测试流程已保存；机器代码由标题自动生成。");
  });

  const prepareSelectedWorkflow = () => void execute(async () => {
    if (!workflowId) return;
    const result = await planAutomationWorkflow(workflowId);
    setPreparedPlan(result.plan);
    setMessage("所选流程的有界执行计划已重新生成。启动前仍需明确确认。");
  });

  const startSelectedWorkflow = () => {
    if (workflowId && preparedPlan) setConfirmOpen(true);
  };

  const stepSelectedWorkflow = () => void execute(async () => {
    if (!run) return;
    const result = await stepAutomationWorkflowRun(run.id, { machine_context: { status: "PASS" }, ai_scores: {} });
    setRun(result.run);
    setMessage("已执行一个受限测试步骤；AI 评分不具备批准权限。");
  });

  const visibleRun = run ?? templateRun;

  return <section className="panel automation-workflow-panel" aria-labelledby="automation-workflow-title">
    <div className="panel-heading">
      <div><p className="eyebrow">整剧编排</p><h3 id="automation-workflow-title">让后台连续完成重复制作</h3></div>
      <span className="status-pill neutral">有界执行 · 仅本地</span>
    </div>
    <p className="muted">选择编排方式后，系统会自动创建流程、检查任务上限并冻结执行计划。你只需在启动前确认一次；关键帧缺失或机器检查失败时，流程会自动暂停等待你决定。</p>

    <div className="automation-template-choice">
      <label>编排方式
        <select value={templateCode} onChange={(event) => setTemplateCode(event.target.value)} aria-label="自动化模板选择">
          {templates.length === 0 && <option value="WHOLE_DRAMA">整剧一键编排</option>}
          {templates.map((item) => <option key={item.code} value={item.code}>{item.title}</option>)}
        </select>
      </label>
      <label>任务名称（可选）
        <input value={templateTitle} onChange={(event) => setTemplateTitle(event.target.value)} aria-label="编排标题" />
      </label>
    </div>
    {selectedTemplate && <p className="automation-template-note" role="note">{selectedTemplate.description.replaceAll("TTS", "台词配音")}</p>}

    <div className="button-row">
      <button className="primary-action" type="button" onClick={prepareTemplate} disabled={busy || !templateCode || !templateTitle.trim()}>
        {hasActiveTemplateRun ? "查看当前整剧编排" : "准备并启动整剧编排"}
      </button>
    </div>

    {visibleRun && <div className={`automation-run-state${visibleRun.status === "PAUSED_HITL" ? " needs-action" : ""}`} aria-live="polite">
      <div>
        <small>当前状态</small>
        <strong>{STATUS_LABELS[visibleRun.status] ?? visibleRun.status}</strong>
        <span>已处理 {visibleRun.task_count} 个任务 · {visibleRun.iteration_count} 个批次</span>
      </div>
      {visibleRun.status === "PAUSED_HITL" && <div className="button-row">
        <button className="primary-action" type="button" onClick={() => resume("HUMAN_APPROVED")} disabled={busy}>确认结果并继续</button>
        <button className="secondary" type="button" onClick={() => resume("HUMAN_REJECTED")} disabled={busy}>拒绝并保持暂停</button>
      </div>}
      {visibleRun.status === "RUNNING" && <button className="secondary" type="button" onClick={cancel} disabled={busy}>停止整剧编排</button>}
    </div>}

    {message && <p className="review-success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">整剧编排失败：{error}</p>}

    <details className="automation-expert-tools" open={expertOpen} onToggle={(event) => setExpertOpen(event.currentTarget.open)}>
      <summary><span>专家：有限流程与执行诊断</span><small>仅用于自定义本机流程、手动计划和故障定位</small></summary>
      {expertOpen && <div className="automation-expert-body">
        <p className="muted">流程仍受迭代、任务、磁盘和人工闸门上限约束。机器代码由标题自动生成，AI 评分永远不能替代人工批准。</p>
        <div className="field-grid">
          <label>已有流程
            <select aria-label="已有流程" value={workflowId ?? ""} onChange={(event) => selectExistingWorkflow(event.target.value)}>
              <option value="">选择已有流程</option>
              {workflows.map((item) => <option key={item.id} value={item.id}>{item.title} · {optionLabel(WORKFLOW_MODE_LABELS, item.mode)}</option>)}
            </select>
          </label>
          <div className="button-row"><button className="secondary" type="button" onClick={() => void execute(refreshExistingWorkflows)} disabled={busy}>刷新流程状态</button></div>
        </div>
        <div className="field-grid">
          <label>测试流程标题<input value={customTitle} onChange={(event) => setCustomTitle(event.target.value)} /></label>
          <div className="automation-derived-code"><small>自动生成的机器代码</small><code>{customCode}</code></div>
        </div>
        <div className="button-row">
          <button className="secondary" type="button" onClick={createCustomWorkflow} disabled={busy || !customTitle.trim()}>创建有限测试流程</button>
          <button className="secondary" type="button" onClick={prepareSelectedWorkflow} disabled={busy || !workflowId}>重新生成执行计划</button>
          <button className="secondary" type="button" onClick={startSelectedWorkflow} disabled={busy || !workflowId || !preparedPlan}>确认并启动所选流程</button>
          <button className="secondary" type="button" onClick={stepSelectedWorkflow} disabled={busy || run?.status !== "RUNNING" || workflows.find((item) => item.id === run?.workflow_id)?.mode === "BATCH_AUTOMATED"}>执行受限测试步骤</button>
        </div>
        {visibleRun && <div className="review-meta">
          <span>状态：{visibleRun.status}</span><span>迭代：{visibleRun.iteration_count} / {visibleRun.limits.max_iterations}</span><span>任务：{visibleRun.task_count} / {visibleRun.limits.max_tasks}</span><span>磁盘：{formatBytes(visibleRun.disk_bytes)} / {formatBytes(visibleRun.limits.max_disk_bytes)}</span><span>人工：{visibleRun.human_approval_status}</span>
        </div>}
      </div>}
    </details>

    <Dialog
      open={confirmOpen}
      title="确认启动整剧编排"
      onClose={() => !busy && setConfirmOpen(false)}
      footer={<><button type="button" className="secondary" onClick={() => setConfirmOpen(false)} disabled={busy}>返回检查</button><button type="button" className="primary-action" onClick={confirmStart} disabled={busy || !preparedPlan}>{busy ? "启动中…" : "确认启动"}</button></>}
    >
      <p>系统已在后台完成流程创建和执行计划检查。启动后会连续占用本机生成资源，直到完成、达到安全上限或进入人工闸门。</p>
      {preparedPlan && <dl className="automation-plan-summary">
        <div><dt>预计任务</dt><dd>{preparedPlan.estimated_tasks}</dd></div>
        <div><dt>预计批次</dt><dd>{preparedPlan.estimated_iterations}</dd></div>
        <div><dt>任务上限</dt><dd>{preparedPlan.max_tasks}</dd></div>
        <div><dt>磁盘上限</dt><dd>{formatBytes(preparedPlan.max_disk_bytes)}</dd></div>
        <div><dt>人工闸门</dt><dd>{preparedPlan.human_gate === "NEVER" ? "无" : "已启用"}</dd></div>
        <div><dt>网络访问</dt><dd>{preparedPlan.network_contacted ? "会访问网络" : "仅本机"}</dd></div>
      </dl>}
    </Dialog>
  </section>;
}

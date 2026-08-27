import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AutomationWorkflowPanel } from "./AutomationWorkflowPanel";
import {
  cancelAutomationWorkflowRun,
  createAutomationWorkflow,
  createAutomationWorkflowFromTemplate,
  listAutomationWorkflowRuns,
  listAutomationWorkflowTemplates,
  listAutomationWorkflows,
  planAutomationWorkflow,
  resumeAutomationWorkflowRun,
  startAutomationWorkflowRun,
  stepAutomationWorkflowRun,
} from "../../generated/api";
import type { AutomationWorkflowRun } from "../../generated/api";

vi.mock("../../generated/api", () => ({
  cancelAutomationWorkflowRun: vi.fn(),
  createAutomationWorkflow: vi.fn(),
  createAutomationWorkflowFromTemplate: vi.fn(),
  getAutomationWorkflowRun: vi.fn(),
  listAutomationWorkflowRuns: vi.fn(),
  listAutomationWorkflowTemplates: vi.fn(),
  listAutomationWorkflows: vi.fn(),
  planAutomationWorkflow: vi.fn(),
  resumeAutomationWorkflowRun: vi.fn(),
  startAutomationWorkflowRun: vi.fn(),
  stepAutomationWorkflowRun: vi.fn(),
}));

const planHash = "a".repeat(64);

type RunStatus = "RUNNING" | "PAUSED_HITL" | "SUCCEEDED" | "STOPPED" | "FAILED" | "CANCELLED" | "LIMIT_REACHED";

function runView(status: RunStatus): AutomationWorkflowRun {
  return {
    id: "run-1",
    workflow_id: "wf-1",
    project_id: "project-1",
    status,
    plan_hash: planHash,
    iteration_count: 1,
    task_count: 1,
    disk_bytes: 0,
    limits: { max_iterations: 5, max_tasks: 5, max_disk_bytes: 1024 },
    pending_gate: {},
    machine_context: {},
    ai_scores: {},
    human_approval_status: status === "PAUSED_HITL" ? "PENDING" : "NOT_REQUIRED",
    tasks: [],
    events: [],
    local_only: true,
    network_contacted: false,
    ai_scores_can_approve: false,
  };
}

describe("AutomationWorkflowPanel", () => {
  beforeEach(() => {
    vi.mocked(listAutomationWorkflowTemplates).mockReset();
    vi.mocked(createAutomationWorkflowFromTemplate).mockReset();
    vi.mocked(createAutomationWorkflow).mockReset();
    vi.mocked(listAutomationWorkflows).mockReset().mockResolvedValue({ items: [], local_only: true, network_contacted: false });
    vi.mocked(listAutomationWorkflowRuns).mockReset().mockResolvedValue({ items: [], limit: 100, local_only: true, network_contacted: false });
    vi.mocked(planAutomationWorkflow).mockReset();
    vi.mocked(startAutomationWorkflowRun).mockReset();
    vi.mocked(stepAutomationWorkflowRun).mockReset();
    vi.mocked(resumeAutomationWorkflowRun).mockReset();
    vi.mocked(cancelAutomationWorkflowRun).mockReset();
    vi.mocked(listAutomationWorkflowTemplates).mockResolvedValue({
      items: [{ code: "WHOLE_DRAMA", title: "整剧一键编排", description: "按集顺序执行 关键帧确认→批量TTS→渲染→交付" }],
    });
    vi.mocked(createAutomationWorkflowFromTemplate).mockResolvedValue({
      workflow: {
        id: "wf-1",
        project_id: "project-1",
        code: "WHOLE_DRAMA",
        title: "整剧一键编排",
        mode: "BATCH_AUTOMATED",
        status: "ACTIVE",
        definition: {},
        plan_hash: planHash,
        local_only: true,
        network_contacted: false,
        ai_approval_allowed: false,
      },
    });
    vi.mocked(createAutomationWorkflow).mockResolvedValue({
      workflow: {
        id: "wf-manual",
        project_id: "project-1",
        code: "bounded-local-loop",
        title: "有限批次与人工闸门",
        mode: "ASSISTED",
        status: "ACTIVE",
        definition: {},
        plan_hash: planHash,
        local_only: true,
        network_contacted: false,
        ai_approval_allowed: false,
      },
    });
    vi.mocked(planAutomationWorkflow).mockResolvedValue({
      plan: {
        workflow_id: "wf-1",
        project_id: "project-1",
        plan_hash: planHash,
        mode: "BATCH_AUTOMATED",
        batch_count: 4,
        estimated_iterations: 4,
        estimated_tasks: 4,
        estimated_disk_bytes: 0,
        max_iterations: 5,
        max_tasks: 5,
        max_disk_bytes: 1024,
        human_gate: "ON_CONDITION",
        node_gate: false,
        conditions: [],
        requires_human_confirmation: true,
        ai_scores_can_approve: false,
        network_contacted: false,
        local_only: true,
      },
    });
    vi.mocked(startAutomationWorkflowRun).mockResolvedValue({ run: runView("RUNNING") });
    vi.mocked(stepAutomationWorkflowRun).mockResolvedValue({ run: runView("RUNNING") });
    vi.mocked(resumeAutomationWorkflowRun).mockResolvedValue({ run: runView("RUNNING") });
    vi.mocked(cancelAutomationWorkflowRun).mockResolvedValue({ run: runView("CANCELLED") });
  });

  it("loads built-in templates and shows the WHOLE_DRAMA description", async () => {
    render(<AutomationWorkflowPanel projectId="project-1" />);
    expect(await screen.findByText("整剧一键编排")).toBeTruthy();
    await waitFor(() => expect(listAutomationWorkflowTemplates).toHaveBeenCalledWith());
    expect(await screen.findByText(/按集顺序执行 关键帧确认→批量台词配音→渲染→交付/)).toBeTruthy();
    expect((screen.getByLabelText("自动化模板选择") as HTMLSelectElement).value).toBe("WHOLE_DRAMA");
  });

  it("creates and plans a whole-drama workflow in the background without exposing ids", async () => {
    render(<AutomationWorkflowPanel projectId="project-1" />);
    await screen.findByText(/按集顺序执行 关键帧确认/);
    fireEvent.change(screen.getByLabelText("编排标题"), { target: { value: "整剧一键编排测试" } });
    fireEvent.click(screen.getByRole("button", { name: "准备并启动整剧编排" }));
    await waitFor(() => expect(createAutomationWorkflowFromTemplate).toHaveBeenCalledWith("project-1", {
      template_code: "WHOLE_DRAMA",
      title: "整剧一键编排测试",
    }));
    await waitFor(() => expect(planAutomationWorkflow).toHaveBeenCalledWith("wf-1"));
    expect(await screen.findByRole("dialog", { name: "确认启动整剧编排" })).toBeTruthy();
    expect(screen.queryByText(/wf-1/)).toBeNull();
    expect(startAutomationWorkflowRun).not.toHaveBeenCalled();
  });

  it("starts only after the resource confirmation and leaves worker execution in the background", async () => {
    render(<AutomationWorkflowPanel projectId="project-1" />);
    await screen.findByText(/按集顺序执行 关键帧确认/);
    fireEvent.click(screen.getByRole("button", { name: "准备并启动整剧编排" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认启动" }));
    await waitFor(() => expect(startAutomationWorkflowRun).toHaveBeenCalledWith("wf-1", planHash));
    expect(await screen.findByText("正在后台编排")).toBeTruthy();
    expect(screen.getByRole("button", { name: "停止整剧编排" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "执行一步" })).toBeNull();
    expect(stepAutomationWorkflowRun).not.toHaveBeenCalled();
  });

  it("keeps bounded diagnostic workflows behind expert disclosure and generates their code", async () => {
    render(<AutomationWorkflowPanel projectId="project-1" />);
    await screen.findByText(/按集顺序执行 关键帧确认/);
    fireEvent.click(screen.getByText("专家：有限流程与执行诊断"));
    expect(await screen.findByText("自动生成的机器代码")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "创建有限测试流程" }));
    await waitFor(() => expect(createAutomationWorkflow).toHaveBeenCalledWith("project-1", expect.objectContaining({
      code: expect.stringMatching(/^workflow-/),
      mode: "ASSISTED",
      human_gate: "ON_CONDITION",
      repeat_batch: true,
    })));
    expect(await screen.findByText(/有限测试流程已保存/)).toBeTruthy();
  });

  it("restores an active whole-drama run after remount instead of creating or planning it again", async () => {
    vi.mocked(listAutomationWorkflows).mockResolvedValueOnce({
      items: [{
        id: "wf-existing", project_id: "project-1", code: "WHOLE_DRAMA", title: "已有整剧编排",
        mode: "BATCH_AUTOMATED", status: "ACTIVE", definition: {}, plan_hash: planHash,
        local_only: true, network_contacted: false, ai_approval_allowed: false,
      }],
      local_only: true,
      network_contacted: false,
    });
    vi.mocked(listAutomationWorkflowRuns).mockResolvedValueOnce({ items: [{ ...runView("RUNNING"), workflow_id: "wf-existing" }], limit: 100, local_only: true, network_contacted: false });
    render(<AutomationWorkflowPanel projectId="project-1" />);

    const continueButton = await screen.findByRole("button", { name: "查看当前整剧编排" });
    fireEvent.click(continueButton);
    expect(createAutomationWorkflowFromTemplate).not.toHaveBeenCalled();
    expect(planAutomationWorkflow).not.toHaveBeenCalled();
    expect(await screen.findByText(/已在后台运行，无需重复启动/)).toBeTruthy();
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AutomationWorkflowPanel } from "./AutomationWorkflowPanel";
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
import type { AutomationWorkflowRun } from "../../generated/api";

vi.mock("../../generated/api", () => ({
  cancelAutomationWorkflowRun: vi.fn(),
  createAutomationWorkflow: vi.fn(),
  createAutomationWorkflowFromTemplate: vi.fn(),
  getAutomationWorkflowRun: vi.fn(),
  listAutomationWorkflowTemplates: vi.fn(),
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
    expect(await screen.findByText(/按集顺序执行 关键帧确认→批量TTS→渲染→交付/)).toBeTruthy();
    expect((screen.getByLabelText("自动化模板选择") as HTMLSelectElement).value).toBe("WHOLE_DRAMA");
  });

  it("creates a whole-drama workflow from the template and shows its id", async () => {
    render(<AutomationWorkflowPanel projectId="project-1" />);
    await screen.findByText(/按集顺序执行 关键帧确认/);
    fireEvent.change(screen.getByLabelText("编排标题"), { target: { value: "整剧一键编排测试" } });
    fireEvent.click(screen.getByRole("button", { name: "创建整剧编排 workflow" }));
    await waitFor(() => expect(createAutomationWorkflowFromTemplate).toHaveBeenCalledWith("project-1", {
      template_code: "WHOLE_DRAMA",
      title: "整剧一键编排测试",
    }));
    expect(await screen.findByText(/已创建整剧编排 workflow：wf-1/)).toBeTruthy();
  });

  it("plans, starts and steps the template-created run through the shared button group", async () => {
    render(<AutomationWorkflowPanel projectId="project-1" />);
    await screen.findByText(/按集顺序执行 关键帧确认/);
    fireEvent.click(screen.getByRole("button", { name: "创建整剧编排 workflow" }));
    await screen.findByText(/已创建整剧编排 workflow：wf-1/);
    fireEvent.click(screen.getByRole("button", { name: "读取并冻结 plan" }));
    await waitFor(() => expect(planAutomationWorkflow).toHaveBeenCalledWith("wf-1"));
    fireEvent.click(screen.getByRole("button", { name: "启动 run" }));
    await waitFor(() => expect(startAutomationWorkflowRun).toHaveBeenCalledWith("wf-1", planHash));
    expect(await screen.findByText("状态：RUNNING")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "执行一步" }));
    await waitFor(() => expect(stepAutomationWorkflowRun).toHaveBeenCalledWith("run-1", { machine_context: { status: "PASS" }, ai_scores: {} }));
  });

  it("keeps the manual declarative workflow creation intact", async () => {
    render(<AutomationWorkflowPanel projectId="project-1" />);
    await screen.findByText(/按集顺序执行 关键帧确认/);
    fireEvent.click(screen.getByRole("button", { name: "创建声明式 workflow" }));
    await waitFor(() => expect(createAutomationWorkflow).toHaveBeenCalledWith("project-1", expect.objectContaining({
      code: "bounded-local-loop",
      mode: "ASSISTED",
      human_gate: "ON_CONDITION",
      repeat_batch: true,
    })));
    expect(await screen.findByText(/已保存有限 workflow/)).toBeTruthy();
  });
});

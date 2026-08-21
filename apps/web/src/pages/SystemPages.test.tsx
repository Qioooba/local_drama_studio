import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DiagnosticsPage } from "./DiagnosticsPage";
import { JobsPage } from "./JobsPage";
import { ModelsPage } from "./ModelsPage";
import { AppShell } from "../layouts/AppShell";

const api = vi.hoisted(() => ({
  getCapacitySnapshot: vi.fn(), getDiagnostics: vi.fn(), listAuditEvents: vi.fn(), listJobsPage: vi.fn(), listProfiles: vi.fn(), listProjects: vi.fn(), listWorkflowVersions: vi.fn(), runDiagnostics: vi.fn(),
}));

vi.mock("../generated/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("../generated/api")>()), ...api }));
vi.mock("../features/jobs/JobDetailsPanel", () => ({ JobDetailsPanel: () => null }));
vi.mock("../features/status-v2/LocalRuntimeIndicator", () => ({ LocalRuntimeIndicator: () => <span>本机运行时</span> }));
vi.mock("../features/preferences-v2/GenerationPreferencePanel", () => ({
  GenerationPreferencePanel: () => <section aria-label="生成偏好任务">生成偏好单任务面板</section>,
}));

function mount(node: React.ReactNode, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{node}</MemoryRouter></QueryClientProvider>);
}

describe("V2 system workspaces", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listProjects.mockResolvedValue({ items: [{ id: "project-1", title: "北方小院" }] });
    api.listProfiles.mockResolvedValue({ items: [] });
    api.listWorkflowVersions.mockResolvedValue({ items: [], runtime_contacted: false });
    api.listJobsPage.mockResolvedValue({ items: [{ id: "job-1", type: "GENERATION_VARIANT", project_id: "project-1", state: "FAILED", channel: "GPU_H3", priority: 50, max_attempts: 3, revision: 2 }], next_cursor: null, cursor: 0, limit: 100 });
    api.getCapacitySnapshot.mockResolvedValue({ snapshot: { scope: { project_id: null }, observed_at: "2026-08-20", jobs_by_state: {}, jobs_by_channel: {}, queued_count: 0, oldest_queued_age_seconds: null, active_attempt_count: 0, active_worker_count: 0, gpu_active_count: 0, gpu_concurrency_limit: 1, completed_last_24h: 0, observation_status: "OBSERVED_NOT_BENCHMARKED", webhook_status: "LOOPBACK_EXPLICIT_BOUNDED", would_create_jobs: false, runtime_contacted: false, network_contacted: false, mutated: false } });
    api.getDiagnostics.mockResolvedValue({ run: { status: "DEGRADED", checks: [{ code: "COMFY", status: "WARN", observed: {} }] } });
    api.runDiagnostics.mockResolvedValue({ run: { status: "HEALTHY", checks: [{ code: "COMFY", status: "PASS", observed: {} }] } });
    api.listAuditEvents.mockResolvedValue({ items: [], next_cursor: null, cursor: 0, limit: 50, filters: {}, local_only: true, network_contacted: false, mutated: false });
  });

  it("renders real jobs and capacity and preserves project scope in the URL", async () => {
    mount(<JobsPage />);
    expect(await screen.findByText("GENERATION_VARIANT")).toBeInTheDocument();
    expect(screen.getByText("本机队列产能快照")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "任务项目范围" }), { target: { value: "project-1" } });
    await waitFor(() => expect(api.listJobsPage).toHaveBeenLastCalledWith("project-1", 0, 100));
  });

  it("uses the project route parameter as the jobs owner scope", async () => {
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId/jobs", element: <JobsPage /> }],
      { initialEntries: ["/projects/project-1/jobs"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    expect(await screen.findByText("GENERATION_VARIANT")).toBeInTheDocument();
    await waitFor(() => expect(api.listJobsPage).toHaveBeenLastCalledWith("project-1", 0, 100));
    expect(screen.getByRole("combobox", { name: "任务项目范围" })).toHaveValue("project-1");
    fireEvent.click(screen.getByRole("button", { name: "详情 · 产物" }));
    expect(await screen.findByRole("dialog", { name: "任务详情与产物" })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe("?job=job-1"));
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));
    await waitFor(() => expect(router.state.location.search).toBe(""));
  });

  it("runs explicit diagnostics and keeps audit read-only", async () => {
    mount(<DiagnosticsPage />);
    expect(await screen.findByText("DEGRADED")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "运行诊断" }));
    expect(await screen.findByText("HEALTHY")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /审计历史/i }));
    await waitFor(() => expect(api.listAuditEvents).toHaveBeenCalled());
  });

  it("loads real profile and workflow facts instead of a legacy placeholder", async () => {
    mount(<ModelsPage />);
    expect(await screen.findByText(/Profile 定义能力/)).toBeInTheDocument();
    expect(api.listProfiles).toHaveBeenCalledOnce();
    expect(api.listWorkflowVersions).not.toHaveBeenCalled();
  });

  it("restores the Models task from view, preserves scope, and unmounts inactive tasks", async () => {
    const router = createMemoryRouter(
      [{ path: "/models", element: <ModelsPage /> }],
      { initialEntries: ["/models?project=project-1&view=workflows"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "工作流版本、验证与发布证据" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "本地能力契约与不可变版本" })).not.toBeInTheDocument();
    expect(api.listWorkflowVersions).toHaveBeenCalledOnce();
    expect(api.listProfiles).not.toHaveBeenCalled();

    const workflowTab = screen.getByRole("tab", { name: "Workflow 版本" });
    workflowTab.focus();
    fireEvent.keyDown(workflowTab, { key: "ArrowRight" });
    expect(await screen.findByRole("region", { name: "生成偏好任务" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "工作流版本、验证与发布证据" })).not.toBeInTheDocument();
    expect(router.state.location.search).toContain("project=project-1");
    expect(router.state.location.search).toContain("view=preferences");

    const preferencesTab = screen.getByRole("tab", { name: "生成偏好" });
    fireEvent.keyDown(preferencesTab, { key: "Home" });
    expect(await screen.findByRole("heading", { name: "本地能力契约与不可变版本" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "生成偏好任务" })).not.toBeInTheDocument();
    expect(router.state.location.search).toContain("project=project-1");
    expect(router.state.location.search).toContain("view=profile-contracts");
    await waitFor(() => expect(api.listProfiles).toHaveBeenCalledOnce());
  });

  it("keeps system pages inside the common AppShell", async () => {
    const router = createMemoryRouter([{ element: <AppShell />, children: [{ path: "/jobs", element: <JobsPage /> }] }], { initialEntries: ["/jobs?project=project-1"] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    expect(await screen.findByRole("link", { name: "返回项目列表" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "项目导航" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "任务与机器", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("系统区", { selector: "summary" }).closest("details")).toHaveAttribute("open");
    await waitFor(() => expect(screen.getByRole("combobox", { name: "当前项目" })).toHaveValue("project-1"));
  });

  it("keeps the system navigation collapsed outside system workspaces", async () => {
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId", element: <AppShell />, children: [{ index: true, element: <div>项目内容</div> }] }],
      { initialEntries: ["/projects/project-1"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    expect(await screen.findByText("项目内容")).toBeInTheDocument();
    expect(screen.getByText("系统区", { selector: "summary" }).closest("details")).not.toHaveAttribute("open");
  });
});

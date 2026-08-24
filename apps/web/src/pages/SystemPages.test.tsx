import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DiagnosticsPage } from "./DiagnosticsPage";
import { JobsPage } from "./JobsPage";
import { ModelsPage } from "./ModelsPage";
import { AppShell } from "../layouts/AppShell";
import { queryKeys } from "../query/queryKeys";

const api = vi.hoisted(() => ({
  getCapacitySnapshot: vi.fn(), getDiagnostics: vi.fn(), getEpisodeTimelineStatus: vi.fn(), getProjectCreatorSetup: vi.fn(), getProjectEpisodeCatalog: vi.fn(), getStoryboardWorkspace: vi.fn(), listAuditEvents: vi.fn(), listEpisodes: vi.fn(), listJobsPage: vi.fn(), listProfiles: vi.fn(), listProjects: vi.fn(), listSeasons: vi.fn(), listWorkflowVersions: vi.fn(), runDiagnostics: vi.fn(),
}));

vi.mock("../generated/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("../generated/api")>()), ...api }));
vi.mock("../features/jobs/JobDetailsPanel", () => ({ JobDetailsPanel: () => null }));
vi.mock("../features/status-v2/LocalRuntimeIndicator", () => ({ LocalRuntimeIndicator: () => <span>本机运行时</span> }));
vi.mock("../features/episode-cockpit/api", () => ({ getEpisodeCockpit: () => Promise.resolve({
  episode: { id: "episode-1", code: "E01", title: "第一集", project_id: "project-1" },
  shots: { total: 2, directed: 1, with_candidates: 1, remaining_generation: 0, selected: 1, approved: 0, failed: 1, stale: 0 },
  jobs: { failed: 1 }, bridges: { total: 1, ready: 1, stale: 0 }, audio: { bindings: 2, verified: 1 },
  qc: { candidate_versions: 1, checked: 1, passed: 0, failed: 1 }, blockers: [{ code: "UNDIRECTED_SHOTS", count: 1, label: "仍有镜头未完成导演意图" }, { code: "FAILED_SHOTS", count: 1, label: "失败镜头需要处理" }],
  observed_at: "2026-08-24T00:00:00Z", read_only: true, mutated: false,
}) }));
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
    api.listSeasons.mockResolvedValue({ items: [{ id: "season-1", code: "S01", title: "第一季" }, { id: "season-2", code: "S02", title: "第二季" }] });
    api.listEpisodes.mockImplementation((seasonId: string) => Promise.resolve({ items: seasonId === "season-2"
      ? [{ id: "episode-3", code: "E03", title: "第三集", production_status: "DRAFT" }]
      : [{ id: "episode-1", code: "E01", title: "第一集", production_status: "DRAFT" }, { id: "episode-2", code: "E02", title: "第二集", production_status: "DRAFT" }] }));
    api.getProjectEpisodeCatalog.mockResolvedValue({ catalog: { project_id: "project-1", seasons: [
      { id: "season-1", code: "S01", title: "第一季", episodes: [{ id: "episode-1", code: "E01", title: "第一集", production_status: "DRAFT" }, { id: "episode-2", code: "E02", title: "第二集", production_status: "DRAFT" }] },
      { id: "season-2", code: "S02", title: "第二季", episodes: [{ id: "episode-3", code: "E03", title: "第三集", production_status: "DRAFT" }] },
    ], read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } });
    api.getProjectCreatorSetup.mockResolvedValue({ setup: { project_id: "project-1", milestones: {
      episode_count: { ready: true, count: 3 }, production_plan_count: { ready: true, count: 1 }, published_profile_binding_count: { ready: false, count: 0 },
      reviewable_story_draft_count: { ready: true, count: 1 }, active_story_asset_count: { ready: true, count: 4 }, shot_intent_count: { ready: true, count: 2 }, shot_generation_job_count: { ready: true, count: 1 },
    }, operations: { worker_ready: true, active_worker_count: 1 }, completed_count: 6, total_count: 7, observed_at: "2026-08-24T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } });
    api.getStoryboardWorkspace.mockResolvedValue({ storyboard: { episode: { id: "episode-1", title: "第一集" }, items: [
      { id: "shot-1", code: "SH001", order_key: "1", target_duration_ms: 1000, shot_type: "中景", status: "DRAFT", revision: 1, current_revision_id: "rev-1", current_revision_no: 1, is_frozen: 0, fields: {}, display_ordinal: 1, timeline_start_ms: 0, timeline_end_ms: 1000 },
      { id: "shot-2", code: "SH002", order_key: "2", target_duration_ms: 1000, shot_type: "近景", status: "DRAFT", revision: 1, current_revision_id: "rev-2", current_revision_no: 1, is_frozen: 0, fields: {}, display_ordinal: 2, timeline_start_ms: 1000, timeline_end_ms: 2000 },
    ], views: ["TABLE"], identity_invariant: "stable", total_duration_ms: 2000 } });
    api.getEpisodeTimelineStatus.mockResolvedValue({ status: { episode: { id: "episode-1", code: "E01", title: "第一集", project_id: "project-1" }, timeline: { revision_count: 0, latest: null }, subtitles: { revision_count: 0, latest: null }, audio: { binding_count: 0, verified_local_count: 0 }, renders: { count: 0, verified_count: 0, latest: null }, delivery: { count: 0, verified_count: 0, latest: null }, observed_at: "2026-08-24T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } });
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
    expect(await screen.findByText(/先告诉系统项目需要图像、视频、声音或故事拆解能力/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "从项目要完成的创作任务开始" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "生成偏好任务" })).toBeInTheDocument();
    expect(screen.getByText("专家工具：执行契约与工作流版本")).toBeInTheDocument();
    expect(api.listProfiles).not.toHaveBeenCalled();
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

    expect(screen.getByText("专家工具：执行契约与工作流版本").closest("details")).toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: "返回创作能力配置" }));
    expect(await screen.findByRole("region", { name: "生成偏好任务" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "工作流版本、验证与发布证据" })).not.toBeInTheDocument();
    expect(router.state.location.search).toContain("project=project-1");
    expect(router.state.location.search).toContain("view=preferences");

    fireEvent.click(screen.getByText("专家工具：执行契约与工作流版本"));
    fireEvent.click(screen.getByRole("button", { name: /Profile 契约/ }));
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
    const projectNavigation = screen.getByRole("navigation", { name: "项目导航" });
    const systemNavigation = within(projectNavigation).getByText("系统区").nextElementSibling;
    expect(systemNavigation).toHaveClass("sidebar-system-links");
    expect(within(systemNavigation as HTMLElement).getByRole("link", { name: "任务与机器" })).toBeVisible();
    await waitFor(() => expect(screen.getByRole("combobox", { name: "当前项目" })).toHaveValue("project-1"));
  });

  it("refreshes the shell project switcher through the canonical project-list invalidation boundary", async () => {
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId", element: <AppShell />, children: [{ index: true, element: <div>项目内容</div> }] }],
      { initialEntries: ["/projects/project-new"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    api.listProjects.mockResolvedValueOnce({ items: [{ id: "project-1", title: "北方小院" }] });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    expect(await screen.findByText("项目内容")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "当前项目" })).toHaveValue("");

    api.listProjects.mockResolvedValue({ items: [
      { id: "project-new", title: "刚创建的项目" },
      { id: "project-1", title: "北方小院" },
    ] });
    await client.invalidateQueries({ queryKey: queryKeys.projects.lists() });

    await waitFor(() => expect(screen.getByRole("combobox", { name: "当前项目" })).toHaveValue("project-new"));
    expect(screen.getByRole("option", { name: "刚创建的项目" })).toBeInTheDocument();
  });

  it("keeps season, episode, and shot context in the shell and preserves the current task", async () => {
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId", element: <AppShell />, children: [
        { path: "episodes/:episodeId/direct/:shotId?", element: <div>导演内容</div> },
      ] }],
      { initialEntries: ["/projects/project-1/episodes/episode-1/direct/shot-1"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    await waitFor(() => expect(screen.getByRole("combobox", { name: "当前季度" })).toHaveValue("season-1"));
    expect(screen.getByRole("combobox", { name: "当前分集" })).toHaveValue("episode-1");
    await waitFor(() => expect(screen.getByRole("combobox", { name: "当前镜头" })).toHaveValue("shot-1"));
    const projectNav = screen.getByRole("navigation", { name: "项目导航" });
    expect(await within(projectNav).findByRole("link", { name: /生产设置.*1 待办/ })).toBeInTheDocument();
    expect(within(projectNav).getByRole("link", { name: /导演台.*1\/2/ })).toBeInTheDocument();
    expect(within(projectNav).getByRole("link", { name: /镜头生成.*1 失败/ })).toBeInTheDocument();
    expect(within(projectNav).getByRole("link", { name: /声音.*1\/2/ })).toBeInTheDocument();
    expect(within(projectNav).getByRole("link", { name: /时间线.*未创建/ })).toBeInTheDocument();
    expect(within(projectNav).getByRole("link", { name: /交付.*未创建/ })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "当前位置" })).toHaveTextContent("北方小院");
    expect(screen.getByRole("navigation", { name: "当前位置" })).toHaveTextContent("第一季 / 第一集");

    fireEvent.change(screen.getByRole("combobox", { name: "当前镜头" }), { target: { value: "shot-2" } });
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/project-1/episodes/episode-1/direct/shot-2"));

    fireEvent.change(screen.getByRole("combobox", { name: "当前分集" }), { target: { value: "episode-2" } });
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/project-1/episodes/episode-2/direct"));

    fireEvent.change(screen.getByRole("combobox", { name: "当前季度" }), { target: { value: "season-2" } });
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/project-1/episodes/episode-3/direct"));
  });

  it("announces a stale frozen timeline globally and links to the explicit recovery flow", async () => {
    api.getEpisodeTimelineStatus.mockResolvedValue({ status: { episode: { id: "episode-1", code: "E01", title: "第一集", project_id: "project-1" }, timeline: { revision_count: 2, latest: { id: "timeline-2", status: "STALE" } }, subtitles: { revision_count: 1, latest: {} }, audio: { binding_count: 1, verified_local_count: 1 }, renders: { count: 0, verified_count: 0, latest: null }, delivery: { count: 0, verified_count: 0, latest: null }, observed_at: "2026-08-24T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } });
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId", element: <AppShell />, children: [{ path: "episodes/:episodeId/timeline", element: <div>时间线内容</div> }] }],
      { initialEntries: ["/projects/project-1/episodes/episode-1/timeline"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("本集冻结时间线已过期");
    expect(within(alert).getByRole("link", { name: "同步、复检并重新冻结" })).toHaveAttribute("href", "/projects/project-1/episodes/episode-1/delivery");
    expect(within(screen.getByRole("navigation", { name: "项目导航" })).getByRole("link", { name: /时间线.*已过期/ })).toBeInTheDocument();
  });

  it("uses one compact navigation drawer with Escape and focus return", async () => {
    const router = createMemoryRouter([{ element: <AppShell />, children: [{ path: "/jobs", element: <JobsPage /> }] }], { initialEntries: ["/jobs?project=project-1"] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    await screen.findByRole("heading", { name: "任务与机器", level: 2 });
    const trigger = screen.getByRole("button", { name: "打开项目导航" });
    const navigation = screen.getByRole("navigation", { name: "项目导航" });
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(navigation).toHaveClass("mobile-open");
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "任务与机器" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(navigation).not.toHaveClass("mobile-open");
    expect(document.activeElement).toBe(trigger);
    expect(document.body.style.overflow).toBe("");
    expect(document.documentElement.style.overflow).toBe("");
  });

  it("keeps global workspaces separate and system recovery links visible", async () => {
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId", element: <AppShell />, children: [{ index: true, element: <div>项目内容</div> }] }],
      { initialEntries: ["/projects/project-1"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    expect(await screen.findByText("项目内容")).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "项目导航" });
    expect(within(navigation).getByText("工作空间")).toBeInTheDocument();
    expect(within(navigation).getByText("创作区")).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "模型与能力" })).toBeVisible();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DiagnosticsPage } from "./DiagnosticsPage";
import { JobsPage } from "./JobsPage";
import { ModelsPage } from "./ModelsPage";
import { SystemWorkflowsPage } from "./SystemWorkflowsPage";
import { AppShell } from "../layouts/AppShell";
import { queryKeys } from "../query/queryKeys";
import { notifyDraftDirty } from "../features/drafts/draftGuard";
import { apiJsonResponse } from "../test/apiResponse";

const api = vi.hoisted(() => ({
  getCapacitySnapshot: vi.fn(), getDiagnostics: vi.fn(), getEpisodeTimelineStatus: vi.fn(), getProjectCreatorSetup: vi.fn(), getProjectEpisodeCatalog: vi.fn(), getStoryboardWorkspace: vi.fn(), listAuditEvents: vi.fn(), listEpisodes: vi.fn(), listJobsPage: vi.fn(), listProfiles: vi.fn(), listProjects: vi.fn(), listSeasons: vi.fn(), listWorkflowVersions: vi.fn(), runDiagnostics: vi.fn(),
}));

vi.mock("../generated/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("../generated/api")>()), ...api }));
vi.mock("../features/jobs/JobDetailsPanel", () => ({ JobDetailsPanel: () => null }));
vi.mock("../features/status-v2/LocalRuntimeIndicator", () => ({ LocalRuntimeIndicator: () => <span>本机运行时</span> }));
vi.mock("../features/preferences-v2/GenerationPreferencePanel", () => ({
  GenerationPreferencePanel: () => <section aria-label="生成偏好任务">生成偏好单任务面板</section>,
}));
vi.mock("../features/model-config/RuntimeEnvironmentsPanel", () => ({
  RuntimeEnvironmentsPanel: () => <section aria-label="运行环境">运行环境面板</section>,
}));
vi.mock("../features/shared/ComfyLabPanel", () => ({
  ComfyLabPanel: () => <section aria-label="Comfy 开发工具">Comfy 开发工具</section>,
}));

let systemAssignmentItems: Array<Record<string, unknown>> = [];

function mount(node: React.ReactNode, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{node}</MemoryRouter></QueryClientProvider>);
}

describe("V2 system workspaces", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    systemAssignmentItems = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const path = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
      if (path.endsWith("/api/v1/session/bootstrap")) {
        return Promise.resolve(apiJsonResponse({ token: "system-pages-token", mode: "LOCAL_ONLY" }));
      }
      if (path.endsWith("/api/v2/model-platform/overview")) {
        return Promise.resolve(apiJsonResponse({ overview: {
          capability_count: 35, registered_model_release_count: 0, runtime_installation_count: 0,
          discovery_observation_count: 0, published_profile_count: 0,
        }, read_only: true }));
      }
      if (path.endsWith("/api/v2/model-platform/storage-policy")) {
        return Promise.resolve(apiJsonResponse({ storage_policy: {
          model_root_configured: true, root_kind: "INSTANCE_DEFAULT", discovery_library_count: 4,
          discovery_libraries: ["ComfyUI 模型库", "PyTorch 模型库", "Ollama 模型库", "音频模型库"],
          operational_areas: ["下载队列", "暂存区", "隔离区"], configuration_authority: "HOST_CLI", absolute_paths_exposed: false,
        }, read_only: true }));
      }
      if (path.endsWith("/api/v2/model-platform/business-selection-rollouts")) {
        return Promise.resolve(apiJsonResponse({ items: [], count: 0, read_only: true, execution_switched: false }));
      }
      if (path.endsWith("/api/v2/model-platform/system-capability-assignments")) {
        return Promise.resolve(apiJsonResponse({ items: systemAssignmentItems, count: systemAssignmentItems.length, read_only: true, scope_type: "SYSTEM" }));
      }
      if (path.endsWith("/api/v2/model-platform/capability-assignments")) {
        return Promise.resolve(apiJsonResponse({ status: "SAVED" }));
      }
      if (path.endsWith("/api/v2/model-platform/capabilities")) {
        return Promise.resolve(apiJsonResponse({ items: [
          { code: "LLM_STORY_PARSE", title: "故事拆解", family: "TEXT", input_modalities: ["TEXT"], output_modalities: ["TEXT"], business_surfaces: ["story"], background_only: false },
          { code: "EMBEDDING_TEXT", title: "文本向量化", family: "RETRIEVAL", input_modalities: ["TEXT"], output_modalities: ["VECTOR"], business_surfaces: ["project-knowledge"], background_only: true },
        ], count: 2, read_only: true }));
      }
      if (path.endsWith("/api/v2/model-platform/discovery-observations")) {
        return Promise.resolve(apiJsonResponse({ items: [
          { id: "observation-qwen", native_id: "qwen3.8:27b", runtime_kind: "OLLAMA", presence: "PRESENT", size_bytes: 17179869184, candidate_capabilities: ["LLM_STORY_PARSE"], metadata: { family: "qwen3" }, observed_at: "2026-08-29T01:00:00Z", discovery_run_status: "SUCCEEDED" },
        ], count: 1, read_only: true }));
      }
      if (path.endsWith("/api/v2/model-platform/registered-candidates")) {
        return Promise.resolve(apiJsonResponse({ items: [{
          runtime_model_installation_id: "runtime-model-qwen", model_release_id: "release-qwen", model_release_code: "ollama-qwen3-8-27b", model_title: "qwen3.8:27b",
          runtime_kind: "OLLAMA", install_state: "DISCOVERED", integrity_status: "NOT_RUN", readiness_status: "VALIDATION_REQUIRED", assignable_capability_count: 0,
          blockers: ["CAPABILITY_SMOKE_NOT_PASSED", "PROFILE_REQUIRED"],
          capabilities: [{ code: "LLM_STORY_PARSE", title: "故事拆解", offering_validation_status: "NOT_RUN", profile_version_count: 0, published_profile_count: 0, workflow_binding_count: 0, workflow_schema_validated_count: 0, readiness_status: "VALIDATION_REQUIRED", blockers: ["CAPABILITY_SMOKE_NOT_PASSED", "PROFILE_REQUIRED"] }],
        }], count: 1, read_only: true }));
      }
      if (path.endsWith("/api/v2/model-platform/registered-candidates/runtime-model-qwen/validation-history")) {
        return Promise.resolve(apiJsonResponse({ items: [{
          validation_run_id: "validation-qwen", target: "CAPABILITY", capability_code: "LLM_STORY_PARSE",
          validation_kind: "CAPABILITY_SMOKE", status: "SMOKE_PASSED", occurred_at: "2026-08-29T02:01:00Z",
        }], count: 1, read_only: true, evidence_payload_exposed: false }));
      }
      if (path.endsWith("/api/v2/model-platform/profile-versions")) {
        return Promise.resolve(apiJsonResponse({ items: [], count: 0, read_only: true }));
      }
      if (path.endsWith("/api/v2/model-platform/registered-candidates/runtime-model-qwen/capability-offerings/LLM_STORY_PARSE:smoke")) {
        return Promise.resolve(apiJsonResponse({ validation: { validation_run_id: "smoke-qwen", runtime_model_installation_id: "runtime-model-qwen", capability_code: "LLM_STORY_PARSE", status: "SMOKE_PASSED", installation_ready: false } }));
      }
      if (path.endsWith("/api/v2/model-platform/discovery-runs:ollama")) {
        return Promise.resolve(apiJsonResponse({ discovery_run: { id: "scan-ollama", status: "SUCCEEDED", observation_count: 1 } }));
      }
      if (path.endsWith("/api/v2/model-platform/discovery-runs:model-lock")) {
        return Promise.resolve(apiJsonResponse({ discovery_runs: [{ id: "scan-lock", status: "SUCCEEDED", observation_count: 2 }] }));
      }
      if (path.endsWith("/api/v2/model-platform/discovery-observations/observation-qwen:register")) {
        return Promise.resolve(apiJsonResponse({ candidate: { model_release_id: "release-qwen", model_release_code: "ollama-qwen3-8-27b", runtime_model_installation_id: "runtime-model-qwen", created: true, validation_status: "NOT_RUN" } }));
      }
      return Promise.resolve(apiJsonResponse({ message: `Unexpected request: ${path}` }, { status: 404 }));
    }));
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
    api.getDiagnostics.mockResolvedValue({ run: { id: "diagnostic-1", status: "DEGRADED", created_at: "2026-08-29T01:00:00Z", checks: [{ code: "COMFYUI_LOOPBACK", category: "runtime", status: "WARN", observed: { reason: "ConnectionRefusedError" } }] } });
    api.runDiagnostics.mockResolvedValue({ run: { id: "diagnostic-2", status: "HEALTHY", created_at: "2026-08-29T02:00:00Z", checks: [{ code: "COMFYUI_LOOPBACK", category: "runtime", status: "PASS", observed: { status_code: 200 } }] } });
    api.listAuditEvents.mockResolvedValue({ items: [], next_cursor: null, cursor: 0, limit: 50, filters: {}, local_only: true, network_contacted: false, mutated: false });
  });

  it("renders real jobs and capacity and preserves project scope in the URL", async () => {
    api.listProjects.mockResolvedValue({ items: [
      { id: "project-1", title: "北方小院" },
      { id: "project-2", title: "南城夜景" },
    ] });
    api.listJobsPage.mockImplementation((projectId?: string) => Promise.resolve({
      items: projectId === "project-1"
        ? [{ id: "job-1", type: "GENERATION_VARIANT", project_id: "project-1", state: "FAILED", channel: "GPU_H3", priority: 50, max_attempts: 3, revision: 2 }]
        : [
          { id: "job-1", type: "GENERATION_VARIANT", project_id: "project-1", state: "FAILED", channel: "GPU_H3", priority: 50, max_attempts: 3, revision: 2 },
          { id: "job-2", type: "TTS_GENERATION", project_id: "project-2", state: "SUCCEEDED", channel: "CPU", priority: 80, max_attempts: 2, revision: 1 },
        ],
      next_cursor: null, cursor: 0, limit: 100,
    }));
    mount(<JobsPage />);
    expect(await screen.findByText("生成镜头候选")).toBeInTheDocument();
    expect(screen.getByText("本机队列产能快照")).toBeInTheDocument();
    expect(screen.getByText("当前范围：全部任务（含独立生成） · 已读取 2 个任务")).toBeInTheDocument();
    expect(screen.getByText(/南城夜景 ·/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "后台任务范围" }), { target: { value: "project-1" } });
    await waitFor(() => expect(api.listJobsPage).toHaveBeenLastCalledWith("project-1", 0, 100));
    expect(await screen.findByText("当前范围：北方小院 · 已读取 1 个任务")).toBeInTheDocument();
    expect(screen.queryByText("生成对白配音")).not.toBeInTheDocument();
  });

  it("uses the project route parameter as the jobs owner scope", async () => {
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId/jobs", element: <JobsPage /> }],
      { initialEntries: ["/projects/project-1/jobs"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    expect(await screen.findByText("生成镜头候选")).toBeInTheDocument();
    await waitFor(() => expect(api.listJobsPage).toHaveBeenLastCalledWith("project-1", 0, 100));
    expect(screen.getByRole("combobox", { name: "后台任务范围" })).toHaveValue("project-1");
    fireEvent.click(screen.getByRole("button", { name: "查看详情和产物" }));
    expect(await screen.findByRole("dialog", { name: "任务详情与产物" })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toBe("?job=job-1"));
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));
    await waitFor(() => expect(router.state.location.search).toBe(""));
  });

  it("runs explicit diagnostics and keeps audit read-only", async () => {
    mount(<DiagnosticsPage />);
    expect(await screen.findByRole("heading", { name: "有 1 项需要留意" })).toBeInTheDocument();
    expect(screen.getByText("图像与视频生成服务")).toBeInTheDocument();
    expect(screen.queryByText("DEGRADED")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
    expect(await screen.findByRole("heading", { name: "生产环境可用" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /操作记录/i }));
    await waitFor(() => expect(api.listAuditEvents).toHaveBeenCalled());
  });

  it("keeps system capability publishing separate from project capability binding", async () => {
    mount(<ModelsPage />);
    expect(await screen.findByRole("heading", { name: "能力与模型" })).toBeInTheDocument();
    expect(screen.getByText("接入本机模型或模型服务，验证后发布为创作能力。")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "能力优先的模型中心" })).toBeInTheDocument();
    expect(screen.getByText("扫描发现")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "最近发现的本机模型" })).toBeInTheDocument();
    expect(screen.getAllByText("qwen3.8:27b")).toHaveLength(2);
    expect(screen.getByText("文件/标签已发现")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "已登记候选的能力门禁" })).toBeInTheDocument();
    expect(screen.getAllByText("需要验证").length).toBeGreaterThan(0);
    expect(screen.getAllByText("能力冒烟未通过").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText("查看验证记录"));
    expect(await screen.findByText("能力冒烟")).toBeInTheDocument();
    expect(screen.getAllByText(/LLM_STORY_PARSE/).length).toBeGreaterThan(1);
    expect(screen.queryByText(/127\.0\.0\.1/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "运行能力冒烟" }));
    expect(await screen.findByRole("status")).toHaveTextContent("LLM_STORY_PARSE 能力冒烟通过，其余声明能力仍需验证。");
    fireEvent.click(screen.getByRole("button", { name: "扫描 Ollama" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Ollama 扫描完成：发现 1 条记录。");
    fireEvent.click(screen.getByRole("button", { name: "登记候选" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("已登记候选模型：ollama-qwen3-8-27b。下一步需要验证与发布。"));
    expect(screen.getByText(/扫描只产生候选证据，不会自动发布或影响创作任务。/)).toBeInTheDocument();
    expect(screen.queryByText(/所有项目复用/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开专家配置" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "执行配置契约" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "接入与验证" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "故事拆解模型" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "生成偏好任务" })).not.toBeInTheDocument();
    expect(api.listProfiles).not.toHaveBeenCalled();
    expect(api.listWorkflowVersions).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "打开专家配置" }));
    expect(await screen.findByRole("dialog", { name: "执行配置契约" })).toBeInTheDocument();
    await waitFor(() => expect(api.listWorkflowVersions).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "执行配置契约" })).not.toBeInTheDocument());
  });

  it("deep-links to and highlights the exact version returned by publishing", async () => {
    api.listProfiles.mockResolvedValue({ items: [{
      id: "profile-qwen",
      code: "local-llm-ollama-qwen3-8-27b",
      title: "OLLAMA_LOOPBACK qwen3.8:27b QC_VISUAL 候选 Profile",
      version_id: "published-qwen-v1",
      version_no: 1,
      capability: "QC_VISUAL",
      status: "PUBLISHED",
    }] });

    mount(<ModelsPage />, "/system/capabilities?view=catalog&published=published-qwen-v1");

    expect(await screen.findByText("已定位到刚发布的版本")).toBeInTheDocument();
    const capability = screen.getAllByText("画面画质质检").at(-1)?.closest("article");
    expect(capability).toHaveAttribute("aria-current", "true");
    expect(capability).toHaveTextContent("v1 · 已发布");
  });

  it("limits system assignment editing to published Profile and safe declared fields", async () => {
    systemAssignmentItems = [{
      capability_code: "EMBEDDING_TEXT", title: "文本向量化", family: "RETRIEVAL", background_only: true,
      assignment: { resolution_mode: "AUTO", execution_profile_version_id: null, revision: null, overrides: {}, has_unrenderable_override: false },
      profiles: [{
        profile_version_id: "embedding-profile-v1", profile_code: "embedding-default", profile_title: "Qwen3 文本向量", version_no: 1,
        system_override_fields: [{ name: "max_length", label: "最大文本长度", help: "", schema: { type: "integer", minimum: 1, maximum: 8192, default: 4096 } }],
      }],
    }];

    mount(<ModelsPage />);

    expect(await screen.findByRole("heading", { name: "为 V2 设置默认 Profile 与受控参数" })).toBeInTheDocument();
    const panel = screen.getByRole("heading", { name: "为 V2 设置默认 Profile 与受控参数" }).closest("section")!;
    expect(within(panel).getByText("文本向量化")).toBeInTheDocument();
    expect(within(panel).queryByText(/模型路径|原生 locator|endpoint|密钥|Python/)).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByLabelText("固定已发布 Profile"));
    fireEvent.change(within(panel).getByLabelText("已发布 Profile"), { target: { value: "embedding-profile-v1" } });
    fireEvent.change(within(panel).getByLabelText("最大文本长度"), { target: { value: "2048" } });
    expect(within(panel).getByLabelText("变更理由")).toBeInTheDocument();
    fireEvent.change(within(panel).getByLabelText("变更理由"), { target: { value: "索引长度已审核" } });
    fireEvent.change(within(panel).getByLabelText("操作人"), { target: { value: "release-operator" } });
    fireEvent.click(within(panel).getByRole("button", { name: "保存系统能力设置" }));
    expect(await screen.findByRole("status")).toHaveTextContent("范围参数已版本化并写入审计");
    const request = vi.mocked(fetch).mock.calls.find(([input]) => String(input).endsWith("/api/v2/model-platform/capability-assignments"));
    expect(request).toBeDefined();
    expect(JSON.parse(String((request?.[1] as RequestInit).body))).toEqual({
      scope_type: "SYSTEM", scope_id: "", capability_code: "EMBEDDING_TEXT", resolution_mode: "EXPLICIT",
      execution_profile_version_id: "embedding-profile-v1", overrides: { max_length: 2048 }, reason: "索引长度已审核", actor: "release-operator",
    });
  });

  it("gives workflow publishing its own system owner instead of nesting it in Models", async () => {
    const router = createMemoryRouter(
      [{ path: "/system/workflows", element: <SystemWorkflowsPage /> }],
      { initialEntries: ["/system/workflows"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "工作流与运行环境" })).toBeInTheDocument();
    expect(screen.getByText(/项目只能消费已发布且兼容的版本/)).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "运行环境" })).toBeInTheDocument();
    expect(screen.getByText("开发者：Comfy 工作流捕获与本机测试")).toBeInTheDocument();
    expect(api.listWorkflowVersions).toHaveBeenCalledOnce();
    expect(api.listProfiles).not.toHaveBeenCalled();
  });

  it("keeps system pages inside the common AppShell", async () => {
    const router = createMemoryRouter([{ element: <AppShell />, children: [{ path: "/jobs", element: <JobsPage /> }] }], { initialEntries: ["/jobs?project=project-1"] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    expect(await screen.findByRole("link", { name: "返回全局工作台" })).toBeInTheDocument();
    const projectNavigation = screen.getByRole("navigation", { name: "主导航" });
    expect(projectNavigation).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "任务与机器", level: 2 })).toBeInTheDocument();
    expect(within(projectNavigation).getByRole("link", { name: "项目首页" })).toBeVisible();
    expect(within(projectNavigation).getByRole("link", { name: "故事" })).toBeVisible();
    expect(within(projectNavigation).getByRole("link", { name: "资产" })).toBeVisible();
    expect(screen.getByRole("link", { name: "打开任务中心" })).toBeVisible();
    expect(within(projectNavigation).queryByText("系统区")).not.toBeInTheDocument();
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

  it("keeps only project and episode context in the shell and preserves the current stage", async () => {
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId", element: <AppShell />, children: [
        { path: "episodes/:episodeId/studio/:shotId?", element: <div>镜头工作台内容</div> },
      ] }],
      { initialEntries: ["/projects/project-1/episodes/episode-1/studio/shot-1"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    await waitFor(() => expect(screen.getByRole("combobox", { name: "当前分集" })).toHaveValue("episode-1"));
    expect(screen.queryByRole("combobox", { name: "当前季度" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "当前镜头" })).not.toBeInTheDocument();
    const projectNav = screen.getByRole("navigation", { name: "主导航" });
    expect(within(projectNav).getByRole("link", { name: "策划" })).toBeInTheDocument();
    expect(within(projectNav).getByRole("link", { name: "镜头" })).toBeInTheDocument();
    expect(within(projectNav).getByRole("link", { name: "生产" })).toBeInTheDocument();
    expect(within(projectNav).getByRole("link", { name: "后期" })).toBeInTheDocument();
    expect(within(projectNav).getByRole("link", { name: "交付" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "当前位置" })).toHaveTextContent("北方小院");
    expect(screen.getByRole("navigation", { name: "当前位置" })).toHaveTextContent("第一季 / 第一集");

    fireEvent.change(screen.getByRole("combobox", { name: "当前分集" }), { target: { value: "episode-2" } });
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/project-1/episodes/episode-2/studio"));
  });

  it("does not poll episode timeline status globally outside its post-production owner", async () => {
    api.getEpisodeTimelineStatus.mockResolvedValue({ status: { episode: { id: "episode-1", code: "E01", title: "第一集", project_id: "project-1" }, timeline: { revision_count: 2, latest: { id: "timeline-2", status: "STALE" } }, subtitles: { revision_count: 1, latest: {} }, audio: { binding_count: 1, verified_local_count: 1 }, renders: { count: 0, verified_count: 0, latest: null }, delivery: { count: 0, verified_count: 0, latest: null }, observed_at: "2026-08-24T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } });
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId", element: <AppShell />, children: [{ path: "episodes/:episodeId/post/edit", element: <div>剪辑内容</div> }] }],
      { initialEntries: ["/projects/project-1/episodes/episode-1/post/edit"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    expect(await screen.findByText("剪辑内容")).toBeInTheDocument();
    expect(api.getEpisodeTimelineStatus).not.toHaveBeenCalled();
    expect(screen.queryByText("本集冻结时间线已过期")).not.toBeInTheDocument();
  });

  it("uses one compact navigation drawer with Escape and focus return", async () => {
    const router = createMemoryRouter([{ element: <AppShell />, children: [{ path: "/jobs", element: <JobsPage /> }] }], { initialEntries: ["/jobs?project=project-1"] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    await screen.findByRole("heading", { name: "任务与机器", level: 2 });
    const trigger = screen.getByRole("button", { name: "打开主导航" });
    const navigation = screen.getByRole("navigation", { name: "主导航" });
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(navigation).toHaveClass("mobile-open");
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(navigation).not.toHaveClass("mobile-open");
    expect(document.activeElement).toBe(trigger);
    expect(document.body.style.overflow).toBe("");
  });

  it("keeps the canonical project stages compact and system recovery in the top bar", async () => {
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId", element: <AppShell />, children: [{ index: true, element: <div>项目内容</div> }] }],
      { initialEntries: ["/projects/project-1"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
    expect(await screen.findByText("项目内容")).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "主导航" });
    expect(within(navigation).getByText("当前项目")).toBeInTheDocument();
    expect(within(navigation).getByText("项目工具")).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "设置" })).toBeVisible();
    expect(within(navigation).getByRole("link", { name: "Visual Lab" })).toBeVisible();
    expect(screen.getByRole("link", { name: "打开任务中心" })).toBeVisible();
    expect(within(navigation).queryByRole("link", { name: "模型与能力" })).not.toBeInTheDocument();
  });

  it("offers save, discard, and cancel before leaving an entity draft", async () => {
    const save = vi.fn(() => true);
    const discard = vi.fn(() => true);
    const router = createMemoryRouter(
      [{ path: "/projects/:projectId", element: <AppShell />, children: [
        { index: true, element: <button type="button" onClick={() => notifyDraftDirty(true, { save, discard })}>修改草稿</button> },
        { path: "story", element: <div>故事内容</div> },
      ] }],
      { initialEntries: ["/projects/project-1"] },
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);

    fireEvent.click(await screen.findByRole("button", { name: "修改草稿" }));
    fireEvent.click(screen.getByRole("link", { name: "故事" }));
    expect(await screen.findByRole("dialog", { name: "当前页面有未保存内容" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消切换" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "放弃并切换" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存并切换" }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(discard).not.toHaveBeenCalled();
    expect(await screen.findByText("故事内容")).toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getCapacitySnapshot, getProjectConfiguration, getProjectHealth, listEpisodes, listSeasons } from "../../generated/api";
import { listGenerationPreferences } from "../preferences-v2/api";
import { listQcPolicies } from "../qc-policy-v2/api";
import { getDirectorRecipeBinding } from "../recipes-v2/api";
import { ProductionSettingsOverview } from "./ProductionSettingsOverview";

vi.mock("../../generated/api", () => ({
  getCapacitySnapshot: vi.fn(),
  getProjectConfiguration: vi.fn(),
  getProjectHealth: vi.fn(),
  listEpisodes: vi.fn(),
  listSeasons: vi.fn(),
}));
vi.mock("../preferences-v2/api", () => ({ listGenerationPreferences: vi.fn() }));
vi.mock("../qc-policy-v2/api", () => ({ listQcPolicies: vi.fn() }));
vi.mock("../recipes-v2/api", () => ({ getDirectorRecipeBinding: vi.fn() }));
vi.mock("./ProductionSpecEditor", () => ({ ProductionSpecEditor: () => <div data-testid="production-spec-editor" /> }));
vi.mock("./ProjectTargetDurationEditor", () => ({ ProjectTargetDurationEditor: () => <div data-testid="duration-editor" /> }));

const baseConfiguration = (projectId: string) => ({
  project: { id: projectId, code: projectId, title: projectId },
  production_plan: { id: "plan-1", code: "plan", title: "计划", version_id: "plan-version-1", version_no: 1, status: "ACTIVE", plan: { presentation: { aspect_ratio: "9:16" } } },
  production_spec: { status: "READY", blockers: [] },
  profile_bindings: [],
  delivery_targets: [{ target_id: "target-1", code: "vertical", title: "竖屏", transport: "LOCAL", target_status: "ACTIVE", version_id: "target-version-1", version_no: 1, version_status: "PUBLISHED", spec: {}, delivery_package_count: 0 }],
  selected_delivery_target_version_id: "target-version-1",
  impact: { profile_switches_preserve_frozen_jobs: true, profile_frozen_job_counts: {}, delivery_package_counts: {}, remote_transport_allowed: false },
  runtime_contacted: false,
  network_contacted: false,
  mutated: false,
});

function mount(projectId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><ProductionSettingsOverview projectId={projectId} /></MemoryRouter></QueryClientProvider>);
}

function mockReadOnlyFacts(projectId: string, healthBlockers: string[] = []) {
  vi.mocked(getProjectConfiguration).mockResolvedValue({ configuration: baseConfiguration(projectId) } as never);
  vi.mocked(getProjectHealth).mockResolvedValue({ project_id: projectId, status: healthBlockers.length ? "BLOCKED" : "HEALTHY", root_exists: true, database_integrity: "ok", media: { referenced_count: 0, missing: [], size_mismatch: [], hash_mismatch: [] }, orphan_files: [], orphan_count: 0, disk: { free_bytes: 10_000_000_000, total_bytes: 20_000_000_000 }, blockers: healthBlockers, runtime_contacted: false, network_contacted: false, mutated: false });
  vi.mocked(getCapacitySnapshot).mockResolvedValue({ snapshot: { disk: { free_bytes: 10_000_000_000 }, gpu_active_count: 0, gpu_concurrency_limit: 1, queued_count: 0, active_worker_count: 0, gpu: {} } } as never);
  vi.mocked(listGenerationPreferences).mockResolvedValue([]);
  vi.mocked(listQcPolicies).mockResolvedValue([]);
  vi.mocked(getDirectorRecipeBinding).mockResolvedValue(null);
  vi.mocked(listSeasons).mockResolvedValue({ items: [] });
  vi.mocked(listEpisodes).mockResolvedValue({ items: [] });
}

describe("ProductionSettingsOverview readiness severity", () => {
  beforeEach(() => vi.clearAllMocks());

  it.each(["project-a", "project-b"])("keeps optional defaults as notices for %s without claiming a launch block", async (projectId) => {
    mockReadOnlyFacts(projectId);
    mount(projectId);

    await waitFor(() => expect(screen.getByRole("heading", { name: "本集制作可启动" })).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "2 项可选配置" })).toBeInTheDocument();
    expect(screen.getByText(/尚未绑定导演模板（提醒）/)).toBeInTheDocument();
    expect(screen.getByText(/项目级自动质检规则尚缺/)).toBeInTheDocument();
    expect(screen.queryByText("尚有阻塞")).not.toBeInTheDocument();
    expect(screen.getAllByText(/不会阻止 EpisodeProductionRun/)).toHaveLength(2);
  });

  it("keeps an actual project-health blocker in the hard-blocker section", async () => {
    mockReadOnlyFacts("project-with-media-gap", ["MEDIA_MISSING"]);
    mount("project-with-media-gap");

    await waitFor(() => expect(screen.getByRole("heading", { name: "1 项必须处理" })).toBeInTheDocument());
    expect(screen.getByText("有硬阻塞")).toBeInTheDocument();
    expect(screen.getByText("MEDIA_MISSING")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "2 项可选配置" })).toBeInTheDocument();
  });
});

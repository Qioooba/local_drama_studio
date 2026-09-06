import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { applyProjectTargetDuration, updateProjectTargetDuration, type ProjectConfiguration } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { ProjectTargetDurationEditor } from "./ProjectTargetDurationEditor";

vi.mock("../../generated/api", () => ({
  applyProjectTargetDuration: vi.fn(),
  updateProjectTargetDuration: vi.fn(),
}));

const episodes = [
  { id: "episode-a-1", code: "EP_A_001", title: "A · 第一集", target_duration_ms: 60_000, number: 1 },
  { id: "episode-a-2", code: "EP_A_002", title: "A · 第二集（覆盖）", target_duration_ms: 45_000, number: 2 },
];

function configuration(projectId: string, duration: number, revision: number): ProjectConfiguration {
  return {
    project: { id: projectId, code: projectId, title: projectId, target_duration_ms: duration, revision },
    production_plan: null,
    production_spec: null,
    profile_bindings: [],
    delivery_targets: [],
    selected_delivery_target_version_id: null,
    impact: { profile_switches_preserve_frozen_jobs: true, profile_frozen_job_counts: {}, delivery_package_counts: {}, remote_transport_allowed: false },
    runtime_contacted: false,
    network_contacted: false,
    mutated: false,
  } as ProjectConfiguration;
}

function mount(config: ProjectConfiguration, projectEpisodes = episodes) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><ProjectTargetDurationEditor projectId={config.project.id} configuration={config} episodes={projectEpisodes} /></QueryClientProvider>);
  return { client, view };
}

describe("ProjectTargetDurationEditor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(updateProjectTargetDuration).mockResolvedValue({ project: { id: "project-a", code: "project-a", title: "project-a", status: "ACTIVE", revision: 4, target_duration_ms: 120_000 } });
    vi.mocked(applyProjectTargetDuration).mockResolvedValue({ application: { project: { id: "project-a", code: "project-a", title: "project-a", status: "ACTIVE", revision: 4, target_duration_ms: 120_000 }, episode_ids: ["episode-a-1"], target_duration_ms: 120_000, requires_replan: true } });
  });

  it("saves a project default while preserving episode overrides until explicit apply", async () => {
    const { client, view } = mount(configuration("project-a", 120_000, 3));
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    fireEvent.change(screen.getByRole("spinbutton", { name: "项目默认单集时长（秒）" }), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: "保存项目默认时长" }));
    await waitFor(() => expect(updateProjectTargetDuration).toHaveBeenCalledWith("project-a", { target_duration_ms: 150_000, expected_revision: 3 }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/已有分集保持不变/));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.episodes.lists() });
    view.rerender(<QueryClientProvider client={client}><ProjectTargetDurationEditor projectId="project-a" configuration={configuration("project-a", 150_000, 4)} episodes={episodes} /></QueryClientProvider>);
    expect(screen.getByRole("status")).toHaveTextContent(/已有分集保持不变/);
    expect(screen.getByText(/EP_A_002/)).toBeInTheDocument();
  });

  it("requires selecting existing episodes before the explicit propagation command", async () => {
    const { client, view } = mount(configuration("project-a", 120_000, 3));
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const apply = screen.getByRole("button", { name: "将默认时长应用到选定分集" });
    expect(apply).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /EP_A_001/ }));
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await waitFor(() => expect(applyProjectTargetDuration).toHaveBeenCalledWith("project-a", { episode_ids: ["episode-a-1"], expected_revision: 3 }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/已将 1 个选定分集/));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.episodes.lists() });
    view.rerender(<QueryClientProvider client={client}><ProjectTargetDurationEditor projectId="project-a" configuration={configuration("project-a", 120_000, 4)} episodes={episodes.map((episode) => episode.id === "episode-a-1" ? { ...episode, target_duration_ms: 120_000 } : episode)} /></QueryClientProvider>);
    expect(screen.getByRole("status")).toHaveTextContent(/已将 1 个选定分集/);
  });

  it("keeps defaults independent for another project", () => {
    mount(configuration("project-b", 90_000, 7), [{ id: "episode-b-1", code: "EP_B_001", title: "B · 第一集", target_duration_ms: 120_000, number: 1 }]);
    expect(screen.getByRole("spinbutton", { name: "项目默认单集时长（秒）" })).toHaveValue(90);
    expect(screen.getByText(/EP_B_001/)).toBeInTheDocument();
    expect(screen.getByText(/当前 120 秒/)).toBeInTheDocument();
  });
});

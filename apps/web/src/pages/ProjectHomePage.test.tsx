import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appendProjectEpisode, getProjectCreatorSetup, getProjectEpisodeCatalog, getProjectHealth, listProjects } from "../generated/api";
import { ProjectHomePage } from "./ProjectHomePage";

vi.mock("../generated/api", () => ({ appendProjectEpisode: vi.fn(), getProjectCreatorSetup: vi.fn(), getProjectEpisodeCatalog: vi.fn(), getProjectHealth: vi.fn(), listProjects: vi.fn() }));

const setupMilestones = {
  episode_count: { ready: true, count: 3 },
  production_plan_count: { ready: false, count: 0 },
  published_profile_binding_count: { ready: false, count: 0 },
  reviewable_story_draft_count: { ready: false, count: 0 },
  active_story_asset_count: { ready: false, count: 0 },
  shot_intent_count: { ready: false, count: 0 },
  shot_generation_job_count: { ready: false, count: 0 },
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/project-1"]}><Routes><Route path="/projects/:projectId" element={<ProjectHomePage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe("ProjectHomePage multi-season cockpit entry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(appendProjectEpisode).mockResolvedValue({} as never);
    vi.mocked(listProjects).mockResolvedValue({ items: [{ id: "project-1", code: "DRAMA", title: "本地短剧", aspect_ratio: "16:9", status: "ACTIVE" }] } as never);
    vi.mocked(getProjectHealth).mockResolvedValue({ blockers: [], status: "READY" } as never);
    vi.mocked(getProjectEpisodeCatalog).mockResolvedValue({ catalog: { project_id: "project-1", seasons: [
      { id: "season-2", code: "S02", title: "第二季", number: 2, episodes: [{ id: "episode-3", code: "EP03", title: "第二季首集", number: 1, production_status: "PLANNED" }] },
      { id: "season-1", code: "S01", title: "第一季", number: 1, episodes: [
        { id: "episode-2", code: "EP02", title: "未完成", number: 2, production_status: "PLANNED" },
        { id: "episode-1", code: "EP01", title: "已交付", number: 1, production_status: "DELIVERED" },
      ] },
    ], read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } } as never);
    vi.mocked(getProjectCreatorSetup).mockResolvedValue({ setup: { project_id: "project-1", milestones: setupMilestones, operations: { worker_ready: false, active_worker_count: 0 }, completed_count: 1, total_count: 7, observed_at: "2026-08-24T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } } as never);
  });

  it("loads every bounded season, sorts groups and targets the first unfinished episode", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "S01 · 第一季" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "S02 · 第二季" })).toBeTruthy();
    await waitFor(() => expect(getProjectEpisodeCatalog).toHaveBeenCalledTimes(1));
    expect(screen.getByText("目标分集：EP02 · 未完成")).toBeTruthy();
    expect(screen.getByRole("link", { name: "继续 EP02" }).getAttribute("href")).toContain("/episodes/episode-2/plan");
    expect(screen.getAllByRole("link", { name: "打开资产圣经" }).some((link) => link.getAttribute("href")?.includes("episode=episode-2"))).toBe(true);
    const seasonHeadings = screen.getAllByRole("heading", { level: 4 }).map((node) => node.textContent);
    expect(seasonHeadings).toEqual(["S01 · 第一季", "S02 · 第二季"]);
    expect(screen.getByRole("heading", { name: "从故事到第一镜" })).toBeTruthy();
    expect(screen.getByText("1/7 已就绪")).toBeTruthy();
    expect(screen.getByText(/建议下一步/).textContent).toContain("导入并拆解故事");
    expect(screen.getAllByRole("link", { name: "导入并拆解故事" }).every((link) => link.getAttribute("href") === "/projects/project-1/story#story-import")).toBe(true);
    expect(screen.queryByRole("link", { name: "准备开始生成" })).toBeNull();
  });

  it("shows a recoverable directory error instead of a misleading empty state", async () => {
    vi.mocked(getProjectEpisodeCatalog).mockRejectedValue(new Error("分集目录暂不可用"));
    renderPage();
    expect((await screen.findByRole("alert")).textContent).toContain("分集目录暂不可用");
    expect(screen.queryByText("当前项目还没有季度；请先建立季度与分集。")).not.toBeTruthy();
  });

  it("continues through shot intent and first generation using persisted project facts", async () => {
    vi.mocked(getProjectCreatorSetup).mockResolvedValue({ setup: { project_id: "project-1", milestones: {
      ...setupMilestones,
      production_plan_count: { ready: true, count: 1 }, published_profile_binding_count: { ready: true, count: 2 },
      reviewable_story_draft_count: { ready: true, count: 1 }, active_story_asset_count: { ready: true, count: 4 },
    }, operations: { worker_ready: true, active_worker_count: 1 }, completed_count: 5, total_count: 7, observed_at: "2026-08-24T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } } as never);
    renderPage();
    expect(await screen.findByText("5/7 已就绪")).toBeTruthy();
    const next = screen.getByText(/建议下一步/);
    expect(next.textContent).toContain("进入导演台创建意图");
    expect(within(next).getByRole("link", { name: "进入导演台创建意图" }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-2/direct");
  });
});

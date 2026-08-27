import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appendProjectEpisode, getProjectOverviewV2, type ProductRouteTarget } from "../generated/api";
import { ProjectHomePage } from "./ProjectHomePage";

vi.mock("../generated/api", () => ({ appendProjectEpisode: vi.fn(), getProjectOverviewV2: vi.fn() }));

const seasons = [
  { id: "season-2", code: "S02", title: "第二季", number: 2, episodes: [{ id: "episode-3", code: "EP03", title: "第二季首集", number: 1, production_status: "PLANNED" }] },
  { id: "season-1", code: "S01", title: "第一季", number: 1, episodes: [
    { id: "episode-2", code: "EP02", title: "未完成", number: 2, production_status: "PLANNED" },
    { id: "episode-1", code: "EP01", title: "已交付", number: 1, production_status: "DELIVERED" },
  ] },
];

const overview = (nextAction: { title: string; description: string; label: string; reason_code?: string; target: ProductRouteTarget } = {
  title: "导入并拆解故事", description: "从原文建立可审阅的故事事实。", label: "进入故事",
  reason_code: "reviewable_story_draft_count", target: { kind: "STORY", project_id: "project-1" },
}) => ({
  project: { id: "project-1", code: "DRAMA", title: "本地短剧", status: "ACTIVE", revision: 1 },
  next_action: nextAction,
  blockers: [{ code: "REVIEWABLE_STORY_DRAFT_COUNT", label: "尚无可审阅的故事拆解", owner: { kind: "STORY", project_id: "project-1" } }],
  seasons,
  recent_activity: [],
  observed_at: "2026-08-24T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false,
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/project-1"]}><Routes><Route path="/projects/:projectId" element={<ProjectHomePage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe("ProjectHomePage v2 overview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(appendProjectEpisode).mockResolvedValue({} as never);
    vi.mocked(getProjectOverviewV2).mockResolvedValue(overview() as never);
  });

  it("uses one bounded overview, sorts seasons and exposes one deterministic next action", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "S01 · 第一季" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "S02 · 第二季" })).toBeTruthy();
    await waitFor(() => expect(getProjectOverviewV2).toHaveBeenCalledTimes(1));
    expect(screen.getAllByRole("heading", { level: 4 }).map((node) => node.textContent)).toEqual(["S01 · 第一季", "S02 · 第二季"]);
    expect(screen.getByRole("heading", { name: "导入并拆解故事" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "进入故事" }).getAttribute("href")).toBe("/projects/project-1/story#story-import");
    expect(screen.getAllByRole("link", { name: "继续制作" })).toHaveLength(2);
  });

  it("shows one recoverable overview error without presenting partial projections", async () => {
    vi.mocked(getProjectOverviewV2).mockRejectedValue(new Error("项目聚合暂不可用"));
    renderPage();
    expect((await screen.findByRole("alert")).textContent).toContain("项目聚合暂不可用");
    expect(screen.queryByRole("heading", { name: "制作进度" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 4 })).not.toBeTruthy();
  });

  it("maps a typed shot-studio target to the canonical route", async () => {
    vi.mocked(getProjectOverviewV2).mockResolvedValue(overview({
      title: "完成 EP02 的第一镜", description: "保存镜头意图并标记为可生产。", label: "打开镜头工作台",
      reason_code: "shot_intent_count", target: { kind: "SHOT_STUDIO", project_id: "project-1", episode_id: "episode-2", focus: "design" },
    }) as never);
    renderPage();
    expect(await screen.findByRole("heading", { name: "完成 EP02 的第一镜" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开镜头工作台" }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-2/studio?focus=design");
  });
});

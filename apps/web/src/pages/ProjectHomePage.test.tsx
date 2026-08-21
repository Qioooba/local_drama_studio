import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getProjectHealth, listEpisodes, listProjects, listSeasons } from "../generated/api";
import { ProjectHomePage } from "./ProjectHomePage";

vi.mock("../generated/api", () => ({ getProjectHealth: vi.fn(), listEpisodes: vi.fn(), listProjects: vi.fn(), listSeasons: vi.fn() }));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/project-1"]}><Routes><Route path="/projects/:projectId" element={<ProjectHomePage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe("ProjectHomePage multi-season cockpit entry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProjects).mockResolvedValue({ items: [{ id: "project-1", code: "DRAMA", title: "本地短剧", aspect_ratio: "16:9", status: "ACTIVE" }] } as never);
    vi.mocked(getProjectHealth).mockResolvedValue({ blockers: [], status: "READY" } as never);
    vi.mocked(listSeasons).mockResolvedValue({ items: [
      { id: "season-2", code: "S02", title: "第二季", number: 2 },
      { id: "season-1", code: "S01", title: "第一季", number: 1 },
    ] } as never);
    vi.mocked(listEpisodes).mockImplementation(async (seasonId) => seasonId === "season-1" ? { items: [
      { id: "episode-2", code: "EP02", title: "未完成", number: 2, production_status: "PLANNED" },
      { id: "episode-1", code: "EP01", title: "已交付", number: 1, production_status: "DELIVERED" },
    ] } : { items: [{ id: "episode-3", code: "EP03", title: "第二季首集", number: 1, production_status: "PLANNED" }] } as never);
  });

  it("loads every bounded season, sorts groups and targets the first unfinished episode", async () => {
    renderPage();
    expect(await screen.findByText("S01 · 第一季")).toBeTruthy();
    expect(screen.getByText("S02 · 第二季")).toBeTruthy();
    await waitFor(() => expect(listEpisodes).toHaveBeenCalledTimes(2));
    expect(screen.getByText("目标分集：EP02 · 未完成")).toBeTruthy();
    expect(screen.getByRole("link", { name: "继续 EP02" }).getAttribute("href")).toContain("/episodes/episode-2/plan");
    expect(screen.getAllByRole("link", { name: "打开资产圣经" }).some((link) => link.getAttribute("href")?.includes("episode=episode-2"))).toBe(true);
    const seasonHeadings = screen.getAllByRole("heading", { level: 4 }).map((node) => node.textContent);
    expect(seasonHeadings).toEqual(["S01 · 第一季", "S02 · 第二季"]);
  });

  it("shows a recoverable partial-directory error instead of a misleading empty state", async () => {
    vi.mocked(listEpisodes).mockImplementation(async (seasonId) => {
      if (seasonId === "season-2") throw new Error("第二季目录暂不可用");
      return { items: [{ id: "episode-1", code: "EP01", title: "已交付", production_status: "DELIVERED" }] } as never;
    });
    renderPage();
    expect((await screen.findByRole("alert")).textContent).toContain("第二季目录暂不可用");
    expect(screen.getByText("EP01 · 已交付")).toBeTruthy();
  });
});

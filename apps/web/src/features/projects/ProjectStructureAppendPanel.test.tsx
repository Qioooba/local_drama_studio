import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { appendProjectEpisode } from "../../generated/api";
import { ProjectStructureAppendPanel } from "./ProjectStructureAppendPanel";

vi.mock("../../generated/api", () => ({ appendProjectEpisode: vi.fn() }));

function mount(seasons: Array<{ id: string; code: string; title: string; episodes: unknown[] }>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}><ProjectStructureAppendPanel projectId="project-1" seasons={seasons} /></QueryClientProvider></MemoryRouter>);
}

describe("ProjectStructureAppendPanel", () => {
  beforeEach(() => {
    vi.mocked(appendProjectEpisode).mockReset().mockResolvedValue({ append: { project_id: "project-1", season_created: true, season: { id: "season-new", code: "SEASON_001", title: "第一季" }, episode: { id: "episode-new", code: "EPISODE_001", title: "第 1 集" } } } as never);
  });

  it("creates the first season and episode from an empty project and exposes the real next step", async () => {
    mount([]);
    fireEvent.click(screen.getByRole("button", { name: "创建季度与首集" }));
    await waitFor(() => expect(appendProjectEpisode).toHaveBeenCalledWith("project-1", { create_new_season: true, episode_title: "第 1 集", target_duration_ms: 60000 }));
    expect(await screen.findByRole("link", { name: "进入新分集规划" })).toHaveAttribute("href", "/projects/project-1/episodes/episode-new/plan");
  });

  it("appends to an explicitly selected existing season", async () => {
    vi.mocked(appendProjectEpisode).mockResolvedValueOnce({ append: { project_id: "project-1", season_created: false, season: { id: "season-2", code: "SEASON_002", title: "第二季" }, episode: { id: "episode-new", code: "EPISODE_004", title: "特别篇" } } } as never);
    mount([{ id: "season-1", code: "SEASON_001", title: "第一季", episodes: [{}] }, { id: "season-2", code: "SEASON_002", title: "第二季", episodes: [{}, {}] }]);
    fireEvent.click(screen.getByText("添加季度或分集"));
    fireEvent.change(screen.getByLabelText("追加分集目标季度"), { target: { value: "season-2" } });
    fireEvent.change(screen.getByLabelText("分集标题"), { target: { value: "特别篇" } });
    fireEvent.click(screen.getByRole("button", { name: "追加分集" }));
    await waitFor(() => expect(appendProjectEpisode).toHaveBeenCalledWith("project-1", { season_id: "season-2", create_new_season: false, episode_title: "特别篇", target_duration_ms: 60000 }));
  });
});

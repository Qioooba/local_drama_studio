import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { bindEpisodeSceneRange, createProjectScene, listEpisodeSceneRanges, listProjectScenes } from "../../generated/api";
import { EpisodeSceneRanges } from "./EpisodeSceneRanges";

vi.mock("../../generated/api", () => ({ bindEpisodeSceneRange: vi.fn(), createProjectScene: vi.fn(), listEpisodeSceneRanges: vi.fn(), listProjectScenes: vi.fn() }));

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><EpisodeSceneRanges projectId="project-1" episodeId="episode-1" /></QueryClientProvider>);
}

describe("EpisodeSceneRanges", () => {
  beforeEach(() => {
    vi.mocked(listProjectScenes).mockReset().mockResolvedValue({ items: [{ id: "scene-1", project_id: "project-1", code: "SC-001", title: "老屋重逢", location: null, time_of_day: null, revision: 1 }] });
    vi.mocked(listEpisodeSceneRanges).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(createProjectScene).mockReset().mockResolvedValue({ scene: { id: "scene-2", project_id: "project-1", code: "SC-002", title: "续场", location: null, time_of_day: null, revision: 1 } });
    vi.mocked(bindEpisodeSceneRange).mockReset().mockResolvedValue({ range: { id: "range-1", episode_id: "episode-1", scene_id: "scene-1", ordinal: 1, source_start: 20, source_end: 80, source_label: "开场", scene_code: "SC-001", scene_title: "老屋重逢", location: null, time_of_day: null } });
  });

  it("creates a project-level master scene explicitly", async () => {
    renderPanel();
    fireEvent.click(screen.getByText("管理母本场次与范围"));
    fireEvent.change(screen.getByLabelText("场次 code"), { target: { value: "SC-002" } });
    fireEvent.change(screen.getByLabelText("场次标题"), { target: { value: "续场" } });
    fireEvent.click(screen.getByRole("button", { name: "创建母本场次" }));
    await waitFor(() => expect(createProjectScene).toHaveBeenCalledWith("project-1", { code: "SC-002", title: "续场" }));
  });

  it("binds an existing scene to explicit episode offsets", async () => {
    renderPanel();
    fireEvent.click(screen.getByText("管理母本场次与范围"));
    await waitFor(() => expect((screen.getByLabelText("已有母本场次") as HTMLSelectElement).value).toBe("scene-1"));
    fireEvent.change(screen.getByLabelText("来源起点"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("来源终点"), { target: { value: "80" } });
    fireEvent.change(screen.getByLabelText("范围说明"), { target: { value: "开场" } });
    fireEvent.click(screen.getByRole("button", { name: "关联到当前集" }));
    await waitFor(() => expect(bindEpisodeSceneRange).toHaveBeenCalledWith("episode-1", { scene_id: "scene-1", ordinal: 1, source_start: 20, source_end: 80, source_label: "开场" }));
  });
});

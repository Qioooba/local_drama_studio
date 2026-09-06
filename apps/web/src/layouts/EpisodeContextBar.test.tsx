import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { EpisodeContextBar } from "./EpisodeContextBar";

const seasons = [{
  id: "season-1",
  title: "第 1 季",
  episodes: [
    { id: "episode-1", title: "第 1 集 起点" },
    { id: "episode-2", title: "第 2 集 转折" },
    { id: "episode-3", title: "第 3 集 结局" },
  ],
}];

describe("EpisodeContextBar", () => {
  it("keeps episode switching and the four production stages in one context bar", () => {
    const onEpisodeChange = vi.fn();
    render(<MemoryRouter><EpisodeContextBar
      projectId="project-1"
      episodeId="episode-2"
      pathname="/projects/project-1/episodes/episode-2/post/audio"
      seasons={seasons}
      onEpisodeChange={onEpisodeChange}
    /></MemoryRouter>);

    expect(screen.getByText("2 / 3 集")).toBeTruthy();
    expect(screen.getByRole("link", { name: /后期成片/ }).getAttribute("aria-current")).toBe("step");
    expect(screen.getByRole("link", { name: /本集生成/ }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-2/plan");
    expect(screen.getByRole("link", { name: /镜头修正/ }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-2/studio");

    fireEvent.click(screen.getByRole("button", { name: "上一集：第 1 集 起点" }));
    fireEvent.click(screen.getByRole("button", { name: "下一集：第 3 集 结局" }));
    fireEvent.change(screen.getByRole("combobox", { name: "切换当前分集" }), { target: { value: "episode-3" } });
    expect(onEpisodeChange.mock.calls.map(([episodeId]) => episodeId)).toEqual(["episode-1", "episode-3", "episode-3"]);
  });
});

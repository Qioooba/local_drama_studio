import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { EpisodeRunPage } from "./EpisodeRunPage";

vi.mock("../features/episode-production-v2/EpisodeProductionWorkspace", () => ({
  EpisodeProductionWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => (
    <section aria-label="规范化整集生产工作区">{projectId}/{episodeId}</section>
  ),
}));

describe("EpisodeRunPage", () => {
  it("mounts the single v2 production owner with route context", () => {
    render(<MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/production"]}>
      <Routes><Route path="/projects/:projectId/episodes/:episodeId/production" element={<EpisodeRunPage />} /></Routes>
    </MemoryRouter>);
    expect(screen.getByRole("region", { name: "规范化整集生产工作区" }).textContent).toBe("project-1/ep-1");
  });
});

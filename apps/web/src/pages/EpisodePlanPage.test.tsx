import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { EpisodePlanPage } from "./EpisodePlanPage";

vi.mock("../features/episode-production-v2/EpisodeProductionWorkspace", () => ({
  EpisodeProductionWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => <section aria-label="本集 Agent 制作">{projectId}/{episodeId}</section>,
}));

describe("EpisodePlanPage", () => {
  it("owns the single episode creation workspace", () => {
    render(<MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/plan"]}><Routes><Route path="/projects/:projectId/episodes/:episodeId/plan" element={<EpisodePlanPage />} /></Routes></MemoryRouter>);
    expect(screen.getByRole("region", { name: "本集 Agent 制作" }).textContent).toContain("project-1/ep-1");
  });
});

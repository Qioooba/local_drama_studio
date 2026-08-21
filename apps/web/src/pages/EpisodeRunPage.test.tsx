import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { EpisodeRunPage } from "./EpisodeRunPage";

vi.mock("../features/episode-cockpit/EpisodeCockpit", () => ({
  EpisodeCockpit: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => (
    <div>EpisodeCockpit {projectId} {episodeId}</div>
  ),
}));
vi.mock("../features/freshness/FreshnessPanel", () => ({
  FreshnessPanel: ({ projectId, scopeId }: { projectId: string; scopeId: string }) => (
    <div>FreshnessPanel {projectId} {scopeId}</div>
  ),
}));
vi.mock("../features/episode-run-v2/EpisodeRunPanel", () => ({
  EpisodeRunPanel: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => (
    <div>EpisodeRunPanel {projectId} {episodeId}</div>
  ),
}));

describe("EpisodeRunPage (009G)", () => {
  it("mounts only the selected run task and switches owners", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/run"]}>
          <Routes>
            <Route path="/projects/:projectId/episodes/:episodeId/run" element={<EpisodeRunPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText("EpisodeRunPanel project-1 ep-1")).toBeTruthy();
    expect(screen.queryByText("EpisodeCockpit project-1 ep-1")).toBeNull();
    expect(screen.queryByText("FreshnessPanel project-1 ep-1")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "关卡总览" }));
    expect(screen.getByText("EpisodeCockpit project-1 ep-1")).toBeTruthy();
    expect(screen.queryByText("EpisodeRunPanel project-1 ep-1")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "失效与影响" }));
    expect(screen.getByText("FreshnessPanel project-1 ep-1")).toBeTruthy();
    expect(screen.queryByText("EpisodeCockpit project-1 ep-1")).toBeNull();
  });

  it("restores the freshness owner from the URL", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/run?view=freshness"]}>
          <Routes><Route path="/projects/:projectId/episodes/:episodeId/run" element={<EpisodeRunPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText("FreshnessPanel project-1 ep-1")).toBeTruthy();
    expect(screen.queryByText("EpisodeRunPanel project-1 ep-1")).toBeNull();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { EpisodeReviewPage } from "./EpisodeReviewPage";

vi.mock("../features/episode-review-v2/EpisodeReviewWorkspace", () => ({
  EpisodeReviewWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => (
    <div>EpisodeReviewWorkspace {projectId} {episodeId}</div>
  ),
}));

describe("EpisodeReviewPage (009C)", () => {
  it("renders review workspace with episode context and navigation links", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/post/review"]}>
          <Routes>
            <Route path="/projects/:projectId/episodes/:episodeId/post/review" element={<EpisodeReviewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByRole("heading", { name: "从候选问题到整集批准" })).toBeTruthy();
    expect(screen.getByText("EpisodeReviewWorkspace project-1 ep-1")).toBeTruthy();
    expect(screen.getByRole("link", { name: "返回镜头" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/studio");
    expect(screen.getByRole("link", { name: "查看编辑" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/post/edit");
  });
});

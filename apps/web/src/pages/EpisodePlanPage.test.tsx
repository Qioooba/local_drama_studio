import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProfiles } from "../generated/api";
import { EpisodePlanPage } from "./EpisodePlanPage";

vi.mock("../generated/api", () => ({
  listProfiles: vi.fn(),
}));

vi.mock("../features/projects/StoryboardBatchWorkbench", () => ({
  StoryboardBatchWorkbench: () => <div>StoryboardBatchWorkbench</div>,
}));
vi.mock("../features/episode-plan-v2/ShotGroupPlanner", () => ({
  ShotGroupPlanner: () => <div>ShotGroupPlanner</div>,
}));
vi.mock("../features/projects/EpisodeSceneRanges", () => ({
  EpisodeSceneRanges: () => <div>EpisodeSceneRanges</div>,
}));
vi.mock("../features/source-passage/EpisodeSourcePassage", () => ({
  EpisodeSourcePassage: () => <div>EpisodeSourcePassage</div>,
}));
vi.mock("../features/projects/AIDraftReviewPanel", () => ({
  AIDraftReviewPanel: () => <div>AIDraftReviewPanel</div>,
}));
vi.mock("../features/episode-plan-v2/AssetProposalReviewPanel", () => ({
  AssetProposalReviewPanel: () => <div>AssetProposalReviewPanel</div>,
}));
vi.mock("../features/episode-plan-v2/SelectedBeatReplanPanel", () => ({
  SelectedBeatReplanPanel: () => <div>SelectedBeatReplanPanel</div>,
}));
vi.mock("../features/production/PromptTemplatePanel", () => ({
  PromptTemplatePanel: () => <div>PromptTemplatePanel</div>,
}));

describe("EpisodePlanPage (009B)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProfiles).mockResolvedValue({ items: [] } as never);
  });

  it("renders plan tabs, storyboard workbench, and toggles replan drawer", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/plan"]}>
          <Routes>
            <Route path="/projects/:projectId/episodes/:episodeId/plan" element={<EpisodePlanPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByRole("heading", { name: "从原文证据到可生产镜头" })).toBeTruthy();
    expect(screen.getByText("StoryboardBatchWorkbench")).toBeTruthy();

    // Switch to scenes tab
    fireEvent.click(screen.getByRole("tab", { name: /场景与分组/i }));
    expect(screen.getByText("ShotGroupPlanner")).toBeTruthy();
    expect(screen.getByText("EpisodeSceneRanges")).toBeTruthy();
    expect(screen.queryByText("StoryboardBatchWorkbench")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "原文证据" }));
    expect(screen.getByText("EpisodeSourcePassage")).toBeTruthy();
    expect(screen.queryByText("AIDraftReviewPanel")).toBeNull();
    expect(screen.queryByText("AssetProposalReviewPanel")).toBeNull();

    // Toggle replan drawer
    fireEvent.click(screen.getByLabelText("打开重排抽屉"));
    expect(screen.getByText("SelectedBeatReplanPanel")).toBeTruthy();
  });
});

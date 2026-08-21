import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getEpisodeProduction, getG6Readiness, h3CandidateRuntime, listProfiles, listProjects, planG6I2VProbe, reviewInbox } from "../generated/api";
import { GenerationPage } from "./GenerationPage";

vi.mock("../generated/api", () => ({
  getEpisodeProduction: vi.fn(), getG6Readiness: vi.fn(), h3CandidateRuntime: vi.fn(), listProfiles: vi.fn(), listProjects: vi.fn(), planG6I2VProbe: vi.fn(), reviewInbox: vi.fn(),
}));
vi.mock("../features/generation/GenerationWorkbench", () => ({
  GenerationWorkbench: ({ selectedShotId, onSelectShot, onOpenReviews }: { selectedShotId: string | null; onSelectShot: (id: string) => void; onOpenReviews: () => void }) => <section aria-label="manual-generation-authority"><span>selected:{selectedShotId}</span><button onClick={() => onSelectShot("shot-2")}>select-shot-2</button><button onClick={onOpenReviews}>open-review</button></section>,
}));

function renderPage(entry = "/projects/project-1/episodes/episode-1/generation/shot-1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><Routes>
    <Route path="/projects/:projectId/episodes/:episodeId/generation" element={<GenerationPage />} />
    <Route path="/projects/:projectId/episodes/:episodeId/generation/:shotId" element={<GenerationPage />} />
    <Route path="/projects/:projectId/episodes/:episodeId/review" element={<div>review-v2</div>} />
  </Routes></MemoryRouter></QueryClientProvider>);
}

describe("GenerationPage", () => {
  beforeEach(() => {
    vi.mocked(listProfiles).mockResolvedValue({ items: [] });
    vi.mocked(listProjects).mockResolvedValue({ items: [{ id: "project-1", code: "P1", title: "Project", status: "ACTIVE", revision: 1, aspect_ratio: "9:16" }] } as never);
    vi.mocked(getEpisodeProduction).mockResolvedValue({ episode: {}, items: [{ id: "shot-1", code: "S1" }, { id: "shot-2", code: "S2" }] });
    vi.mocked(reviewInbox).mockResolvedValue({ items: [] });
    vi.mocked(h3CandidateRuntime).mockResolvedValue({ runtime: { status: "READY" }, candidate_assets: {} } as never);
    vi.mocked(getG6Readiness).mockResolvedValue({ readiness: {} } as never);
    vi.mocked(planG6I2VProbe).mockResolvedValue({ plan: {} } as never);
  });

  it("owns the shot deep link and keeps selection/review navigation inside V2", async () => {
    renderPage();
    expect(await screen.findByText("selected:shot-1")).toBeTruthy();
    expect(reviewInbox).toHaveBeenCalledWith("project-1", "", { episode_id: "episode-1" });
    fireEvent.click(screen.getByRole("button", { name: "select-shot-2" }));
    expect(await screen.findByText("selected:shot-2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "open-review" }));
    expect(await screen.findByText("review-v2")).toBeTruthy();
  });

  it("renders an honest planning recovery when the episode has no shots", async () => {
    vi.mocked(getEpisodeProduction).mockResolvedValue({ episode: {}, items: [] });
    renderPage("/projects/project-1/episodes/episode-1/generation");
    expect(await screen.findByRole("heading", { name: "本集还没有可生成镜头" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("region", { name: "manual-generation-authority" })).toBeNull());
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getEpisodeTimelineStatus,
  getReviewContext,
  listFormalSelectionCandidates,
  listReviewTemplates,
  reviewInbox,
} from "../../generated/api";
import { getShotGroupWorkspace } from "../episode-plan-v2/shotGroupsApi";
import { EpisodeReviewWorkspace } from "./EpisodeReviewWorkspace";

vi.mock("../../generated/api", () => ({
  getEpisodeTimelineStatus: vi.fn(),
  getReviewContext: vi.fn(),
  listFormalSelectionCandidates: vi.fn(),
  listReviewTemplates: vi.fn(),
  reviewInbox: vi.fn(),
  runMachineCheck: vi.fn(),
  selectMediaVersion: vi.fn(),
  submitReview: vi.fn(),
}));
vi.mock("../episode-plan-v2/shotGroupsApi", () => ({ getShotGroupWorkspace: vi.fn() }));
vi.mock("../production/EpisodeReviewPanel", () => ({ EpisodeReviewPanel: () => <div>EpisodeReviewPanel</div> }));
vi.mock("../reviews/FormalSelectionPanel", () => ({ FormalSelectionPanel: () => <div>FormalSelectionPanel</div> }));
vi.mock("../reviews/ReviewInboxPanel", () => ({ ReviewInboxPanel: () => <div>ReviewInboxPanel</div> }));

describe("EpisodeReviewWorkspace (009C)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(reviewInbox).mockResolvedValue({ items: [{ media_version_id: "media-1", shot_code: "S001", stage: "VIDEO", media_kind: "VIDEO", is_blocked: 0, is_stale: false, machine_status: "PASS" }] } as never);
    vi.mocked(listReviewTemplates).mockResolvedValue({ items: [] } as never);
    vi.mocked(getEpisodeTimelineStatus).mockResolvedValue({ status: { renders: { latest: { id: "render-1" } } } } as never);
    vi.mocked(listFormalSelectionCandidates).mockResolvedValue({ items: [] } as never);
    vi.mocked(getShotGroupWorkspace).mockResolvedValue({ shots: [] } as never);
    vi.mocked(getReviewContext).mockResolvedValue({} as never);
  });

  it("conditionally mounts Shot, Render, and Delivery owners and lazily opens formal selection", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><EpisodeReviewWorkspace projectId="project-1" episodeId="episode-1" /></MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("ReviewInboxPanel")).toBeTruthy();
    expect(screen.queryByText("EpisodeReviewPanel")).toBeNull();
    expect(screen.queryByText("FormalSelectionPanel")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "采用正式版本" }));
    expect(await screen.findByText("FormalSelectionPanel")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "采用正式版本" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));
    expect(screen.queryByText("FormalSelectionPanel")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /Render 审核/ }));
    expect(screen.getByText("EpisodeReviewPanel")).toBeTruthy();
    expect(screen.queryByText("ReviewInboxPanel")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /交付交接/ }));
    expect(screen.getByRole("link", { name: "进入交付工作区" }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-1/delivery");
    expect(screen.queryByText("EpisodeReviewPanel")).toBeNull();
  });

  it("restores Render review from the URL", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/?view=render"]}><EpisodeReviewWorkspace projectId="project-1" episodeId="episode-1" /></MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("EpisodeReviewPanel")).toBeTruthy();
    expect(screen.queryByText("ReviewInboxPanel")).toBeNull();
  });
});

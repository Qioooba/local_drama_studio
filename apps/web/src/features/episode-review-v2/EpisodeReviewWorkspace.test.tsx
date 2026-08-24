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
  selectMediaVersion,
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
vi.mock("../reviews/ReviewInboxPanel", () => ({
  ReviewInboxPanel: ({ selectedVersionId, onSelect, onPromote }: { selectedVersionId: string | null; onSelect: (id: string) => void; onPromote: (id: string, type: string) => void }) => <div>ReviewInboxPanel<span>selected-media:{selectedVersionId}</span><button onClick={() => onSelect("media-1")}>select-media-1</button><button onClick={() => onPromote("media-1", "FORMAL_SELECTION")}>promote-media-1</button></div>,
}));

describe("EpisodeReviewWorkspace (009C)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(reviewInbox).mockResolvedValue({ items: [{ media_version_id: "media-1", shot_code: "S001", stage: "VIDEO", media_kind: "VIDEO", is_blocked: 0, is_stale: false, machine_status: "PASS" }] } as never);
    vi.mocked(listReviewTemplates).mockResolvedValue({ items: [] } as never);
    vi.mocked(getEpisodeTimelineStatus).mockResolvedValue({ status: { renders: { latest: { id: "render-1" } } } } as never);
    vi.mocked(listFormalSelectionCandidates).mockResolvedValue({ items: [] } as never);
    vi.mocked(getShotGroupWorkspace).mockResolvedValue({ shots: [] } as never);
    vi.mocked(getReviewContext).mockResolvedValue({} as never);
    vi.mocked(selectMediaVersion).mockResolvedValue({} as never);
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

    fireEvent.click(screen.getByRole("button", { name: "采用已批准成片" }));
    expect(await screen.findByText("FormalSelectionPanel")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "采用已批准成片" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));
    expect(screen.queryByText("FormalSelectionPanel")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /整集成片/ }));
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

  it("restores the exact review candidate from the URL", async () => {
    vi.mocked(reviewInbox).mockResolvedValue({ items: [
      { media_version_id: "media-1", shot_code: "S001", stage: "VIDEO", media_kind: "VIDEO", is_blocked: 0, is_stale: false, machine_status: "PASS" },
      { media_version_id: "media-2", shot_code: "S002", stage: "KEYFRAME", media_kind: "IMAGE", is_blocked: 0, is_stale: false, machine_status: "NOT_RUN" },
    ] } as never);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/?media=media-2"]}><EpisodeReviewWorkspace projectId="project-1" episodeId="episode-1" /></MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("selected-media:media-2")).toBeTruthy();
    expect(getReviewContext).toHaveBeenCalledWith("media-2");
  });

  it("does not let a stale cached first row overwrite a requested media deep link", async () => {
    let resolveInbox!: (value: Awaited<ReturnType<typeof reviewInbox>>) => void;
    vi.mocked(reviewInbox).mockReturnValue(new Promise((resolve) => { resolveInbox = resolve; }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(["reviews", "inbox", "project-1", "episode-1"], { items: [
      { media_version_id: "media-1", shot_code: "S001", stage: "VIDEO", media_kind: "VIDEO", is_blocked: 0, is_stale: false, machine_status: "PASS" },
    ] });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/?media=media-2"]}><EpisodeReviewWorkspace projectId="project-1" episodeId="episode-1" /></MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByRole("status")).toBeTruthy();
    expect(screen.queryByText("ReviewInboxPanel")).toBeNull();
    resolveInbox({ items: [
      { media_version_id: "media-1", shot_code: "S001", stage: "VIDEO", media_kind: "VIDEO", is_blocked: 0, is_stale: false, machine_status: "PASS" },
      { media_version_id: "media-2", shot_code: "S002", stage: "KEYFRAME", media_kind: "IMAGE", is_blocked: 0, is_stale: false, machine_status: "NOT_RUN" },
    ] } as never);
    expect(await screen.findByText("selected-media:media-2")).toBeTruthy();
  });

  it("initializes selection from the URL before a fresh cached list can choose its first row", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(["reviews", "inbox", "project-1", "episode-1"], { items: [
      { media_version_id: "media-1", shot_code: "S001", stage: "VIDEO", media_kind: "VIDEO", is_blocked: 0, is_stale: false, machine_status: "PASS" },
      { media_version_id: "media-2", shot_code: "S002", stage: "KEYFRAME", media_kind: "IMAGE", is_blocked: 0, is_stale: false, machine_status: "NOT_RUN" },
    ] });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/?media=media-2"]}><EpisodeReviewWorkspace projectId="project-1" episodeId="episode-1" /></MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("selected-media:media-2")).toBeTruthy();
    expect(screen.queryByText("selected-media:media-1")).toBeNull();
  });

  it("confirms that a formal selection record was saved", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><EpisodeReviewWorkspace projectId="project-1" episodeId="episode-1" /></MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.click(await screen.findByRole("button", { name: "promote-media-1" }));
    expect((await screen.findByRole("status")).textContent).toContain("已保存正式采用版本");
    expect(selectMediaVersion).toHaveBeenCalledWith("media-1", "FORMAL_SELECTION");
  });
});

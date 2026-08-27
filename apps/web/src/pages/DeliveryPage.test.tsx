import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { commitEpisodeTimelineRefresh, getEpisodeTimelineStatus, getG8Readiness, getProjectConfiguration, planEpisodeTimelineRefresh, reviewInbox } from "../generated/api";
import { DeliveryPage } from "./DeliveryPage";

vi.mock("../generated/api", () => ({
  getEpisodeTimelineStatus: vi.fn(),
  getProjectConfiguration: vi.fn(),
  reviewInbox: vi.fn(),
  getG8Readiness: vi.fn(),
  planEpisodeTimelineRefresh: vi.fn(),
  commitEpisodeTimelineRefresh: vi.fn(),
}));

vi.mock("../features/status/ReadinessPanels", () => ({
  G8ReadinessPanel: () => <div>G8ReadinessPanel</div>,
}));
vi.mock("../features/production/DeliveryWorkflowPanel", () => ({
  DeliveryWorkflowPanel: ({ focus }: { focus?: string }) => <div>DeliveryWorkflowPanel {focus}</div>,
}));
vi.mock("../features/production/EpisodeContactSheetAction", () => ({
  EpisodeContactSheetAction: () => <div>EpisodeContactSheetAction</div>,
}));
vi.mock("../features/generation/PostProcessPanel", () => ({
  PostProcessPanel: () => <div>PostProcessPanel</div>,
}));

describe("DeliveryPage (009F)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getEpisodeTimelineStatus).mockResolvedValue({ status: {} } as never);
    vi.mocked(getProjectConfiguration).mockResolvedValue({ configuration: {} } as never);
    vi.mocked(reviewInbox).mockResolvedValue({ items: [] } as never);
    vi.mocked(getG8Readiness).mockResolvedValue({ readiness: {} } as never);
    vi.mocked(planEpisodeTimelineRefresh).mockResolvedValue({ plan: { status: "BLOCKED", plan_hash: "plan-1", summary: { shot_count: 0, video_count: 0, audio_count: 0, subtitle_count: 0, duration_us: 0 }, blockers: [], warnings: [] } } as never);
    vi.mocked(commitEpisodeTimelineRefresh).mockResolvedValue({ timeline: { revision_no: 2 } } as never);
  });

  it("mounts the four delivery steps independently and keeps every owner reachable", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/delivery"]}>
          <Routes>
            <Route path="/projects/:projectId/episodes/:episodeId/delivery" element={<DeliveryPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByRole("heading", { name: "从冻结时间线创建可验证成片" })).toBeTruthy();
    expect(await screen.findByText("G8ReadinessPanel")).toBeTruthy();
    expect(screen.queryByText(/DeliveryWorkflowPanel/)).toBeNull();
    expect(screen.queryByText("EpisodeContactSheetAction")).toBeNull();

    const progress = screen.getByRole("navigation", { name: "交付进度" });
    expect(progress.querySelector('[aria-current="step"]')?.textContent).toContain("交付检查");

    fireEvent.click(within(progress).getByRole("button", { name: /合成候选/ }));
    expect(screen.getByText("DeliveryWorkflowPanel COMPOSE")).toBeTruthy();
    expect(screen.queryByText("G8ReadinessPanel")).toBeNull();

    fireEvent.click(within(progress).getByRole("button", { name: /审核成片/ }));
    expect(screen.getByText("DeliveryWorkflowPanel REVIEW")).toBeTruthy();
    expect(screen.queryByText("DeliveryWorkflowPanel COMPOSE")).toBeNull();

    fireEvent.click(within(progress).getByRole("button", { name: /打包交付/ }));
    expect(screen.getByText("DeliveryWorkflowPanel PACKAGE")).toBeTruthy();
    expect(screen.getByText("EpisodeContactSheetAction")).toBeTruthy();
    expect(await screen.findByText("PostProcessPanel")).toBeTruthy();
    expect(screen.queryByText("DeliveryWorkflowPanel REVIEW")).toBeNull();
    expect(screen.getByRole("link", { name: "返回编辑" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/post/edit");
  });

  it("restores a staged delivery task from the URL", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/delivery?view=review"]}>
          <Routes><Route path="/projects/:projectId/episodes/:episodeId/delivery" element={<DeliveryPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByText("DeliveryWorkflowPanel REVIEW")).toBeTruthy();
    expect(screen.queryByText("G8ReadinessPanel")).toBeNull();
  });

  it("keeps the preflight CTA blocked until the frozen timeline and delivery target both exist", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/delivery"]}>
          <Routes><Route path="/projects/:projectId/episodes/:episodeId/delivery" element={<DeliveryPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    const next = await screen.findByRole("button", { name: "继续到合成候选" }) as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    expect(await screen.findByText(/尚无可用的冻结时间线/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开编辑并冻结" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/post/edit");
  });

  it("opens compose from preflight only after required inputs are present", async () => {
    vi.mocked(getEpisodeTimelineStatus).mockResolvedValue({ status: { timeline: { latest: { id: "timeline-1" } } } } as never);
    vi.mocked(getProjectConfiguration).mockResolvedValue({ configuration: { selected_delivery_target_version_id: "target-1" } } as never);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/delivery"]}>
          <Routes><Route path="/projects/:projectId/episodes/:episodeId/delivery" element={<DeliveryPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    const next = await screen.findByRole("button", { name: "继续到合成候选" }) as HTMLButtonElement;
    await waitFor(() => expect(next.disabled).toBe(false));
    fireEvent.click(next);
    expect(screen.getByText("DeliveryWorkflowPanel COMPOSE")).toBeTruthy();
  });

  it("rechecks stale inputs and only freezes after explicit confirmation", async () => {
    vi.mocked(getEpisodeTimelineStatus)
      .mockResolvedValueOnce({ status: { timeline: { latest: { id: "timeline-1", status: "STALE" } } } } as never)
      .mockResolvedValue({ status: { timeline: { latest: { id: "timeline-2", status: "FROZEN" } } } } as never);
    vi.mocked(planEpisodeTimelineRefresh).mockResolvedValue({ plan: { status: "READY", plan_hash: "plan-ready", summary: { shot_count: 3, video_count: 3, audio_count: 2, subtitle_count: 1, duration_us: 3_000_000 }, blockers: [], warnings: [{ code: "REVIEW", message: "第二镜连续性建议人工复核" }] } } as never);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/delivery"]}>
          <Routes><Route path="/projects/:projectId/episodes/:episodeId/delivery" element={<DeliveryPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByRole("heading", { name: "同步最新采用事实并重新冻结" })).toBeTruthy();
    const prepare = await screen.findByRole("button", { name: "准备重新冻结" }) as HTMLButtonElement;
    expect(prepare.disabled).toBe(false);
    fireEvent.click(prepare);
    expect(await screen.findByRole("dialog", { name: "确认创建新的冻结时间线" })).toBeTruthy();
    expect(commitEpisodeTimelineRefresh).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认同步并冻结" }));
    await waitFor(() => expect(commitEpisodeTimelineRefresh).toHaveBeenCalledWith("ep-1", "plan-ready"));
    expect(await screen.findByText(/已从最新采用事实创建冻结时间线 v2/)).toBeTruthy();
  });
});

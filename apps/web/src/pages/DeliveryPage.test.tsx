import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getEpisodeTimelineStatus, getG8Readiness, getProjectConfiguration, reviewInbox } from "../generated/api";
import { DeliveryPage } from "./DeliveryPage";

vi.mock("../generated/api", () => ({
  getEpisodeTimelineStatus: vi.fn(),
  getProjectConfiguration: vi.fn(),
  reviewInbox: vi.fn(),
  getG8Readiness: vi.fn(),
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

    fireEvent.click(screen.getByRole("tab", { name: "2 合成候选" }));
    expect(screen.getByText("DeliveryWorkflowPanel COMPOSE")).toBeTruthy();
    expect(screen.queryByText("G8ReadinessPanel")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "3 审核证据" }));
    expect(screen.getByText("DeliveryWorkflowPanel REVIEW")).toBeTruthy();
    expect(screen.queryByText("DeliveryWorkflowPanel COMPOSE")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "4 打包交付" }));
    expect(screen.getByText("DeliveryWorkflowPanel PACKAGE")).toBeTruthy();
    expect(screen.getByText("EpisodeContactSheetAction")).toBeTruthy();
    expect(await screen.findByText("PostProcessPanel")).toBeTruthy();
    expect(screen.queryByText("DeliveryWorkflowPanel REVIEW")).toBeNull();
    expect(screen.getByRole("link", { name: "返回时间线" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/timeline");
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
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getEpisodeTimelineStatus } from "../generated/api";
import { TimelinePage } from "./TimelinePage";

vi.mock("../generated/api", () => ({
  getEpisodeTimelineStatus: vi.fn(),
}));

vi.mock("../features/timeline-v2/TimelineComposer", () => ({
  TimelineComposer: () => <div>TimelineComposer</div>,
}));
vi.mock("../features/production/SubtitleRevisionPanel", () => ({
  SubtitleRevisionPanel: () => <div>SubtitleRevisionPanel</div>,
}));
vi.mock("../features/timeline-v2/TimelineExportPanel", () => ({
  TimelineExportPanel: () => <div>TimelineExportPanel</div>,
}));
vi.mock("../features/status/ReadinessPanels", () => ({
  TimelineStatusPanel: () => <div>TimelineStatusPanel</div>,
}));

describe("TimelinePage (009E)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getEpisodeTimelineStatus).mockResolvedValue({
      status: {
        timeline: { revision_count: 2, latest: { id: "tl-1", status: "FROZEN" } },
        audio: { binding_count: 3, verified_local_count: 3 },
        renders: { count: 1, verified_count: 1 },
        subtitles: { latest: { id: "sub-1", source_document_version_id: "doc-1" } },
      },
    } as never);
  });

  it("mounts one timeline task at a time and opens evidence in a drawer", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/timeline"]}>
          <Routes>
            <Route path="/projects/:projectId/episodes/:episodeId/timeline" element={<TimelinePage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByRole("heading", { name: "版本化成片编排" })).toBeTruthy();
    expect(await screen.findByText("TimelineComposer")).toBeTruthy();
    expect(screen.queryByText("TimelineExportPanel")).toBeNull();
    expect(screen.queryByText("SubtitleRevisionPanel")).toBeNull();
    expect(screen.queryByText("TimelineStatusPanel")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "字幕 revision" }));
    expect(screen.getByText("SubtitleRevisionPanel")).toBeTruthy();
    expect(screen.queryByText("TimelineComposer")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "导出与合成" }));
    expect(screen.getByText("TimelineExportPanel")).toBeTruthy();
    expect(screen.queryByText("SubtitleRevisionPanel")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "版本证据" }));
    expect(screen.getByRole("dialog", { name: "时间线版本证据" })).toBeTruthy();
    expect(screen.getByText("TimelineStatusPanel")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));
    expect(screen.queryByText("TimelineStatusPanel")).toBeNull();
    expect(screen.getByRole("link", { name: "合成与交付" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/delivery");
  });

  it("restores export from the URL without mounting the editor", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/timeline?view=export"]}>
          <Routes><Route path="/projects/:projectId/episodes/:episodeId/timeline" element={<TimelinePage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("TimelineExportPanel")).toBeTruthy();
    expect(screen.queryByText("TimelineComposer")).toBeNull();
  });
});

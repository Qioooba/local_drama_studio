import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { listEpisodeJobs, retryJob } from "../generated/api";
import { EpisodeTaskDrawer } from "./EpisodeTaskDrawer";

vi.mock("../generated/api", () => ({ listEpisodeJobs: vi.fn(), retryJob: vi.fn() }));

describe("EpisodeTaskDrawer", () => {
  it("loads only the server-filtered project and episode scope and exposes recoverable failure", async () => {
    vi.mocked(listEpisodeJobs).mockResolvedValue({ items: [{ id: "job-failed-1", type: "VIDEO", project_id: "p1", state: "FAILED", channel: "GPU", priority: 1, max_attempts: 3, revision: 1, stage_code: "VIDEO", last_error_detail_redacted: "显存不足" }], next_cursor: null, cursor: 0, limit: 100 });
    vi.mocked(retryJob).mockResolvedValue({ job: {} } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter><QueryClientProvider client={client}><EpisodeTaskDrawer open onClose={vi.fn()} projectId="p1" episodeId="e1" /></QueryClientProvider></MemoryRouter>);
    expect(await screen.findByText("显存不足")).toBeTruthy();
    expect(listEpisodeJobs).toHaveBeenCalledWith("p1", "e1");
    fireEvent.click(screen.getByRole("button", { name: "按原输入重试" }));
    await waitFor(() => expect(retryJob).toHaveBeenCalledWith("job-failed-1"));
    expect(screen.getByRole("link", { name: "查看详情" }).getAttribute("href")).toContain("job=job-failed-1");
  });
});

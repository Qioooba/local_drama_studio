import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ApiRequestError } from "../generated/api";
import { listEpisodeJobs, retryJob } from "../generated/api";
import { EpisodeTaskDrawer } from "./EpisodeTaskDrawer";

vi.mock("../generated/api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../generated/api")>();
  return { ...original, listEpisodeJobs: vi.fn(), retryJob: vi.fn() };
});

function job(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    type: "VIDEO",
    project_id: "p1",
    state: "FAILED",
    channel: "GPU",
    priority: 1,
    max_attempts: 3,
    revision: 1,
    stage_code: "VIDEO",
    last_error_detail_redacted: "显存不足",
    progress: {},
    ...overrides,
  };
}

function renderDrawer(projectId = "p1", episodeId = "e1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <EpisodeTaskDrawer open onClose={vi.fn()} projectId={projectId} episodeId={episodeId} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return client;
}

describe("R03 episode retry boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("双击只发一次", async () => {
    vi.mocked(listEpisodeJobs).mockResolvedValue({ items: [job("job-1")], next_cursor: null, cursor: 0, limit: 100 } as never);
    let calls = 0;
    vi.mocked(retryJob).mockImplementation(async () => {
      calls += 1;
      await new Promise((resolve) => setTimeout(resolve, 50));
      return { job: { id: "job-1", revision: 2 } } as never;
    });
    renderDrawer();
    const button = await screen.findByRole("button", { name: "按原输入重试" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(calls).toBe(1));
  });

  it("确定拒绝时行内错误可见", async () => {
    vi.mocked(listEpisodeJobs).mockResolvedValue({ items: [job("job-2")], next_cursor: null, cursor: 0, limit: 100 } as never);
    vi.mocked(retryJob).mockRejectedValueOnce(
      new ApiRequestError("只有失败任务可重试", 409, "JOB_NOT_RETRYABLE", null, false, null, null),
    );
    renderDrawer();
    fireEvent.click(await screen.findByRole("button", { name: "按原输入重试" }));
    expect(await screen.findByText(/重试未受理/)).toBeTruthy();
  });

  it("受理成功但刷新失败不显示重试失败", async () => {
    vi.mocked(listEpisodeJobs)
      .mockResolvedValueOnce({ items: [job("job-3")], next_cursor: null, cursor: 0, limit: 100 } as never)
      .mockRejectedValueOnce(new Error("列表炸了"));
    vi.mocked(retryJob).mockResolvedValueOnce({ job: { id: "job-3", revision: 2 } } as never);
    renderDrawer();
    fireEvent.click(await screen.findByRole("button", { name: "按原输入重试" }));
    expect(await screen.findByText("重试已受理，正在刷新任务状态。")).toBeTruthy();
    expect(await screen.findByText(/列表刷新失败/)).toBeTruthy();
    expect(screen.queryByText(/重试失败/)).toBeNull();
  });

  it("超时未知先核对，不自动连发", async () => {
    vi.mocked(listEpisodeJobs).mockResolvedValue({ items: [job("job-4")], next_cursor: null, cursor: 0, limit: 100 } as never);
    vi.mocked(retryJob).mockRejectedValueOnce(new TypeError("超时"));
    renderDrawer();
    fireEvent.click(await screen.findByRole("button", { name: "按原输入重试" }));
    expect(await screen.findByText(/结果待确认/)).toBeTruthy();
    expect(vi.mocked(retryJob)).toHaveBeenCalledTimes(1);
  });

  it("切集后不污染当前集（mutationKey 隔离）", async () => {
    vi.mocked(listEpisodeJobs).mockImplementation(async (projectId: string, episodeId: string) => ({
      items: [job(`job-${episodeId}`, { last_error_detail_redacted: `err-${episodeId}` })],
      next_cursor: null,
      cursor: 0,
      limit: 100,
    }) as never);
    vi.mocked(retryJob).mockResolvedValue({ job: { id: "job-e1", revision: 2 } } as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <EpisodeTaskDrawer open onClose={vi.fn()} projectId="p1" episodeId="e1" />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "按原输入重试" }));
    await waitFor(() => expect(retryJob).toHaveBeenCalledWith("job-e1"));
    rerender(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <EpisodeTaskDrawer open onClose={vi.fn()} projectId="p1" episodeId="e2" />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText("err-e2")).toBeTruthy();
    expect(listEpisodeJobs).toHaveBeenCalledWith("p1", "e2");
  });
});

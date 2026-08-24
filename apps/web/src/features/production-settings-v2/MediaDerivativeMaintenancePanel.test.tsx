import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { backfillProjectMediaDerivatives } from "../../generated/api";
import { MediaDerivativeMaintenancePanel } from "./MediaDerivativeMaintenancePanel";

vi.mock("../../generated/api", () => ({
  ApiRequestError: class ApiRequestError extends Error {},
  backfillProjectMediaDerivatives: vi.fn(),
}));

describe("MediaDerivativeMaintenancePanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("submits bounded pages and accumulates creator-facing results", async () => {
    vi.mocked(backfillProjectMediaDerivatives)
      .mockResolvedValueOnce({
        backfill: { project_id: "project-1", cursor: 0, limit: 50, scanned: 50, submitted: 8, replayed: 72, jobs: [], has_more: true, next_cursor: 50 },
      })
      .mockResolvedValueOnce({
        backfill: { project_id: "project-1", cursor: 50, limit: 50, scanned: 4, submitted: 2, replayed: 5, jobs: [], has_more: false, next_cursor: null },
      });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    render(<QueryClientProvider client={client}><MediaDerivativeMaintenancePanel projectId="project-1" /></QueryClientProvider>);

    fireEvent.click(screen.getByRole("button", { name: "补齐当前项目预览缓存" }));
    expect(await screen.findByText("50 个媒体版本")).toBeTruthy();
    expect(screen.getByText("8 个后台任务")).toBeTruthy();
    expect(screen.getByRole("button", { name: "继续扫描下一批" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "继续扫描下一批" }));
    expect(await screen.findByText("54 个媒体版本")).toBeTruthy();
    expect(screen.getByText("10 个后台任务")).toBeTruthy();
    expect(screen.getByText("77 个既有任务")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新扫描当前项目" })).toBeTruthy();
    expect(backfillProjectMediaDerivatives).toHaveBeenNthCalledWith(1, "project-1", 0, 50);
    expect(backfillProjectMediaDerivatives).toHaveBeenNthCalledWith(2, "project-1", 50, 50);
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["jobs"] }));
  });
});

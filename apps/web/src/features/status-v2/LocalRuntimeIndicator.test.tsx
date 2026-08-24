import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LocalRuntimeIndicator } from "./LocalRuntimeIndicator";

function renderIndicator(responses: Record<string, { status: string; checks: Record<string, string> }>) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).split("/").at(-1) ?? "";
    return { ok: true, json: async () => responses[path] } as Response;
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}><LocalRuntimeIndicator /></QueryClientProvider></MemoryRouter>);
}

afterEach(() => vi.unstubAllGlobals());

describe("LocalRuntimeIndicator", () => {
  it("reports a healthy local environment only when production dependencies are ready", async () => {
    renderIndicator({
      ready: { status: "HEALTHY", checks: { mode: "ok", database: "ok" } },
      dependencies: { status: "HEALTHY", checks: { ffmpeg: "discovered", database: "ok", comfy_designer: "ready", production_profiles: "synced_candidates", worker_supervisor: "ready:CPU,GPU_H3" } },
    });
    expect(await screen.findByText("本机生产环境正常")).toBeTruthy();
  });

  it("surfaces an offline Comfy runtime as degraded instead of claiming normal", async () => {
    renderIndicator({
      ready: { status: "HEALTHY", checks: { mode: "ok", database: "ok" } },
      dependencies: { status: "DEGRADED", checks: { ffmpeg: "discovered", database: "ok", comfy_designer: "blocked:ConnectionRefusedError", production_profiles: "synced_candidates", worker_supervisor: "ready:CPU,GPU_H3" } },
    });
    expect(await screen.findByText("本机生产环境需处理")).toBeTruthy();
    expect(screen.getByText("blocked:ConnectionRefusedError")).toBeTruthy();
  });

  it("closes the runtime details with Escape and returns focus to the summary", async () => {
    renderIndicator({
      ready: { status: "HEALTHY", checks: { mode: "ok", database: "ok" } },
      dependencies: { status: "HEALTHY", checks: { ffmpeg: "discovered", database: "ok", comfy_designer: "ready", production_profiles: "synced_candidates", worker_supervisor: "ready:CPU,GPU_H3" } },
    });
    const summary = await screen.findByLabelText("本机生产环境正常，展开查看详情");
    fireEvent.click(summary);
    expect(summary.closest("details")?.open).toBe(true);
    fireEvent.keyDown(summary, { key: "Escape" });
    expect(summary.closest("details")?.open).toBe(false);
    expect(document.activeElement).toBe(summary);
  });
});

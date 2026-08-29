import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LocalRuntimeIndicator } from "./LocalRuntimeIndicator";
import { apiJsonResponse } from "../../test/apiResponse";

function renderIndicator(responses: Record<string, { status: string; checks: Record<string, string> }>) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).split("/").at(-1) ?? "";
    const fallback = { status: "HEALTHY", checks: { process: "ok", network_mode: "LOCAL_ONLY" } };
    return apiJsonResponse(responses[path] ?? fallback);
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}><LocalRuntimeIndicator /></QueryClientProvider></MemoryRouter>);
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("LocalRuntimeIndicator", () => {
  it("reports a healthy local environment only when production dependencies are ready", async () => {
    renderIndicator({
      ready: { status: "HEALTHY", checks: { mode: "ok", database: "ok" } },
      dependencies: { status: "HEALTHY", checks: { ffmpeg: "discovered", database: "ok", comfy_designer: "ready", production_profiles: "synced_candidates", worker_supervisor: "ready:CPU,GPU_H3" } },
    });
    expect(await screen.findByText("本机生产环境正常")).toBeTruthy();
  });

  it("keeps the trusted LAN no-login boundary visible while dependencies are healthy", async () => {
    renderIndicator({
      live: { status: "HEALTHY", checks: { process: "ok", network_mode: "LAN_SERVICE" } },
      ready: { status: "HEALTHY", checks: { mode: "ok", database: "ok" } },
      dependencies: { status: "HEALTHY", checks: { ffmpeg: "discovered", database: "ok", comfy_designer: "ready", production_profiles: "synced_candidates", worker_supervisor: "ready:CPU,GPU_H3" } },
    });
    const summary = await screen.findByText("可信局域网运行中（无登录）");
    fireEvent.click(summary);
    expect(screen.getByText(/禁止端口映射到公网/)).toBeTruthy();
  });

  it("degrades immediately on cold start when dependencies are blocked", async () => {
    renderIndicator({
      ready: { status: "HEALTHY", checks: { mode: "ok", database: "ok" } },
      dependencies: { status: "DEGRADED", checks: { ffmpeg: "discovered", database: "ok", comfy_designer: "blocked:ConnectionRefusedError", production_profiles: "synced_candidates", worker_supervisor: "ready:CPU,GPU_H3" } },
    });
    expect(await screen.findByText("本机生产环境需处理")).toBeTruthy();
    expect(screen.getByText("blocked:ConnectionRefusedError")).toBeTruthy();
  });

  it("suppresses one unhealthy poll after a healthy baseline but degrades on consecutive failures", async () => {
    vi.useFakeTimers();
    const responses: Record<string, { status: string; checks: Record<string, string> }> = {
      ready: { status: "HEALTHY", checks: { mode: "ok", database: "ok" } },
      dependencies: { status: "HEALTHY", checks: { ffmpeg: "discovered", database: "ok", comfy_designer: "ready", production_profiles: "synced_candidates", worker_supervisor: "ready:CPU,GPU_H3" } },
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).split("/").at(-1) ?? "";
      const fallback = { status: "HEALTHY", checks: { process: "ok", network_mode: "LOCAL_ONLY" } };
      return apiJsonResponse(responses[path] ?? fallback);
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter><QueryClientProvider client={client}><LocalRuntimeIndicator /></QueryClientProvider></MemoryRouter>);
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(screen.getByText("本机生产环境正常")).toBeTruthy();
    // One busy-runtime blip must not flip the pill.
    responses.dependencies = { status: "DEGRADED", checks: { ffmpeg: "discovered", database: "ok", comfy_designer: "blocked:TimeoutError", production_profiles: "synced_candidates", worker_supervisor: "ready:CPU,GPU_H3" } };
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(screen.getByText("本机生产环境正常")).toBeTruthy();
    // A second consecutive failure is a real state change.
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(screen.getByText("本机生产环境需处理")).toBeTruthy();
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

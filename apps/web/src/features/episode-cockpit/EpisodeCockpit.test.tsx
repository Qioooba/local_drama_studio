import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EpisodeCockpit } from "./EpisodeCockpit";

describe("EpisodeCockpit", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders persisted counts and honest deep-link actions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ cockpit: {
      episode: { id: "e1", code: "E01", title: "第一集", project_id: "p1" },
      shots: { total: 8, directed: 7, with_candidates: 5, remaining_generation: 2, selected: 4, approved: 3, failed: 1, stale: 1 },
      jobs: { failed: 2 }, bridges: { total: 7, ready: 6, stale: 1 }, audio: { bindings: 3, verified: 2 },
      qc: { candidate_versions: 10, checked: 8, passed: 6, failed: 2 },
      blockers: [{ code: "FAILED_SHOTS", count: 1, label: "失败镜头需要处理" }],
      observed_at: "2026-08-20T00:00:00Z", read_only: true, mutated: false,
    } }), { status: 200 })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><EpisodeCockpit projectId="p1" episodeId="e1" /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText("E01 生产驾驶舱")).toBeTruthy();
    expect(screen.getByText("失败镜头需要处理")).toBeTruthy();
    expect(screen.getByRole("link", { name: /生成剩余/ }).getAttribute("href")).toBe("#episode-production-controls");
    expect(screen.getByRole("link", { name: "仅重试失败" }).getAttribute("href")).toContain("/direct?filter=failed");
    expect(screen.getByRole("link", { name: "运行 QC" }).getAttribute("href")).toContain("/review");
    expect(screen.getByRole("link", { name: "导出" }).getAttribute("href")).toContain("/delivery");
  });
});

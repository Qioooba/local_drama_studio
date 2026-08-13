import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App, focusCanvasNodeIds } from "./App";

vi.mock("../generated/api", () => ({
  healthLive: vi.fn().mockResolvedValue({ status: "HEALTHY", checks: { mode: "LOCAL_ONLY" } }),
  systemContract: vi.fn().mockResolvedValue({ mode: "LOCAL_ONLY", remote_provider: "disabled", legacy_migration: "deferred_to_g11" }),
  getAdapterContracts: vi.fn().mockResolvedValue({ registry: { mode: "LOCAL_ONLY", contracts: [], remote_transport_allowed: false, runtime_contacted: false, network_contacted: false, mutated: false } }),
  getCapacitySnapshot: vi.fn().mockResolvedValue({ snapshot: { scope: { project_id: null }, observed_at: "2026-08-14T00:00:00Z", jobs_by_state: {}, jobs_by_channel: {}, queued_count: 0, oldest_queued_age_seconds: null, active_attempt_count: 0, active_worker_count: 0, gpu_active_count: 0, gpu_concurrency_limit: 1, completed_last_24h: 0, observation_status: "OBSERVED_NOT_BENCHMARKED", webhook_status: "NOT_IMPLEMENTED", would_create_jobs: false, runtime_contacted: false, network_contacted: false, mutated: false } }),
  listProjects: vi.fn().mockResolvedValue({ items: [] }),
  listProfiles: vi.fn().mockResolvedValue({ items: [] }),
  latestDiagnostics: vi.fn().mockResolvedValue({ run: null }),
  runDiagnostics: vi.fn().mockResolvedValue({ run: { status: "HEALTHY", checks: [] } }),
  listSeasons: vi.fn().mockResolvedValue({ items: [] }),
  listEpisodes: vi.fn().mockResolvedValue({ items: [] }),
  getEpisodeProduction: vi.fn().mockResolvedValue({ episode: {}, items: [] }),
  getEpisodeTimelineStatus: vi.fn().mockResolvedValue({ status: { episode: { id: "e", code: "E", title: "E", project_id: "p" }, timeline: { revision_count: 0, latest: null }, subtitles: { revision_count: 0, latest: null }, audio: { binding_count: 0, verified_local_count: 0 }, renders: { count: 0, verified_count: 0, latest: null }, delivery: { count: 0, verified_count: 0, latest: null }, observed_at: "2026-08-14T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } }),
  h3CandidateRuntime: vi.fn().mockResolvedValue({ runtime: { status: "BLOCKED" } }),
  reviewInbox: vi.fn().mockResolvedValue({ items: [] }),
  createFrameAnchor: vi.fn(),
}));

describe("G1 app shell", () => {
  beforeEach(() => window.history.replaceState({}, "", "/"));

  it("shows the local-only contract from API state", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
    expect(screen.getByText("LOCAL_ONLY")).toBeTruthy();
    expect(await screen.findByText(/deferred_to_g11/)).toBeTruthy();
  });

  it("restores a deep-linked workspace view after refresh", async () => {
    window.history.replaceState({}, "", "/?view=generation");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
    expect(await screen.findByRole("heading", { name: /先锁定镜头/ })).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("view")).toBe("generation");
  });
});

describe("G9 canvas focus", () => {
  const edges = [{ source: "a", target: "b" }, { source: "b", target: "c" }, { source: "x", target: "b" }];

  it("finds transitive upstream and downstream nodes without changing edges", () => {
    expect([...focusCanvasNodeIds("b", edges, "UPSTREAM")].sort()).toEqual(["a", "b", "x"]);
    expect([...focusCanvasNodeIds("b", edges, "DOWNSTREAM")].sort()).toEqual(["b", "c"]);
  });

  it("returns an empty filter for all or no selection", () => {
    expect(focusCanvasNodeIds(null, edges, "UPSTREAM").size).toBe(0);
    expect(focusCanvasNodeIds("b", edges, "ALL").size).toBe(0);
  });

});

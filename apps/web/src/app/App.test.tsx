import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { canvasThumbnailUrl, focusCanvasNodeIds } from "../features/canvas/ProductionCanvasPanel";
import { listProjects } from "../generated/api";
import { progressiveSlice } from "../features/shared/progressive";
import { selectedItemOrFirst } from "../features/shared/selection";

vi.mock("../generated/api", () => ({
  healthLive: vi.fn().mockResolvedValue({ status: "HEALTHY", checks: { mode: "LOCAL_ONLY" } }),
  systemContract: vi.fn().mockResolvedValue({ mode: "LOCAL_ONLY", remote_provider: "disabled", legacy_migration: "deferred_to_g11" }),
  getAdapterContracts: vi.fn().mockResolvedValue({ registry: { mode: "LOCAL_ONLY", contracts: [], remote_transport_allowed: false, runtime_contacted: false, network_contacted: false, mutated: false } }),
  getCapacitySnapshot: vi.fn().mockResolvedValue({ snapshot: { scope: { project_id: null }, observed_at: "2026-08-14T00:00:00Z", jobs_by_state: {}, jobs_by_channel: {}, queued_count: 0, oldest_queued_age_seconds: null, active_attempt_count: 0, active_worker_count: 0, gpu_active_count: 0, gpu_concurrency_limit: 1, completed_last_24h: 0, observation_status: "OBSERVED_NOT_BENCHMARKED", webhook_status: "LOOPBACK_EXPLICIT_BOUNDED", would_create_jobs: false, runtime_contacted: false, network_contacted: false, mutated: false } }),
  listProjects: vi.fn().mockResolvedValue({ items: [] }),
  listProfiles: vi.fn().mockResolvedValue({ items: [] }),
  listPostProcessRecipes: vi.fn().mockResolvedValue({ items: [] }),
  latestDiagnostics: vi.fn().mockResolvedValue({ run: null }),
  listAuditEvents: vi.fn().mockResolvedValue({ items: [], next_cursor: null, cursor: 0, limit: 50, filters: {}, local_only: true, network_contacted: false, mutated: false }),
  runDiagnostics: vi.fn().mockResolvedValue({ run: { status: "HEALTHY", checks: [] } }),
  listSeasons: vi.fn().mockResolvedValue({ items: [] }),
  listEpisodes: vi.fn().mockResolvedValue({ items: [] }),
  listDialogueLines: vi.fn().mockResolvedValue({ items: [] }),
  listEpisodeAudioBindings: vi.fn().mockResolvedValue({ items: [] }),
  listVoiceProfileVersions: vi.fn().mockResolvedValue({ items: [] }),
  getEpisodeProduction: vi.fn().mockResolvedValue({ episode: {}, items: [] }),
  getEpisodeTimelineStatus: vi.fn().mockResolvedValue({ status: { episode: { id: "e", code: "E", title: "E", project_id: "p" }, timeline: { revision_count: 0, latest: null }, subtitles: { revision_count: 0, latest: null }, audio: { binding_count: 0, verified_local_count: 0 }, renders: { count: 0, verified_count: 0, latest: null }, delivery: { count: 0, verified_count: 0, latest: null }, observed_at: "2026-08-14T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } }),
  h3CandidateRuntime: vi.fn().mockResolvedValue({ runtime: { status: "BLOCKED" } }),
  reviewInbox: vi.fn().mockResolvedValue({ items: [] }),
  createFrameAnchor: vi.fn(),
  pickLocalDocumentFile: vi.fn(),
  importScriptDocument: vi.fn(),
  commitImportSession: vi.fn(),
  getStoryboardWorkspace: vi.fn().mockResolvedValue({ storyboard: { episode: { id: "e", title: "E" }, items: [], views: ["TABLE", "STORYBOARD", "TIMELINE"], identity_invariant: "stable", total_duration_ms: 0 } }),
  planStoryboardBatch: vi.fn(),
  commitStoryboardBatch: vi.fn(),
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

  it("shows a regional retry with the traceable API request ID", async () => {
    vi.mocked(listProjects).mockRejectedValueOnce(new Error("项目读取失败 · 请求 ID req-ui-123"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
    expect((await screen.findByRole("alert")).textContent).toContain("请求 ID req-ui-123");
    fireEvent.click(screen.getByRole("button", { name: "重试当前区域" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("sends title/code search and status filters to the project API", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("搜索项目"), { target: { value: "北方" } });
    fireEvent.change(screen.getByLabelText("项目状态"), { target: { value: "ACTIVE" } });
    await waitFor(() => expect(listProjects).toHaveBeenLastCalledWith({ search: "北方", status: "ACTIVE" }));
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

  it("resolves canvas previews only to derived small thumbnails", () => {
    expect(canvasThumbnailUrl("media/preview 1")).toBe("/api/v1/media-versions/media%2Fpreview%201/thumbnail?size=small&frame=poster");
    expect(canvasThumbnailUrl(null)).toBeNull();
  });

});

describe("bounded production lists", () => {
  it("renders an initial window while retaining a deep-linked selection", () => {
    const items = Array.from({ length: 120 }, (_, index) => index);
    expect(progressiveSlice(items, 50)).toEqual(items.slice(0, 50));
    expect(progressiveSlice(items, 50, 74)).toEqual(items.slice(0, 75));
    expect(progressiveSlice(items, 100, 74)).toEqual(items.slice(0, 100));
  });
});

describe("deep-linked bounded selections", () => {
  const episodes = Array.from({ length: 60 }, (_, index) => ({ id: `episode-${index + 1}`, title: `第 ${index + 1} 集` }));

  it("keeps the selected episode record instead of displaying the first episode", () => {
    expect(selectedItemOrFirst(episodes, "episode-47")?.title).toBe("第 47 集");
    expect(selectedItemOrFirst(episodes, "missing")?.title).toBe("第 1 集");
    expect(selectedItemOrFirst([], "episode-1")).toBeNull();
  });
});

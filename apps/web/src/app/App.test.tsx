import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";

vi.mock("../generated/api", () => ({
  healthLive: vi.fn().mockResolvedValue({ status: "HEALTHY", checks: { mode: "LOCAL_ONLY" } }),
  systemContract: vi.fn().mockResolvedValue({ mode: "LOCAL_ONLY", remote_provider: "disabled", legacy_migration: "deferred_to_g11" }),
  listProjects: vi.fn().mockResolvedValue({ items: [] }),
  listProfiles: vi.fn().mockResolvedValue({ items: [] }),
  latestDiagnostics: vi.fn().mockResolvedValue({ run: null }),
  runDiagnostics: vi.fn().mockResolvedValue({ run: { status: "HEALTHY", checks: [] } }),
  listSeasons: vi.fn().mockResolvedValue({ items: [] }),
  listEpisodes: vi.fn().mockResolvedValue({ items: [] }),
  getEpisodeProduction: vi.fn().mockResolvedValue({ episode: {}, items: [] }),
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

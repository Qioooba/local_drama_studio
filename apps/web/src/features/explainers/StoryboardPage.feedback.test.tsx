/**
 * FE-A06: the storyboard page must SHOW save/lock/impact outcomes.
 *
 * The page wrote ``error`` and ``feedback`` into state but never rendered them, so
 * a 409 ``CANDIDATE_REVISION_CONFLICT`` on adopt, a failed lock, a failed impact
 * preview and a failed candidate read all looked identical to nothing happening.
 * These tests assert the three outcomes are visible, that a read failure is
 * distinguishable from "this beat has no candidates", and that the impact result is
 * a panel rather than a discarded string.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../generated/api", () => ({
  adoptExplainerGeneratedBeats: vi.fn(),
  getExplainerBeatImpact: vi.fn(),
  selectExplainerBeatCandidate: vi.fn(),
}));

import { getExplainerBeatImpact, selectExplainerBeatCandidate } from "../../generated/api";
import { ExplainerStoryboardPage } from "./StoryboardPage";

const BEAT = {
  id: "beat-1",
  code: "BEAT_001",
  revision: 3,
  render_type: "I2V",
  render_type_actual: null,
  must_be_motion: true,
  locked_by_human: false,
  visual_factuality: "FACTUAL",
  visual_intent: "值班员推开门，光线短暂中断。",
  preferred_duration_ms: 2400,
  fallback_reason: null,
  narration_links: [{ canonical_segment_id: "seg_001" }],
  active_selection: { candidate_id: "cand-2" },
};

const CANDIDATES = {
  candidates: [
    { id: "cand-1", variant_no: 1, candidate_kind: "CREATIVE", media_version_id: "mv-1", media_sha256: "a".repeat(64), render_type_actual: "I2V" },
    { id: "cand-2", variant_no: 2, candidate_kind: "CREATIVE", media_version_id: "mv-2", media_sha256: "b".repeat(64), render_type_actual: "I2V" },
  ],
};

vi.mock("./useExplainerQueries", () => ({
  useExplainerEditions: () => ({ data: { editions: [{ id: "e1" }] }, isPending: false }),
  useExplainerBeats: () => ({ data: { beats: [BEAT] }, isPending: false, isError: false }),
  useExplainerBeatCandidates: vi.fn(),
}));

import { useExplainerBeatCandidates } from "./useExplainerQueries";

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/p/p1/explainers/storyboard"]}>
        <Routes>
          <Route path="/p/:projectId/explainers/storyboard" element={<ExplainerStoryboardPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function candidatesQuery(overrides: Record<string, unknown>) {
  return { data: CANDIDATES, isPending: false, isError: false, isSuccess: true, refetch: vi.fn(), ...overrides };
}

describe("storyboard operation feedback (FE-A06)", () => {
  beforeEach(() => {
    vi.mocked(selectExplainerBeatCandidate).mockReset();
    vi.mocked(getExplainerBeatImpact).mockReset();
    vi.mocked(useExplainerBeatCandidates).mockReturnValue(candidatesQuery({}) as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("shows a revision conflict on adopt instead of silently doing nothing", async () => {
    vi.mocked(selectExplainerBeatCandidate).mockRejectedValue(
      new Error("CANDIDATE_REVISION_CONFLICT"),
    );
    mount();
    fireEvent.click(screen.getByRole("button", { name: /候选 2/ }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("CANDIDATE_REVISION_CONFLICT");
    // The recovery instruction is shown, and the button is still usable.
    expect(alert.textContent).toContain("已保留当前选择");
    expect(screen.getByRole("button", { name: /候选 2/ })).toBeEnabled();
  });

  it("shows a successful adopt as a status message", async () => {
    vi.mocked(selectExplainerBeatCandidate).mockResolvedValue({ degraded: false } as never);
    mount();
    fireEvent.click(screen.getByRole("button", { name: /候选 1/ }));
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("已采用候选");
  });

  it("shows the impact preview as a panel with counts", async () => {
    vi.mocked(getExplainerBeatImpact).mockResolvedValue({
      affected_edition_count: 2,
      affected_edition_ids: ["edition-aaaaaaaa", "edition-bbbbbbbb"],
      stale_render_count: 3,
      reusable_asset_count: 4,
      note: "后续时码会过期。",
    } as never);
    mount();
    fireEvent.click(screen.getByRole("button", { name: "查看更换影响" }));
    const panel = await screen.findByLabelText("更换影响");
    expect(panel.textContent).toContain("2 个");
    expect(panel.textContent).toContain("3 个");
    expect(panel.textContent).toContain("4 项");
    expect(panel.textContent).toContain("后续时码会过期。");
  });

  it("distinguishes a candidate read failure from having no candidates", async () => {
    const refetch = vi.fn();
    vi.mocked(useExplainerBeatCandidates).mockReturnValue(
      candidatesQuery({ data: undefined, isError: true, isSuccess: false, error: new Error("500 读取失败"), refetch }) as never,
    );
    mount();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("候选列表读取失败");
    expect(alert.textContent).toContain("这不表示该画面段没有候选");
    fireEvent.click(screen.getByRole("button", { name: "重新读取候选" }));
    await waitFor(() => expect(refetch).toHaveBeenCalled());
  });
});

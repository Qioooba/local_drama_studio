/**
 * The operator's batch adoption, wired to the real command.
 *
 * Generation registers candidates; adoption is a separate decision made after the
 * checks, and a machine may only adopt material whose required checks all passed.
 * When a content check cannot run, the operator decides — once, for the whole class
 * of beats — instead of clicking every beat (design §2.5, §6.3).
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

import { adoptExplainerGeneratedBeats } from "../../generated/api";
import { resetCommandIdCache } from "../../services/commandId";
import { ExplainerStoryboardPage } from "./StoryboardPage";

const BEAT = {
  id: "beat-1",
  code: "B001",
  ordinal: 0,
  revision: 4,
  render_type: "STILL_MOTION",
  render_type_actual: null,
  must_be_motion: false,
  locked_by_human: false,
  visual_factuality: "SYMBOLIC",
  visual_intent: "画面",
  preferred_duration_ms: 3000,
  fallback_reason: null,
  narration_links: [{ canonical_segment_id: "seg_001" }],
  active_selection: null,
};

vi.mock("./useExplainerQueries", () => ({
  useExplainerEditions: () => ({ data: { editions: [{ id: "e1" }] }, isPending: false }),
  useExplainerBeats: () => ({ data: { beats: [BEAT] }, isPending: false, isError: false }),
  useExplainerBeatCandidates: () => ({
    data: { candidates: [] },
    isPending: false,
    isError: false,
    isSuccess: true,
    refetch: vi.fn(),
  }),
}));

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

describe("explainer batch picture adoption", () => {
  beforeEach(() => {
    resetCommandIdCache();
    vi.mocked(adoptExplainerGeneratedBeats).mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("previews the scope without submitting anything", async () => {
    vi.mocked(adoptExplainerGeneratedBeats).mockResolvedValue({
      requires_confirmation: true,
      planned_count: 12,
      needs_review: [{ beat_id: "b9" }],
      plan: { planned_count: 12 },
    } as never);
    mount();

    fireEvent.click(screen.getByRole("button", { name: "预览待采用画面" }));

    await waitFor(() => expect(adoptExplainerGeneratedBeats).toHaveBeenCalled());
    const [projectId, payload, key] = vi.mocked(adoptExplainerGeneratedBeats).mock.calls[0];
    expect(projectId).toBe("p1");
    expect((payload as Record<string, unknown>).confirm).toBe(false);
    // A preview writes nothing, so it needs no operation key.
    expect(key).toBeUndefined();
    expect(await screen.findByText(/预览：将采用 12 个已生成画面/)).toBeInTheDocument();
  });

  it("confirms with an operation key and reports what was adopted", async () => {
    vi.mocked(adoptExplainerGeneratedBeats).mockResolvedValue({
      requires_confirmation: false,
      submitted: true,
      adopted_count: 12,
      needs_review_count: 0,
    } as never);
    mount();

    fireEvent.click(screen.getByRole("button", { name: "采用全部已生成画面" }));

    await waitFor(() => expect(adoptExplainerGeneratedBeats).toHaveBeenCalled());
    const [, payload, key] = vi.mocked(adoptExplainerGeneratedBeats).mock.calls[0];
    expect((payload as Record<string, unknown>).confirm).toBe(true);
    expect((payload as Record<string, unknown>).actor).toBe("local-user");
    expect((payload as Record<string, unknown>).expected_revision).toBe(4);
    // The confirming call records HUMAN adoptions, so it must carry the key.
    expect(typeof key).toBe("string");
    expect(await screen.findByText(/已按人工权威采用 12 个画面/)).toBeInTheDocument();
  });

  it("does not claim success when nothing could be adopted", async () => {
    vi.mocked(adoptExplainerGeneratedBeats).mockResolvedValue({
      requires_confirmation: false,
      submitted: true,
      adopted_count: 0,
      needs_review_count: 3,
    } as never);
    mount();

    fireEvent.click(screen.getByRole("button", { name: "采用全部已生成画面" }));

    expect(
      await screen.findByText(/未采用任何画面；3 个画面段缺少可采用的候选/),
    ).toBeInTheDocument();
  });
});

/**
 * The operator's batch adoption, wired to the real command.
 *
 * Generation registers candidates; adoption is a separate decision made after the
 * checks, and a machine may only adopt material whose required checks all passed.
 * When a content check cannot run, the operator decides — once, for the whole class
 * of beats — instead of clicking every beat (design §2.5, §6.3).
 *
 * §B9 keeps the two halves apart: the preview reads a plan and submits nothing (so
 * it carries no Idempotency-Key), while the confirming call is the one operation
 * that records HUMAN adoptions and therefore must carry a key and an actor.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../generated/api", () => ({
  adoptExplainerGeneratedBeats: vi.fn(),
  getExplainerBeatImpact: vi.fn(),
  getExplainerOverview: vi.fn(),
  getExplainerWorkspaceReadiness: vi.fn(),
  listExplainerOwnerCandidates: vi.fn(),
  planExplainerBeatGeneration: vi.fn(),
  preflightExplainerPlan: vi.fn(),
  retryJob: vi.fn(),
  selectExplainerBeatCandidate: vi.fn(),
  startExplainerRun: vi.fn(),
  submitExplainerBeatGeneration: vi.fn(),
  unlockExplainerSelection: vi.fn(),
}));

vi.mock("../model-config/CapabilityPicker", () => ({
  CapabilityPicker: ({ label }: { label?: string }) => <div>{label ?? "能力选择"}</div>,
  useCapabilityOptions: () => ({ data: undefined, isPending: false, error: null, refetch: vi.fn() }),
  effectiveCapabilityProfile: () => ({
    profileVersionId: "profile-1",
    option: { selectable: true, blockers: [], warnings: [], input_slots: [] },
    ready: true,
  }),
}));

import {
  adoptExplainerGeneratedBeats,
  getExplainerOverview,
  getExplainerWorkspaceReadiness,
  listExplainerOwnerCandidates,
} from "../../generated/api";
import { resetCommandIdCache } from "../../services/commandId";
import { ExplainerStoryboardPage } from "./StoryboardPage";

const BEAT = {
  id: "beat-1",
  code: "B001",
  ordinal: 0,
  revision: 4,
  render_type: "I2V",
  render_type_actual: null,
  must_be_motion: false,
  locked_by_human: false,
  visual_factuality: "SYMBOLIC",
  visual_intent: "画面",
  preferred_duration_ms: 3000,
  fallback_reason: null,
  narration_links: [{ canonical_segment_id: "seg_001", display_text: "一段旁白。" }],
  candidates: [],
  active_selection: null,
};

vi.mock("./useExplainerQueries", () => ({
  useExplainerEditions: () => ({ data: { editions: [{ id: "e1" }] }, isPending: false }),
  useExplainerBeats: () => ({ data: { beats: [BEAT] }, isPending: false, isError: false, error: null, refetch: vi.fn() }),
  useExplainerAssets: () => ({ data: { entities: [] }, isPending: false, isError: false, error: null, refetch: vi.fn() }),
}));

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/explainers/p1/storyboard"]}>
        <Routes>
          <Route path="/explainers/:projectId/storyboard" element={<ExplainerStoryboardPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("explainer batch picture adoption", () => {
  beforeEach(() => {
    resetCommandIdCache();
    vi.clearAllMocks();
    vi.mocked(listExplainerOwnerCandidates).mockResolvedValue({
      project_id: "p1",
      owner_kind: "BEAT",
      owner_id: "beat-1",
      purpose: "KEYFRAME",
      edition_id: null,
      candidates: [],
      counts: { REFERENCE: 0, KEYFRAME: 0, VISUAL: 0 },
      active_selection: null,
      empty_state: "NO_CANDIDATES_YET",
      candidates_newest_first: true,
      read_error_keeps_known_selection: true,
    } as never);
    vi.mocked(getExplainerOverview).mockResolvedValue({
      capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
    } as never);
    vi.mocked(getExplainerWorkspaceReadiness).mockResolvedValue({ steps: [] } as never);
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

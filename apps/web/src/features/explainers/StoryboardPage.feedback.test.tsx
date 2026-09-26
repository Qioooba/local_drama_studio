/**
 * FE-A06: the storyboard page must SHOW save/lock/impact outcomes.
 *
 * The page wrote ``error`` and ``feedback`` into state but never rendered them, so
 * a 409 ``CANDIDATE_REVISION_CONFLICT`` on adopt, a failed lock, a failed impact
 * preview and a failed candidate read all looked identical to nothing happening.
 * These tests assert the three outcomes are visible, that a read failure is
 * distinguishable from "this beat has no candidates", and that the impact result is
 * a panel rather than a discarded string.
 *
 * Step 4 now separates 预览 from 采用 (§B5.1): the adopt button lives under the
 * current candidate preview and is only enabled after a candidate is previewed, so
 * every adopt test previews first — that separation is itself asserted here.
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
  getExplainerBeatImpact,
  getExplainerOverview,
  getExplainerWorkspaceReadiness,
  listExplainerOwnerCandidates,
  selectExplainerBeatCandidate,
} from "../../generated/api";
import { ExplainerStoryboardPage } from "./StoryboardPage";

const BEAT = {
  id: "beat-1",
  code: "BEAT_001",
  ordinal: 0,
  revision: 3,
  render_type: "I2V",
  must_be_motion: false,
  locked_by_human: false,
  visual_factuality: "FACTUAL",
  visual_intent: "值班员推开门，光线短暂中断。",
  prompt_intent: "镜头缓慢推进，值班员推开门。",
  preferred_duration_ms: 2400,
  status: "PLANNED",
  fallback_reason: null,
  narration_links: [{ canonical_segment_id: "seg_001", display_text: "他推开门，光线短暂中断。" }],
  candidates: [
    { id: "cand-1", purpose: "KEYFRAME", variant_no: 1, status: "READY", media_version_id: "mv-1", adopted: false },
    { id: "cand-2", purpose: "KEYFRAME", variant_no: 2, status: "READY", media_version_id: "mv-2", adopted: true },
  ],
  active_selection: { id: "sel-1", purpose: "KEYFRAME", candidate_id: "cand-2", media_version_id: "mv-2" },
};

function candidateRow(id: string, variantNo: number, selected: boolean) {
  return {
    id,
    purpose: "KEYFRAME",
    candidate_kind: "CREATIVE",
    owner_kind: "BEAT",
    owner_id: "beat-1",
    variant_no: variantNo,
    media_kind: "IMAGE",
    media_version_id: `mv-${variantNo}`,
    media_sha256: "a".repeat(64),
    thumbnail_url: `/api/v1/media-versions/mv-${variantNo}/content`,
    preview_url: `/api/v1/media-versions/mv-${variantNo}/content`,
    playback_url: null,
    status: "READY",
    selected,
    adopted: selected,
    locked: false,
    stale: false,
    retryable: false,
    seed: 100 + variantNo,
    job_id: `job-${variantNo}`,
    reference_media_version_ids: [],
    short_label: null,
    error_message: null,
    error_code: null,
  };
}

const CANDIDATE_PAGE = {
  project_id: "p1",
  owner_kind: "BEAT",
  owner_id: "beat-1",
  purpose: "KEYFRAME",
  edition_id: null,
  candidates: [candidateRow("cand-1", 1, false), candidateRow("cand-2", 2, true)],
  counts: { REFERENCE: 0, KEYFRAME: 2, VISUAL: 0 },
  active_selection: { id: "sel-1", purpose: "KEYFRAME", candidate_id: "cand-2", media_version_id: "mv-2" },
  empty_state: null,
  candidates_newest_first: true,
  read_error_keeps_known_selection: true,
};

vi.mock("./useExplainerQueries", () => ({
  useExplainerEditions: () => ({ data: { editions: [{ id: "e1" }] }, isPending: false }),
  useExplainerBeats: () => ({
    data: { beats: [BEAT], render_type_counts: { I2V: 1 }, actual_render_type_counts: {} },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
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

/** §B5.1: choosing a candidate only changes the preview. */
async function previewCandidate(name: string) {
  const card = await screen.findByRole("button", { name });
  fireEvent.click(card);
  return card;
}

describe("storyboard operation feedback (FE-A06)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listExplainerOwnerCandidates).mockResolvedValue(CANDIDATE_PAGE as never);
    vi.mocked(getExplainerBeatImpact).mockResolvedValue({} as never);
    vi.mocked(getExplainerOverview).mockResolvedValue({
      capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
    } as never);
    vi.mocked(getExplainerWorkspaceReadiness).mockResolvedValue({ steps: [] } as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("shows a revision conflict on adopt instead of silently doing nothing", async () => {
    vi.mocked(selectExplainerBeatCandidate).mockRejectedValue(new Error("CANDIDATE_REVISION_CONFLICT"));
    mount();
    await previewCandidate("预览候选 2");
    // Previewing alone must not adopt.
    expect(selectExplainerBeatCandidate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "采用" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("CANDIDATE_REVISION_CONFLICT");
    // The recovery instruction is shown, and the local intent is still usable.
    expect(alert.textContent).toContain("已保留当前选择");
    expect(screen.getByRole("button", { name: "刷新后重试" })).toBeEnabled();
  });

  it("shows a successful adopt as a status message with its downstream scope", async () => {
    vi.mocked(selectExplainerBeatCandidate).mockResolvedValue({ degraded: false } as never);
    vi.mocked(getExplainerBeatImpact).mockResolvedValue({ affected_edition_count: 2 } as never);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "查看更换影响" }));
    await previewCandidate("预览候选 1");
    fireEvent.click(screen.getByRole("button", { name: "采用" }));
    const status = await screen.findByText(/已采用候选/);
    expect(status.textContent).toContain("输出版本的合成");
    expect(vi.mocked(selectExplainerBeatCandidate).mock.calls[0][2]).toMatchObject({
      candidate_id: "cand-1",
      lock: false,
      purpose: "KEYFRAME",
      actor: null,
    });
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
    fireEvent.click(await screen.findByRole("button", { name: "查看更换影响" }));
    const panel = await screen.findByLabelText("更换影响");
    expect(panel.textContent).toContain("2 个");
    expect(panel.textContent).toContain("3 个");
    expect(panel.textContent).toContain("4 项");
    expect(panel.textContent).toContain("后续时码会过期。");
  });

  it("distinguishes a candidate read failure from having no candidates", async () => {
    vi.mocked(listExplainerOwnerCandidates).mockRejectedValue(new Error("500 读取失败"));
    mount();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("候选读取失败");
    expect(alert.textContent).toContain("已保留当前采用的画面");
    // The read failure is explained as such, and is not "this beat has no candidates".
    expect(screen.getByText(/这不表示该画面段没有候选/)).toBeTruthy();
    expect(screen.getAllByText(/当前采用/).length).toBeGreaterThan(0);
    expect(screen.queryByText("还没有候选")).toBeNull();
    const calls = vi.mocked(listExplainerOwnerCandidates).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(vi.mocked(listExplainerOwnerCandidates).mock.calls.length).toBeGreaterThan(calls));
  });
});

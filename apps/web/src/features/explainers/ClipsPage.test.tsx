/**
 * Step 5 (视频片段) page tests.
 *
 * They lock the semantics the design calls out for the clips step:
 * real media (never a fake placeholder), 预览候选 kept apart from 采用, the three
 * distinct renderings (empty / interface error / not configured), and the clip
 * labels: every clip comes from real AI 图生视频, 图形动画 and 上传素材 stay their own
 * separately named sources, and the removed 静图推拉 is gone.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../generated/api", () => ({
  batchRetryJobs: vi.fn(),
  getExplainerBeatImpact: vi.fn(),
  getExplainerOverview: vi.fn(),
  listExplainerBeatCandidates: vi.fn(),
  listExplainerBeats: vi.fn(),
  listExplainerEditions: vi.fn(),
  listExplainerOwnerCandidates: vi.fn(),
  planExplainerBeatGeneration: vi.fn(),
  requestJson: vi.fn(),
  retryJob: vi.fn(),
  selectExplainerBeatCandidate: vi.fn(),
  submitExplainerBeatGeneration: vi.fn(),
  unlockExplainerSelection: vi.fn(),
}));

let profileReady = true;
let profileInputSlots: string[] = ["FIRST_FRAME"];

vi.mock("../model-config/CapabilityPicker", () => ({
  CapabilityPicker: ({ label }: { label?: string }) => <div>{label}</div>,
  useCapabilityOptions: () => ({ data: undefined, isPending: false, error: null, refetch: vi.fn() }),
  effectiveCapabilityProfile: () => ({
    profileVersionId: "prof-1",
    option: {
      selectable: profileReady,
      blockers: profileReady ? [] : [{ code: "EXPLAINER_PROFILE_UNAVAILABLE", message: "本机没有 VIDEO_I2V 的已发布 Profile。" }],
      warnings: [],
      input_slots: profileInputSlots,
    },
    ready: profileReady,
  }),
}));

import * as api from "../../generated/api";
import type { ExplainerBeat } from "../../generated/api";
import { ExplainerActionBarProvider, ExplainerStepActionBar } from "./ExplainerStepActionBar";
import type { ExplainerStepStatusMap } from "./ExplainerSteps";
import { ExplainerClipsPage, clearMotionDraftCache, clipCandidateStatusLabel, clipIsRealVideo, clipModeLabel } from "./ClipsPage";

/**
 * A legacy beat: the database still stores the removed 静图推拉 render type and an
 * adopted VISUAL record for it.  It must read as an outdated plan / 尚未生成, never as
 * a usable clip and never as a still-motion label.
 */
const BEAT_LEGACY_STILL: ExplainerBeat = {
  id: "b1",
  code: "BEAT_001",
  ordinal: 0,
  render_type: "STILL_MOTION",
  visual_intent: "静态地图推出",
  prompt_intent: "地图缓慢推进",
  must_be_motion: false,
  preferred_duration_ms: 4000,
  visual_factuality: "RECONSTRUCTION",
  status: "PLANNED",
  actual_fallback_type: null,
  fallback_reason: null,
  locked_by_human: false,
  active_selection: { id: "sel-legacy", purpose: "VISUAL", candidate_id: "c-legacy", media_version_id: "mv-legacy" },
  narration_links: [{ canonical_segment_id: "seg_001", display_text: "灯塔的光斑缓缓移动。" }],
  candidates: [
    { id: "c-legacy", purpose: "VISUAL", variant_no: 1, status: "READY", media_version_id: "mv-legacy", media_kind: "IMAGE", adopted: true },
  ],
};

const BEAT_MOTION: ExplainerBeat = {
  ...BEAT_LEGACY_STILL,
  id: "b2",
  code: "BEAT_002",
  ordinal: 1,
  render_type: "I2V",
  visual_intent: "观察窗中的光斑缓慢移动",
  prompt_intent: "人物缓慢转头，镜头轻微向前移动",
  must_be_motion: true,
  active_selection: { id: "sel-1", purpose: "VISUAL", candidate_id: "c-adopted", media_version_id: "mv-adopted" },
  narration_links: [{ canonical_segment_id: "seg_002", display_text: "他盯着观察窗里的光斑。" }],
  candidates: [
    { id: "c-adopted", purpose: "VISUAL", variant_no: 1, status: "READY", media_version_id: "mv-adopted", media_kind: "VIDEO", adopted: true },
    { id: "c-kf", purpose: "KEYFRAME", variant_no: 1, status: "READY", media_version_id: "mv-kf", adopted: true },
  ],
};

function candidateRow(overrides: Record<string, unknown>) {
  return {
    candidate_kind: "CREATIVE",
    owner_kind: "BEAT",
    owner_id: "b2",
    media_kind: "VIDEO",
    media_sha256: "a".repeat(64),
    status: "READY",
    selected: false,
    adopted: false,
    locked: false,
    stale: false,
    retryable: false,
    reference_media_version_ids: [],
    short_label: null,
    error_message: null,
    error_code: null,
    ...overrides,
  };
}

const VISUAL_PAGE = {
  project_id: "p1",
  owner_kind: "BEAT",
  owner_id: "b2",
  purpose: "VISUAL",
  edition_id: null,
  candidates: [
    candidateRow({
      id: "c-adopted",
      purpose: "VISUAL",
      variant_no: 1,
      media_version_id: "mv-adopted",
      render_type_planned: "I2V",
      render_type_actual: "I2V",
      thumbnail_url: "/api/v1/media-versions/mv-adopted/content",
      preview_url: "/api/v1/media-versions/mv-adopted/content",
      playback_url: "/api/v1/media-versions/mv-adopted/proxy",
      selected: true,
      adopted: true,
      job_id: "job-1",
      seed: 11,
    }),
    candidateRow({
      id: "c-new",
      purpose: "VISUAL",
      variant_no: 2,
      media_version_id: "mv-new",
      render_type_planned: "I2V",
      render_type_actual: "I2V",
      thumbnail_url: "/api/v1/media-versions/mv-new/content",
      preview_url: "/api/v1/media-versions/mv-new/content",
      playback_url: "/api/v1/media-versions/mv-new/proxy",
      job_id: "job-2",
      seed: 12,
    }),
  ],
  counts: { REFERENCE: 0, KEYFRAME: 0, VISUAL: 2 },
  active_selection: { id: "sel-1", purpose: "VISUAL", candidate_id: "c-adopted", media_version_id: "mv-adopted" },
  empty_state: null,
  candidates_newest_first: true,
  read_error_keeps_known_selection: true,
};

const KEYFRAME_PAGE = {
  project_id: "p1",
  owner_kind: "BEAT",
  owner_id: "b2",
  purpose: "KEYFRAME",
  edition_id: null,
  candidates: [
    candidateRow({
      id: "c-kf",
      purpose: "KEYFRAME",
      variant_no: 1,
      media_kind: "IMAGE",
      media_version_id: "mv-kf",
      render_type_planned: "I2V",
      thumbnail_url: "/api/v1/media-versions/mv-kf/content",
      selected: true,
      adopted: true,
    }),
  ],
  counts: { REFERENCE: 0, KEYFRAME: 1, VISUAL: 0 },
  active_selection: { id: "sel-kf", purpose: "KEYFRAME", candidate_id: "c-kf", media_version_id: "mv-kf" },
  empty_state: null,
  candidates_newest_first: true,
  read_error_keeps_known_selection: true,
};

function emptyPage(ownerId: string, purpose: string) {
  return {
    project_id: "p1",
    owner_kind: "BEAT",
    owner_id: ownerId,
    purpose,
    edition_id: null,
    candidates: [],
    counts: { REFERENCE: 0, KEYFRAME: 0, VISUAL: 0 },
    active_selection: null,
    empty_state: "NO_CANDIDATES_YET",
    candidates_newest_first: true,
    read_error_keeps_known_selection: true,
  };
}

function beatsResponse(beats: ExplainerBeat[]) {
  return {
    video_id: "v1",
    beats,
    render_type_counts: { I2V: 1, INFOGRAPHIC: 1 },
    actual_render_type_counts: { I2V: 1 },
    planned_and_actual_reported_separately: true as const,
  };
}

const STATUSES: ExplainerStepStatusMap = {
  script: "DONE",
  assets: "NOT_STARTED",
  audio: "NOT_STARTED",
  storyboard: "DONE",
  clips: "NEEDS_SELECTION",
  review: "NOT_STARTED",
};

/** The page alone, plus the shared bar so its registration contract is visible. */
function renderClips(path = "/explainers/p1/clips?beat=b2") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/explainers/:projectId/clips"
            element={
              <ExplainerActionBarProvider>
                <ExplainerClipsPage />
                <ExplainerStepActionBar projectId="p1" activePage="clips" statuses={STATUSES} />
              </ExplainerActionBarProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function ownerPage(ownerId: string, purpose: string) {
  if (purpose === "KEYFRAME") return ownerId === "b2" ? KEYFRAME_PAGE : emptyPage(ownerId, "KEYFRAME");
  return ownerId === "b1" ? emptyPage(ownerId, "VISUAL") : VISUAL_PAGE;
}

beforeEach(() => {
  vi.clearAllMocks();
  clearMotionDraftCache();
  profileReady = true;
  profileInputSlots = ["FIRST_FRAME"];
  vi.mocked(api.getExplainerOverview).mockResolvedValue({
    capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
  } as never);
  vi.mocked(api.listExplainerBeats).mockResolvedValue(beatsResponse([BEAT_LEGACY_STILL, BEAT_MOTION]) as never);
  vi.mocked(api.listExplainerEditions).mockResolvedValue({ video_id: "v1", editions: [{ id: "e1" }] } as never);
  vi.mocked(api.listExplainerOwnerCandidates).mockImplementation((async (
    _projectId: string,
    _kind: string,
    ownerId: string,
    filter?: { purpose?: string },
  ) => ownerPage(ownerId, filter?.purpose ?? "VISUAL")) as never);
  vi.mocked(api.selectExplainerBeatCandidate).mockResolvedValue({ selection_id: "sel-2", degraded: false });
  vi.mocked(api.getExplainerBeatImpact).mockResolvedValue({
    beat_id: "b2",
    locked_by_human: false,
    affected_edition_ids: ["e1"],
    affected_edition_count: 1,
    invalidates: ["BEAT_SELECTION", "COMPOSITION_REVISION"],
    preserves: ["FACT_LEDGER"],
    recorded_dependencies: [],
    reuses_successful_products: "only products whose hashes and encoding conditions still match",
    would_require_full_redraw: false,
  });
});

afterEach(() => {
  cleanup();
});

describe("clip mode and candidate labels", () => {
  it("keeps AI 动态, 图形动画 and 上传素材 apart and reports a retired value as outdated", () => {
    expect(clipModeLabel("I2V")).toBe("AI 动态（真实图生视频）");
    expect(clipModeLabel("LICENSED_MEDIA")).toBe("上传素材");
    expect(clipModeLabel("INFOGRAPHIC")).toBe("图形动画（信息图）");
    // The three labels are distinct: nothing is merged into AI 动态.
    expect(new Set([clipModeLabel("I2V"), clipModeLabel("LICENSED_MEDIA"), clipModeLabel("INFOGRAPHIC")]).size).toBe(3);
    // A stale 静图推拉 value in the database is an outdated plan, never a still label.
    expect(clipModeLabel("STILL_MOTION")).toBe("计划方式已停用");
    expect(clipModeLabel("PARALLAX")).toBe("计划方式已停用");
    expect(clipModeLabel("IMAGE_MOTION")).toBe("计划方式已停用");
    expect(clipModeLabel("STILL_MOTION")).not.toContain("静图");
    expect(clipModeLabel(null)).toBe("尚未决定");
  });

  it("only counts a clip as usable when it is a real generated video", () => {
    // A real AI 图生视频 record with a video product is a usable clip.
    expect(clipIsRealVideo(BEAT_MOTION)).toBe(true);
    // A legacy 静图推拉 record is not: it has to be regenerated as AI 动态.
    expect(clipIsRealVideo(BEAT_LEGACY_STILL)).toBe(false);
    // No adopted VISUAL candidate at all is 尚未生成, never a clip.
    expect(clipIsRealVideo({ ...BEAT_MOTION, active_selection: null, candidates: [] })).toBe(false);
  });

  it("never invents a candidate status", () => {
    expect(clipCandidateStatusLabel("READY")).toBe("可用");
    expect(clipCandidateStatusLabel("FAILED")).toBe("生成失败");
    expect(clipCandidateStatusLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
});

describe("clips page", () => {
  it("plays the adopted clip and shows every candidate's real media", async () => {
    renderClips();
    await screen.findByRole("button", { name: "预览候选 1" });
    expect(screen.getByText("视频片段")).toBeTruthy();
    // Adopted I2V clip is a real video behind the proxy policy.
    const video = document.querySelector("video");
    expect(video?.getAttribute("src")).toBe("/api/v1/media-versions/mv-adopted/proxy");
    expect(video?.getAttribute("data-original-src")).toBe("/api/v1/media-versions/mv-adopted/content");
    expect(video?.getAttribute("preload")).toBe("none");
    // Candidate thumbnails come from the media-version thumbnail endpoint.
    const thumbs = Array.from(document.querySelectorAll("img")).map((image) => image.getAttribute("src") ?? "");
    expect(thumbs.some((src) => src.includes("/api/v1/media-versions/mv-new/thumbnail"))).toBe(true);
    expect(screen.getAllByText(/AI 动态/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("当前采用").length).toBeGreaterThan(0);
  });

  it("keeps 预览候选 separate from 采用", async () => {
    renderClips();
    // 补齐缺失片段 (not 采用) is the step's primary action while a clip is missing.
    await waitFor(() => expect(screen.getByRole("button", { name: /补齐缺失片段/ })).toBeTruthy());

    const previewButton = await screen.findByRole("button", { name: "预览候选 2" });
    fireEvent.click(previewButton);
    expect(previewButton.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/正在预览：候选 2（未采用）/)).toBeTruthy();
    // Previewing never writes an adoption.
    expect(api.selectExplainerBeatCandidate).not.toHaveBeenCalled();
    const previewVideo = document.querySelector("video");
    expect(previewVideo?.getAttribute("src")).toBe("/api/v1/media-versions/mv-new/proxy");

    const adopt = screen.getByRole("button", { name: "采用" });
    await waitFor(() => expect(adopt.hasAttribute("disabled")).toBe(false));
    fireEvent.click(adopt);
    await waitFor(() => expect(api.selectExplainerBeatCandidate).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.selectExplainerBeatCandidate).mock.calls[0][2]).toMatchObject({
      candidate_id: "c-new",
      lock: false,
      actor: null,
      purpose: "VISUAL",
    });
  });

  it("offers 采用并锁定 as a separate human-authority action", async () => {
    renderClips();
    fireEvent.click(await screen.findByRole("button", { name: "预览候选 2" }));
    fireEvent.click(screen.getByRole("button", { name: "采用并锁定" }));
    await waitFor(() => expect(api.selectExplainerBeatCandidate).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.selectExplainerBeatCandidate).mock.calls[0][2]).toMatchObject({
      candidate_id: "c-new",
      lock: true,
      actor: "local-user",
    });
  });

  it("shows the real server impact report instead of guessing", async () => {
    renderClips();
    fireEvent.click(await screen.findByRole("button", { name: "查看更换影响" }));
    await waitFor(() => expect(screen.getByLabelText("更换影响")).toBeTruthy());
    expect(screen.getByText("受影响输出版本")).toBeTruthy();
    expect(screen.getByText("BEAT_SELECTION、COMPOSITION_REVISION")).toBeTruthy();
    expect(screen.getByText("FACT_LEDGER")).toBeTruthy();
  });

  it("renders empty, interface error and not-configured as three different states", async () => {
    // Not configured: capability probe never ran.
    vi.mocked(api.getExplainerOverview).mockResolvedValue({
      capability_snapshot: { probed: false, capabilities: [], unknown_count: 0, unavailable_count: 0 },
    } as never);
    const notConfigured = renderClips();
    await waitFor(() => expect(screen.getByText("尚未接入能力探查")).toBeTruthy());
    expect(screen.getByRole("link", { name: "前往能力与模型" })).toBeTruthy();
    notConfigured.unmount();

    // Empty: the workspace really has no beats yet.
    vi.mocked(api.getExplainerOverview).mockResolvedValue({
      capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
    } as never);
    vi.mocked(api.listExplainerBeats).mockResolvedValue(beatsResponse([]) as never);
    const empty = renderClips();
    await waitFor(() => expect(screen.getByText("还没有画面段")).toBeTruthy());
    expect(screen.getByText(/片段来自第 4 步的分镜计划/)).toBeTruthy();
    empty.unmount();

    // Interface error: a failed read must never be dressed up as "no material".
    vi.mocked(api.listExplainerBeats).mockRejectedValue(new Error("读取超时"));
    renderClips();
    await waitFor(() => expect(screen.getByText("无法载入片段计划")).toBeTruthy());
    expect(screen.getByText(/读取超时/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新读取" })).toBeTruthy();
  });

  it("keeps a failed candidate read apart from 'this beat has no candidates'", async () => {
    vi.mocked(api.listExplainerOwnerCandidates).mockImplementation((async (
      _projectId: string,
      _kind: string,
      ownerId: string,
      filter?: { purpose?: string },
    ) => {
      if ((filter?.purpose ?? "VISUAL") === "KEYFRAME") return ownerPage(ownerId, "KEYFRAME");
      throw new Error("候选接口 500");
    }) as never);
    renderClips();
    await waitFor(() => expect(screen.getByText(/候选读取失败/)).toBeTruthy());
    expect(screen.getByText(/这不表示该镜头没有候选/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新读取" })).toBeTruthy();
    expect(screen.queryByText(/还没有片段候选/)).toBeNull();
  });

  it("reports a legacy 静图推拉 beat as 尚未生成 instead of showing a still-motion clip", async () => {
    renderClips("/explainers/p1/clips?beat=b1");
    await waitFor(() => expect(screen.getByText(/这一段还没有可播放的片段或候选/)).toBeTruthy());
    // The centre stage holds no media at all: no fake frame for a missing clip.
    expect(screen.queryByAltText(/镜头 BEAT_001 预览/)).toBeNull();
    expect(screen.getByText(/不会显示占位画面假装已有片段/)).toBeTruthy();
    // The removed mode is never offered or shown as a still label.
    expect(screen.queryByText("静图推拉")).toBeNull();
    expect(screen.queryByRole("button", { name: /静图推拉/ })).toBeNull();
    expect(screen.getAllByText("计划方式已停用").length).toBeGreaterThan(0);
    expect(screen.getAllByText("尚未生成").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/都必须来自真实 AI 图生视频/).length).toBeGreaterThan(0);
  });
});

/**
 * 第 5 步 视频片段: the §B6.1/§B6.2 contract.
 *
 * The clips step must (a) reuse the step-4 workspace instead of a second card
 * implementation, (b) produce every clip with real AI 图生视频 — the removed
 * 静图推拉 is never offered, never labelled and never counted as a clip, (c) read
 * every parameter (model, durations, end-frame support, motion text) from real
 * server data, and (d) keep 生成 / 再生成 / 重试失败 / 重新载入 / 补齐缺失片段 apart.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../generated/api", () => ({
  batchRetryJobs: vi.fn(),
  getExplainerBeatImpact: vi.fn(),
  getExplainerOverview: vi.fn(),
  listExplainerBeats: vi.fn(),
  listExplainerEditions: vi.fn(),
  listExplainerOwnerCandidates: vi.fn(),
  planExplainerBeatGeneration: vi.fn(),
  requestJson: vi.fn(),
  retryJob: vi.fn(),
  selectExplainerBeatCandidate: vi.fn(),
  submitExplainerBeatGeneration: vi.fn(),
  submitExplainerVisualGeneration: vi.fn(),
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
import { ExplainerClipsPage, clearMotionDraftCache, defaultMotionText } from "./ClipsPage";
import { shotTypeShortLabel } from "./StoryboardPage";

const MOTION_BEAT: ExplainerBeat = {
  id: "b2",
  code: "BEAT_002",
  ordinal: 1,
  render_type: "I2V",
  visual_intent: "观察窗中的光斑缓慢移动",
  prompt_intent: "人物缓慢转头，镜头轻微向前移动",
  must_be_motion: true,
  preferred_duration_ms: 3000,
  visual_factuality: "RECONSTRUCTION",
  status: "PLANNED",
  actual_fallback_type: null,
  fallback_reason: null,
  locked_by_human: false,
  active_selection: { id: "sel-1", purpose: "VISUAL", candidate_id: "c-adopted", media_version_id: "mv-adopted" },
  narration_links: [{ canonical_segment_id: "seg_002", display_text: "他盯着观察窗里的光斑，一句话也没说。" }],
  candidates: [
    { id: "c-adopted", purpose: "VISUAL", variant_no: 1, status: "READY", media_version_id: "mv-adopted", adopted: true },
    { id: "c-kf", purpose: "KEYFRAME", variant_no: 1, status: "READY", media_version_id: "mv-kf", adopted: true },
  ],
};

/** A legacy row: the database still holds the removed 静图推拉 render type. */
const LEGACY_STILL_BEAT: ExplainerBeat = {
  ...MOTION_BEAT,
  id: "b1",
  code: "BEAT_001",
  ordinal: 0,
  render_type: "STILL_MOTION",
  must_be_motion: false,
  active_selection: { id: "sel-still", purpose: "VISUAL", candidate_id: "c-still", media_version_id: "mv-still" },
  narration_links: [{ canonical_segment_id: "seg_001", display_text: "灯塔的光斑缓缓移动。" }],
  candidates: [{ id: "c-still", purpose: "VISUAL", variant_no: 1, status: "READY", media_version_id: "mv-still", adopted: true }],
} as ExplainerBeat;

const FAILED_BEAT: ExplainerBeat = {
  ...MOTION_BEAT,
  id: "b3",
  code: "BEAT_003",
  ordinal: 2,
  active_selection: null,
  candidates: [
    { id: "c-failed", purpose: "VISUAL", variant_no: 3, status: "FAILED", media_version_id: null, adopted: false, job_id: "job-failed" },
  ],
};

/** An AI-motion shot that still has no adopted clip — the 补齐缺失片段 case. */
const NEEDS_CLIP_BEAT: ExplainerBeat = {
  ...MOTION_BEAT,
  id: "b4",
  code: "BEAT_004",
  ordinal: 3,
  active_selection: null,
  candidates: [{ id: "c-kf", purpose: "KEYFRAME", variant_no: 1, status: "READY", media_version_id: "mv-kf", adopted: true }],
};

function videoRow(overrides: Record<string, unknown>) {
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
    render_type_planned: "I2V",
    render_type_actual: "I2V",
    ...overrides,
  };
}

const MOTION_PAGE = {
  project_id: "p1",
  owner_kind: "BEAT",
  owner_id: "b2",
  purpose: "VISUAL",
  edition_id: null,
  candidates: [
    videoRow({
      id: "c-adopted",
      purpose: "VISUAL",
      variant_no: 1,
      media_version_id: "mv-adopted",
      selected: true,
      adopted: true,
      thumbnail_url: "/api/v1/media-versions/mv-adopted/content",
      playback_url: "/api/v1/media-versions/mv-adopted/proxy",
      job_id: "job-1",
      seed: 11,
    }),
  ],
  counts: { REFERENCE: 0, KEYFRAME: 0, VISUAL: 1 },
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
    videoRow({
      id: "c-kf",
      purpose: "KEYFRAME",
      variant_no: 1,
      media_kind: "IMAGE",
      media_version_id: "mv-kf",
      render_type_planned: "I2V",
      selected: true,
      adopted: true,
      thumbnail_url: "/api/v1/media-versions/mv-kf/content",
    }),
  ],
  counts: { REFERENCE: 0, KEYFRAME: 1, VISUAL: 0 },
  active_selection: { id: "sel-kf", purpose: "KEYFRAME", candidate_id: "c-kf", media_version_id: "mv-kf" },
  empty_state: null,
  candidates_newest_first: true,
  read_error_keeps_known_selection: true,
};

const VIDEO_PLAN = {
  status: "EXECUTABLE",
  owner_kind: "BEAT",
  owner_id: "b2",
  purpose: "VISUAL",
  mode: "IMAGE_TO_VIDEO",
  candidate_count: 1,
  plan_hash: "c".repeat(64),
  candidate_seeds: [900001],
  execution_profile_version_id: "prof-video",
  profile_title: "已验证图生视频 v1",
  expected_resolution_hash: "d".repeat(64),
  media_kind: "VIDEO",
  render_type_planned: "I2V",
  render_type_actual: null,
  prompt: "人物缓慢转头",
  negative_prompt: null,
  reference_capacity: { max_image_references: 1, resolved_image_references: 1 },
  resolved_references: [],
  frozen_inputs: {},
  blockers: [],
  budget: { remaining_video_candidates: 4 },
  planned_duration_ms: 3000,
  allowed_durations_ms: [3000, 6000],
};

const STATUSES: ExplainerStepStatusMap = {
  script: "DONE",
  assets: "DONE",
  audio: "DONE",
  storyboard: "DONE",
  clips: "NEEDS_SELECTION",
  review: "NOT_STARTED",
};

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function beatsResponse(beats: ExplainerBeat[]) {
  return {
    video_id: "v1",
    beats,
    render_type_counts: {},
    actual_render_type_counts: {},
    planned_and_actual_reported_separately: true as const,
  };
}

/**
 * Click the page-level 生成缺失片段 action.  The button is disabled until the page has
 * really read the shot plan, so the click waits for the real enabled state.
 */
async function startGenerateMissingClips() {
  const button = await screen.findByRole("button", { name: "生成缺失片段" });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  fireEvent.click(button);
  return button;
}

/**
 * The picture-stage receipt.  `ACCEPTED` means a real Job was queued — it never means
 * the clips exist, so the page may only report the job and the refresh.
 */
function visualGenerationReceipt(overrides: Record<string, unknown> = {}) {
  return {
    status: "ACCEPTED",
    stage_code: "VISUAL_GENERATION",
    operation_id: "op-visual-1",
    job_id: "job-clips-77",
    job_state: "QUEUED",
    subject: { kind: "VIDEO", id: "v1", snapshot_hash: "b".repeat(64) },
    frozen_plan: { stage_code: "VISUAL_GENERATION" },
    idempotent_replay: false,
    durable_intent_persisted: true,
    would_create_jobs: true,
    ...overrides,
  };
}

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
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const LEGACY_STILL_PAGE = {
  project_id: "p1",
  owner_kind: "BEAT",
  owner_id: "b1",
  purpose: "VISUAL",
  edition_id: null,
  candidates: [
    videoRow({
      id: "c-still",
      owner_id: "b1",
      purpose: "VISUAL",
      variant_no: 1,
      media_version_id: "mv-still",
      render_type_planned: "STILL_MOTION",
      render_type_actual: "STILL_MOTION",
      selected: true,
      adopted: true,
      thumbnail_url: "/api/v1/media-versions/mv-still/content",
      playback_url: "/api/v1/media-versions/mv-still/proxy",
      job_id: "job-still",
      seed: 21,
    }),
  ],
  counts: { REFERENCE: 0, KEYFRAME: 0, VISUAL: 1 },
  active_selection: { id: "sel-still", purpose: "VISUAL", candidate_id: "c-still", media_version_id: "mv-still" },
  empty_state: null,
  candidates_newest_first: true,
  read_error_keeps_known_selection: true,
};

const FAILED_PAGE = {
  project_id: "p1",
  owner_kind: "BEAT",
  owner_id: "b3",
  purpose: "VISUAL",
  edition_id: null,
  candidates: [
    videoRow({
      id: "c-failed",
      owner_id: "b3",
      purpose: "VISUAL",
      variant_no: 3,
      status: "FAILED",
      media_version_id: null,
      job_id: "job-failed",
      retryable: true,
      render_type_planned: "I2V",
      render_type_actual: null,
      error_message: "ComfyUI 返回空结果",
      error_code: "PIPELINE_OUTPUT_MISSING",
    }),
  ],
  counts: { REFERENCE: 0, KEYFRAME: 0, VISUAL: 1 },
  active_selection: null,
  empty_state: null,
  candidates_newest_first: true,
  read_error_keeps_known_selection: true,
};

function ownerPage(ownerId: string, purpose: string) {
  if (purpose === "KEYFRAME") {
    return ownerId === "b2"
      ? KEYFRAME_PAGE
      : { ...KEYFRAME_PAGE, owner_id: ownerId, candidates: [], active_selection: null };
  }
  if (ownerId === "b1") return LEGACY_STILL_PAGE;
  if (ownerId === "b3") return FAILED_PAGE;
  if (ownerId === "b2") return MOTION_PAGE;
  return { ...MOTION_PAGE, owner_id: ownerId, candidates: [], active_selection: null };
}

describe("step 5 clips implementation (B6)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearMotionDraftCache();
    profileReady = true;
    profileInputSlots = ["FIRST_FRAME"];
    vi.mocked(api.getExplainerOverview).mockResolvedValue({
      capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
    } as never);
    vi.mocked(api.listExplainerBeats).mockResolvedValue(beatsResponse([LEGACY_STILL_BEAT, MOTION_BEAT]) as never);
    vi.mocked(api.listExplainerEditions).mockResolvedValue({ video_id: "v1", editions: [{ id: "e1" }] } as never);
    vi.mocked(api.listExplainerOwnerCandidates).mockImplementation((async (
      _projectId: string,
      _kind: string,
      ownerId: string,
      filter?: { purpose?: string },
    ) => ownerPage(ownerId, filter?.purpose ?? "VISUAL")) as never);
    vi.mocked(api.planExplainerBeatGeneration).mockResolvedValue(VIDEO_PLAN as never);
    vi.mocked(api.submitExplainerBeatGeneration).mockResolvedValue({
      operation_id: "op-video",
      status: "ACCEPTED",
      requested_count: 1,
      accepted_count: 1,
      items: [
        {
          ordinal: 0,
          submission_status: "ACCEPTED",
          candidate_id: "cand-video",
          candidate_status: "PENDING",
          job_id: "job-video",
          job_state: "QUEUED",
          seed: 900001,
          error: null,
        },
      ],
      idempotent_replay: false,
    } as never);
    vi.mocked(api.batchRetryJobs).mockResolvedValue({ retried_count: 1, jobs: [] } as never);
    vi.mocked(api.retryJob).mockResolvedValue({ job: { id: "job-failed", status: "QUEUED" } } as never);
    vi.mocked(api.selectExplainerBeatCandidate).mockResolvedValue({ degraded: false } as never);
    vi.mocked(api.getExplainerBeatImpact).mockResolvedValue({ affected_edition_count: 1 } as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("labels each shot with the short type and keeps the adopted source image beside the video", async () => {
    renderClips();
    const list = await screen.findByRole("button", { name: "镜头 BEAT_002" });
    expect(list.textContent).toContain("AI 动态");
    // A legacy 静图推拉 row is an outdated plan, not a clip type.
    expect(screen.getByRole("button", { name: "镜头 BEAT_001" }).textContent).toContain("计划方式已停用");
    // The centre stage plays the real video and shows the adopted still next to it.
    expect(document.querySelector("video")?.getAttribute("src")).toBe("/api/v1/media-versions/mv-adopted/proxy");
    const still = screen.getByAltText("镜头 BEAT_002 已采用首帧");
    expect(still.getAttribute("src")).toContain("/api/v1/media-versions/mv-kf/thumbnail");
    // 图形动画 / 已有视频 are their own labels, never merged into AI 动态.
    expect(shotTypeShortLabel("INFOGRAPHIC")).toBe("图形动画");
    expect(shotTypeShortLabel("LICENSED_MEDIA")).toBe("已有视频");
    expect(shotTypeShortLabel("I2V")).toBe("AI 动态（图生视频）");
    expect(new Set([
      shotTypeShortLabel("I2V"),
      shotTypeShortLabel("INFOGRAPHIC"),
      shotTypeShortLabel("LICENSED_MEDIA"),
    ]).size).toBe(3);
  });

  it("never offers 静图推拉, never labels a legacy record as a still, and reports it as not generated", async () => {
    renderClips("/explainers/p1/clips?beat=b1");
    // No control, button or label offers the removed deterministic composition.
    expect(screen.queryByRole("button", { name: /静图推拉/ })).toBeNull();
    expect(screen.queryByLabelText(/静图推拉/)).toBeNull();
    expect(screen.queryByText("静图推拉")).toBeNull();
    expect(screen.queryByText(/本片使用静图推拉/)).toBeNull();
    // The legacy plan is surfaced as outdated and the shot as 尚未生成.
    await waitFor(() => expect(screen.getAllByText("计划方式已停用").length).toBeGreaterThan(0));
    expect(screen.getAllByText("尚未生成").length).toBeGreaterThan(0);
    // The truth is stated (state notice + panel notice both say it).
    expect(screen.getAllByText(/都必须来自真实 AI 图生视频/).length).toBeGreaterThan(0);
    // The retired record is not counted as a ready clip: only the real I2V shot is.
    expect(screen.getByText(/1 \/ 2 已就绪/)).toBeTruthy();
  });

  it("offers no still fallback when the video model is unconfigured", async () => {
    profileReady = false;
    renderClips();
    await screen.findByRole("button", { name: "镜头 BEAT_002" });
    const notice = await screen.findByText(/视频模型未配置（缺少 VIDEO_I2V 能力）/);
    expect(notice).toBeTruthy();
    expect(notice.textContent).toContain("静图推拉已从产品中移除");
    expect(screen.getByRole("link", { name: "配置模型" })).toBeTruthy();
    // There is no local-composition exit any more: only the real capability is offered.
    expect(screen.queryByRole("button", { name: /静图推拉/ })).toBeNull();
    const mode = screen.getByLabelText(/生成方式/) as HTMLSelectElement;
    expect(Array.from(mode.options).map((option) => option.value)).toEqual(["IMAGE_TO_VIDEO"]);
    const claims = screen.queryAllByText(/视频已生成/);
    expect(claims.map((node) => node.textContent ?? "").every((text) => /不会|尚未/.test(text))).toBe(true);
  });

  it("takes 片段时长 from the server's allowed durations instead of hard-coded seconds", async () => {
    renderClips();
    // Wait for the real beats read: the draw needs a working object.
    await screen.findByRole("button", { name: "镜头 BEAT_002" });
    fireEvent.click(await screen.findByRole("button", { name: /生成视频（1 段）|再生成 1 段/ }));
    await screen.findByText("已验证图生视频 v1");
    const select = (await screen.findByLabelText(/片段时长/)) as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll("option")).map((option) => option.textContent);
    expect(options).toEqual(["00:03", "00:06"]);
    expect(options.join(" ")).not.toContain("5 秒");
  });

  it("only shows 尾帧 for a Profile that declares the slot", async () => {
    profileInputSlots = ["FIRST_FRAME"];
    const withoutTail = renderClips();
    expect(await screen.findByText(/未声明首尾帧输入槽位/)).toBeTruthy();
    expect(screen.queryByRole("group", { name: /尾帧/ })).toBeNull();
    withoutTail.unmount();

    profileInputSlots = ["FIRST_FRAME", "END_FRAME"];
    renderClips();
    await screen.findByRole("button", { name: "镜头 BEAT_002" });
    const tail = await screen.findByRole("group", { name: /尾帧/ });
    // The gap stays visible: the frozen command has no tail-frame field.
    expect(tail.textContent).toContain("没有尾帧字段");
  });

  it("defaults 运动描述 from the storyboard plan and never from the narration", async () => {
    renderClips();
    await screen.findByRole("button", { name: "镜头 BEAT_002" });
    const motion = (await screen.findByLabelText("运动描述")) as HTMLTextAreaElement;
    expect(defaultMotionText(MOTION_BEAT)).toBe("人物缓慢转头，镜头轻微向前移动");
    expect(motion.value).toBe("人物缓慢转头，镜头轻微向前移动");
    expect(motion.value).not.toBe("他盯着观察窗里的光斑，一句话也没说。");
    // The narration is shown as a *different* fact, not as the motion prompt.
    expect(screen.getByText(/不是讲稿原文/)).toBeTruthy();
  });

  it("runs 生成视频 → plan → submit with the frozen plan and reports 已排队, never 生成完成", async () => {
    renderClips();
    fireEvent.click(await screen.findByRole("button", { name: "再生成 1 段" }));
    await waitFor(() => expect(api.planExplainerBeatGeneration).toHaveBeenCalledTimes(1));
    const planBody = vi.mocked(api.planExplainerBeatGeneration).mock.calls[0][2] as Record<string, unknown>;
    expect(planBody).toMatchObject({
      purpose: "VISUAL",
      mode: "IMAGE_TO_VIDEO",
      input_keyframe_selection_id: "sel-kf",
    });
    const submit = screen.getByRole("button", { name: /提交生成（本次 1 段）/ });
    await waitFor(() => expect(submit.hasAttribute("disabled")).toBe(false));
    fireEvent.click(submit);
    await waitFor(() => expect(api.submitExplainerBeatGeneration).toHaveBeenCalledTimes(1));
    const [, , submitBody] = vi.mocked(api.submitExplainerBeatGeneration).mock.calls[0];
    expect(submitBody).toMatchObject({ expected_plan_hash: "c".repeat(64), candidate_seeds: [900001] });
    expect(await screen.findByText(/第 1 段已排队/)).toBeTruthy();
    const claims = screen.queryAllByText(/生成完成/);
    expect(claims.every((node) => node.textContent?.includes("不等于"))).toBe(true);
  });

  it("retries only the failed clips and never starts a new draw for it", async () => {
    vi.mocked(api.listExplainerBeats).mockResolvedValue(beatsResponse([MOTION_BEAT, FAILED_BEAT]) as never);
    renderClips("/explainers/p1/clips?beat=b3");
    const retry = await screen.findByRole("button", { name: /仅重试失败片段（1）/ });
    fireEvent.click(retry);
    await waitFor(() => expect(api.batchRetryJobs).toHaveBeenCalledWith({ job_ids: ["job-failed"] }));
    expect(api.planExplainerBeatGeneration).not.toHaveBeenCalled();
    expect(api.submitExplainerBeatGeneration).not.toHaveBeenCalled();
  });

  it("keeps the motion draft when 更换源图 jumps to step 4 for that beat", async () => {
    const view = renderClips();
    await screen.findByRole("button", { name: "镜头 BEAT_002" });
    const motion = (await screen.findByLabelText("运动描述")) as HTMLTextAreaElement;
    fireEvent.change(motion, { target: { value: "镜头缓慢拉远，人物不动" } });
    fireEvent.click(await screen.findByRole("button", { name: "更换源图" }));
    // The dialog-free jump keeps the object locator.
    expect(screen.getByTestId("location").textContent).toContain("/explainers/p1/storyboard?beat=b2");
    // Coming back restores the unsubmitted draft instead of the server default.
    view.unmount();
    renderClips();
    await screen.findByRole("button", { name: "镜头 BEAT_002" });
    const restored = (await screen.findByLabelText("运动描述")) as HTMLTextAreaElement;
    await waitFor(() => expect(restored.value).toBe("镜头缓慢拉远，人物不动"));
  });

  it("reports the missing adopted keyframe when 补齐缺失片段 cannot submit AI video", async () => {
    vi.mocked(api.listExplainerBeats).mockResolvedValue(beatsResponse([NEEDS_CLIP_BEAT]) as never);
    vi.mocked(api.listExplainerOwnerCandidates).mockImplementation((async (
      _projectId: string,
      _kind: string,
      ownerId: string,
      filter?: { purpose?: string },
    ) => {
      if ((filter?.purpose ?? "VISUAL") === "KEYFRAME") {
        return { ...KEYFRAME_PAGE, owner_id: ownerId, candidates: [], active_selection: null };
      }
      return { ...MOTION_PAGE, owner_id: ownerId, candidates: [], active_selection: null };
    }) as never);
    renderClips("/explainers/p1/clips?beat=b4");
    await screen.findByRole("button", { name: "镜头 BEAT_004" });
    fireEvent.click(await screen.findByRole("button", { name: /补齐缺失片段/ }));
    expect((await screen.findAllByText(/缺少已采用首帧（KEYFRAME selection），未提交 AI 动态任务/)).length).toBeGreaterThan(0);
    expect(api.submitExplainerBeatGeneration).not.toHaveBeenCalled();
  });

  it("exposes the §B6.1 controls and §B6.2 buttons with their real option sets", async () => {
    renderClips();
    await screen.findByRole("button", { name: "镜头 BEAT_002" });

    const presentation = screen.getByLabelText(/最终片段方式/) as HTMLSelectElement;
    // Only the three remaining routes: real AI 图生视频, 图形动画 and 已有视频.
    expect(Array.from(presentation.options).map((option) => option.value)).toEqual([
      "AI_VIDEO",
      "GRAPHIC_ANIMATION",
      "SOURCE_VIDEO",
    ]);
    expect(Array.from(presentation.options).map((option) => option.textContent)).toEqual([
      "AI 动态（图生视频）",
      "图形动画 / 信息图",
      "已有视频 / 授权素材",
    ]);
    const counts = screen.getByLabelText(/每次候选数/) as HTMLSelectElement;
    expect(Array.from(counts.options).map((option) => option.value)).toEqual(["1", "2"]);
    expect(counts.value).toBe("1");
    // The removed 静图推拉 has no parameter control at all, in any state.
    expect(screen.queryByLabelText(/静图推拉/)).toBeNull();
    expect(screen.queryByRole("option", { name: /静图推拉/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /静图推拉/ })).toBeNull();
    expect(screen.getByText("视频模型")).toBeTruthy();
    const camera = screen.getByLabelText(/镜头运动/) as HTMLSelectElement;
    expect(Array.from(camera.options).map((option) => option.textContent)).toEqual([
      "自动",
      "固定",
      "缓慢推进",
      "缓慢拉远",
      "横移",
    ]);
    expect(screen.getByText(/高级设置：负向提示词/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "更换源图" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "播放" })).toBeTruthy();

    // Switching to a non-AI route never creates a still-motion control and never
    // changes the candidate-count choices either.
    //
    // This clip is planned as 必须运动, so the two non-AI routes are refused (and
    // disabled) rather than silently degrading the plan.
    expect(Array.from(presentation.options).filter((option) => option.value !== "AI_VIDEO").every((option) => option.disabled)).toBe(true);
    fireEvent.change(presentation, { target: { value: "GRAPHIC_ANIMATION" } });
    expect((screen.getByLabelText(/最终片段方式/) as HTMLSelectElement).value).toBe("AI_VIDEO");
    expect(screen.queryByLabelText(/静图推拉/)).toBeNull();
    const countsAfter = screen.getByLabelText(/每次候选数/) as HTMLSelectElement;
    expect(Array.from(countsAfter.options).map((option) => option.value)).toEqual(["1", "2"]);

    // On a clip that is not required to move, the non-AI routes are selectable — and
    // they are still not a model draw.
    fireEvent.click(screen.getByRole("button", { name: "镜头 BEAT_001" }));
    const otherPresentation = (await screen.findByLabelText(/最终片段方式/)) as HTMLSelectElement;
    expect(otherPresentation.value).toBe("AI_VIDEO");
    fireEvent.change(otherPresentation, { target: { value: "GRAPHIC_ANIMATION" } });
    expect((screen.getByLabelText(/最终片段方式/) as HTMLSelectElement).value).toBe("GRAPHIC_ANIMATION");
    expect(await screen.findByText(/图形动画当前没有声明可执行的生成命令/)).toBeTruthy();
    expect(screen.queryByLabelText(/静图推拉/)).toBeNull();
  });

  it("sends only the real 图生视频 command, never a removed still_motion field", async () => {
    renderClips();
    fireEvent.click(await screen.findByRole("button", { name: "再生成 1 段" }));
    await waitFor(() => expect(api.planExplainerBeatGeneration).toHaveBeenCalledTimes(1));
    const planBody = vi.mocked(api.planExplainerBeatGeneration).mock.calls[0][2] as Record<string, unknown>;
    expect(planBody.mode).toBe("IMAGE_TO_VIDEO");
    expect("still_motion" in planBody).toBe(false);
  });

  /* ---------- 生成缺失片段: the whole film's picture stage ---------- */

  it("runs 生成缺失片段 for the whole film with an empty beat_ids and an Idempotency-Key header", async () => {
    vi.mocked(api.submitExplainerVisualGeneration).mockResolvedValue(visualGenerationReceipt() as never);
    renderClips();
    const button = await startGenerateMissingClips();
    await waitFor(() => expect(api.submitExplainerVisualGeneration).toHaveBeenCalledTimes(1));

    const [projectId, payload, idempotencyKey] =
      vi.mocked(api.submitExplainerVisualGeneration).mock.calls[0];
    expect(projectId).toBe("p1");
    // beat_ids: [] is the whole film; the page's current edition travels with it.
    expect(payload).toEqual({ edition_id: "e1", beat_ids: [] });
    expect(idempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );

    // Same intent, same key: pressing again replays the command instead of queueing a
    // second job for the same film.
    fireEvent.click(button);
    await waitFor(() => expect(api.submitExplainerVisualGeneration).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.submitExplainerVisualGeneration).mock.calls[1][2]).toBe(idempotencyKey);
  });

  it("disables 生成缺失片段 while the submit is in flight", async () => {
    let settle: (value: unknown) => void = () => undefined;
    vi.mocked(api.submitExplainerVisualGeneration).mockReturnValue(new Promise((resolve) => { settle = resolve; }) as never);
    renderClips();
    const button = await screen.findByRole("button", { name: "生成缺失片段" });
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(true));
    settle(visualGenerationReceipt());
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  });

  it("reports the queued background job on ACCEPTED and never claims the clips were generated", async () => {
    vi.mocked(api.submitExplainerVisualGeneration).mockResolvedValue(visualGenerationReceipt() as never);
    renderClips();
    const button = await screen.findByRole("button", { name: "生成缺失片段" });
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    const beatsReadsBefore = vi.mocked(api.listExplainerBeats).mock.calls.length;
    const candidateReadsBefore = vi.mocked(api.listExplainerOwnerCandidates).mock.calls.length;
    fireEvent.click(button);

    const notice = await screen.findByText(/job-clips-77/);
    expect(notice.getAttribute("role")).toBe("status");
    // A real job id, the background execution and the refresh are all named.
    expect(notice.textContent).toContain("已启动后台任务 job-clips-77");
    expect(notice.textContent).toContain("后台任务");
    expect(notice.textContent).toContain("片段列表");
    expect(notice.textContent).toContain("刷新");
    // …and the receipt never claims a clip exists: 已受理 is not 已生成.
    expect(notice.textContent).not.toContain("已生成");
    expect(screen.queryByText(/片段已生成/)).toBeNull();
    expect(screen.queryByText(/生成完成/)).toBeNull();
    // The receipt invalidates the explainer query keys, so the beat and candidate
    // lists are really re-read instead of showing a stale picture.
    await waitFor(() =>
      expect(vi.mocked(api.listExplainerBeats).mock.calls.length).toBeGreaterThan(beatsReadsBefore),
    );
    await waitFor(() =>
      expect(vi.mocked(api.listExplainerOwnerCandidates).mock.calls.length).toBeGreaterThan(candidateReadsBefore),
    );
  });

  it("shows the backend's blocker message and next_step verbatim when the picture stage is blocked", async () => {
    vi.mocked(api.submitExplainerVisualGeneration).mockResolvedValue(
      visualGenerationReceipt({
        status: "BLOCKED",
        operation_id: null,
        job_id: null,
        job_state: null,
        durable_intent_persisted: false,
        would_create_jobs: false,
        blockers: [
          {
            code: "KEYFRAME_MISSING",
            message: "有 2 个画面段还没有已采用首帧（KEYFRAME selection）",
            next_step: "先在第 4 步为这 2 个画面段采用首帧。",
          },
        ],
      }) as never,
    );
    renderClips();
    await startGenerateMissingClips();

    const notice = await screen.findByText(/有 2 个画面段还没有已采用首帧/);
    expect(notice.getAttribute("role")).toBe("alert");
    // Both sentences are the backend's own words, shown unchanged.
    expect(notice.textContent).toContain("有 2 个画面段还没有已采用首帧（KEYFRAME selection）");
    expect(notice.textContent).toContain("先在第 4 步为这 2 个画面段采用首帧。");
    // No job exists, so the page must not report one.
    expect(screen.queryByText(/已启动后台任务/)).toBeNull();
  });

  it("shows the returned capability blocker when the picture stage is unavailable", async () => {
    vi.mocked(api.submitExplainerVisualGeneration).mockResolvedValue(
      visualGenerationReceipt({
        status: "CAPABILITY_UNAVAILABLE",
        operation_id: null,
        job_id: null,
        job_state: null,
        durable_intent_persisted: false,
        would_create_jobs: false,
        reason: "VIDEO_I2V_UNAVAILABLE",
        blockers: [
          {
            code: "CAPABILITY_UNAVAILABLE",
            message: "本机没有可执行的 VIDEO_I2V Profile",
            next_step: "在系统能力页登记可执行的图生视频 Profile。",
          },
        ],
      }) as never,
    );
    renderClips();
    await startGenerateMissingClips();

    const notice = await screen.findByText(/本机没有可执行的 VIDEO_I2V Profile/);
    expect(notice.getAttribute("role")).toBe("alert");
    expect(notice.textContent).toContain("本机没有可执行的 VIDEO_I2V Profile");
    expect(notice.textContent).toContain("在系统能力页登记可执行的图生视频 Profile。");
    expect(screen.queryByText(/已启动后台任务/)).toBeNull();
  });

  it("disables 生成缺失片段 when every 画面段 already has a real clip", async () => {
    vi.mocked(api.listExplainerBeats).mockResolvedValue(beatsResponse([MOTION_BEAT]) as never);
    renderClips();
    const button = await screen.findByRole("button", { name: "生成缺失片段" });
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(true));
    fireEvent.click(button);
    expect(api.submitExplainerVisualGeneration).not.toHaveBeenCalled();
  });
});

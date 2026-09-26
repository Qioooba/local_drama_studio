/**
 * Step 6 (预览与导出) page tests — design §B7, §B8, §F2.2, §F3.
 *
 * They lock the honesty rules the design makes non-negotiable:
 *
 * * 「已提交」 only ever appears with a real job id; a blocked plan creates nothing,
 * * a new preview keeps the previous film playable and labels it 旧版,
 * * `planExplainerRepairs` alone is reported as a plan, never as 已返工,
 * * 「用现有结果继续」 really calls `continueExplainerCollection` (with an
 *   Idempotency-Key) and renders the real receipt / the real missing-owner error,
 * * problems are understandable sentences with a jump to the correct step *and*
 *   object, while the machine / human / publication records stay in 详情,
 * * a percentage is only shown next to a ratio the server really reported,
 * * 只改字幕样式/音乐 creates no image or TTS task,
 * * 下载成片 exists only next to a playable file.
 *
 * Matchers are plain Vitest assertions; this repository does not register
 * jest-dom matchers globally.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../generated/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../generated/api")>();
  return {
    ...actual,
    getExplainerOverview: vi.fn(),
    getExplainerRun: vi.fn(),
    listExplainerEditions: vi.fn(),
    getExplainerQc: vi.fn(),
    getExplainerNarration: vi.fn(),
    getExplainerSubtitles: vi.fn(),
    listExplainerBeats: vi.fn(),
    listExplainerBeatCandidates: vi.fn(),
    listExplainerAssets: vi.fn(),
    listExplainerEntityCandidates: vi.fn(),
    listSubtitleStyleTemplates: vi.fn(),
    saveSubtitleStyleTemplate: vi.fn(),
    discoverLocalSapiVoices: vi.fn(),
    listCapabilityOptions: vi.fn(),
    startExplainerRender: vi.fn(),
    startExplainerExport: vi.fn(),
    runExplainerCompositionQc: vi.fn(),
    planExplainerRepairs: vi.fn(),
    recordExplainerDecision: vi.fn(),
    preflightExplainerPlan: vi.fn(),
    createExplainerEdition: vi.fn(),
    continueExplainerCollection: vi.fn(),
  };
});

import * as api from "../../generated/api";
import { ExplainerActionBarProvider, ExplainerStepActionBar } from "./ExplainerStepActionBar";
import type { ExplainerStepStatusMap } from "./ExplainerSteps";
import {
  ExplainerReviewPage,
  FACT_PROBLEM_SENTENCE,
  capabilityAvailable,
  collectionSteps,
  currentCueFor,
  problemSentence,
  problemTarget,
  profileDuckingSupported,
  resolutionTierLabel,
  reviewedIntervals,
  runStateWording,
  trustworthyProgress,
} from "./ReviewPage";

const RENDER = {
  id: "render-1",
  media_version_id: "mv-render",
  mime_type: "video/mp4",
  byte_size: 2048,
  duration_ms: 7300,
  frame_count: 183,
  fps_num: 25,
  fps_den: 1,
  playback_url: "/api/v1/media-versions/mv-render/content",
  thumbnail_url: "/api/v1/media-versions/mv-render/thumbnail",
  availability: "PLAYABLE",
  playable: true,
  sha256: "a".repeat(64),
  integrity_status: "VERIFIED",
  status: "SUCCEEDED",
};

const EDITION_WITH_RENDER = {
  id: "e1",
  edition_key: "zh-captioned-169",
  voice_locale: "zh-CN",
  aspect_ratio: "16:9",
  subtitle_mode: "BURNED",
  subtitle_locales_json: ["zh-CN"],
  duration_policy: "NATURAL_NARRATION",
  width: 854,
  height: 480,
  current_render: RENDER,
  composition: { id: "comp-1", revision_no: 3, status: "FROZEN", fps_num: 25, fps_den: 1, total_frames: 183 },
  composition_items: [
    { id: "ci-1", track: "VIDEO", item_kind: "VIDEO_CLIP", ordinal: 0, start_frame: 0, end_frame_exclusive: 100, beat_id: "b8" },
    { id: "ci-2", track: "SUBTITLE", item_kind: "SUBTITLE_CLIP", ordinal: 0, start_frame: 0, end_frame_exclusive: 100 },
  ],
};

const EDITION_WITHOUT_RENDER = { ...EDITION_WITH_RENDER, current_render: null, composition_items: [] };

const BEAT_VIDEO_ISSUE = {
  id: "i1",
  severity: "BLOCKER",
  issue_kind: "MISSING_VIDEO",
  observed: "画面段没有可用视频",
  detector: "media_integrity",
  status: "OPEN",
  start_ms: 4000,
  beat_id: "b8",
  responsible_step_code: "VISUAL_GENERATION",
};
const NARRATION_ISSUE = {
  id: "i2",
  severity: "MAJOR",
  issue_kind: "TTS_FAILED",
  observed: "第三段合成失败",
  detector: "tts_check",
  status: "OPEN",
  start_ms: 1000,
  narration_segment_id: "s3",
  responsible_step_code: "NARRATION_TTS",
};
const FACT_ISSUE_ONE = {
  id: "i3",
  severity: "MINOR",
  issue_kind: "UNVERIFIED_CLAIM",
  observed: "数值待核对",
  detector: "fact_check",
  status: "OPEN",
  responsible_step_code: "FACT_EXTRACT",
};
const FACT_ISSUE_TWO = { ...FACT_ISSUE_ONE, id: "i4" };

const BEATS = {
  video_id: "v1",
  beats: [
    { id: "b8", code: "BEAT_008", ordinal: 7, render_type: "I2V", visual_intent: "光斑移动", must_be_motion: true, preferred_duration_ms: 4000, visual_factuality: "RECONSTRUCTION", status: "PLANNED", actual_fallback_type: null, fallback_reason: null, locked_by_human: false },
  ],
  render_type_counts: {},
  actual_render_type_counts: {},
  planned_and_actual_reported_separately: true as const,
};

const NARRATION_SEGMENTS = {
  video_id: "v1",
  voice_locale: "zh-CN",
  frozen_script_revision_id: "rev-1",
  segments: [
    { id: "s3", canonical_segment_id: "seg_003", display_text: "第三段", spoken_text: "第三段", locale: "zh-CN", ordinal: 2 },
  ],
  segment_states: [],
  takes: [],
  alignments: [],
  measured_total_ms: null,
  independent_clock: true,
  clock_source: "NATURAL_NARRATION",
  null_means_not_generated: true,
};

const RUN = {
  id: "run-1",
  project_id: "p1",
  video_id: "v1",
  status: "RUNNING",
  projected_status: "RUNNING",
  automation_mode: "AUTO_WITH_EXCEPTIONS",
  plan_hash: "x".repeat(64),
  current_stage_code: "VISUAL_GENERATION",
  progress_json: {},
  budget_json: {},
  budget_used_json: {},
  blockers_json: [],
  inference_mode: "LOCAL_ONLY",
  research_mode: "OFFLINE_IMPORT",
  automation_workflow_run_id: "wf-1",
  steps: [
    {
      id: "sb-1",
      run_id: "run-1",
      planned_step_code: "VISUAL_GENERATION",
      task_key: "visual-generation",
      status: "BLOCKED",
      job_id: "job-collect-1",
      job_state: "BLOCKED",
      attempt_count: 1,
      output_kind: "IMAGE",
      skip_reason: null,
      blocker_code: "DEPENDENCY_FAILED",
      started_at: null,
      finished_at: null,
      revision: 2,
    },
  ],
  step_statuses: { VISUAL_GENERATION: "BLOCKED" },
  workflow_run: null,
  execution_authority: { source_of_truth: "automation_workflow_runs+jobs+job_attempts", explainer_runs_is_projection: true, second_claim_queue: false },
};

function overviewFixture(overrides: Record<string, unknown> = {}) {
  return {
    project_id: "p1",
    video: { id: "v1", project_id: "p1", title: "灯塔", revision: 3, aspect_ratio: "16:9", source_locale: "zh-CN" },
    editions: [],
    beat_count: 1,
    render_type_counts: {},
    latest_run: RUN,
    open_issues: [BEAT_VIDEO_ISSUE, NARRATION_ISSUE],
    open_issue_count: 2,
    blocking_issue_count: 1,
    active_decisions: [],
    authority_labels: { machine: "自动检查结果", human: "人工确认", publication: "发布授权" },
    capability_snapshot: { probed: true, capabilities: [{ name: "IMAGE_CHARACTER", status: "AVAILABLE" }], unknown_count: 0, unavailable_count: 0 },
    ...overrides,
  };
}

function qcFixture(overrides: Record<string, unknown> = {}) {
  return {
    edition_id: "e1",
    video_id: "v1",
    subject: { kind: "COMPOSITION_RENDER", revision_id: "render-1", hash: "a".repeat(64) },
    film_review_target: { render_id: "render-1", has_render: true },
    subject_is_the_film: true,
    report: { id: "qc-1" },
    status: "COMPLETED",
    coverage: {
      total_frames: 7500,
      decoded_frames: 7500,
      technical_checked_frames: 7500,
      semantic_checked_frames: 750,
      human_reviewed_frames: 0,
    },
    coverage_layers_reported_separately: true as const,
    decoded_is_not_semantic: true as const,
    issues: [BEAT_VIDEO_ISSUE, NARRATION_ISSUE, FACT_ISSUE_ONE, FACT_ISSUE_TWO],
    open_issues: [BEAT_VIDEO_ISSUE, NARRATION_ISSUE, FACT_ISSUE_ONE, FACT_ISSUE_TWO],
    unverified_checks: ["MEDIA_NOT_GENERATED"],
    machine_decision: { id: "d1", decision_kind: "POLICY_ACCEPTED" },
    human_decision: null,
    publication_decision: null,
    automatic_pass_does_not_mean_human_review: true as const,
    ...overrides,
  };
}

const STATUSES: ExplainerStepStatusMap = {
  script: "DONE",
  assets: "DONE",
  audio: "DONE",
  storyboard: "NEEDS_SELECTION",
  clips: "FAILED",
  review: "RUNNING",
};

function renderReviewPage(path = "/explainers/p1/review") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/explainers/:projectId/review"
            element={
              <ExplainerActionBarProvider>
                <ExplainerReviewPage />
                <ExplainerStepActionBar projectId="p1" activePage="review" statuses={STATUSES} />
              </ExplainerActionBarProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  HTMLMediaElement.prototype.play = vi.fn() as unknown as () => Promise<void>;
  HTMLMediaElement.prototype.pause = vi.fn();
  // jsdom implements neither of these; the page refuses to claim a download when
  // it cannot really produce the file, so the happy path needs a real stub.
  URL.createObjectURL = vi.fn(() => "blob:explainer-subtitles") as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
  vi.mocked(api.getExplainerOverview).mockResolvedValue(overviewFixture() as never);
  vi.mocked(api.getExplainerRun).mockResolvedValue({ run: RUN } as never);
  vi.mocked(api.listExplainerEditions).mockResolvedValue({
    video_id: "v1",
    editions: [EDITION_WITH_RENDER],
    independent_clocks: {},
    english_timing_copied_from_source_locale: false,
  } as never);
  vi.mocked(api.getExplainerQc).mockResolvedValue(qcFixture() as never);
  vi.mocked(api.getExplainerNarration).mockResolvedValue(NARRATION_SEGMENTS as never);
  vi.mocked(api.getExplainerSubtitles).mockResolvedValue({
    edition_id: "e1",
    locale: "zh-CN",
    revision: { id: "sub-1", revision_no: 2 },
    cues: [{ start_ms: 0, end_ms: 3200, text: "第一条字幕", font_size_px: 48 }],
    rendered: "1\r\n00:00:00,000 --> 00:00:03,200\r\n第一条字幕\r\n",
    format: "SRT",
  } as never);
  vi.mocked(api.listExplainerBeats).mockResolvedValue(BEATS as never);
  vi.mocked(api.listExplainerBeatCandidates).mockResolvedValue({
    candidates: [{ id: "c1", status: "READY", selected: true, adopted: true, purpose: "KEYFRAME", variant_no: 1 }],
    active_selection: { id: "sel-1", candidate_id: "c1" },
  } as never);
  vi.mocked(api.listExplainerAssets).mockResolvedValue({
    video_id: "v1",
    entities: [],
    entity_counts: {},
    missing_reference_count: 0,
    channel_profile_version: { id: "cpv-1", bgm_policy_json: { ducking: true, duck_db: -16 } },
    channel_profile_is_frozen_snapshot: true as const,
    three_view_is_display_only: true as const,
    visual_preferences: null,
    resolved_style: null,
    unresolved_constraints: [],
  } as never);
  vi.mocked(api.listExplainerEntityCandidates).mockResolvedValue({ candidates: [] } as never);
  vi.mocked(api.listSubtitleStyleTemplates).mockResolvedValue({
    items: [{ id: "st-1", code: "project-caption", title: "项目字幕样式", version_no: 2, style: {} }],
  } as never);
  vi.mocked(api.saveSubtitleStyleTemplate).mockResolvedValue({
    template: { id: "st-2", code: "explainer-caption-m-bottom", title: "解说字幕 · 标准 · 底部", version_no: 1, style: {} },
  } as never);
  vi.mocked(api.discoverLocalSapiVoices).mockResolvedValue({
    status: "AVAILABLE",
    items: [{ name: "Microsoft Huihui", culture: "zh-CN", gender: "Female", age: "Adult", voice_ref: "sapi:huihui" }],
    message: null,
    runtime_contacted: true,
    network_contacted: false,
    mutated: false,
  } as never);
  vi.mocked(api.listCapabilityOptions).mockResolvedValue({
    capability: "TTS",
    scope: { project_id: "p1", episode_id: null, shot_id: null },
    selection: { mode: "AUTO", source: "PROJECT", profile_version_id: null, ready: true, option: null },
    options: [],
    configured_runtime: null,
    summary: { total_count: 0, selectable_count: 0, blocked_count: 0 },
    repair_href: "/system/capabilities",
    read_only: true,
    runtime_contacted: false,
    network_contacted: false,
    mutated: false,
  } as never);
  vi.mocked(api.startExplainerRender).mockResolvedValue({
    status: "READY_TO_START",
    composition_revision_id: "comp-1",
  } as never);
  vi.mocked(api.startExplainerExport).mockResolvedValue({
    status: "ACCEPTED",
    package_id: "pkg-1",
    job_id: "job-export-1",
    job_state: "QUEUED",
  } as never);
  vi.mocked(api.planExplainerRepairs).mockResolvedValue({
    plan: {
      video_id: "v1",
      issue_ids: ["i1"],
      revision: 3,
      responsible_steps: ["VISUAL_GENERATION"],
      beats: [],
      locked_beats_skipped: [],
      task_count: 1,
      would_create_jobs: false,
    },
    requires_confirmation: true,
    status: "REPAIR_NOT_SCHEDULABLE",
    submitted: false,
    job_ids: [],
    unschedulable: [{ responsible_step_code: "VISUAL_GENERATION", reason: "NO_STANDALONE_REPAIR_COMMAND" }],
  } as never);
  vi.mocked(api.recordExplainerDecision).mockResolvedValue({ id: "d2" } as never);
  vi.mocked(api.preflightExplainerPlan).mockResolvedValue({
    project_id: "p1",
    video_id: "v1",
    status: "EXECUTABLE",
    executable: true,
    would_create_jobs: false,
    categories: {},
    blockers: [],
    plan_hash: "y".repeat(64),
    parent_plan_id: null,
    frozen_inputs: {},
    policy_snapshot: {},
    capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
    budget: {},
    task_skeleton: [],
    estimate: {},
    generated_at: "2026-01-01T00:00:00Z",
    plan_scope: { covers: [], does_not_cover: [], internal_expansion_authorized: true, internal_expansion_can_self_stale: false },
  } as never);
  vi.mocked(api.createExplainerEdition).mockResolvedValue({ id: "e2", edition_key: "en-captioned-169" } as never);
  vi.mocked(api.continueExplainerCollection).mockResolvedValue({
    run_id: "run-1",
    step_binding_id: "sb-1",
    task_code: "VISUAL_GENERATION",
    status: "REPLACED",
    replacement_job_id: "job-collect-2",
    previous_job_id: "job-collect-1",
    selected_candidate_ids: ["c1"],
    failed_candidate_ids: [],
    reason: null,
    machine_policy_applied: false,
    idempotent_replay: false,
  } as never);
});

describe("review helpers — §B8 wording and problem sentences", () => {
  it("maps run states to the documented wording and flags unknown receipts", () => {
    expect(runStateWording("QUEUED").text).toBe("等待开始 / 等待 GPU");
    expect(runStateWording("PREFLIGHT").text).toBe("检查制作条件");
    expect(runStateWording("RUNNING", { currentItem: "VISUAL_GENERATION" }).text).toBe("正在制作：VISUAL_GENERATION");
    expect(runStateWording("QC_RUNNING").text).toBe("正在检查草稿");
    expect(runStateWording("PAUSING").text).toBe("正在暂停");
    expect(runStateWording("PAUSED").text).toBe("已暂停");
    expect(runStateWording("WAITING_INPUT", { problemCount: 3 }).text).toBe("需要处理 3 项问题");
    expect(runStateWording("FAILED").text).toBe("本次制作未完成");
    expect(runStateWording("CANCELLING").text).toBe("正在停止");
    expect(runStateWording("CANCELLED").text).toBe("已停止");
    expect(runStateWording("READY_TO_EXPORT").text).toBe("草稿已生成");
    expect(runStateWording("EXPORTING").text).toBe("正在保存视频");
    expect(runStateWording("COMPLETED").text).toBe("草稿已完成 / 视频已保存");
    expect(runStateWording(null).text).toBe("尚未开始制作");
    expect(runStateWording("SOMETHING_NEW").unknownReceipt).toBe(true);
  });

  it("only reports progress from a trustworthy real ratio", () => {
    expect(trustworthyProgress([{ status: "SUCCEEDED" }, { status: "RUNNING" }], {})).toEqual({
      completed: 1,
      total: 2,
      source: "按真实任务状态统计",
    });
    expect(trustworthyProgress([], { completed_steps: 3, total_steps: 6 })?.completed).toBe(3);
    expect(trustworthyProgress([], {})).toBeNull();
    expect(trustworthyProgress([], { completed_steps: 1, total_steps: 0 })).toBeNull();
  });

  it("describes problems in understandable sentences", () => {
    const context = {
      beatOrdinal: (id: string) => (id === "b8" ? 8 : null),
      segmentOrdinal: (id: string) => (id === "s3" ? 3 : null),
    };
    expect(problemSentence(BEAT_VIDEO_ISSUE, context)).toBe("第 8 镜缺视频");
    expect(problemSentence(NARRATION_ISSUE, context)).toBe("第 3 段配音失败");
    expect(problemSentence(FACT_ISSUE_ONE, context)).toBe(FACT_PROBLEM_SENTENCE);
  });

  it("jumps to the correct step and object", () => {
    const context = {
      beatOrdinal: () => 12,
      segmentOrdinal: () => 3,
    };
    expect(problemTarget(BEAT_VIDEO_ISSUE, context)).toEqual({
      page: "storyboard",
      search: "?beat=b8",
      label: "修改第 12 镜",
    });
    expect(problemTarget(NARRATION_ISSUE, context)).toEqual({
      page: "audio",
      search: "?segment=s3",
      label: "修改第 3 段配音",
    });
    expect(problemTarget(FACT_ISSUE_ONE, context).page).toBe("script");
  });

  it("reports the real tier instead of implying HD", () => {
    expect(resolutionTierLabel(854, 480)).toBe("480p（854×480）");
    expect(resolutionTierLabel(null, null)).toBe("当前原生档位（未记录像素）");
  });

  it("never assumes a capability is available", () => {
    expect(capabilityAvailable({ capabilities: [{ name: "UPSCALE_VIDEO", status: "AVAILABLE" }] }, "UPSCALE_VIDEO")).toBe(true);
    expect(capabilityAvailable({ capabilities: [{ name: "UPSCALE_VIDEO", status: "UNAVAILABLE" }] }, "UPSCALE_VIDEO")).toBe(false);
    expect(capabilityAvailable(undefined, "UPSCALE_VIDEO")).toBe(false);
  });

  it("only treats really blocked collection stages as continuable", () => {
    expect(
      collectionSteps({ steps: [{ id: "sb-1", planned_step_code: "VISUAL_GENERATION", status: "BLOCKED", job_id: "j1" }] }).length,
    ).toBe(1);
    expect(
      collectionSteps({ steps: [{ id: "sb-2", planned_step_code: "NARRATION_TTS", status: "BLOCKED", job_id: "j2" }] }).length,
    ).toBe(0);
    expect(
      collectionSteps({ steps: [{ id: "sb-3", planned_step_code: "VISUAL_GENERATION", status: "SUCCEEDED", job_id: "j3" }] }).length,
    ).toBe(0);
  });

  it("reads ducking support from the frozen profile only", () => {
    expect(profileDuckingSupported({ bgm_policy_json: { ducking: true } })).toBe(true);
    expect(profileDuckingSupported({ bgm_policy_json: JSON.stringify({ sidechain: true }) })).toBe(true);
    expect(profileDuckingSupported({ bgm_policy_json: {} })).toBe(false);
    expect(profileDuckingSupported({ bgm_policy_json: "not-json" })).toBe(false);
    expect(profileDuckingSupported(null)).toBe(false);
  });

  it("records the reviewed interval from the real playhead", () => {
    expect(reviewedIntervals(0, 0)).toEqual([[0, 0]]);
    expect(reviewedIntervals(30, 10)).toEqual([[10, 30]]);
  });

  it("picks the cue the decoder is really inside", () => {
    const cues = [{ start_ms: 0, end_ms: 1000, text: "a" }, { start_ms: 1000, end_ms: 2000, text: "b" }];
    expect(currentCueFor(30, cues, 25, 1)?.text).toBe("b");
    expect(currentCueFor(10, cues, 25, 1)?.text).toBe("a");
    expect(currentCueFor(3000, cues, 25, 1)).toBeNull();
    expect(currentCueFor(0, [], 25, 1)).toBeNull();
  });
});

describe("review page — film states", () => {
  it("distinguishes 'no edition' from 'edition without render'", async () => {
    vi.mocked(api.listExplainerEditions).mockResolvedValue({
      video_id: "v1",
      editions: [],
      independent_clocks: {},
      english_timing_copied_from_source_locale: false,
    } as never);
    const first = renderReviewPage();
    await waitFor(() => expect(screen.getByText("还没有输出版本")).toBeTruthy());
    first.unmount();

    vi.mocked(api.listExplainerEditions).mockResolvedValue({
      video_id: "v1",
      editions: [EDITION_WITHOUT_RENDER],
      independent_clocks: {},
      english_timing_copied_from_source_locale: false,
    } as never);
    renderReviewPage();
    await waitFor(() => expect(screen.getByText("还没有可审的成片")).toBeTruthy());
    expect(screen.getByText(/已提交渲染不等于制作成功/)).toBeTruthy();
  });

  it("shows 尚无审批记录 until a real decision exists", async () => {
    vi.mocked(api.getExplainerQc).mockResolvedValue(qcFixture({ machine_decision: null }) as never);
    renderReviewPage();
    await waitFor(() => expect(screen.getByText("尚无审批记录")).toBeTruthy());
    expect(screen.queryByText("已人工确认")).toBeNull();
  });

  it("plays the real film and never shows machine acceptance as human review", async () => {
    renderReviewPage();
    await waitFor(() => expect(document.querySelector("video")?.getAttribute("src")).toBe("/api/v1/media-versions/mv-render/content"));
    // Machine acceptance is on screen, but only as an automatic check.
    expect(screen.getByText("自动检查结果 · 未人工审阅")).toBeTruthy();
    expect(screen.queryByText("已人工确认")).toBeNull();
    fireEvent.click(screen.getByText("详情：机器 / 人工 / 发布三种决策记录"));
    expect(screen.getByText("政策接受（自动，未人工审阅）")).toBeTruthy();
    expect(screen.getByText(/HTTP 客户端不能自填机器接受/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认当前成片" })).toBeTruthy();
  });

  it("keeps the coverage layers separate and folded by default", async () => {
    renderReviewPage();
    await waitFor(() => expect(screen.getByText("技术解码")).toBeTruthy());
    expect(screen.getByText("审查覆盖范围")).toBeTruthy();
    expect(screen.getByText("视觉语义")).toBeTruthy();
    expect(screen.getByText("人工审阅")).toBeTruthy();
    expect(screen.getByText(/技术全量解码 100% 不等于全帧语义理解/)).toBeTruthy();
    await waitFor(() => expect(document.body.textContent ?? "").toContain("未检查项：MEDIA_NOT_GENERATED"));
  });

  it("shows the §B8 run wording and refuses to invent a percentage", async () => {
    vi.mocked(api.getExplainerOverview).mockResolvedValue(
      overviewFixture({ latest_run: { ...RUN, projected_status: "QUEUED", steps: [] } }) as never,
    );
    vi.mocked(api.getExplainerRun).mockResolvedValue({ run: { ...RUN, projected_status: "QUEUED", steps: [] } } as never);
    renderReviewPage();
    await waitFor(() => expect(screen.getByText("等待开始 / 等待 GPU")).toBeTruthy());
    expect(screen.getByText(/没有可信的总量比例/)).toBeTruthy();
  });
});

describe("review page — problems", () => {
  it("renders understandable sentences with real jump links, facts folded into one line", async () => {
    renderReviewPage();
    await waitFor(() => expect(screen.getByText("第 8 镜缺视频")).toBeTruthy());
    expect(screen.getByText("第 3 段配音失败")).toBeTruthy();
    expect(screen.getByText("2 条核心事实待核对")).toBeTruthy();
    expect(screen.getByRole("link", { name: /修改第 8 镜/ }).getAttribute("href")).toBe("/explainers/p1/storyboard?beat=b8");
    expect(screen.getByRole("link", { name: /修改第 3 段配音/ }).getAttribute("href")).toBe("/explainers/p1/audio?segment=s3");
    expect(screen.getByRole("link", { name: /去第 1 步核对事实与来源/ }).getAttribute("href")).toBe("/explainers/p1/script");
  });

  it("reports a repair that could not be scheduled as a plan, never as 已提交", async () => {
    renderReviewPage();
    await waitFor(() => expect(screen.getByText("第 8 镜缺视频")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("button", { name: "选择并定位" })[0]);
    fireEvent.click(await screen.findByRole("button", { name: "只修复这项" }));
    await waitFor(() => expect(api.planExplainerRepairs).toHaveBeenCalledTimes(2));
    const planCall = vi.mocked(api.planExplainerRepairs).mock.calls[0][1] as Record<string, unknown>;
    expect(planCall.confirm).toBe(false);
    const confirmCall = vi.mocked(api.planExplainerRepairs).mock.calls[1][1] as Record<string, unknown>;
    expect(confirmCall.confirm).toBe(true);
    expect(vi.mocked(api.planExplainerRepairs).mock.calls[1][2]).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() => expect(screen.getByText(/这是修复计划，没有创建任务/)).toBeTruthy());
    expect(screen.queryByText(/已提交局部返工/)).toBeNull();
  });

  it("reports a real repair job only when the server returned one", async () => {
    vi.mocked(api.planExplainerRepairs).mockResolvedValue({
      plan: { task_count: 1, would_create_jobs: false },
      status: "ACCEPTED",
      submitted: true,
      job_ids: ["job-repair-1"],
      unschedulable: [],
    } as never);
    renderReviewPage();
    await waitFor(() => expect(screen.getByText("第 8 镜缺视频")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("button", { name: "选择并定位" })[0]);
    fireEvent.click(await screen.findByRole("button", { name: "只修复这项" }));
    await waitFor(() => expect(screen.getByText(/已提交局部返工：1 个真实任务/)).toBeTruthy());
  });

  it("continues a blocked collection with real candidates and renders the real receipt", async () => {
    renderReviewPage();
    const button = await screen.findByRole("button", { name: "用现有结果继续" });
    expect(screen.getByText(/收集阶段被阻塞/)).toBeTruthy();
    fireEvent.click(button);
    await waitFor(() => expect(api.continueExplainerCollection).toHaveBeenCalledTimes(1));
    const call = vi.mocked(api.continueExplainerCollection).mock.calls[0];
    expect(call[0]).toBe("run-1");
    expect(call[1]).toBe("sb-1");
    expect(call[2]).toMatchObject({
      expected_old_job_id: "job-collect-1",
      expected_task_revision: 2,
      selected_candidate_ids: ["c1"],
      reason: "使用已有结果继续",
    });
    expect(call[3]).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() => expect(screen.getByText(/已用现有结果继续：替换了收集任务/)).toBeTruthy());
    expect(screen.getAllByText(/新作业 job-collect-2/).length).toBeGreaterThan(0);
  });

  it("says nothing was created when a required owner still has no candidate", async () => {
    vi.mocked(api.continueExplainerCollection).mockRejectedValue(
      new Error("EXPLAINER_REQUIRED_OWNER_MISSING：仍有关键对象没有任何可用候选，不能继续"),
    );
    renderReviewPage();
    fireEvent.click(await screen.findByRole("button", { name: "用现有结果继续" }));
    await waitFor(() => expect(screen.getByText(/没有创建或替换任何作业/)).toBeTruthy());
    expect(screen.queryByText(/已用现有结果继续/)).toBeNull();
  });
});

describe("review page — preview, export and the bottom bar", () => {
  it("never says a blocked preview was submitted, and never confirms the plan", async () => {
    vi.mocked(api.startExplainerRender).mockResolvedValue({
      status: "BLOCKED",
      blockers: [{ code: "MISSING_CAPABILITY", message: "没有可执行的合成工作流" }],
    } as never);
    vi.mocked(api.listExplainerEditions).mockResolvedValue({
      video_id: "v1",
      editions: [EDITION_WITHOUT_RENDER],
      independent_clocks: {},
      english_timing_copied_from_source_locale: false,
    } as never);
    renderReviewPage();
    await waitFor(() => expect(screen.getByRole("button", { name: "生成预览" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "生成预览" }));
    await waitFor(() => expect(screen.getByText(/预览被阻塞，未提交任何任务/)).toBeTruthy());
    expect(api.startExplainerRender).toHaveBeenCalledTimes(1);
  });

  it("submits a real preview job and keeps the old film playable as 旧版", async () => {
    vi.mocked(api.startExplainerRender)
      .mockResolvedValueOnce({ status: "READY_TO_START", composition_revision_id: "comp-1" } as never)
      .mockResolvedValueOnce({ status: "ACCEPTED", job_id: "job-render-1", composition_revision_id: "comp-1" } as never);
    renderReviewPage();
    await waitFor(() => expect(screen.getByRole("button", { name: "更新预览" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "更新预览" }));
    await waitFor(() => expect(screen.getByText(/已提交预览合成任务 job-render-1/)).toBeTruthy());
    expect(vi.mocked(api.startExplainerRender).mock.calls[1][1]).toMatchObject({ confirm: true, freeze: true });
    // The old film is still there, now labelled 旧版.
    expect(document.querySelector("video")?.getAttribute("src")).toBe("/api/v1/media-versions/mv-render/content");
    await waitFor(() => expect(screen.getByText("旧版")).toBeTruthy());
  });

  it("uses the bottom bar for the one primary action and switches to 导出视频 once a film exists", async () => {
    renderReviewPage();
    await waitFor(() => expect(screen.getByRole("button", { name: "导出视频" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "导出视频" }));
    await waitFor(() => expect(api.startExplainerExport).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.startExplainerExport).mock.calls[0][1]).toMatchObject({
      render_id: "render-1",
      include_subtitles: true,
      confirm: true,
    });
    await waitFor(() => expect(screen.getByText(/已提交导出任务 job-export-1/)).toBeTruthy());
    // 下载成片 exists only because a playable file really exists.
    const download = screen.getByRole("link", { name: "下载成片" });
    expect(download.getAttribute("href")).toBe("/api/v1/media-versions/mv-render/content");
  });

  it("submits a real composition QC job for the current render", async () => {
    vi.mocked(api.runExplainerCompositionQc).mockResolvedValue({
      status: "ACCEPTED",
      job_id: "job-qc-1",
    } as never);
    renderReviewPage();
    const button = await screen.findByRole("button", { name: "运行技术质检" });
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(api.runExplainerCompositionQc).toHaveBeenCalledTimes(1));
    const call = vi.mocked(api.runExplainerCompositionQc).mock.calls[0];
    expect(call[1]).toMatchObject({ render_id: "render-1" });
    expect(String(call[2])).toMatch(/^[0-9a-f-]{36}$/);
    await waitFor(() => expect(screen.getByText(/已提交成片技术质检任务 job-qc-1/)).toBeTruthy());
  });

  it("says the QC created nothing when the server returned a blocker", async () => {
    vi.mocked(api.runExplainerCompositionQc).mockResolvedValue({
      status: "BLOCKED",
      blockers: [{ code: "SCHEMA_INVALID", message: "该输出版本还没有可质检的成片" }],
    } as never);
    renderReviewPage();
    const button = await screen.findByRole("button", { name: "运行技术质检" });
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByText(/技术质检未提交（BLOCKED）/)).toBeTruthy());
    expect(screen.queryByText(/已提交成片技术质检任务/)).toBeNull();
  });

  it("says the export created nothing when the server did not accept it", async () => {
    vi.mocked(api.startExplainerExport).mockResolvedValue({
      status: "CAPABILITY_UNAVAILABLE",
      reason: "没有可执行的导出工作流",
    } as never);
    renderReviewPage();
    fireEvent.click(await screen.findByRole("button", { name: "导出视频" }));
    await waitFor(() => expect(screen.getByText(/导出未创建任务（CAPABILITY_UNAVAILABLE）/)).toBeTruthy());
    expect(screen.queryByText(/已提交导出任务/)).toBeNull();
  });

  it("creates no image or TTS task when only the subtitle style or the music changes", async () => {
    renderReviewPage();
    await waitFor(() => expect(screen.getByText("字幕")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("字号"), { target: { value: "大" } });
    fireEvent.change(screen.getByLabelText("位置"), { target: { value: "顶部" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为项目字幕样式模板" }));
    await waitFor(() => expect(api.saveSubtitleStyleTemplate).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.saveSubtitleStyleTemplate).mock.calls[0][1]).toMatchObject({
      style: { size: 60, position: "TOP", safe_area: "STANDARD" },
    });
    expect(screen.getAllByText(/不会重跑图像或 TTS/).length).toBeGreaterThan(0);
    expect(api.startExplainerRender).not.toHaveBeenCalled();
    expect(api.startExplainerExport).not.toHaveBeenCalled();
    expect(api.planExplainerRepairs).not.toHaveBeenCalled();
    expect(api.continueExplainerCollection).not.toHaveBeenCalled();
  });

  it("only offers 中英双语 after a real capability preflight passes", async () => {
    renderReviewPage();
    await waitFor(() => expect(screen.getByText(/中英双语只有完整翻译/)).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "检查中英双语是否可用" }));
    await waitFor(() => expect(api.preflightExplainerPlan).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/能力预检通过/)).toBeTruthy());
  });

  it("does not create a new output version when the capability preflight blocks it", async () => {
    vi.mocked(api.preflightExplainerPlan).mockResolvedValue({
      executable: false,
      blockers: [{ code: "MISSING_LOCAL_CAPABILITY", message: "本机没有可用的英语 TTS" }],
    } as never);
    renderReviewPage();
    fireEvent.click(await screen.findByRole("button", { name: "添加输出版本" }));
    const openDrawer = await screen.findAllByRole("button", { name: "添加输出版本" });
    // The drawer mounts a second button with the same label; the last one submits.
    fireEvent.click(openDrawer[openDrawer.length - 1]);
    await waitFor(() => expect(screen.getByText(/能力预检未通过，没有创建任何版本/)).toBeTruthy());
    expect(api.createExplainerEdition).not.toHaveBeenCalled();
  });

  it("downloads the subtitle sidecar from the real subtitle revision", async () => {
    const anchors: HTMLAnchorElement[] = [];
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function mockClick(this: HTMLAnchorElement) {
        anchors.push(this);
      });
    try {
      renderReviewPage();
      await waitFor(() => expect(document.querySelector("video")).toBeTruthy());
      const button = screen.getByRole("button", { name: "下载字幕附件" });
      await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
      fireEvent.click(button);
      await waitFor(() => expect(api.getExplainerSubtitles).toHaveBeenCalled());
      await waitFor(() => expect(screen.getByText(/已下载字幕附件/)).toBeTruthy());
      expect(anchors.length).toBe(1);
      expect(anchors[0].download).toBe("subtitles-zh-CN.srt");
      expect(anchors[0].getAttribute("href")).toContain("blob:");
    } finally {
      clickSpy.mockRestore();
    }
  });
});

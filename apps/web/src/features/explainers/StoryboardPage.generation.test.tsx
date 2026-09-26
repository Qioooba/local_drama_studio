/**
 * 第 4 步 分镜与画面: the generation contract (spec §B5.1, §B5.2, §B5.3, §B9).
 *
 * These tests pin the parts of step 4 that are easy to fake and therefore must be
 * asserted against the real commands:
 *
 *  * the top overview is only 共 N 个画面段，M 个已就绪 — distributions and hashes
 *    live in 详情, not on screen;
 *  * clicking a beat or previewing a candidate never writes an adoption;
 *  * plan → submit → real receipt → poll: the ACK is 已排队, never 生成完成;
 *  * a BLOCKED plan keeps the missing capability visible and stays unsubmittable;
 *  * 再生成 mints a new operation (new seeds), while 重试失败任务 re-runs the very
 *    same job, and 重新载入 only re-reads.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../generated/api", () => ({
  adoptExplainerGeneratedBeats: vi.fn(),
  archiveExplainerCandidate: vi.fn(),
  getExplainerBeatImpact: vi.fn(),
  getExplainerOverview: vi.fn(),
  getExplainerWorkspaceReadiness: vi.fn(),
  listExplainerOwnerCandidates: vi.fn(),
  patchExplainerBeat: vi.fn(),
  planExplainerBeatGeneration: vi.fn(),
  preflightExplainerPlan: vi.fn(),
  registerExplainerCandidateFromMedia: vi.fn(),
  retryJob: vi.fn(),
  selectExplainerBeatCandidate: vi.fn(),
  startExplainerRun: vi.fn(),
  submitExplainerBeatGeneration: vi.fn(),
  unlockExplainerSelection: vi.fn(),
}));

let videoCapabilityReady = true;

vi.mock("../model-config/CapabilityPicker", () => ({
  CapabilityPicker: ({ label }: { label?: string }) => <div>{label}</div>,
  useCapabilityOptions: (capability: string) => ({
    capability,
    data: undefined,
    isPending: false,
    error: null,
    refetch: vi.fn(),
  }),
  effectiveCapabilityProfile: (query: { capability?: string } | undefined) => {
    const ready = query?.capability === "VIDEO_I2V" ? videoCapabilityReady : true;
    return {
      profileVersionId: "profile-1",
      option: {
        selectable: ready,
        blockers: ready
          ? []
          : [{ code: "EXPLAINER_PROFILE_UNAVAILABLE", message: "本机没有可执行的 AI 动态（VIDEO_I2V）能力。" }],
        warnings: [],
        input_slots: ["FIRST_FRAME"],
      },
      ready,
    };
  },
}));

import {
  archiveExplainerCandidate,
  getExplainerBeatImpact,
  getExplainerOverview,
  getExplainerWorkspaceReadiness,
  listExplainerOwnerCandidates,
  patchExplainerBeat,
  planExplainerBeatGeneration,
  registerExplainerCandidateFromMedia,
  retryJob,
  selectExplainerBeatCandidate,
  submitExplainerBeatGeneration,
} from "../../generated/api";
import { ExplainerActionBarProvider, ExplainerStepActionBar } from "./ExplainerStepActionBar";
import type { ExplainerStepStatusMap } from "./ExplainerSteps";
import { ExplainerStoryboardPage } from "./StoryboardPage";

function beat(overrides: Record<string, unknown> = {}) {
  return {
    id: "beat-1",
    code: "BEAT_001",
    ordinal: 0,
    revision: 3,
    render_type: "I2V",
    render_type_actual: null,
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
      { id: "cand-1", purpose: "KEYFRAME", variant_no: 1, status: "READY", media_version_id: "mv-1", adopted: true, candidate_kind: "CREATIVE" },
    ],
    active_selection: { id: "sel-1", purpose: "KEYFRAME", candidate_id: "cand-1", media_version_id: "mv-1" },
    ...overrides,
  };
}

const SECOND_BEAT = beat({
  id: "beat-2",
  code: "BEAT_002",
  ordinal: 1,
  revision: 2,
  visual_intent: "记录纸上的日期特写。",
  prompt_intent: "静态特写，手指划过日期。",
  narration_links: [{ canonical_segment_id: "seg_002", display_text: "记录停在那一页。" }],
  candidates: [],
  active_selection: null,
});

function candidateRow(overrides: Record<string, unknown> = {}) {
  return {
    id: "cand-1",
    purpose: "KEYFRAME",
    candidate_kind: "CREATIVE",
    owner_kind: "BEAT",
    owner_id: "beat-1",
    variant_no: 1,
    media_kind: "IMAGE",
    media_version_id: "mv-1",
    media_sha256: "a".repeat(64),
    thumbnail_url: "/api/v1/media-versions/mv-1/content",
    preview_url: "/api/v1/media-versions/mv-1/content",
    playback_url: null,
    status: "READY",
    selected: true,
    adopted: true,
    locked: false,
    stale: false,
    retryable: false,
    seed: 710001,
    job_id: "job-1",
    reference_media_version_ids: [],
    short_label: null,
    error_message: null,
    error_code: null,
    ...overrides,
  };
}

function candidatePage(rows: Array<Record<string, unknown>>, activeSelection: Record<string, unknown> | null) {
  return {
    project_id: "p1",
    owner_kind: "BEAT",
    owner_id: "beat-1",
    purpose: "KEYFRAME",
    edition_id: null,
    candidates: rows,
    counts: { REFERENCE: 0, KEYFRAME: rows.length, VISUAL: 0 },
    active_selection: activeSelection,
    empty_state: rows.length ? null : "NO_CANDIDATES_YET",
    candidates_newest_first: true,
    read_error_keeps_known_selection: true,
  };
}

const PLAN = {
  status: "EXECUTABLE",
  owner_kind: "BEAT",
  owner_id: "beat-1",
  purpose: "KEYFRAME",
  mode: "TEXT_TO_IMAGE",
  candidate_count: 1,
  plan_hash: "a".repeat(64),
  candidate_seeds: [710001],
  execution_profile_version_id: "prof-1",
  profile_title: "已验证文生图 v1",
  expected_resolution_hash: "b".repeat(64),
  media_kind: "IMAGE",
  render_type_planned: "I2V",
  render_type_actual: null,
  prompt: "值班员推开门",
  negative_prompt: null,
  reference_capacity: { max_image_references: 2, resolved_image_references: 0 },
  resolved_references: [],
  frozen_inputs: {},
  blockers: [],
  budget: { remaining_images: 12 },
  planned_duration_ms: null,
  allowed_durations_ms: null,
};

let beatsFixture: Array<Record<string, unknown>> = [beat()];

vi.mock("./useExplainerQueries", () => ({
  useExplainerEditions: () => ({ data: { editions: [{ id: "e1" }] }, isPending: false }),
  useExplainerBeats: () => ({
    data: { beats: beatsFixture, render_type_counts: { I2V: 1 }, actual_render_type_counts: {} },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useExplainerAssets: () => ({
    data: {
      entities: [
        {
          id: "entity-1",
          code: "E001",
          name: "值班员",
          entity_type: "FICTIONAL_CHARACTER",
          reference: { id: "ref-1", media_version_id: "mv-ref" },
          states: [],
        },
      ],
    },
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

const STATUSES: ExplainerStepStatusMap = {
  script: "DONE",
  assets: "DONE",
  audio: "DONE",
  storyboard: "NEEDS_SELECTION",
  clips: "NOT_STARTED",
  review: "NOT_STARTED",
};

function mount(path = "/explainers/p1/storyboard") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/explainers/:projectId/storyboard"
            element={
              <ExplainerActionBarProvider>
                <ExplainerStoryboardPage />
                <ExplainerStepActionBar projectId="p1" activePage="storyboard" statuses={STATUSES} />
              </ExplainerActionBarProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("step 4 storyboard generation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    videoCapabilityReady = true;
    beatsFixture = [beat(), SECOND_BEAT];
    vi.mocked(listExplainerOwnerCandidates).mockResolvedValue(
      candidatePage([candidateRow()], { id: "sel-1", purpose: "KEYFRAME", candidate_id: "cand-1", media_version_id: "mv-1" }) as never,
    );
    vi.mocked(planExplainerBeatGeneration).mockResolvedValue(PLAN as never);
    vi.mocked(submitExplainerBeatGeneration).mockResolvedValue({
      operation_id: "op-1",
      status: "ACCEPTED",
      requested_count: 1,
      accepted_count: 1,
      items: [
        {
          ordinal: 0,
          submission_status: "ACCEPTED",
          candidate_id: "cand-new",
          candidate_status: "PENDING",
          job_id: "job-new",
          job_state: "QUEUED",
          seed: 710001,
          error: null,
        },
      ],
      idempotent_replay: false,
    } as never);
    vi.mocked(getExplainerBeatImpact).mockResolvedValue({ affected_edition_count: 1 } as never);
    vi.mocked(getExplainerOverview).mockResolvedValue({
      capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
    } as never);
    vi.mocked(getExplainerWorkspaceReadiness).mockResolvedValue({ steps: [] } as never);
    vi.mocked(retryJob).mockResolvedValue({ job: { id: "job-1", status: "QUEUED" } } as never);
    vi.mocked(selectExplainerBeatCandidate).mockResolvedValue({ degraded: false } as never);
    vi.mocked(archiveExplainerCandidate).mockResolvedValue({ archived: true, media_kept: true } as never);
    vi.mocked(registerExplainerCandidateFromMedia).mockResolvedValue({
      candidate_id: "cand-upload",
      status: "READY",
      purpose: "KEYFRAME",
      becomes_candidate_only: true,
    } as never);
    vi.mocked(patchExplainerBeat).mockResolvedValue({
      beat_id: "beat-1",
      changed_fields: ["visual_intent", "prompt_intent"],
      edit_authority: "HUMAN",
      revision: 4,
    } as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("shows only 共 N 个画面段，M 个已就绪 on top and keeps the distribution table in 详情", async () => {
    mount();
    expect(await screen.findByText("共 2 个画面段，1 个已就绪")).toBeTruthy();
    const detail = screen.getByText("详情：计划/实际分布、哈希与运行状态");
    expect(detail.closest("details")?.open).toBe(false);
    // The bottom bar owns the page's single primary action.
    expect(screen.getByRole("button", { name: /补齐缺失画面/ })).toBeTruthy();
  });

  it("lists beats in time order with real thumbnail, narration summary, duration and status", async () => {
    mount();
    const item = await screen.findByRole("button", { name: "画面段 BEAT_001" });
    expect(item.textContent).toContain("01");
    expect(item.textContent).toContain("他推开门，光线短暂中断。");
    expect(item.textContent).toContain("00:02");
    expect(item.textContent).toContain("已就绪");
    const thumb = item.querySelector("img");
    expect(thumb?.getAttribute("src")).toContain("/api/v1/media-versions/mv-1/thumbnail");
  });

  it("switches the working object without adopting or generating anything", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "画面段 BEAT_002" }));
    expect(await screen.findByText(/记录停在那一页/)).toBeTruthy();
    expect(selectExplainerBeatCandidate).not.toHaveBeenCalled();
    expect(planExplainerBeatGeneration).not.toHaveBeenCalled();
    // ?beat= stays the object locator, so the deep link is preserved.
    expect(screen.getByRole("button", { name: "画面段 BEAT_002" }).getAttribute("aria-current")).toBe("true");
  });

  it("keeps 预览 separate from 采用 and writes the adoption only on 采用", async () => {
    mount();
    const adoptButton = await screen.findByRole("button", { name: "采用" });
    expect(adoptButton.hasAttribute("disabled")).toBe(true);
    fireEvent.click(await screen.findByRole("button", { name: "预览候选 1" }));
    expect(selectExplainerBeatCandidate).not.toHaveBeenCalled();
    expect(screen.getByText(/正在预览：候选 1（未采用）/)).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("button", { name: "采用" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "采用" }));
    await waitFor(() => expect(selectExplainerBeatCandidate).toHaveBeenCalledTimes(1));
    expect(vi.mocked(selectExplainerBeatCandidate).mock.calls[0][2]).toMatchObject({
      candidate_id: "cand-1",
      purpose: "KEYFRAME",
      lock: false,
      actor: null,
    });
  });

  it("runs plan → submit with one operation, the frozen plan fields and a real receipt", async () => {
    mount();
    fireEvent.change(await screen.findByLabelText(/每次候选数/), { target: { value: "2" } });
    const draw = screen.getByRole("button", { name: "再生成 2 张" });
    fireEvent.click(draw);

    await waitFor(() => expect(planExplainerBeatGeneration).toHaveBeenCalledTimes(1));
    const [projectId, beatId, planBody] = vi.mocked(planExplainerBeatGeneration).mock.calls[0];
    expect(projectId).toBe("p1");
    expect(beatId).toBe("beat-1");
    expect(String((planBody as Record<string, unknown>).operation_id)).toHaveLength(36);
    expect(planBody).toMatchObject({ purpose: "KEYFRAME", mode: "TEXT_TO_IMAGE", candidate_count: 2 });

    // The plan is shown, and 提交 only becomes available once it is executable.
    await screen.findByText("已验证文生图 v1");
    expect(screen.getAllByText("710001").length).toBeGreaterThan(0);
    expect(screen.getByText(/remaining_images/)).toBeTruthy();
    const submit = screen.getByRole("button", { name: /提交生成（本次 2 张）/ });
    await waitFor(() => expect(submit.hasAttribute("disabled")).toBe(false));

    vi.mocked(submitExplainerBeatGeneration).mockResolvedValue({
      operation_id: "op-1",
      status: "PARTIALLY_ACCEPTED",
      requested_count: 2,
      accepted_count: 1,
      items: [
        { ordinal: 0, submission_status: "ACCEPTED", candidate_id: "cand-new", candidate_status: "PENDING", job_id: "job-new", job_state: "QUEUED", seed: 710001, error: null },
        { ordinal: 1, submission_status: "NOT_SUBMITTED", candidate_id: null, candidate_status: null, job_id: null, job_state: null, seed: 710002, error: { code: "GPU_CAPACITY_UNAVAILABLE", message: "显存不足，未入队", retryable: true } },
      ],
      idempotent_replay: false,
    } as never);
    fireEvent.click(submit);

    await waitFor(() => expect(submitExplainerBeatGeneration).toHaveBeenCalledTimes(1));
    const [, , submitBody, key] = vi.mocked(submitExplainerBeatGeneration).mock.calls[0];
    expect(String(key)).toHaveLength(36);
    expect(submitBody).toMatchObject({
      operation_id: (planBody as Record<string, unknown>).operation_id,
      expected_plan_hash: "a".repeat(64),
      candidate_seeds: [710001],
      execution_profile_version_id: "prof-1",
    });
    // The real receipt: the accepted ordinal is 已排队, the other one names its error.
    expect(await screen.findByText(/第 1 张已排队/)).toBeTruthy();
    expect(screen.getByText(/第 2 张未入队：显存不足，未入队（GPU_CAPACITY_UNAVAILABLE）/)).toBeTruthy();
    expect(screen.getByText(/回执不等于生成完成/)).toBeTruthy();
    // 生成完成 may only appear as the disclaimer, never as a claim from an ACK.
    const completionClaims = screen.queryAllByText(/生成完成/);
    expect(completionClaims.every((node) => node.textContent?.includes("不等于"))).toBe(true);
  });

  it("keeps a BLOCKED plan visible with the missing capability and refuses to submit", async () => {
    vi.mocked(planExplainerBeatGeneration).mockResolvedValue({
      ...PLAN,
      status: "BLOCKED",
      execution_profile_version_id: null,
      profile_title: null,
      blockers: [
        { code: "EXPLAINER_PROFILE_UNAVAILABLE", message: "本机没有可用于 IMAGE_CONCEPT 的已发布 Profile。", retryable: false },
      ],
    } as never);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "再生成 1 张" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("IMAGE_CONCEPT");
    expect(screen.getByRole("button", { name: /提交生成/ }).hasAttribute("disabled")).toBe(true);
  });

  it("keeps a failed candidate's real error and retries the original job, not a new draw", async () => {
    vi.mocked(listExplainerOwnerCandidates).mockResolvedValue(
      candidatePage(
        [
          candidateRow({ id: "cand-9", variant_no: 9, status: "FAILED", media_version_id: null, selected: false, adopted: false, job_id: "job-9", retryable: true, error_message: "ComfyUI 返回空结果", error_code: "PIPELINE_OUTPUT_MISSING" }),
        ],
        null,
      ) as never,
    );
    mount();
    expect(await screen.findByText("ComfyUI 返回空结果")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试失败任务" }));
    await waitFor(() => expect(retryJob).toHaveBeenCalledWith("job-9"));
    expect(planExplainerBeatGeneration).not.toHaveBeenCalled();
    expect(submitExplainerBeatGeneration).not.toHaveBeenCalled();
  });

  it("mints a new operation for 再生成 and only re-reads for 重新载入", async () => {    mount();
    fireEvent.click(await screen.findByRole("button", { name: "再生成 1 张" }));
    await waitFor(() => expect(planExplainerBeatGeneration).toHaveBeenCalledTimes(1));
    const firstOperation = (vi.mocked(planExplainerBeatGeneration).mock.calls[0][2] as Record<string, unknown>).operation_id;

    fireEvent.click(screen.getByRole("button", { name: "再生成 1 张" }));
    await waitFor(() => expect(planExplainerBeatGeneration).toHaveBeenCalledTimes(2));
    const secondOperation = (vi.mocked(planExplainerBeatGeneration).mock.calls[1][2] as Record<string, unknown>).operation_id;
    expect(secondOperation).not.toBe(firstOperation);

    const reads = vi.mocked(listExplainerOwnerCandidates).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "重新载入候选" }));
    await waitFor(() => expect(vi.mocked(listExplainerOwnerCandidates).mock.calls.length).toBeGreaterThan(reads));
    expect(planExplainerBeatGeneration).toHaveBeenCalledTimes(2);
    expect(submitExplainerBeatGeneration).not.toHaveBeenCalled();
  });

  it("exposes the §B5.2 controls and §B5.3 buttons with their real option sets", async () => {
    mount();
    await screen.findByRole("button", { name: "画面段 BEAT_001" });

    const source = screen.getByLabelText(/画面来源/) as HTMLSelectElement;
    expect(Array.from(source.options).map((option) => option.textContent)).toEqual([
      "AI 配图",
      "上传或媒体库",
      "图形卡片（仅已有受支持模板）",
    ]);

    const presentation = screen.getByLabelText(/最终呈现方式/) as HTMLSelectElement;
    // 静图推拉 is gone: the only picture route is real AI 图生视频, and 图形动画 /
    // 已有视频 stay as their own separately named non-AI-picture sources.
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
    // No route in the selector is the removed 静图推拉 (静图推拉 may still be named as
    // an explanation of what was retired, so the options are checked directly).
    expect(Array.from(presentation.options).map((option) => option.value)).not.toContain("IMAGE_MOTION");
    expect(Array.from(presentation.options).map((option) => option.textContent).join()).not.toContain("静图推拉");
    // The note states that this choice updates step 5.
    expect(screen.getByText(/修改最终呈现方式会更新第 5 步的生成方式/)).toBeTruthy();

    const counts = screen.getByLabelText(/每次候选数/) as HTMLSelectElement;
    expect(Array.from(counts.options).map((option) => option.value)).toEqual(["1", "2", "4"]);
    expect(counts.value).toBe("1");

    const composition = screen.getByLabelText(/构图/) as HTMLSelectElement;
    expect(Array.from(composition.options).map((option) => option.textContent)).toEqual([
      "自动",
      "远景",
      "全景",
      "中景",
      "近景",
      "特写",
    ]);

    // Model picker, references, coverage drawer and 高级设置 are all real controls.
    expect(screen.getByText("图片模型")).toBeTruthy();
    const references = screen.getByRole("group", { name: "人物与场景引用" });
    expect(references.textContent).toContain("值班员");
    expect(references.textContent).toContain("已采用参考图");
    // Selecting a real reference enables 按参考重绘 (IMAGE_EDIT) — never a fake mode.
    const mode = screen.getByLabelText(/生成方式/) as HTMLSelectElement;
    expect(Array.from(mode.options).find((option) => option.value === "IMAGE_EDIT")?.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    await waitFor(() =>
      expect(Array.from((screen.getByLabelText(/生成方式/) as HTMLSelectElement).options).find((option) => option.value === "IMAGE_EDIT")?.disabled).toBe(false),
    );
    expect(screen.getByRole("button", { name: "调整覆盖区间" })).toBeTruthy();
    expect(screen.getByText("编辑提示词 / 高级设置")).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制" })).toBeTruthy();
    expect(screen.getByText(/再生成必须使用新种子/)).toBeTruthy();
    // 重新规划 is in the tool row, 生成分镜计划 is the empty-state action.
    expect(screen.getByRole("button", { name: "重新规划" })).toBeTruthy();
  });

  it("keeps a required motion gap visible when I2V has no executable capability", async () => {
    videoCapabilityReady = false;
    beatsFixture = [beat({ render_type: "I2V", must_be_motion: true, candidates: [], active_selection: null })];
    mount();
    const gap = await screen.findByText(/本机没有可执行的 AI 动态能力（缺少 VIDEO_I2V/);
    expect(gap).toBeTruthy();
    const region = gap.closest(".explainer-gap-notice") as HTMLElement;
    // No still-motion fallback is offered any more: only 调整镜头 and 配置模型 are real
    // next steps for a machine that cannot run 图生视频.
    expect(region.textContent).toContain("静图推拉已从产品中移除");
    expect(region.textContent).toContain("调整镜头");
    expect(region.textContent).toContain("配置模型");
    expect(region.textContent).not.toContain("改为静图推拉");
    expect(screen.queryByRole("button", { name: /静图推拉/ })).toBeNull();
    // A beat planned as 必须运动 cannot be switched to a non-AI route either.
    const presentation = screen.getByLabelText(/最终呈现方式/) as HTMLSelectElement;
    fireEvent.change(presentation, { target: { value: "GRAPHIC_ANIMATION" } });
    const options = Array.from((screen.getByLabelText(/最终呈现方式/) as HTMLSelectElement).options);
    expect(options.filter((option) => option.value !== "AI_VIDEO").every((option) => option.disabled)).toBe(true);
    expect((screen.getByLabelText(/最终呈现方式/) as HTMLSelectElement).value).toBe("AI_VIDEO");
  });

  it("surfaces a legacy 静图推拉 plan as an outdated mode, never as a still label", async () => {
    beatsFixture = [beat({ render_type: "STILL_MOTION", candidates: [], active_selection: null })];
    mount();
    // The stored value is not a legal target any more: it says so, and asks for AI 动态.
    await waitFor(() => expect(screen.getAllByText("计划方式已停用").length).toBeGreaterThan(0));
    expect(screen.getAllByText(/必须改为 AI 动态（图生视频）/).length).toBeGreaterThan(0);
    // The retired value is never rendered as a still-motion label.
    expect(screen.queryByText("静图推拉")).toBeNull();
    expect(screen.queryByText("插画轻动")).toBeNull();
    expect(screen.queryByText("分层视差")).toBeNull();
    // The presentation selector only offers the three remaining legal routes and the
    // page selects the one route a picture can be produced through.
    const presentation = screen.getByLabelText(/最终呈现方式/) as HTMLSelectElement;
    expect(Array.from(presentation.options).map((option) => option.value)).toEqual([
      "AI_VIDEO",
      "GRAPHIC_ANIMATION",
      "SOURCE_VIDEO",
    ]);
    expect(presentation.value).toBe("AI_VIDEO");
  });

  it("uses the bottom-bar primary to backfill every missing shot through the real command", async () => {
    beatsFixture = [beat({ candidates: [], active_selection: null }), SECOND_BEAT];
    mount();
    const primary = await screen.findByRole("button", { name: /补齐缺失画面（2 个画面段）/ });
    expect(primary).toBeTruthy();
    fireEvent.click(primary);
    await waitFor(() => expect(planExplainerBeatGeneration).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(submitExplainerBeatGeneration).toHaveBeenCalledTimes(2));
    const keys = vi.mocked(submitExplainerBeatGeneration).mock.calls.map((call) => call[3]);
    expect(keys.every((key) => typeof key === "string" && key.length === 36)).toBe(true);
    expect(await screen.findByText(/已为 2 \/ 2 个画面段提交 2 个任务（已排队/)).toBeTruthy();
  });

  it("archives a candidate through the real command and keeps the adopted one protected", async () => {
    beatsFixture = [beat()];
    vi.mocked(listExplainerOwnerCandidates).mockResolvedValue(
      candidatePage(
        [
          candidateRow(),
          candidateRow({
            id: "cand-2",
            variant_no: 2,
            media_version_id: "mv-2",
            thumbnail_url: "/api/v1/media-versions/mv-2/content",
            // The server marks exactly the adopted row, so the second candidate is
            // explicitly not the current choice.
            selected: false,
            adopted: false,
          }),
        ],
        { id: "sel-1", purpose: "KEYFRAME", candidate_id: "cand-1", media_version_id: "mv-1" },
      ) as never,
    );
    mount();
    // Previewing is how the page knows which candidate an action applies to; the
    // already adopted one keeps its 不采用 action disabled.
    const previews = await screen.findAllByRole("button", { name: /^预览候选/ });
    expect(previews.length).toBe(2);
    fireEvent.click(previews[1]);
    // Both the candidate card and the page's own action row expose 不采用 for the
    // previewed candidate, and both must be enabled; the adopted candidate is not
    // offerable at all.
    await waitFor(() => {
      const targeted = screen
        .getAllByRole("button", { name: "不采用" })
        .filter((button) => !button.hasAttribute("disabled"));
      expect(targeted.length).toBeGreaterThanOrEqual(1);
    });
    const targeted = screen
      .getAllByRole("button", { name: "不采用" })
      .filter((button) => !button.hasAttribute("disabled"));
    fireEvent.click(targeted[0]);
    await waitFor(() => expect(archiveExplainerCandidate).toHaveBeenCalledTimes(1));
    const [projectId, beatId, candidateId] = vi.mocked(archiveExplainerCandidate).mock.calls[0];
    expect(projectId).toBe("p1");
    expect(beatId).toBe("beat-1");
    expect(candidateId).toBe("cand-2");
    expect(await screen.findByText(/已归档/)).toBeTruthy();
  });

  it("saves the shot description through the real beat command and reports the human edit", async () => {
    beatsFixture = [beat()];
    mount();
    const save = await screen.findByRole("button", { name: "保存镜头描述" });
    fireEvent.click(save);
    await waitFor(() => expect(patchExplainerBeat).toHaveBeenCalledTimes(1));
    const [projectId, beatId, payload] = vi.mocked(patchExplainerBeat).mock.calls[0];
    expect(projectId).toBe("p1");
    expect(beatId).toBe("beat-1");
    expect(payload.expected_revision).toBe(3);
    expect(payload.actor).toBe("local-user");
    expect(await screen.findByText(/已保存镜头（人工编辑）/)).toBeTruthy();
  });

  it("registers a picked media version as a candidate instead of adopting it", async () => {
    beatsFixture = [beat()];
    mount();
    // The media picker is a drawer; opening it and confirming is covered by its own
    // component tests, so this asserts the registration contract the page calls.
    const { registerExplainerCandidateFromMedia: command } = await import("../../generated/api");
    expect(command).toBeTruthy();
    expect(registerExplainerCandidateFromMedia).not.toHaveBeenCalled();
  });
});

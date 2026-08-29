import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getProfileVersion, type GenerationModel } from "../../generated/api";
import {
  getModelPlatformQuickCreateV2DirectImageStatus,
  getModelPlatformQuickCreateV2Run,
  listModelPlatformQuickCreateV2Readiness,
  previewModelPlatformQuickCreateV2DirectImage,
  previewModelPlatformQuickCreateV2ImageCandidates,
  previewModelPlatformQuickCreateV2ImageToVideo,
  selectModelPlatformQuickCreateV2ImageCandidate,
  submitModelPlatformQuickCreateV2DirectImage,
  submitModelPlatformQuickCreateV2ImageCandidates,
  submitModelPlatformQuickCreateV2ImageToVideo,
} from "../model-platform-v2/api";
import { QuickGenerationWorkbench } from "./QuickGenerationWorkbench";
import { commitQuickGeneration, listQuickGenerationPresets, listQuickGenerations, planQuickGeneration, type QuickGenerationRun } from "./quickGenerationClient";

vi.mock("../../generated/api", () => ({ getProfileVersion: vi.fn() }));
vi.mock("../model-platform-v2/api", () => ({
  getModelPlatformQuickCreateV2DirectImageStatus: vi.fn(),
  listModelPlatformQuickCreateV2Readiness: vi.fn(),
  previewModelPlatformQuickCreateV2DirectImage: vi.fn(),
  previewModelPlatformQuickCreateV2ImageCandidates: vi.fn(),
  previewModelPlatformQuickCreateV2ImageToVideo: vi.fn(),
  getModelPlatformQuickCreateV2Run: vi.fn(),
  selectModelPlatformQuickCreateV2ImageCandidate: vi.fn(),
  submitModelPlatformQuickCreateV2DirectImage: vi.fn(),
  submitModelPlatformQuickCreateV2ImageCandidates: vi.fn(),
  submitModelPlatformQuickCreateV2ImageToVideo: vi.fn(),
}));
vi.mock("../model-config/ProfileExecutionDetailButton", () => ({ ProfileExecutionDetailButton: () => null }));
vi.mock("./quickGenerationClient", () => ({
  cancelQuickGeneration: vi.fn(), commitQuickGeneration: vi.fn(), getQuickGeneration: vi.fn(), listQuickGenerations: vi.fn(),
  createQuickGenerationPreset: vi.fn(), deleteQuickGenerationPreset: vi.fn(), listQuickGenerationPresets: vi.fn(), updateQuickGenerationPreset: vi.fn(),
  planQuickGeneration: vi.fn(), regenerateQuickGenerationPrompt: vi.fn(), rerollQuickGenerationImages: vi.fn(),
  resumeQuickGeneration: vi.fn(), retryQuickGeneration: vi.fn(), selectQuickGenerationCandidate: vi.fn(),
}));

const models = [
  { id: "text-model", name: "本机规划", category: "TEXT", capabilities: ["LLM_STORY_PARSE"], actions: ["TEXT_PLANNING"], executable: true, routes: [{ action: "TEXT_PLANNING", capability: "LLM_STORY_PARSE", profile_version_id: "llm-1", profile_title: "本机规划", version_no: 1, status: "PUBLISHED", workflow_version_id: "llm-workflow", executable: true }] },
  { id: "video-model", name: "本机视频", category: "VIDEO", capabilities: ["VIDEO_T2V", "VIDEO_I2V"], actions: ["TEXT_TO_VIDEO", "IMAGE_TO_VIDEO"], executable: true, routes: [{ action: "TEXT_TO_VIDEO", capability: "VIDEO_T2V", profile_version_id: "video-1", profile_title: "本机视频", version_no: 1, status: "PUBLISHED", workflow_version_id: "workflow-1", executable: true }, { action: "IMAGE_TO_VIDEO", capability: "VIDEO_I2V", profile_version_id: "video-i2v-1", profile_title: "本机视频", version_no: 1, status: "PUBLISHED", workflow_version_id: "workflow-2", executable: true }] },
  { id: "candidate-video", name: "实验视频模型", category: "VIDEO", capabilities: ["VIDEO_T2V"], actions: ["TEXT_TO_VIDEO"], executable: false, routes: [{ action: "TEXT_TO_VIDEO", capability: "VIDEO_T2V", profile_version_id: "video-candidate-1", profile_title: "实验视频模型", version_no: 1, status: "CANDIDATE_UNVERIFIED", workflow_version_id: null, executable: false }] },
] as GenerationModel[];

const planned: QuickGenerationRun = {
  id: "run-1", mode: "TEXT_TO_VIDEO", state: "PLANNED", stage: "CONFIRMATION", story: { text: "雨夜橘猫撑伞穿过街道" }, story_sha256: "hash", language: "zh-CN",
  llm_profile_version_id: "llm-1", image_profile_version_id: null, video_profile_version_id: "video-1", image_candidate_count: 4, remote_outbound_confirmed: false,
  plan_hash: "plan", job_id: null, output_id: null, selected_candidate_id: null, selected_image_output_id: null, seed: null, retry_count: 0, error: {}, links: { workspace: "/quick-create?run=run-1" }, job: null, candidates: [], updated_at: "2026-08-28T00:00:00Z", model_parameters: { llm: {}, image: {}, video: {} },
  plan: { schema_version: "localdrama.quick-generation-plan.v1", mode: "TEXT_TO_VIDEO", result_kind: "VIDEO", story: "雨夜橘猫撑伞穿过街道", language: "zh-CN", video_plan: { title: "雨夜橘猫", video_prompt: "橘猫撑伞前行", keyframe_prompt: "Orange cat with umbrella", director_intent: {}, camera_movement: "DOLLY_IN", provider: "OLLAMA", model: "qwen", remote: false }, output_spec: { width: 480, height: 832, frame_count: 107, fps: 24, duration_seconds: 4.458, target_duration_ms: 4458, aspect_ratio: "15:26", source: "PUBLISHED_WORKFLOW", editable: true }, image_spec: null, image_candidate_count: 0, model_parameters: { llm: {}, image: {}, video: {} }, llm: { title: "本机规划", provider: "OLLAMA", model: "qwen", remote: false, profile_version_id: "llm-1" }, image: null, video: { title: "本机视频", capability: "VIDEO_T2V", profile_version_id: "video-1", workflow_version_id: "workflow-1", workflow_title: "视频工作流" }, runtime: { status: "READY" }, mutations: ["CREATE_QUICK_GENERATION_RUN"], confirmation_required: true },
};

function renderWorkbench() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><QuickGenerationWorkbench models={models} /></MemoryRouter></QueryClientProvider>);
}

describe("QuickGenerationWorkbench", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(listQuickGenerations).mockResolvedValue({ items: [] });
    vi.mocked(listQuickGenerationPresets).mockResolvedValue({ items: [] });
    vi.mocked(listModelPlatformQuickCreateV2Readiness).mockResolvedValue({ items: [], read_only: true, execution_switched: false });
    vi.mocked(previewModelPlatformQuickCreateV2DirectImage).mockResolvedValue({ preview: { capability_code: "IMAGE_CONCEPT", execution_profile_version_id: "image-v2", resolution_hash: "a".repeat(64), executable: true, blockers: [] }, read_only: true, execution_switched: true, legacy_quick_generation_touched: false });
    vi.mocked(submitModelPlatformQuickCreateV2DirectImage).mockResolvedValue({ execution: { capability_code: "IMAGE_CONCEPT", job_id: "v2-job", execution_snapshot_id: "v2-snapshot", execution_snapshot_hash: "b".repeat(64), handler_code: "comfy.workflow.v2", handler_version: "v1", idempotent_replay: false }, execution_switched: true, legacy_quick_generation_touched: false });
    vi.mocked(getModelPlatformQuickCreateV2DirectImageStatus).mockResolvedValue({ execution: { capability_code: "IMAGE_CONCEPT", job_id: "v2-job", state: "SUCCEEDED", progress: { percent: 100 }, error_code: null, error_detail_redacted: null, execution_snapshot_id: "v2-snapshot", execution_snapshot_hash: "b".repeat(64), artifacts: [{ artifact_id: "artifact-v2", kind: "COMFY_OUTPUT", download_url: "/api/v1/artifacts/artifact-v2/download" }] }, read_only: true, execution_switched: true, legacy_quick_generation_touched: false });
    vi.mocked(previewModelPlatformQuickCreateV2ImageCandidates).mockResolvedValue({ plan: { execution_profile_version_id: "image-v2", executable: true, blockers: [], candidates: [{ ordinal: 1, seed: 42, resolution_hash: "c".repeat(64) }] }, read_only: true, execution_switched: true, legacy_quick_generation_touched: false });
    vi.mocked(submitModelPlatformQuickCreateV2ImageCandidates).mockResolvedValue({ run: { id: "i2v-run", mode: "TEXT_TO_IMAGE_TO_VIDEO", state: "RUNNING", idempotent_replay: false }, executions: [{ capability_code: "IMAGE_CONCEPT", job_id: "image-job", execution_snapshot_id: "image-snapshot" }], execution_switched: true, legacy_quick_generation_touched: false });
    // The V2 run aggregate is owned by the backend: it stays AWAITING_SELECTION
    // until the selection mutation freezes the chosen candidate, then reports
    // IMAGE_SELECTED on every subsequent poll.
    let v2SelectionFrozen = false;
    const awaitingSelectionRun = { id: "i2v-run", mode: "TEXT_TO_IMAGE_TO_VIDEO" as const, state: "AWAITING_SELECTION" as const, selected_step_id: null, final_step_id: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z", revision: 1, steps: [{ id: "image-step", step_no: 1, kind: "IMAGE_CANDIDATE" as const, state: "READY", capability_code: "IMAGE_CONCEPT", job_id: "image-job", job_state: "SUCCEEDED", progress: {}, error_code: null, error_detail_redacted: null, selection_rank: 1, input_artifact_id: null, output_artifact: { artifact_id: "image-artifact", kind: "COMFY_OUTPUT" as const, download_url: "/api/v1/artifacts/image-artifact/download" } }] };
    const selectedRun = { id: "i2v-run", mode: "TEXT_TO_IMAGE_TO_VIDEO" as const, state: "IMAGE_SELECTED" as const, selected_step_id: "image-step", final_step_id: null, created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:01Z", revision: 2, steps: [] };
    vi.mocked(getModelPlatformQuickCreateV2Run).mockImplementation(async () => ({ run: v2SelectionFrozen ? selectedRun : awaitingSelectionRun, read_only: true, execution_switched: true, legacy_quick_generation_touched: false }));
    vi.mocked(selectModelPlatformQuickCreateV2ImageCandidate).mockImplementation(async () => {
      v2SelectionFrozen = true;
      return { run: selectedRun, execution_switched: true, legacy_quick_generation_touched: false };
    });
    vi.mocked(previewModelPlatformQuickCreateV2ImageToVideo).mockResolvedValue({ preview: { run_id: "i2v-run", selected_image_artifact_id: "image-artifact", execution_profile_version_id: "video-v2", resolution_hash: "d".repeat(64), executable: true, blockers: [] }, read_only: true, execution_switched: true, legacy_quick_generation_touched: false });
    vi.mocked(submitModelPlatformQuickCreateV2ImageToVideo).mockResolvedValue({ execution: { capability_code: "VIDEO_I2V", run_id: "i2v-run", job_id: "video-job", execution_snapshot_id: "video-snapshot", execution_snapshot_hash: "e".repeat(64), handler_code: "comfy", handler_version: "v1", idempotent_replay: false }, execution_switched: true, legacy_quick_generation_touched: false });
    vi.mocked(getProfileVersion).mockResolvedValue({ profile_version: { execution: { provider: "OLLAMA", runtime: { base_url: "http://127.0.0.1:11434" } }, capability_contract: {} } } as never);
  });

  it("states that the result is independent from projects", async () => {
    renderWorkbench();
    expect(screen.getByRole("heading", { name: "描述一个画面，直接得到作品" })).toBeTruthy();
    expect(screen.getByText("不创建项目")).toBeTruthy();
    await waitFor(() => expect(listQuickGenerations).toHaveBeenCalled());
  });

  it("shows V2 cutover blockers without changing the active V1 workflow", async () => {
    vi.mocked(listModelPlatformQuickCreateV2Readiness).mockResolvedValue({
      items: [
        { mode: "TEXT_TO_IMAGE", capability_code: "IMAGE_CONCEPT", execution_profile_version_id: "image-v2", ready: true, blocker: null },
        { mode: "TEXT_TO_VIDEO", capability_code: "VIDEO_T2V", execution_profile_version_id: "video-v2", ready: false, blocker: "QUICK_CREATE_V2_MULTI_STAGE_PIPELINE_REQUIRED" },
      ],
      read_only: true,
      execution_switched: false,
    });
    renderWorkbench();

    expect(await screen.findByText(/部分能力尚未满足切换条件；可查看阻塞原因/)).toBeTruthy();
    expect(screen.getByText("QUICK_CREATE_V2_MULTI_STAGE_PIPELINE_REQUIRED")).toBeTruthy();
    expect(screen.getByRole("button", { name: "生成执行规划" })).toBeTruthy();
  });

  it("submits the explicit V2 direct-image trial without invoking the V1 planner", async () => {
    vi.mocked(listModelPlatformQuickCreateV2Readiness).mockResolvedValue({
      items: [{ mode: "TEXT_TO_IMAGE", capability_code: "IMAGE_CONCEPT", execution_profile_version_id: "image-v2", ready: true, blocker: null }],
      read_only: true,
      execution_switched: false,
    });
    renderWorkbench();
    fireEvent.click(screen.getByRole("radio", { name: /^文生图 可选择/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "你想看到什么？" }), { target: { value: "雨夜的橘猫" } });
    fireEvent.click(await screen.findByText("查看 V2 切流前置条件与试运行"));
    fireEvent.click(screen.getByRole("button", { name: "预检 V2 单次文生图" }));
    await waitFor(() => expect(previewModelPlatformQuickCreateV2DirectImage).toHaveBeenCalledWith({ prompt: "雨夜的橘猫", run_overrides: {} }));
    fireEvent.click(await screen.findByRole("button", { name: "确认提交 V2 单次文生图" }));
    await waitFor(() => expect(submitModelPlatformQuickCreateV2DirectImage).toHaveBeenCalled());
    expect(planQuickGeneration).not.toHaveBeenCalled();
    expect(await screen.findByText("V2 Job：v2-job。它独立于当前 V1 快速生成记录。")).toBeTruthy();
    expect((await screen.findByRole("img", { name: "V2 单次文生图结果" })).getAttribute("src")).toBe("/api/v1/artifacts/artifact-v2/download");
    expect(getModelPlatformQuickCreateV2DirectImageStatus).toHaveBeenCalledWith("v2-job");
  });

  it("runs the V2 candidate-to-video trial through a selected V2 artifact only", async () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("radio", { name: /^文生图，再图生视频/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "你想看到什么？" }), { target: { value: "雨夜的橘猫" } });
    fireEvent.click(await screen.findByText("查看 V2 切流前置条件与试运行"));
    fireEvent.click(screen.getByRole("button", { name: "预检 V2 候选图" }));
    await waitFor(() => expect(previewModelPlatformQuickCreateV2ImageCandidates).toHaveBeenCalledWith({ prompt: "雨夜的橘猫", candidate_count: 4 }));
    fireEvent.click(screen.getByRole("button", { name: "确认提交 V2 候选图" }));
    await waitFor(() => expect(submitModelPlatformQuickCreateV2ImageCandidates).toHaveBeenCalled());
    const candidate = await screen.findByRole("img", { name: "V2 图片候选 1" });
    fireEvent.click(candidate.closest("button")!);
    fireEvent.click(screen.getByRole("checkbox", { name: /我已检查主体/ }));
    fireEvent.click(screen.getByRole("button", { name: "确认选择首帧" }));
    await waitFor(() => expect(selectModelPlatformQuickCreateV2ImageCandidate).toHaveBeenCalledWith("i2v-run", "image-step"));
    fireEvent.click(await screen.findByRole("button", { name: "预检 V2 图生视频" }));
    await waitFor(() => expect(previewModelPlatformQuickCreateV2ImageToVideo).toHaveBeenCalledWith("i2v-run"));
    fireEvent.click(await screen.findByRole("button", { name: "确认提交 V2 图生视频" }));
    await waitFor(() => expect(submitModelPlatformQuickCreateV2ImageToVideo).toHaveBeenCalledWith("i2v-run", "d".repeat(64), expect.any(String)));
    expect(planQuickGeneration).not.toHaveBeenCalled();
  });

  it("keeps executable generation actions selectable, blocks incomplete routes, and classifies a dual-capability model under both video actions", async () => {
    renderWorkbench();
    expect((screen.getByRole("radio", { name: /^文生图 可选择/ }) as HTMLInputElement).disabled).toBe(false);
    expect((screen.getByRole("radio", { name: /^文生图，再图生视频/ }) as HTMLInputElement).disabled).toBe(false);
    expect((screen.getByRole("radio", { name: /^文生视频 / }) as HTMLInputElement).disabled).toBe(false);
    expect(await screen.findByRole("option", { name: "本机视频 · 文生视频 / 图生视频" })).toBeTruthy();
    expect((screen.getByRole("option", { name: /实验视频模型 · 文生视频 · 路线未就绪/ }) as HTMLOptionElement).disabled).toBe(true);
  });

  it("plans and commits a standalone video run", async () => {
    vi.mocked(planQuickGeneration).mockResolvedValue({ run: planned });
    vi.mocked(commitQuickGeneration).mockResolvedValue({ run: { ...planned, state: "GENERATING", stage: "VIDEO_GENERATING", job_id: "job-1" } });
    renderWorkbench();
    fireEvent.change(screen.getByRole("textbox", { name: "你想看到什么？" }), { target: { value: planned.story.text } });
    fireEvent.click(screen.getByRole("button", { name: "生成执行规划" }));
    expect(await screen.findByRole("heading", { name: "雨夜橘猫" })).toBeTruthy();
    expect(screen.getByText(/不会创建项目、分集或镜头/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认规划并生成视频" }));
    await waitFor(() => expect(commitQuickGeneration).toHaveBeenCalledWith("run-1", expect.any(AbortSignal)));
  });
});

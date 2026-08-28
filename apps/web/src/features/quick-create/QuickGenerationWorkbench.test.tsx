import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getProfileVersion, type GenerationModel } from "../../generated/api";
import { QuickGenerationWorkbench } from "./QuickGenerationWorkbench";
import { commitQuickGeneration, listQuickGenerationPresets, listQuickGenerations, planQuickGeneration, type QuickGenerationRun } from "./quickGenerationClient";

vi.mock("../../generated/api", () => ({ getProfileVersion: vi.fn() }));
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
    vi.mocked(getProfileVersion).mockResolvedValue({ profile_version: { execution: { provider: "OLLAMA", runtime: { base_url: "http://127.0.0.1:11434" } }, capability_contract: {} } } as never);
  });

  it("states that the result is independent from projects", async () => {
    renderWorkbench();
    expect(screen.getByRole("heading", { name: "描述一个画面，直接得到作品" })).toBeTruthy();
    expect(screen.getByText("不创建项目")).toBeTruthy();
    await waitFor(() => expect(listQuickGenerations).toHaveBeenCalled());
  });

  it("keeps every generation action selectable and classifies a dual-capability model under both video actions", async () => {
    renderWorkbench();
    expect((screen.getByRole("radio", { name: /^文生图 可选择/ }) as HTMLInputElement).disabled).toBe(false);
    expect((screen.getByRole("radio", { name: /^文生图，再图生视频/ }) as HTMLInputElement).disabled).toBe(false);
    expect((screen.getByRole("radio", { name: /^文生视频 / }) as HTMLInputElement).disabled).toBe(false);
    expect(await screen.findByRole("option", { name: "本机视频 · 文生视频 / 图生视频" })).toBeTruthy();
    expect((screen.getByRole("option", { name: /实验视频模型 · 文生视频 · 路线未就绪/ }) as HTMLOptionElement).disabled).toBe(false);
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

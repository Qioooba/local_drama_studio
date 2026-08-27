import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getProfileVersion, type Profile } from "../../generated/api";
import {
  cancelOneSentenceVideoRun,
  commitOneSentenceVideoRun,
  getOneSentenceVideoRun,
  listOneSentenceVideoRuns,
  planOneSentenceVideo,
  rerollOneSentenceImages,
  selectOneSentenceImageCandidate,
  type OneSentenceMode,
  type OneSentenceVideoRun,
} from "./oneSentenceVideoClient";
import { OneSentenceVideoWizard } from "./OneSentenceVideoWizard";

vi.mock("../../generated/api", () => ({ getProfileVersion: vi.fn() }));
vi.mock("../model-config/ProfileExecutionDetailButton", () => ({ ProfileExecutionDetailButton: () => <button type="button">查看执行详情</button> }));
vi.mock("./oneSentenceVideoClient", () => ({
  cancelOneSentenceVideoRun: vi.fn(), commitOneSentenceVideoRun: vi.fn(), getOneSentenceVideoRun: vi.fn(),
  listOneSentenceVideoRuns: vi.fn(), planOneSentenceVideo: vi.fn(), rerollOneSentenceImages: vi.fn(),
  resumeOneSentenceVideoRun: vi.fn(), retryOneSentenceVideoRun: vi.fn(), selectOneSentenceImageCandidate: vi.fn(),
}));

const remoteLlm = { capability: "LLM_STORY_PARSE", status: "PUBLISHED", title: "Remote Story Planner", version_id: "llm-remote", version_no: 3 } as Profile;
const localLlm = { capability: "LLM_STORY_PARSE", status: "PUBLISHED", title: "Local Story Planner", version_id: "llm-local", version_no: 2 } as Profile;
const t2v = { capability: "VIDEO_T2V", status: "PUBLISHED", title: "H3 Core", version_id: "t2v-1", version_no: 5 } as Profile;
const otherT2v = { capability: "VIDEO_T2V", status: "PUBLISHED", title: "Alternative Workflow", version_id: "t2v-2", version_no: 2 } as Profile;
const t2i = { capability: "IMAGE_CONCEPT", status: "PUBLISHED", title: "Flux Concept", version_id: "t2i-1", version_no: 4 } as Profile;
const i2v = { capability: "VIDEO_I2V", status: "PUBLISHED", title: "H3 Image Motion", version_id: "i2v-1", version_no: 6 } as Profile;
const story = "雨夜霓虹灯下，一只橘猫撑伞穿过街道。";

function job(state: string): NonNullable<OneSentenceVideoRun["job"]> {
  return { id: "job-1", type: "GENERATION_VARIANT", project_id: "project-1", state, channel: "GPU_H3", priority: 100, max_attempts: 1, revision: 1 } as NonNullable<OneSentenceVideoRun["job"]>;
}

function run(mode: OneSentenceMode, state: OneSentenceVideoRun["state"] = "PLANNED"): OneSentenceVideoRun {
  const imageFirst = mode === "KEYFRAME_I2V";
  return {
    id: "run-1", mode, state, stage: state === "PLANNED" ? "CONFIRMATION" : state,
    story: { text: story }, story_sha256: "hash", language: "zh-CN", llm_profile_version_id: "llm-remote",
    image_profile_version_id: imageFirst ? "t2i-1" : null, video_profile_version_id: imageFirst ? "i2v-1" : "t2v-1",
    image_candidate_count: 4, remote_outbound_confirmed: true, plan_hash: "plan-hash", project_id: null,
    episode_id: null, shot_id: null, variant_id: null, job_id: null, media_version_id: null,
    selected_candidate_id: null, selected_image_media_version_id: null, seed: null, retry_count: 0,
    error: {}, links: {}, candidates: [], updated_at: "2026-08-26T10:00:00Z",
    plan: {
      schema_version: "localdrama.one-sentence-video-plan.v2", mode, story, language: "zh-CN",
      video_plan: { title: "雨夜橘猫", video_prompt: "橘猫撑伞前行，镜头稳定推进", keyframe_prompt: "雨夜霓虹街道中的橘猫，透明雨伞，电影构图", director_intent: {}, camera_movement: "DOLLY_IN", provider: "OPENAI_COMPAT", model: "deepseek", remote: true },
      output_spec: { width: 480, height: 832, frame_count: 107, fps: 24, duration_seconds: 4.458, target_duration_ms: 4458, aspect_ratio: "15:26", source: "PUBLISHED_WORKFLOW", editable: false },
      image_spec: imageFirst ? { width: 768, height: 1344, aspect_ratio: "4:7", source: "PUBLISHED_WORKFLOW", editable: false } : null,
      image_candidate_count: 4,
      llm: { title: "Remote Story Planner", provider: "OPENAI_COMPAT", model: "deepseek", remote: true, profile_version_id: "llm-remote" },
      image: imageFirst ? { title: "Flux Concept", profile_version_id: "t2i-1", workflow_version_id: "workflow-image", workflow_title: "Flux 首帧" } : null,
      video: { title: imageFirst ? "H3 Image Motion" : "H3 Core", capability: imageFirst ? "VIDEO_I2V" : "VIDEO_T2V", profile_version_id: imageFirst ? "i2v-1" : "t2v-1", workflow_version_id: "workflow-video", workflow_title: "H3 视频" },
      runtime: { status: "READY" }, production_mutations: [], confirmation_required: true,
    },
  };
}

function awaitingSelection(): OneSentenceVideoRun {
  return {
    ...run("KEYFRAME_I2V", "AWAITING_SELECTION"), stage: "IMAGE_SELECTION", project_id: "project-1", episode_id: "episode-1", shot_id: "shot-1",
    links: { project: "/projects/project-1", generation: "/projects/project-1/episodes/episode-1/generation/shot-1", review: "/projects/project-1/episodes/episode-1/review" },
    candidates: [0, 1, 2, 3].map((ordinal) => ({ id: `candidate-${ordinal}`, batch_no: 1, ordinal, state: "READY", seed: 100 + ordinal, variant_id: `variant-${ordinal}`, job_id: `job-${ordinal}`, media_version_id: `media-${ordinal}`, parent_candidate_id: null, selected: false, error: {}, job: null })),
  };
}

function renderWizard(onProjectCreated = vi.fn(), availableProfiles: Profile[] = [t2v, remoteLlm, localLlm, otherT2v, t2i, i2v]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><OneSentenceVideoWizard profiles={availableProfiles} onProjectCreated={onProjectCreated} /></MemoryRouter></QueryClientProvider>);
  return onProjectCreated;
}

async function enterStoryAndConsent() {
  fireEvent.change(screen.getByRole("textbox", { name: "你想看到什么？" }), { target: { value: story } });
  fireEvent.click(await screen.findByRole("checkbox", { name: /发送到所选远端故事规划服务/ }));
}

describe("OneSentenceVideoWizard", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(listOneSentenceVideoRuns).mockResolvedValue({ items: [] });
    vi.mocked(getProfileVersion).mockImplementation(async (id) => ({ profile_version: {
      id, capability: "LLM_STORY_PARSE", status: "PUBLISHED", title: "Planner", code: "planner", version_no: 1, revision: 1,
      input_contract: {}, parameter_schema: {}, output_contract: {}, resource_policy: {}, contract_hash: "hash", validation: null,
      capability_contract: { provider: id === "llm-local" ? "OLLAMA_LOOPBACK" : "OPENAI_COMPAT" },
      execution: { schema_version: "v1", runtime: { base_url: id === "llm-local" ? "http://127.0.0.1:11434" : "https://api.example.com" }, workflow: null, components: [], provider: id === "llm-local" ? "OLLAMA_LOOPBACK" : "OPENAI_COMPAT", defaults: {}, override_schema: {}, worker_policy: null, model_bundle: {}, fingerprints: { execution: "e", model_bundle: "m", workflow: null, manifest: null }, read_only: true, local_only: true },
    } } as never));
  });

  it("preflights the direct route with explicit remote consent and the real published workflow spec", async () => {
    vi.mocked(planOneSentenceVideo).mockResolvedValue({ run: run("DIRECT_T2V") });
    renderWizard();
    fireEvent.click(screen.getByText("模型、语言与候选数量"));
    expect(within(screen.getByRole("combobox", { name: "故事规划模型" })).getAllByRole("option")).toHaveLength(2);
    expect(within(screen.getByRole("combobox", { name: "本机文生视频模型" })).getAllByRole("option")).toHaveLength(2);
    await enterStoryAndConsent();
    fireEvent.click(screen.getByRole("button", { name: "检查并生成规划" }));

    expect(await screen.findByRole("heading", { name: "雨夜橘猫" })).toBeTruthy();
    expect(screen.getByText("480×832 · 24 fps")).toBeTruthy();
    expect(planOneSentenceVideo).toHaveBeenCalledWith(expect.objectContaining({ mode: "DIRECT_T2V", video_profile_version_id: "t2v-1", image_profile_version_id: null, allow_remote_outbound: true }), expect.any(String), expect.any(AbortSignal));
    expect(commitOneSentenceVideoRun).not.toHaveBeenCalled();
  });

  it("creates a durable direct-video run and cancels the backend job rather than just polling", async () => {
    const planned = run("DIRECT_T2V");
    const generating = { ...planned, state: "GENERATING", stage: "VIDEO_GENERATING", project_id: "project-1", episode_id: "episode-1", shot_id: "shot-1", variant_id: "variant-1", job_id: "job-1", links: { project: "/projects/project-1", generation: "/generation", review: "/review" }, job: job("QUEUED") } as OneSentenceVideoRun;
    vi.mocked(planOneSentenceVideo).mockResolvedValue({ run: planned });
    vi.mocked(commitOneSentenceVideoRun).mockResolvedValue({ run: generating });
    vi.mocked(getOneSentenceVideoRun).mockResolvedValue({ run: generating });
    vi.mocked(cancelOneSentenceVideoRun).mockResolvedValue({ run: { ...generating, state: "CANCELLED", stage: "CANCELLED", job: job("CANCELLED") } });
    const created = renderWizard();
    await enterStoryAndConsent();
    fireEvent.click(screen.getByRole("button", { name: "检查并生成规划" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认规划并生成视频" }));
    await waitFor(() => expect(created).toHaveBeenCalledWith("project-1"));
    fireEvent.click(screen.getByRole("button", { name: "取消后台生成" }));
    await waitFor(() => expect(cancelOneSentenceVideoRun).toHaveBeenCalledWith("run-1", expect.any(AbortSignal)));
    expect(await screen.findByText(/生成已取消/)).toBeTruthy();
  });

  it("separates image selection from approval and supports a selected-composition reroll", async () => {
    const planned = run("KEYFRAME_I2V");
    const awaiting = awaitingSelection();
    vi.mocked(planOneSentenceVideo).mockResolvedValue({ run: planned });
    vi.mocked(commitOneSentenceVideoRun).mockResolvedValue({ run: awaiting });
    vi.mocked(rerollOneSentenceImages).mockResolvedValue({ run: { ...awaiting, state: "GENERATING", stage: "IMAGE_GENERATING", candidates: [...awaiting.candidates, { ...awaiting.candidates[0], id: "candidate-5", batch_no: 2, ordinal: 1, state: "QUEUED", media_version_id: null, parent_candidate_id: "candidate-1" }] } });
    renderWizard();
    fireEvent.click(screen.getByRole("radio", { name: /先选图，再生成视频/ }));
    fireEvent.click(screen.getByText("模型、语言与候选数量"));
    expect(screen.getByRole("combobox", { name: "本机文生图模型" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "本机图生视频模型" })).toBeTruthy();
    await enterStoryAndConsent();
    fireEvent.click(screen.getByRole("button", { name: "检查并生成规划" }));
    expect(await screen.findByText("4 张 · 768×1344")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认规划并生成首帧候选" }));

    const candidate = await screen.findByRole("button", { name: /首帧候选 1/ });
    expect((within(candidate).getByRole("img", { name: "首帧候选 1" }) as HTMLImageElement).src).toContain("/media-0/thumbnail?size=small");
    fireEvent.click(candidate);
    expect((screen.getByRole("button", { name: "批准此首帧并生成视频" }) as HTMLButtonElement).disabled).toBe(true);
    expect(selectOneSentenceImageCandidate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "基于选中构图换 Seed" }));
    await waitFor(() => expect(rerollOneSentenceImages).toHaveBeenCalledWith("run-1", { count: 1, parent_candidate_id: "candidate-0" }, expect.any(AbortSignal)));
  });

  it("requires explicit visual review before approving a KEYFRAME and submitting I2V", async () => {
    const awaiting = awaitingSelection();
    const videoGenerating = { ...awaiting, state: "GENERATING", stage: "VIDEO_GENERATING", selected_candidate_id: "candidate-1", selected_image_media_version_id: "media-1", variant_id: "variant-video", job_id: "job-video", job: job("QUEUED") } as OneSentenceVideoRun;
    vi.mocked(planOneSentenceVideo).mockResolvedValue({ run: run("KEYFRAME_I2V") });
    vi.mocked(commitOneSentenceVideoRun).mockResolvedValue({ run: awaiting });
    vi.mocked(selectOneSentenceImageCandidate).mockResolvedValue({ run: videoGenerating });
    renderWizard();
    fireEvent.click(screen.getByRole("radio", { name: /先选图，再生成视频/ }));
    await enterStoryAndConsent();
    fireEvent.click(screen.getByRole("button", { name: "检查并生成规划" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认规划并生成首帧候选" }));
    fireEvent.click(await screen.findByRole("button", { name: /首帧候选 2/ }));
    const approve = screen.getByRole("button", { name: "批准此首帧并生成视频" });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /我已检查主体、构图、风格和画面质量/ }));
    fireEvent.click(approve);
    await waitFor(() => expect(selectOneSentenceImageCandidate).toHaveBeenCalledWith("run-1", "candidate-1", expect.any(AbortSignal)));
    expect(await screen.findByText(/首帧已批准并锁定/)).toBeTruthy();
  });

  it("falls back to image-first when direct T2V is unavailable", async () => {
    renderWizard(vi.fn(), [remoteLlm, localLlm, t2i, i2v]);

    const direct = screen.getByRole("radio", { name: /直接生成视频/ }) as HTMLInputElement;
    const imageFirst = screen.getByRole("radio", { name: /先选图，再生成视频/ }) as HTMLInputElement;
    await waitFor(() => {
      expect(direct.disabled).toBe(true);
      expect(imageFirst.checked).toBe(true);
    });
    expect(screen.getByText("暂无已发布的文生视频模型")).toBeTruthy();
    fireEvent.click(screen.getByText("模型、语言与候选数量"));
    expect(screen.getByRole("combobox", { name: "本机文生图模型" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "本机图生视频模型" })).toBeTruthy();
  });

  it("keeps a candidate unselectable until its preview thumbnail is ready", async () => {
    const awaiting = awaitingSelection();
    const processing = {
      ...awaiting,
      state: "GENERATING",
      stage: "IMAGE_GENERATING",
      candidates: [{
        ...awaiting.candidates[0],
        state: "PROCESSING",
        job: { ...job("SUCCEEDED"), progress: { percent: 0.38 } },
      }],
    } as OneSentenceVideoRun;
    vi.mocked(listOneSentenceVideoRuns).mockResolvedValue({ items: [processing] });
    renderWizard();
    fireEvent.click(await screen.findByText("最近的一句话任务"));
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(story) }));

    const candidate = await screen.findByRole("button", { name: /候选 1/ });
    expect((candidate as HTMLButtonElement).disabled).toBe(true);
    expect(within(candidate).queryByRole("img")).toBeNull();
    expect(within(candidate).getByText("38%")).toBeTruthy();
    expect(within(candidate).getByText(/正在准备预览/)).toBeTruthy();
    expect(screen.getByText("本机正在生成首帧候选")).toBeTruthy();
  });
});

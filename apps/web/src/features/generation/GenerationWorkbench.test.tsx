import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createFrameAnchor,
  createGenerationIntent,
  createKeyframeCandidate,
  createPrompt,
  planGenerationVariant,
  submitGenerationVariant,
} from "../../generated/api";
import {
  approvedKeyframeMediaIds,
  eligibleGenerationProfiles,
  GenerationWorkbench,
  generationWorkbenchDraftKey,
  type GenerationStep,
} from "./GenerationWorkbench";

vi.mock("../../generated/api", () => ({
  createFrameAnchor: vi.fn(),
  createGenerationIntent: vi.fn(),
  createKeyframeCandidate: vi.fn(),
  createPrompt: vi.fn(),
  planGenerationVariant: vi.fn(),
  resolveEffectiveConfiguration: vi.fn().mockResolvedValue({ configuration: { blocking_errors: [], fingerprint: "sha256:" + "a".repeat(64) } }),
  submitGenerationVariant: vi.fn(),
  listMotionControls: vi.fn().mockResolvedValue({ items: [] }),
  createMotionControl: vi.fn(),
  listProductionTiers: vi.fn().mockResolvedValue({
    items: [
      {
        code: "PRODUCTION",
        label: "正式成片",
        frames: 175,
        resolution: { "9:16": [480, 832], "16:9": [864, 480] },
        denoise: 1.0,
        steps: 20,
        cfg: 1.0,
        default_takes: 8,
      },
    ],
    default_tier: "DRAFT",
  }),
  getRef2VaCapability: vi.fn().mockResolvedValue({
    capability: {
      capability: "H3_REF2VA_UNAVAILABLE",
      supported: false,
      reason: "manifest 缺少 ref2va 模型",
      manifest_hint: {},
    },
  }),
}));

const video = {
  media_version_id: "video-123456789",
  media_asset_id: "asset-1",
  project_id: "project-1",
  media_kind: "VIDEO",
  stage: "PROXY",
  decision: null,
  is_stale: null,
};

function renderWorkbench(
  selectedShotId: string | null = null,
  onOpenReviews = vi.fn(),
  activeStep?: GenerationStep,
  onStepChange?: (step: GenerationStep) => void
) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GenerationWorkbench
        projectId="project-1"
        profiles={[]}
        candidates={[video]}
        shots={selectedShotId ? [{ id: selectedShotId, code: "SH-001", status: "DRAFT" }] : []}
        selectedShotId={selectedShotId}
        onSelectShot={vi.fn()}
        onOpenProfiles={vi.fn()}
        onOpenReviews={onOpenReviews}
        activeStep={activeStep}
        onStepChange={onStepChange}
      />
    </QueryClientProvider>
  );
}

describe("GenerationWorkbench step workflow", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(createGenerationIntent).mockReset();
    vi.mocked(createPrompt).mockReset();
    vi.mocked(planGenerationVariant).mockReset();
    vi.mocked(submitGenerationVariant).mockReset();
    vi.mocked(createFrameAnchor).mockReset().mockImplementation(async (_id, payload) => ({
      frame_anchor: {
        id: "anchor-1",
        source_media_version_id: video.media_version_id,
        source_time_us: payload.source_time_us ?? (payload.position_mode === "LAST_FRAME" ? 2_000_000 : 0),
        source_frame_index: payload.position_mode === "LAST_FRAME" ? 2 : payload.source_time_us ? 1 : 0,
        extracted_media_version_id: "frame-1",
        role_hint: payload.role_hint,
        sha256: "abcdef1234567890",
        requested_time_us: payload.source_time_us ?? null,
        resolved_time_us: payload.source_time_us ?? (payload.position_mode === "LAST_FRAME" ? 2_000_000 : 0),
        source_sha256: "source-sha",
        extraction_method: "FFPROBE_PTS_FRAME_INDEX",
      },
    }));
    vi.mocked(createKeyframeCandidate).mockReset().mockResolvedValue({
      media: {
        id: "keyframe-1",
        media_asset_id: "asset-kf",
        project_id: "project-1",
        owner_type: "SHOT",
        owner_id: "shot-1",
        media_kind: "IMAGE",
        stage: "KEYFRAME",
        parent_version_id: "frame-1",
        duplicate: false,
      },
    });
  });

  it("uses exact canonical capabilities and only accepts approved non-stale keyframes", () => {
    const profiles = [
      { id: "exact", version_id: "exact-v1", code: "i2v", title: "I2V", capability: "VIDEO_I2V", status: "PUBLISHED" },
      { id: "draft", version_id: "draft-v1", code: "i2v-draft", title: "I2V draft", capability: "VIDEO_I2V", status: "DRAFT" },
      { id: "fuzzy", version_id: "fuzzy-v1", code: "first", title: "First frame", capability: "VIDEO_FIRST_FRAME_I2V", status: "PUBLISHED" },
      { id: "reference", version_id: "reference-v1", code: "ref", title: "Reference", capability: "VIDEO_REFERENCE", status: "PUBLISHED" },
    ] as never;
    expect(eligibleGenerationProfiles(profiles, "I2V").map((item) => item.id)).toEqual(["exact"]);
    expect(eligibleGenerationProfiles(profiles, "R2V").map((item) => item.id)).toEqual(["reference"]);

    const keyframes = [
      { media_version_id: "unapproved", shot_id: "shot-1", media_kind: "IMAGE", stage: "KEYFRAME", decision: null, is_stale: 0 },
      { media_version_id: "approved", shot_id: "shot-1", media_kind: "IMAGE", stage: "KEYFRAME", decision: "APPROVED", is_stale: 0 },
      { media_version_id: "other-shot", shot_id: "shot-2", media_kind: "IMAGE", stage: "KEYFRAME", decision: "APPROVED", is_stale: 0 },
      { media_version_id: "stale", shot_id: "shot-1", media_kind: "IMAGE", stage: "KEYFRAME", decision: "APPROVED", is_stale: 1 },
      { media_version_id: "wrong-stage", shot_id: "shot-1", media_kind: "IMAGE", stage: "FORMAL", decision: "APPROVED", is_stale: 0 },
    ] as never;
    expect(approvedKeyframeMediaIds(keyframes, "shot-1", { media_version_id: "gate-approved", shot_id: "shot-1" })).toEqual(["approved", "gate-approved"]);
    expect(approvedKeyframeMediaIds(keyframes, "shot-1", { media_version_id: "wrong-gate", shot_id: "shot-2" })).toEqual(["approved"]);
  });

  it("exposes generation mode selection and step rail navigation", () => {
    const onStepChange = vi.fn();
    renderWorkbench("shot-1", vi.fn(), "setup", onStepChange);

    const imageMode = screen.getByRole("button", { name: /文字生成图片/ });
    const videoMode = screen.getByRole("button", { name: /图片生成视频/ });
    expect(imageMode.getAttribute("aria-pressed")).toBe("false");
    expect(videoMode.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(imageMode);
    expect(imageMode.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "下一步：输入与控制 →" })).toHaveProperty("disabled", true);
    expect(screen.getByText(/还没有可用能力/)).toBeTruthy();

    const nav = screen.getByRole("navigation", { name: "生成阶段导航" });
    const inputStepBtn = within(nav).getByRole("button", { name: /输入与控制/ });
    fireEvent.click(inputStepBtn);
    expect(onStepChange).toHaveBeenCalledWith("inputs");
  });

  it("sends first, current and last requests with mutually exclusive positions on inputs stage", async () => {
    renderWorkbench("shot-1", vi.fn(), "inputs");
    fireEvent.click(screen.getByRole("button", { name: "用作首帧" }));
    await waitFor(() =>
      expect(createFrameAnchor).toHaveBeenLastCalledWith(video.media_version_id, {
        position_mode: "FIRST_FRAME",
        role_hint: "FIRST_FRAME",
      })
    );

    fireEvent.change(screen.getByPlaceholderText("当前秒"), { target: { value: "1.25" } });
    fireEvent.click(screen.getByRole("button", { name: "用作当前帧" }));
    await waitFor(() =>
      expect(createFrameAnchor).toHaveBeenLastCalledWith(video.media_version_id, {
        source_time_us: 1_250_000,
        role_hint: "CURRENT_FRAME",
      })
    );

    fireEvent.click(screen.getByRole("button", { name: "用作末帧" }));
    await waitFor(() =>
      expect(createFrameAnchor).toHaveBeenLastCalledWith(video.media_version_id, {
        position_mode: "LAST_FRAME",
        role_hint: "LAST_FRAME",
      })
    );
    expect(await screen.findByText(/末帧已填入草稿/)).toBeTruthy();
  });

  it("creates a shot-owned candidate and opens review without auto approval", async () => {
    const openReviews = vi.fn();
    renderWorkbench("shot-1", openReviews, "inputs");
    fireEvent.click(screen.getByRole("button", { name: "用作首帧" }));
    await screen.findByText(/首帧已填入草稿/);
    fireEvent.click(screen.getByRole("button", { name: "创建关键帧候选并进入人工审核" }));
    await waitFor(() => expect(createKeyframeCandidate).toHaveBeenCalledWith("frame-1", "shot-1"));
    expect(openReviews).toHaveBeenCalledWith("keyframe-1");
  });

  it("carries a locked Frame Bridge source into review without treating it as approved", async () => {
    const openReviews = vi.fn();
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <GenerationWorkbench
          projectId="project-1"
          profiles={[]}
          candidates={[video]}
          shots={[{ id: "shot-1", code: "SH-001", status: "DRAFT" }]}
          selectedShotId="shot-1"
          initialSourceImageId="frame-bridge-image"
          onSelectShot={vi.fn()}
          onOpenProfiles={vi.fn()}
          onOpenReviews={openReviews}
          activeStep="inputs"
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/已带入导演台锁定的首帧/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "创建关键帧候选并进入人工审核" }));
    await waitFor(() => expect(createKeyframeCandidate).toHaveBeenCalledWith("frame-bridge-image", "shot-1"));
    expect(openReviews).toHaveBeenCalledWith("keyframe-1");
  });

  it("keeps autosaved drafts isolated per project and shot while switching context", async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const view = (shotId: string) => (
      <QueryClientProvider client={client}>
        <GenerationWorkbench
          projectId="project-1"
          profiles={[]}
          candidates={[video]}
          shots={[
            { id: "shot-1", code: "SH-001", status: "DRAFT" },
            { id: "shot-2", code: "SH-002", status: "DRAFT" },
          ]}
          selectedShotId={shotId}
          onSelectShot={vi.fn()}
          onOpenProfiles={vi.fn()}
          activeStep="inputs"
        />
      </QueryClientProvider>
    );
    const rendered = render(view("shot-1"));
    const prompt = screen.getByLabelText("镜头描述") as HTMLTextAreaElement;
    fireEvent.change(prompt, { target: { value: "shot one private draft" } });

    rendered.rerender(view("shot-2"));
    await waitFor(() => expect((screen.getByLabelText("镜头描述") as HTMLTextAreaElement).value).toBe(""));
    expect(window.localStorage.getItem(generationWorkbenchDraftKey("project-1", "shot-1"))).toContain("shot one private draft");

    rendered.rerender(view("shot-1"));
    await waitFor(() => expect((screen.getByLabelText("镜头描述") as HTMLTextAreaElement).value).toBe("shot one private draft"));
  });

  it("persists intent and prompt, preflights, and requires confirmation Dialog before submitting Variant plus Job", async () => {
    const profile = {
      id: "profile",
      code: "i2v",
      title: "本地 I2V",
      version_id: "profile-v1",
      capability: "VIDEO_I2V",
      status: "PUBLISHED",
    };
    const cameraPlan = {
      mode: "NATIVE" as const,
      shot_type: "CLOSEUP",
      movement: "PUSH_IN",
      prompt_text: "",
      direction: "FORWARD",
      intensity: 0.5,
      curve: "LINEAR",
      profile_version_id: "profile-v1",
    };
    const keyframe = {
      media_version_id: "keyframe-approved",
      media_asset_id: "asset-k",
      project_id: "project-1",
      shot_id: "shot-1",
      media_kind: "IMAGE",
      stage: "KEYFRAME",
      decision: "APPROVED",
      is_stale: 0,
    };

    vi.mocked(createGenerationIntent).mockResolvedValue({
      intent: {
        id: "intent-1",
        project_id: "project-1",
        owner_type: "SHOT",
        owner_id: "shot-1",
        purpose: "I2V_PROXY",
        creative_goal: "slow turn",
      },
    });
    vi.mocked(createPrompt).mockResolvedValue({
      prompt: {},
      revision: {
        id: "prompt-r1",
        prompt_id: "prompt-1",
        revision_no: 1,
        parent_revision_id: null,
        content_text: "slow turn",
        structured: {},
        content_hash: "hash",
        status: "FROZEN",
      },
    });
    vi.mocked(planGenerationVariant).mockResolvedValue({
      plan: {
        intent_id: "intent-1",
        status: "READY",
        plan_hash: "a".repeat(64),
        recipe_hash: "b".repeat(64),
        dependencies: {},
        would_persist_variant: false,
        would_create_job: false,
      },
    });
    vi.mocked(submitGenerationVariant).mockResolvedValue({
      variant: {
        id: "variant-1",
        intent_id: "intent-1",
        variant_no: 1,
        variant_type: "BASE",
        parent_variant_id: null,
        recipe_hash: "b".repeat(64),
        status: "QUEUED",
        bindings: [],
      },
      job: {
        id: "job-1",
        type: "GENERATION_VARIANT",
        project_id: "project-1",
        state: "QUEUED",
        channel: "GPU_H3",
        priority: 100,
        max_attempts: 1,
        revision: 1,
      },
    });

    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <GenerationWorkbench
          projectId="project-1"
          profiles={[profile]}
          candidates={[video, keyframe]}
          shots={[{ id: "shot-1", code: "SH-001", status: "READY", current_revision: { camera_plan: cameraPlan } }]}
          selectedShotId="shot-1"
          onSelectShot={vi.fn()}
          onOpenProfiles={vi.fn()}
        />
      </QueryClientProvider>
    );

    // Navigate to inputs
    fireEvent.click(screen.getByRole("button", { name: "下一步：输入与控制 →" }));
    fireEvent.change(screen.getByLabelText("镜头描述"), { target: { value: "slow turn" } });

    // Navigate to preflight
    fireEvent.click(screen.getByRole("button", { name: "下一步：检查并启动 →" }));
    expect(screen.getByText(/预计时长：未声明/)).toBeTruthy();

    // Background preflight opens the one meaningful resource confirmation.
    fireEvent.click(screen.getByRole("button", { name: "检查并准备生成" }));
    expect(await screen.findByRole("dialog", { name: "确认启动生成" })).toBeTruthy();
    expect(submitGenerationVariant).not.toHaveBeenCalled();
    expect(screen.getByText(/即将为镜头/)).toBeTruthy();

    // Confirm inside dialog
    fireEvent.click(screen.getByRole("button", { name: "确认启动生成" }));
    await waitFor(() =>
      expect(submitGenerationVariant).toHaveBeenCalledWith(
        expect.objectContaining({
          plan_hash: "a".repeat(64),
          prompt_revision_id: "prompt-r1",
          bindings: [{ role: "FIRST_FRAME", media_version_id: "keyframe-approved", ordinal: 0 }],
        })
      )
    );
    expect(await screen.findByText(/1 个候选全部创建成功/)).toBeTruthy();
    expect(screen.getByText(/后台任务已创建/)).toBeTruthy();
  });

  it("reports partial multi-Take success and retries only failures with the same idempotency key", async () => {
    const profile = {
      id: "profile-t2i",
      code: "t2i",
      title: "本地 T2I",
      version_id: "profile-t2i-v1",
      capability: "IMAGE_CONCEPT",
      status: "PUBLISHED",
    };
    vi.mocked(createGenerationIntent).mockResolvedValue({
      intent: {
        id: "intent-batch",
        project_id: "project-1",
        owner_type: "SHOT",
        owner_id: "shot-1",
        purpose: "T2I",
        creative_goal: "batch prompt",
      },
    });
    vi.mocked(createPrompt).mockResolvedValue({
      prompt: {},
      revision: {
        id: "prompt-batch-r1",
        prompt_id: "prompt-batch",
        revision_no: 1,
        parent_revision_id: null,
        content_text: "batch prompt",
        structured: {},
        content_hash: "hash",
        status: "FROZEN",
      },
    });
    vi.mocked(planGenerationVariant).mockResolvedValue({
      plan: {
        intent_id: "intent-batch",
        status: "READY",
        plan_hash: "c".repeat(64),
        recipe_hash: "d".repeat(64),
        dependencies: {},
        would_persist_variant: false,
        would_create_job: false,
      },
    });
    const committed = (number: number) => ({
      variant: {
        id: `variant-${number}`,
        intent_id: "intent-batch",
        variant_no: number,
        variant_type: "BASE",
        parent_variant_id: null,
        recipe_hash: "d".repeat(64),
        status: "QUEUED",
        bindings: [],
      },
      job: {
        id: `job-${number}`,
        type: "GENERATION_VARIANT",
        project_id: "project-1",
        state: "QUEUED",
        channel: "GPU_H3",
        priority: 100,
        max_attempts: 1,
        revision: 1,
      },
    });
    vi.mocked(submitGenerationVariant)
      .mockResolvedValueOnce(committed(1))
      .mockRejectedValueOnce(new Error("显存暂时不足"))
      .mockResolvedValueOnce(committed(3))
      .mockResolvedValueOnce(committed(2));

    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <GenerationWorkbench
          projectId="project-1"
          profiles={[profile] as never}
          candidates={[]}
          shots={[{ id: "shot-1", code: "SH-001", status: "READY" }]}
          selectedShotId="shot-1"
          onSelectShot={vi.fn()}
          onOpenProfiles={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /文字生成图片/ }));
    fireEvent.click(screen.getByRole("button", { name: "下一步：输入与控制 →" }));
    fireEvent.change(screen.getByLabelText("镜头描述"), { target: { value: "batch prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步：检查并启动 →" }));
    fireEvent.change(screen.getByLabelText("候选数量"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "检查并准备生成" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认启动生成" }));

    expect(await screen.findByText(/已创建 2\/3 个候选/)).toBeTruthy();
    expect(screen.getByText(/失败：显存暂时不足/)).toBeTruthy();
    expect(submitGenerationVariant).toHaveBeenCalledTimes(3);
    const failedIdempotencyKey = vi.mocked(submitGenerationVariant).mock.calls[1][0].idempotency_key;

    fireEvent.click(screen.getByRole("button", { name: "仅重试失败的 1 个候选" }));
    expect(await screen.findByText(/3 个候选全部创建成功/)).toBeTruthy();
    expect(submitGenerationVariant).toHaveBeenCalledTimes(4);
    expect(vi.mocked(submitGenerationVariant).mock.calls[3][0].idempotency_key).toBe(failedIdempotencyKey);
    expect(vi.mocked(submitGenerationVariant).mock.calls[3][0].explicit_seed).toBe(
      vi.mocked(submitGenerationVariant).mock.calls[1][0].explicit_seed,
    );
  });
});

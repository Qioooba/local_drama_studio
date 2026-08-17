import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createFrameAnchor, createGenerationIntent, createKeyframeCandidate, createPrompt, planGenerationVariant, submitGenerationVariant } from "../../generated/api";
import { GenerationWorkbench } from "./GenerationWorkbench";

vi.mock("../../generated/api", () => ({ createFrameAnchor: vi.fn(), createGenerationIntent: vi.fn(), createKeyframeCandidate: vi.fn(), createPrompt: vi.fn(), planGenerationVariant: vi.fn(), submitGenerationVariant: vi.fn(), listMotionControls: vi.fn().mockResolvedValue({ items: [] }), createMotionControl: vi.fn(), listProductionTiers: vi.fn().mockResolvedValue({ items: [{ code: "PRODUCTION", label: "正式成片", frames: 175, resolution: { "9:16": [480, 832], "16:9": [864, 480] }, denoise: 1.0, steps: 20, cfg: 1.0, default_takes: 8 }], default_tier: "DRAFT" }), getRef2VaCapability: vi.fn().mockResolvedValue({ capability: { capability: "H3_REF2VA_UNAVAILABLE", supported: false, reason: "manifest 缺少 ref2va 模型", manifest_hint: {} } }) }));

const video = { media_version_id: "video-123456789", media_asset_id: "asset-1", project_id: "project-1", media_kind: "VIDEO", stage: "PROXY", decision: null, is_stale: null };

function renderWorkbench(selectedShotId: string | null = null, onOpenReviews = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><GenerationWorkbench projectId="project-1" profiles={[]} candidates={[video]} shots={selectedShotId ? [{ id: selectedShotId, code: "SH-001", status: "DRAFT" }] : []} selectedShotId={selectedShotId} onSelectShot={vi.fn()} onOpenProfiles={vi.fn()} onOpenReviews={onOpenReviews} /></QueryClientProvider>);
}

describe("GenerationWorkbench FrameAnchor actions", () => {
  it("exposes generation mode selection to keyboard and assistive technology", () => {
    renderWorkbench();
    const imageMode = screen.getByRole("button", { name: /文字生成图片/ });
    const videoMode = screen.getByRole("button", { name: /图片生成视频/ });
    expect(imageMode.getAttribute("aria-pressed")).toBe("false");
    expect(videoMode.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(imageMode);
    expect(imageMode.getAttribute("aria-pressed")).toBe("true");
  });

  beforeEach(() => {
    vi.mocked(createFrameAnchor).mockReset().mockImplementation(async (_id, payload) => ({ frame_anchor: {
      id: "anchor-1", source_media_version_id: video.media_version_id, source_time_us: payload.source_time_us ?? (payload.position_mode === "LAST_FRAME" ? 2_000_000 : 0), source_frame_index: payload.position_mode === "LAST_FRAME" ? 2 : payload.source_time_us ? 1 : 0,
      extracted_media_version_id: "frame-1", role_hint: payload.role_hint, sha256: "abcdef1234567890", requested_time_us: payload.source_time_us ?? null, resolved_time_us: payload.source_time_us ?? (payload.position_mode === "LAST_FRAME" ? 2_000_000 : 0), source_sha256: "source-sha", extraction_method: "FFPROBE_PTS_FRAME_INDEX",
    } }));
    vi.mocked(createKeyframeCandidate).mockReset().mockResolvedValue({ media: { id: "keyframe-1", media_asset_id: "asset-kf", project_id: "project-1", owner_type: "SHOT", owner_id: "shot-1", media_kind: "IMAGE", stage: "KEYFRAME", parent_version_id: "frame-1", duplicate: false } });
  });

  it("persists intent and prompt, then requires a second confirmation for Variant plus Job", async () => {
    const profile = { id: "profile", code: "i2v", title: "本地 I2V", version_id: "profile-v1", capability: "I2V", status: "PUBLISHED" };
    const cameraPlan = { mode: "NATIVE" as const, shot_type: "CLOSEUP", movement: "PUSH_IN", prompt_text: "", direction: "FORWARD", intensity: 0.5, curve: "LINEAR", profile_version_id: "profile-v1" };
    const keyframe = { media_version_id: "keyframe-approved", media_asset_id: "asset-k", project_id: "project-1", media_kind: "IMAGE", stage: "KEYFRAME", decision: "APPROVED", is_stale: 0 };
    vi.mocked(createGenerationIntent).mockResolvedValue({ intent: { id: "intent-1", project_id: "project-1", owner_type: "SHOT", owner_id: "shot-1", purpose: "I2V_PROXY", creative_goal: "slow turn" } });
    vi.mocked(createPrompt).mockResolvedValue({ prompt: {}, revision: { id: "prompt-r1", prompt_id: "prompt-1", revision_no: 1, parent_revision_id: null, content_text: "slow turn", structured: {}, content_hash: "hash", status: "FROZEN" } });
    vi.mocked(planGenerationVariant).mockResolvedValue({ plan: { intent_id: "intent-1", status: "READY", plan_hash: "a".repeat(64), recipe_hash: "b".repeat(64), dependencies: {}, would_persist_variant: false, would_create_job: false } });
    vi.mocked(submitGenerationVariant).mockResolvedValue({ variant: { id: "variant-1", intent_id: "intent-1", variant_no: 1, variant_type: "BASE", parent_variant_id: null, recipe_hash: "b".repeat(64), status: "QUEUED", bindings: [] }, job: { id: "job-1", type: "GENERATION_VARIANT", project_id: "project-1", state: "QUEUED", channel: "GPU_H3", priority: 100, max_attempts: 1, revision: 1 } });
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><GenerationWorkbench projectId="project-1" profiles={[profile]} candidates={[video, keyframe]} shots={[{ id: "shot-1", code: "SH-001", status: "READY", current_revision: { camera_plan: cameraPlan } }]} selectedShotId="shot-1" onSelectShot={vi.fn()} onOpenProfiles={vi.fn()} /></QueryClientProvider>);
    expect(screen.getByText(/预计时长：未声明/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "确认创建 Variant 与 Job" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("镜头 Prompt"), { target: { value: "slow turn" } });
    fireEvent.click(screen.getByRole("button", { name: "建立意图并执行只读生成预检" }));
    await screen.findByText(/预检 READY，尚未创建 Job/);
    expect(submitGenerationVariant).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认创建 Variant 与 Job" }));
    await waitFor(() => expect(submitGenerationVariant).toHaveBeenCalledWith(expect.objectContaining({ plan_hash: "a".repeat(64), prompt_revision_id: "prompt-r1", bindings: [{ role: "FIRST_FRAME", media_version_id: "keyframe-approved", ordinal: 0 }] })));
    expect(await screen.findByText(/真实任务已持久化/)).toBeTruthy();
  });

  it("sends first, current and last requests with mutually exclusive positions", async () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("button", { name: "用作首帧" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenLastCalledWith(video.media_version_id, { position_mode: "FIRST_FRAME", role_hint: "FIRST_FRAME" }));
    fireEvent.change(screen.getByLabelText("当前时间（秒）"), { target: { value: "1.25" } });
    fireEvent.click(screen.getByRole("button", { name: "用作当前帧" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenLastCalledWith(video.media_version_id, { source_time_us: 1_250_000, role_hint: "CURRENT_FRAME" }));
    fireEvent.click(screen.getByRole("button", { name: "用作末帧" }));
    await waitFor(() => expect(createFrameAnchor).toHaveBeenLastCalledWith(video.media_version_id, { position_mode: "LAST_FRAME", role_hint: "LAST_FRAME" }));
    expect(await screen.findByText(/末帧已注册并填入当前未提交输入槽/)).toBeTruthy();
    expect(screen.getByAltText(/当前未提交输入：视频末帧缩略图/).getAttribute("src")).toContain("/thumbnail?size=small&frame=poster");
    expect(screen.getByText(/FFPROBE_PTS_FRAME_INDEX/)).toBeTruthy();
  });

  it("creates a shot-owned candidate and opens review without auto approval", async () => {
    const openReviews = vi.fn();
    renderWorkbench("shot-1", openReviews);
    fireEvent.click(screen.getByRole("button", { name: "用作首帧" }));
    await screen.findByText(/首帧已注册并填入当前未提交输入槽/);
    fireEvent.click(screen.getByRole("button", { name: "创建关键帧候选并进入人工审核" }));
    await waitFor(() => expect(createKeyframeCandidate).toHaveBeenCalledWith("frame-1", "shot-1"));
    expect(openReviews).toHaveBeenCalledWith("keyframe-1");
    expect(screen.getByText(/不会自动选择或批准/)).toBeTruthy();
  });

  it("freezes the selected production tier as parameter_set metadata on the draft", async () => {
    const profile = { id: "profile", code: "i2v", title: "本地 I2V", version_id: "profile-v1", capability: "I2V", status: "PUBLISHED" };
    const cameraPlan = { mode: "NATIVE" as const, shot_type: "CLOSEUP", movement: "PUSH_IN", prompt_text: "", direction: "FORWARD", intensity: 0.5, curve: "LINEAR", profile_version_id: "profile-v1" };
    const keyframe = { media_version_id: "keyframe-approved", media_asset_id: "asset-k", project_id: "project-1", media_kind: "IMAGE", stage: "KEYFRAME", decision: "APPROVED", is_stale: 0 };
    vi.mocked(createGenerationIntent).mockResolvedValue({ intent: { id: "intent-1", project_id: "project-1", owner_type: "SHOT", owner_id: "shot-1", purpose: "I2V_PROXY", creative_goal: "slow turn" } });
    vi.mocked(createPrompt).mockResolvedValue({ prompt: {}, revision: { id: "prompt-r1", prompt_id: "prompt-1", revision_no: 1, parent_revision_id: null, content_text: "slow turn", structured: {}, content_hash: "hash", status: "FROZEN" } });
    vi.mocked(planGenerationVariant).mockResolvedValue({ plan: { intent_id: "intent-1", status: "READY", plan_hash: "a".repeat(64), recipe_hash: "b".repeat(64), dependencies: {}, would_persist_variant: false, would_create_job: false } });
    vi.mocked(submitGenerationVariant).mockResolvedValue({ variant: { id: "variant-1", intent_id: "intent-1", variant_no: 1, variant_type: "BASE", parent_variant_id: null, recipe_hash: "b".repeat(64), status: "QUEUED", bindings: [] }, job: { id: "job-1", type: "GENERATION_VARIANT", project_id: "project-1", state: "QUEUED", channel: "GPU_H3", priority: 100, max_attempts: 1, revision: 1 } });
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><GenerationWorkbench projectId="project-1" profiles={[profile]} candidates={[video, keyframe]} shots={[{ id: "shot-1", code: "SH-001", status: "READY", current_revision: { camera_plan: cameraPlan } }]} selectedShotId="shot-1" onSelectShot={vi.fn()} onOpenProfiles={vi.fn()} /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("镜头 Prompt"), { target: { value: "slow turn" } });
    fireEvent.change(screen.getByLabelText(/生产档位/), { target: { value: "PRODUCTION" } });
    fireEvent.click(screen.getByRole("button", { name: "建立意图并执行只读生成预检" }));
    await screen.findByText(/预检 READY，尚未创建 Job/);
    fireEvent.click(screen.getByRole("button", { name: "确认创建 Variant 与 Job" }));
    await waitFor(() => expect(submitGenerationVariant).toHaveBeenCalledWith(expect.objectContaining({ parameter_set: expect.objectContaining({ tier: "PRODUCTION", PROMPT: "slow turn" }) })));
  });
});

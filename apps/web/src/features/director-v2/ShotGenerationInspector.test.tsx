import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createShotGenerationIntentV2,
  preflightShotGenerationV2,
  submitShotGenerationV2,
  type ShotStudio,
} from "../../generated/api";
import { ShotGenerationInspector } from "./ShotGenerationInspector";
import { planShotKeyframeBatch, submitShotKeyframeBatch } from "./shotKeyframeBatchApi";

vi.mock("../../generated/api", () => ({
  createShotGenerationIntentV2: vi.fn(),
  preflightShotGenerationV2: vi.fn(),
  submitShotGenerationV2: vi.fn(),
}));

vi.mock("./shotKeyframeBatchApi", () => ({
  planShotKeyframeBatch: vi.fn(),
  submitShotKeyframeBatch: vi.fn(),
}));

function currentShot(approved = true): ShotStudio["current_shot"] {
  return {
    candidates: approved ? [{
      id: "variant-1",
      intent_id: "old-intent",
      variant_no: 1,
      variant_type: "BASE",
      parent_variant_id: null,
      branch_reason: "keyframe",
      status: "PLANNED",
      is_stale: false,
      stale_reason: null,
      media_asset_id: "asset-1",
      media_kind: "IMAGE",
      media_version_id: "frame-1",
      version_no: 1,
      take_no: 1,
      stage: "KEYFRAME",
      rel_path: "frame.png",
      mime_type: "image/png",
      duration_ms: null,
      integrity_status: "VERIFIED",
      thumbnail_ready: true,
      selected: false,
      approved: true,
      created_at: "2026-08-26T00:00:00Z",
    }] : [],
    generation_preferences: {
      available: true,
      resolutions: [{
        capability: "VIDEO_I2V",
        profile_version_id: "profile-1",
        source: "SHOT",
        blocked_reason: null,
        effective_settings: { steps: 20 },
        setting_sources: { steps: "PREFERENCE" },
        profile: {
          code: "i2v", title: "本地 I2V", version_no: 1, capability: "VIDEO_I2V", status: "PUBLISHED",
          override_schema: { fields: {
            steps: { type: "integer", label: "步数", scopes: ["SHOT"], minimum: 10, maximum: 60, default: 20 },
            cfg: { type: "number", label: "CFG", scopes: ["RUN"], default: 6.5 },
          } },
        },
      }],
    },
    generation_intents: [],
  } as unknown as ShotStudio["current_shot"];
}

function renderInspector(approved = true, onSubmitted = vi.fn(), shot = currentShot(approved), fields: Record<string, unknown> = { subject_action: "向镜头走近", camera_plan: { movement: "PUSH_IN" } }, shotCode = "SHOT-001") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><ShotGenerationInspector
    episodeId="episode-1"
    shotId="shot-1"
    shotCode={shotCode}
    shotRevision={4}
    fields={fields}
    currentShot={shot}
    canGenerate
    reviewHref="/review"
    onSubmitted={onSubmitted}
  /></MemoryRouter></QueryClientProvider>);
  return onSubmitted;
}

function readyFramePlan(overrides: Record<string, unknown> = {}) {
  return {
    episode_id: "episode-1", project_id: "project-1", targets: [{ shot_id: "shot-1", expected_revision: 4 }],
    frame_strategy: "FIRST_AND_LAST" as const, candidate_count: 4, plan_hash: "d".repeat(64), valid: true,
    issues: [], summary: { shots: 1, jobs: 8, blocked: 0 },
    execution_contract: {
      source: "APP_CONTRACT", contract_id: "contract-1", runtime_environment_version_id: "runtime-1",
      published_contract_bound: true, compiler_mode: "WORKFLOW_NEGATIVE_BINDING",
      semantic_roles: ["NEGATIVE_PROMPT", "PROMPT", "SEED"],
      workflow_bindings: {
        PROMPT: { node_id: "5", input: "text" },
        NEGATIVE_PROMPT: { node_id: "6", input: "text" },
        SEED: { node_id: "8", input: "seed" },
      },
      seed_policy: "EXPLICIT_SUBMIT_SEED",
    },
    items: [{
      shot_id: "shot-1", shot_code: "SHOT-001", shot_revision: 4, frame_role: "FIRST_FRAME" as const,
      candidate_index: 1, profile_version_id: "profile-1",
      shot_keyframe_route: { source: "APP_CONTRACT", contract_id: "contract-1", runtime_environment_version_id: "runtime-1", published_contract_bound: true },
      workflow_bindings: {
        PROMPT: { node_id: "5", input: "text" },
        NEGATIVE_PROMPT: { node_id: "6", input: "text" },
        SEED: { node_id: "8", input: "seed" },
      },
      semantic_inputs: { PROMPT: "服务端单画幅正向", NEGATIVE_PROMPT: "triptych, contact sheet", SEED: null },
      prompt: "服务端单画幅正向",
      prompt_bundle: {
        schema_version: "localdrama.prompt-bundle.v1", base_prompt: "镜头事实", effective_base_prompt: "单一画面",
        frame_role: "FIRST_FRAME" as const, frame_reframe_mode: "SINGLE_MOMENT" as const,
        positive_override: "", negative_prompt: "triptych, contact sheet", provenance: "PAGE_USER_EDIT" as const,
        compiler_mode: "WORKFLOW_NEGATIVE_BINDING", final_prompt: "服务端单画幅正向",
      },
      status: "READY" as const, blockers: [],
    }],
    ...overrides,
  };
}

describe("ShotGenerationInspector", () => {
  beforeEach(() => vi.clearAllMocks());

  it("plans one first frame and invalidates that plan when the frame count changes", async () => {
    vi.mocked(planShotKeyframeBatch).mockResolvedValue({ plan: readyFramePlan({ frame_strategy: "FIRST_ONLY", candidate_count: 1 }) });
    renderInspector(false);
    fireEvent.change(screen.getByLabelText("关键帧策略"), { target: { value: "FIRST_ONLY" } });
    fireEvent.change(screen.getByLabelText("每种帧候选数"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "验证生成计划" }));
    await waitFor(() => expect(planShotKeyframeBatch).toHaveBeenCalledWith("episode-1", [{ shot_id: "shot-1", expected_revision: 4 }], "FIRST_ONLY", 1, expect.any(Object)));
    await waitFor(() => expect((screen.getByRole("button", { name: "重新生成首尾帧" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(screen.getByLabelText("每种帧候选数"), { target: { value: "2" } });
    expect((screen.getByRole("button", { name: "重新生成首尾帧" }) as HTMLButtonElement).disabled).toBe(true);
    expect(submitShotKeyframeBatch).not.toHaveBeenCalled();
  });

  it("keeps approval distinct when no eligible first frame exists", () => {
    renderInspector(false);
    expect(screen.getByText("待生成")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新生成首尾帧" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "查看或替换推荐结果" }).getAttribute("href")).toBe("/review");
  });

  it("keeps the explicit frame regeneration action when an old working frame exists", async () => {
    vi.mocked(planShotKeyframeBatch).mockResolvedValue({
      plan: readyFramePlan(),
    });
    vi.mocked(submitShotKeyframeBatch).mockResolvedValue({
      batch: { id: "batch-1", episode_id: "episode-1", project_id: "project-1", frame_strategy: "FIRST_AND_LAST", candidate_count: 4, status: "QUEUED", plan_hash: "d".repeat(64), created_at: "now", summary: { total: 8, succeeded: 0, failed: 0, active: 8 } },
    });
    const onSubmitted = renderInspector(true);
    expect(screen.getByRole("button", { name: "重新生成首尾帧" })).toBeTruthy();
    expect((screen.getByLabelText("反向提示词") as HTMLTextAreaElement).value).toContain("triptych");
    expect((screen.getByRole("checkbox", { name: /单一画面重构/ }) as HTMLInputElement).checked).toBe(true);
    const framePreviews = screen.getByLabelText("首尾帧输入草稿预览");
    expect(framePreviews.textContent).toContain("首帧只呈现第一个视觉瞬间");
    expect(framePreviews.textContent).toContain("尾帧只呈现最后一个视觉瞬间");
    expect(createShotGenerationIntentV2).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "验证生成计划" }));
    await waitFor(() => expect(screen.getByLabelText("服务端生成计划").textContent).toContain("contract-1"));
    expect(screen.getByLabelText("服务端生成计划").textContent).toContain("服务端 NEGATIVE_PROMPT");
    expect(screen.getByLabelText("服务端生成计划").textContent).not.toContain("Avoid:");
    expect(submitShotKeyframeBatch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新生成首尾帧" }));
    await waitFor(() => expect(submitShotKeyframeBatch).toHaveBeenCalledWith(
      "episode-1",
      [{ shot_id: "shot-1", expected_revision: 4 }],
      "FIRST_AND_LAST",
      4,
      "d".repeat(64),
      expect.any(String),
      expect.objectContaining({ provenance: "AI_GENERATED", negative_prompt: expect.stringContaining("triptych"), frame_reframe_mode: "SINGLE_MOMENT" }),
    ));
    expect(onSubmitted).toHaveBeenCalledWith(expect.stringContaining("已排队 8 张"));
  });

  it("shows the server positive and negative inputs separately and keeps planning read-only", async () => {
    vi.mocked(planShotKeyframeBatch).mockResolvedValue({ plan: readyFramePlan() });
    renderInspector(true);

    fireEvent.click(screen.getByRole("button", { name: "验证生成计划" }));
    await waitFor(() => expect(screen.getByLabelText("服务端生成计划")).toBeTruthy());
    expect(screen.getByLabelText("服务端生成计划").textContent).toContain("服务端单画幅正向");
    expect(screen.getByLabelText("服务端生成计划").textContent).toContain("triptych, contact sheet");
    expect(screen.getByLabelText("输入草稿预览").textContent).not.toContain("最终提示词预览");
    expect(submitShotKeyframeBatch).not.toHaveBeenCalled();
  });

  it("invalidates the plan when an editable prompt field changes", async () => {
    vi.mocked(planShotKeyframeBatch).mockResolvedValue({ plan: readyFramePlan() });
    renderInspector(true);
    fireEvent.click(screen.getByRole("button", { name: "验证生成计划" }));
    await waitFor(() => expect(screen.getByLabelText("服务端生成计划").textContent).toContain("计划与当前表单一致"));

    fireEvent.change(screen.getByLabelText("正向提示词补充"), { target: { value: "改变构图" } });
    expect((screen.getByRole("button", { name: "重新生成首尾帧" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByLabelText("服务端生成计划").textContent).toContain("表单已变化");
    expect(submitShotKeyframeBatch).not.toHaveBeenCalled();
  });

  it("keeps submission disabled when the plan has no bound App Contract", async () => {
    vi.mocked(planShotKeyframeBatch).mockResolvedValue({
      plan: readyFramePlan({
        execution_contract: { source: "WORKFLOW_VERSION", contract_id: null, runtime_environment_version_id: null, published_contract_bound: false, compiler_mode: "PROMPT_AVOID_FALLBACK", semantic_roles: ["PROMPT", "SEED"], workflow_bindings: {} },
        items: [{ ...readyFramePlan().items[0], shot_keyframe_route: { source: "WORKFLOW_VERSION", contract_id: null, runtime_environment_version_id: null, published_contract_bound: false } }],
        issues: [{ code: "SHOT_KEYFRAME_RUNTIME_BINDING_REQUIRED", message: "契约未绑定" }],
        valid: false,
      }),
    });
    renderInspector(true);
    fireEvent.click(screen.getByRole("button", { name: "验证生成计划" }));
    await waitFor(() => expect(screen.getByLabelText("服务端生成计划").textContent).toContain("契约未绑定"));
    expect((screen.getByRole("button", { name: "重新生成首尾帧" }) as HTMLButtonElement).disabled).toBe(true);
    expect(submitShotKeyframeBatch).not.toHaveBeenCalled();
  });

  it("accepts the canonical approved working keyframe even when it has no generation Variant", () => {
    const shot = currentShot(false);
    shot.current_media = {
      media_version_id: "working-keyframe-1",
      media_kind: "IMAGE",
      stage: "KEYFRAME",
      integrity_status: "VERIFIED",
      approved: true,
      is_stale: false,
      take_no: 3,
    };
    renderInspector(false, vi.fn(), shot);

    expect(screen.getByText("已批准")).toBeTruthy();
    expect(screen.getByRole("button", { name: "生成视频候选" })).toBeTruthy();
  });

  it("preflights a typed BASE command before submitting the exact confirmed plan", async () => {
    vi.mocked(createShotGenerationIntentV2).mockResolvedValue({
      intent: {
        id: "intent-1",
        project_id: "project-1",
        owner_type: "SHOT",
        owner_id: "shot-1",
        purpose: "I2V_PROXY",
        creative_goal: "镜头 SHOT-001，向镜头走近，运镜 PUSH_IN",
        status: "DRAFT",
        created_at: "now",
        idempotent_replay: false,
      },
    });
    vi.mocked(preflightShotGenerationV2).mockResolvedValue({
      preflight: {
        intent_id: "intent-1",
        shot_id: "shot-1",
        shot_revision: 4,
        status: "READY",
        plan_hash: "a".repeat(64),
        variant_plan_hash: "b".repeat(64),
        recipe_hash: "c".repeat(64),
        dependencies: {},
        disk_gate: { blocking: false },
        reproducibility: {},
        would_persist_variant: false,
        would_create_job: false,
      },
    });
    vi.mocked(submitShotGenerationV2).mockResolvedValue({
      operation: "BASE",
      variant: { id: "variant-2", intent_id: "intent-1", variant_no: 1, status: "QUEUED" },
      job: { id: "job-1", state: "QUEUED" },
      idempotent_replay: false,
    } as never);
    const onSubmitted = renderInspector(true, vi.fn());

    fireEvent.click(screen.getByRole("button", { name: "生成视频候选" }));
    await waitFor(() => expect(preflightShotGenerationV2).toHaveBeenCalledWith("shot-1", expect.objectContaining({
      operation: "BASE",
      stage_code: "VIDEO",
      intent_id: "intent-1",
      expected_shot_revision: 4,
      bindings: [{ role: "FIRST_FRAME", media_version_id: "frame-1", ordinal: 0 }],
      prompt_bundle: expect.objectContaining({ base_prompt: expect.stringContaining("向镜头走近"), negative_prompt: expect.stringContaining("triptych") }),
    })));
    await waitFor(() => expect(submitShotGenerationV2).toHaveBeenCalledWith("shot-1", expect.objectContaining({
      operation: "BASE",
      stage_code: "VIDEO",
      plan_hash: "a".repeat(64),
    })));
    expect(onSubmitted).toHaveBeenCalledWith("视频候选已排队（任务 job-1）。");
    expect(planShotKeyframeBatch).not.toHaveBeenCalled();
  });

  it("keeps both generation actions in one reachable rail and exposes pending/error feedback", async () => {
    let resolvePlan: ((value: unknown) => void) | undefined;
    vi.mocked(planShotKeyframeBatch).mockReturnValue(new Promise((resolve) => { resolvePlan = resolve; }) as never);
    renderInspector(true);

    const rail = document.querySelector('[aria-label="镜头生成操作"]');
    expect(rail).toBeTruthy();
    expect(rail?.querySelectorAll(":scope > .shot-draw-action")).toHaveLength(2);
    expect(rail?.querySelectorAll(".shot-draw-action-sticky")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "验证生成计划" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("正在验证生成计划"));
    expect(createShotGenerationIntentV2).not.toHaveBeenCalled();
    expect(preflightShotGenerationV2).not.toHaveBeenCalled();

    resolvePlan?.({ plan: { valid: false, issues: [{ message: "测试阻塞" }] } });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("测试阻塞"));
    expect(submitShotKeyframeBatch).not.toHaveBeenCalled();
  });

  it("does not submit a frame redraw when the AI base prompt is empty", async () => {
    const onSubmitted = renderInspector(true, vi.fn(), currentShot(true), {}, "");
    fireEvent.click(screen.getByRole("button", { name: "验证生成计划" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("缺少 AI 基础提示词"));
    expect(planShotKeyframeBatch).not.toHaveBeenCalled();
    expect(onSubmitted).not.toHaveBeenCalled();
  });
});

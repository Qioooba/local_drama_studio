import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import {
  createShotGenerationIntentV2,
  preflightShotGenerationV2,
  submitShotGenerationV2,
  type ShotStudio,
} from "../../generated/api";
import { ShotGenerationInspector } from "./ShotGenerationInspector";

vi.mock("../../generated/api", () => ({
  createShotGenerationIntentV2: vi.fn(),
  preflightShotGenerationV2: vi.fn(),
  submitShotGenerationV2: vi.fn(),
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
        profile: { code: "i2v", title: "本地 I2V", version_no: 1, capability: "VIDEO_I2V", status: "PUBLISHED" },
      }],
    },
    generation_intents: [],
  } as unknown as ShotStudio["current_shot"];
}

function renderInspector(approved = true, onSubmitted = vi.fn(), shot = currentShot(approved)) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><ShotGenerationInspector
    shotId="shot-1"
    shotCode="SHOT-001"
    shotRevision={4}
    fields={{ subject_action: "向镜头走近", camera_plan: { movement: "PUSH_IN" } }}
    currentShot={shot}
    canGenerate
    reviewHref="/review"
    onOpenKeyframePicker={vi.fn()}
    onSubmitted={onSubmitted}
  /></MemoryRouter></QueryClientProvider>);
  return onSubmitted;
}

describe("ShotGenerationInspector", () => {
  it("keeps approval distinct when no eligible first frame exists", () => {
    renderInspector(false);
    expect(screen.getByText("缺少已批准关键帧")).toBeTruthy();
    expect(screen.getByRole("link", { name: "前往审核" }).getAttribute("href")).toBe("/review");
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

    expect(screen.queryByText("缺少已批准关键帧")).toBeNull();
    expect((screen.getByRole("combobox", { name: "已批准首帧" }) as HTMLSelectElement).value).toBe("working-keyframe-1");
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

    fireEvent.click(screen.getByRole("button", { name: "检查生成方案" }));
    await waitFor(() => expect(preflightShotGenerationV2).toHaveBeenCalledWith("shot-1", expect.objectContaining({
      operation: "BASE",
      stage_code: "VIDEO",
      intent_id: "intent-1",
      expected_shot_revision: 4,
      bindings: [{ role: "FIRST_FRAME", media_version_id: "frame-1", ordinal: 0 }],
    })));
    expect(screen.getByText("方案可以提交")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "确认并生成" }));
    await waitFor(() => expect(submitShotGenerationV2).toHaveBeenCalledWith("shot-1", expect.objectContaining({
      operation: "BASE",
      stage_code: "VIDEO",
      plan_hash: "a".repeat(64),
    })));
    expect(onSubmitted).toHaveBeenCalledWith("首个视频候选已排队（任务 job-1）。");
  });
});

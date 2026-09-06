import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listCapabilityOptions } from "../../generated/api";
import { capabilityOptionFixture, capabilityOptionsFixture } from "../model-config/capabilityOptionsTestFixtures";
import { AssetImageBatchWorkbench } from "./AssetImageBatchWorkbench";
import { listAssetImageBatches, planAssetImageBatch, submitAssetImageBatch } from "./assetImageBatchClient";
import type { AssetBibleItem } from "./api";

vi.mock("../../generated/api", () => ({
  listCapabilityOptions: vi.fn(),
  getProfileVersion: vi.fn(),
}));

vi.mock("./assetImageBatchClient", async () => {
  const actual = await vi.importActual<typeof import("./assetImageBatchClient")>("./assetImageBatchClient");
  return {
    ...actual,
    listAssetImageBatches: vi.fn(),
    planAssetImageBatch: vi.fn(),
    submitAssetImageBatch: vi.fn(),
  };
});

function asset(id: string, name: string, hero: string | null = null): AssetBibleItem {
  return {
    asset: { id, kind: "CHARACTER", code: id, name, description: `${name}的外观描述`, status: "ACTIVE", revision: 1, canonical_media_version_id: hero },
    states: [],
    base_references: hero ? [{ id: `ref-${id}`, project_id: "p1", story_asset_id: id, asset_state_id: null, media_version_id: hero, reference_kind: "HERO", label: "", priority: 10, is_locked: true, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1 }] : [],
    active_state_id: null,
    voice: null,
    usage: { episode_ids: [], episodes: [], shots: [], shot_count: 0 },
    readiness: { level: hero ? "BASIC" : "EMPTY", missing: hero ? ["FRONT", "LEFT", "RIGHT"] : ["HERO", "FRONT", "LEFT", "RIGHT"] },
    multiview_generations: [],
    expression_generations: [],
    detail_generations: [],
  };
}

describe("AssetImageBatchWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const option = capabilityOptionFixture("IMAGE_CHARACTER", "profile-image-character", "角色模型", "角色文生图");
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityOptionsFixture("IMAGE_CHARACTER", [option]));
    vi.mocked(listAssetImageBatches).mockResolvedValue([]);
    vi.mocked(planAssetImageBatch).mockResolvedValue({
      plan: {
        project_id: "p1", asset_kind: "CHARACTER", capability: "IMAGE_CHARACTER", mode: "MISSING_ONLY",
        profile_version_id: "profile-image-character", plan_hash: "a".repeat(64), valid: true, issues: [],
        items: [{ asset_id: "a1", name: "阿宁", revision: 1, has_hero: false, prompt: "角色概念设定主图。名称：阿宁。", status: "READY", blockers: [] }],
        summary: { selected: 1, ready: 1, skipped: 0, blocked: 0, jobs: 1 },
      },
    });
    vi.mocked(submitAssetImageBatch).mockResolvedValue({
      batch: {
        id: "batch-1", project_id: "p1", asset_kind: "CHARACTER", capability: "IMAGE_CHARACTER", profile_version_id: "profile-image-character",
        mode: "MISSING_ONLY", status: "QUEUED", plan_hash: "a".repeat(64), created_at: "", updated_at: "",
        summary: { total: 1, succeeded: 0, superseded: 0, failed: 0, active: 1 }, items: [],
      },
    });
  });

  it("uses the selected text-to-image profile and only submits missing HERO images", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<MemoryRouter><QueryClientProvider client={client}>
      <AssetImageBatchWorkbench projectId="p1" kind="CHARACTER" items={[asset("a1", "阿宁"), asset("a2", "师父", "hero-2"), { ...asset("prop-1", "纸箱"), asset: { ...asset("prop-1", "纸箱").asset, kind: "PROP" } }]} onChanged={vi.fn()} />
    </QueryClientProvider></MemoryRouter>);

    fireEvent.click(screen.getByRole("button", { name: "配置并生成 1 张主图" }));
    expect(screen.queryByText("纸箱")).toBeNull();
    const picker = await screen.findByLabelText("角色主图文生图方式");
    await screen.findByRole("option", { name: /角色模型 · 角色文生图/ });
    fireEvent.change(picker, { target: { value: "profile-image-character" } });
    await screen.findByText(/本次固定：角色模型/);
    fireEvent.click(screen.getByRole("button", { name: "检查 1 张生成计划" }));
    await waitFor(() => expect(planAssetImageBatch).toHaveBeenCalledWith("p1", {
      asset_kind: "CHARACTER",
      asset_ids: ["a1"],
      profile_version_id: "profile-image-character",
      mode: "MISSING_ONLY",
    }));
    expect(await screen.findByText("1 张可生成 · 0 张已跳过 · 0 张阻塞")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认生成 1 张主图" }));
    await waitFor(() => expect(submitAssetImageBatch).toHaveBeenCalledWith(
      "p1",
      expect.objectContaining({ asset_ids: ["a1"], profile_version_id: "profile-image-character" }),
      "a".repeat(64),
      expect.any(String),
    ));
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectAssetImageWorkbench } from "./ProjectAssetImageWorkbench";
import { listAssetImageBatches, planAssetImageBatch, submitAssetImageBatch } from "./assetImageBatchClient";
import type { AssetBibleItem } from "./api";

vi.mock("./assetImageBatchClient", async () => {
  const actual = await vi.importActual<typeof import("./assetImageBatchClient")>("./assetImageBatchClient");
  return { ...actual, listAssetImageBatches: vi.fn(), planAssetImageBatch: vi.fn(), submitAssetImageBatch: vi.fn() };
});

function item(id: string, kind: "CHARACTER" | "SCENE" | "PROP"): AssetBibleItem {
  return {
    asset: { id, kind, code: id, name: id, description: "", status: "ACTIVE", revision: 1, canonical_media_version_id: null },
    states: [], base_references: [], active_state_id: null, voice: null,
    usage: { episode_ids: [], episodes: [], shots: [], shot_count: 0 },
    readiness: { level: "EMPTY", missing: ["HERO"] },
  };
}

function plan(kind: import("./assetImageBatchClient").AssetImageKind) {
  return { plan: {
    project_id: "p1", asset_kind: kind, capability: `IMAGE_${kind}`, mode: "MISSING_ONLY" as const,
    profile_version_id: null, plan_hash: kind.repeat(8), valid: true, issues: [], items: [],
    summary: { selected: 1, ready: 1, skipped: 0, blocked: 0, jobs: 1 },
  } };
}

function batch(kind: "CHARACTER" | "SCENE" | "PROP", id: string) {
  return { batch: {
    id, project_id: "p1", asset_kind: kind, capability: `IMAGE_${kind}`, profile_version_id: "profile",
    mode: "MISSING_ONLY" as const, status: "QUEUED" as const, plan_hash: kind.repeat(8), created_at: "", updated_at: "",
    summary: { total: 1, succeeded: 0, superseded: 0, failed: 0, active: 1 },
    items: [{ id: `${id}-item`, asset_id: kind, asset_name: kind, asset_kind: kind, status: "QUEUED" as const, job_id: `${id}-job`, job_state: "QUEUED", progress: {}, media_version_id: null, reference_id: null, error: null }],
  } };
}

describe("ProjectAssetImageWorkbench grouped receipts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(planAssetImageBatch).mockImplementation(async (_projectId, request) => plan(request.asset_kind));
    vi.mocked(listAssetImageBatches).mockResolvedValue([]);
  });

  it("retains accepted, rejected and unknown groups independently and always refreshes", async () => {
    vi.mocked(submitAssetImageBatch)
      .mockResolvedValueOnce(batch("CHARACTER", "batch-character"))
      .mockRejectedValueOnce(Object.assign(new Error("配置已失效"), { status: 409 }))
      .mockRejectedValueOnce(new TypeError("网络中断"));
    const changed = vi.fn();
    render(<MemoryRouter><ProjectAssetImageWorkbench projectId="p1" items={[item("c1", "CHARACTER"), item("s1", "SCENE"), item("p1", "PROP")]} onChanged={changed} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "生成缺少的主图" }));
    expect(await screen.findByText(/1 组已受理，1 组未受理，1 组结果待确认/)).toBeTruthy();
    expect(screen.getByText(/批次 batch-ch/)).toBeTruthy();
    expect(screen.getByText("配置已失效")).toBeTruthy();
    expect(screen.getByRole("button", { name: "先核对回执，再继续" })).toBeTruthy();
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("safely reuses the frozen idempotency key after checking an unknown result", async () => {
    vi.mocked(submitAssetImageBatch).mockRejectedValueOnce(new TypeError("超时")).mockResolvedValueOnce(batch("CHARACTER", "batch-replayed"));
    render(<MemoryRouter><ProjectAssetImageWorkbench projectId="p1" items={[item("c1", "CHARACTER")]} onChanged={vi.fn()} /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "生成缺少的主图" }));
    const recover = await screen.findByRole("button", { name: "先核对回执，再继续" });
    const firstKey = vi.mocked(submitAssetImageBatch).mock.calls[0][3];
    fireEvent.click(recover);
    await waitFor(() => expect(submitAssetImageBatch).toHaveBeenCalledTimes(2));
    expect(vi.mocked(submitAssetImageBatch).mock.calls[1][3]).toBe(firstKey);
    expect(await screen.findByText(/批次 batch-re/)).toBeTruthy();
  });
});

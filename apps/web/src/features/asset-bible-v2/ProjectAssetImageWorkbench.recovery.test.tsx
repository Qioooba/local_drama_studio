import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectAssetImageWorkbench } from "./ProjectAssetImageWorkbench";
import {
  findAssetImageBatchByCommandKey,
  planAssetImageBatch,
  submitAssetImageBatch,
} from "./assetImageBatchClient";
import type { AssetBibleItem } from "./api";

vi.mock("./assetImageBatchClient", async () => {
  const actual = await vi.importActual<typeof import("./assetImageBatchClient")>(
    "./assetImageBatchClient",
  );
  return {
    ...actual,
    listAssetImageBatches: vi.fn().mockResolvedValue([]),
    planAssetImageBatch: vi.fn(),
    submitAssetImageBatch: vi.fn(),
    findAssetImageBatchByCommandKey: vi.fn(),
  };
});

function item(id: string, kind: "CHARACTER" | "SCENE" | "PROP"): AssetBibleItem {
  return {
    asset: { id, kind, code: id, name: id, description: "", status: "ACTIVE", revision: 1, canonical_media_version_id: null },
    states: [],
    base_references: [],
    active_state_id: null,
    voice: null,
    usage: { episode_ids: [], episodes: [], shots: [], shot_count: 0 },
    readiness: { level: "EMPTY", missing: ["HERO"] },
  };
}

function plan(kind: "CHARACTER" | "SCENE" | "PROP", hash?: string) {
  const planHash = hash ?? kind.repeat(8);
  return {
    plan: {
      project_id: "p1",
      asset_kind: kind,
      capability: `IMAGE_${kind}`,
      mode: "MISSING_ONLY" as const,
      profile_version_id: null,
      plan_hash: planHash,
      valid: true,
      issues: [],
      items: [],
      summary: { selected: 1, ready: 1, skipped: 0, blocked: 0, jobs: 1 },
    },
  };
}

function batch(kind: "CHARACTER" | "SCENE" | "PROP", id: string, hash?: string) {
  const planHash = hash ?? kind.repeat(8);
  return {
    batch: {
      id,
      project_id: "p1",
      asset_kind: kind,
      capability: `IMAGE_${kind}`,
      profile_version_id: "profile",
      mode: "MISSING_ONLY" as const,
      status: "QUEUED" as const,
      plan_hash: planHash,
      created_at: "",
      updated_at: "",
      summary: { total: 1, succeeded: 0, superseded: 0, failed: 0, active: 1 },
      items: [
        {
          id: `${id}-item`,
          asset_id: kind,
          asset_name: kind,
          asset_kind: kind,
          status: "QUEUED" as const,
          job_id: `${id}-job`,
          job_state: "QUEUED",
          progress: {},
          media_version_id: null,
          reference_id: null,
          error: null,
        },
      ],
    },
  };
}

describe("R02 workbench recovery boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    vi.mocked(planAssetImageBatch).mockImplementation(async (_projectId, request) =>
      plan(request.asset_kind as "CHARACTER" | "SCENE" | "PROP"),
    );
    vi.mocked(findAssetImageBatchByCommandKey).mockResolvedValue(null);
  });

  it("UNKNOWN 后普通生成入口被锁住，不创建新 key", async () => {
    vi.mocked(submitAssetImageBatch).mockRejectedValueOnce(new TypeError("网络中断"));
    render(
      <MemoryRouter>
        <ProjectAssetImageWorkbench projectId="p1" items={[item("c1", "CHARACTER")]} onChanged={vi.fn()} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "生成缺少的主图" }));
    await screen.findByRole("button", { name: "先核对回执，再继续" });
    expect(vi.mocked(submitAssetImageBatch)).toHaveBeenCalledTimes(1);
    const generateBtn = screen.getByRole("button", { name: "生成缺少的主图" });
    expect(generateBtn).toBeDisabled();
    fireEvent.click(generateBtn);
    expect(vi.mocked(submitAssetImageBatch)).toHaveBeenCalledTimes(1);
  });

  it("精确恢复使用原始 request、key、planHash，不用 plan_hash 认领历史", async () => {
    vi.mocked(submitAssetImageBatch)
      .mockRejectedValueOnce(new TypeError("超时"))
      .mockImplementation(async (_projectId, request, expectedPlanHash, idempotencyKey) =>
        batch(request.asset_kind as "CHARACTER", `batch-${idempotencyKey.slice(0, 4)}`),
      );
    render(
      <MemoryRouter>
        <ProjectAssetImageWorkbench projectId="p1" items={[item("c1", "CHARACTER")]} onChanged={vi.fn()} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "生成缺少的主图" }));
    const recover = await screen.findByRole("button", { name: "先核对回执，再继续" });
    const [firstProject, firstRequest, firstHash, firstKey] = vi.mocked(submitAssetImageBatch).mock.calls[0];
    fireEvent.click(recover);
    await waitFor(() => expect(vi.mocked(findAssetImageBatchByCommandKey)).toHaveBeenCalledWith("p1", firstKey));
    await waitFor(() => expect(submitAssetImageBatch).toHaveBeenCalledTimes(2));
    const second = vi.mocked(submitAssetImageBatch).mock.calls[1];
    expect(second[0]).toBe(firstProject);
    expect(second[1]).toEqual(firstRequest);
    expect(second[2]).toBe(firstHash);
    expect(second[3]).toBe(firstKey);
  });

  it("对应批次不在最近 5 条时精确查询仍找到，不重新分派", async () => {
    vi.mocked(submitAssetImageBatch).mockRejectedValueOnce(new TypeError("超时"));
    const existing = batch("CHARACTER", "batch-old-deep");
    vi.mocked(findAssetImageBatchByCommandKey).mockResolvedValueOnce(existing.batch);
    render(
      <MemoryRouter>
        <ProjectAssetImageWorkbench projectId="p1" items={[item("c1", "CHARACTER")]} onChanged={vi.fn()} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "生成缺少的主图" }));
    fireEvent.click(await screen.findByRole("button", { name: "先核对回执，再继续" }));
    expect(await screen.findByText(/批次 batch-ol/)).toBeTruthy();
    expect(vi.mocked(submitAssetImageBatch)).toHaveBeenCalledTimes(1);
  });

  it("查回执失败保持 UNKNOWN，不判断成没提交", async () => {
    vi.mocked(submitAssetImageBatch).mockRejectedValueOnce(new TypeError("超时"));
    vi.mocked(findAssetImageBatchByCommandKey).mockRejectedValueOnce(new TypeError("查询失败"));
    render(
      <MemoryRouter>
        <ProjectAssetImageWorkbench projectId="p1" items={[item("c1", "CHARACTER")]} onChanged={vi.fn()} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "生成缺少的主图" }));
    fireEvent.click(await screen.findByRole("button", { name: "先核对回执，再继续" }));
    await waitFor(() =>
      expect(screen.getByText(/核对回执失败，仍为待确认/)).toBeTruthy(),
    );
    expect(vi.mocked(submitAssetImageBatch)).toHaveBeenCalledTimes(1);
  });

  it("accepted 后 onChanged 抛错仍保留 accepted，busy 释放并显示刷新提示", async () => {
    vi.mocked(submitAssetImageBatch).mockResolvedValueOnce(batch("CHARACTER", "batch-ok"));
    const changed = vi.fn().mockRejectedValueOnce(new Error("刷新炸了"));
    render(
      <MemoryRouter>
        <ProjectAssetImageWorkbench projectId="p1" items={[item("c1", "CHARACTER")]} onChanged={changed} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "生成缺少的主图" }));
    expect(await screen.findByText(/批次 batch-ok/)).toBeTruthy();
    expect(await screen.findByText(/回执已保留，刷新失败/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "生成缺少的主图" })).toBeEnabled();
  });
});

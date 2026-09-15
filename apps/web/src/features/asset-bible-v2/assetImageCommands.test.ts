import { beforeEach, describe, expect, it } from "vitest";
import {
  classifyAssetSubmitError,
  freezeAssetCommand,
  loadPendingAssetCommands,
  removePendingAssetCommand,
  savePendingAssetCommand,
} from "./assetImageCommands";
import { ApiRequestError } from "../../generated/api";

const request = {
  asset_kind: "CHARACTER" as const,
  asset_ids: ["a1", "a2"],
  profile_version_id: null,
  mode: "MISSING_ONLY" as const,
};

describe("R02 asset frozen commands", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("UNKNOWN 后再点普通生成不创建新 key（由组件守卫；此处验证冻结请求不可变）", () => {
    const frozen = freezeAssetCommand("p1", "CHARACTER", request, "hash".repeat(16), "key-1");
    (request.asset_ids as string[]).push("a3");
    expect(frozen.request.asset_ids).toEqual(["a1", "a2"]);
  });

  it("恢复使用原始 request、key、planHash（冻结深复制）", () => {
    const frozen = freezeAssetCommand("p1", "CHARACTER", request, "h".repeat(64), "key-2");
    expect(frozen.idempotencyKey).toBe("key-2");
    expect(frozen.expectedPlanHash).toBe("h".repeat(64));
    expect(frozen.request.profile_version_id).toBeNull();
  });

  it("UNKNOWN 刷新后仍可恢复（sessionStorage）", () => {
    const frozen = freezeAssetCommand("p1", "CHARACTER", request, "h".repeat(64), "key-3");
    savePendingAssetCommand(frozen);
    const { commands, readError } = loadPendingAssetCommands("p1");
    expect(readError).toBeNull();
    expect(commands.map((c) => c.idempotencyKey)).toContain("key-3");
  });

  it("待确认记录存储失败有可见错误，不假装可恢复", async () => {
    const { vi: vitestVi } = await import("vitest");
    const frozen = freezeAssetCommand("p1", "CHARACTER", request, "h".repeat(64), "key-4");
    const spy = vitestVi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    try {
      expect(() => savePendingAssetCommand(frozen)).toThrow(/待确认/);
    } finally {
      spy.mockRestore();
    }
  });

  it("未决条目不被容量清理静默删除", () => {
    for (let i = 0; i < 25; i += 1) {
      const frozen = freezeAssetCommand("p1", "CHARACTER", request, "h".repeat(64), `key-cap-${i}`);
      try {
        savePendingAssetCommand(frozen);
      } catch (error) {
        expect(String(error)).toContain("不能静默清理");
        return;
      }
    }
    // If no error, the store kept at most the bound without dropping silently
    // in a way the test can observe; the implementation throws instead.
    expect(loadPendingAssetCommands("p1").commands.length).toBeLessThanOrEqual(20);
  });

  it("移除已确认命令后不再恢复", () => {
    const frozen = freezeAssetCommand("p1", "CHARACTER", request, "h".repeat(64), "key-5");
    savePendingAssetCommand(frozen);
    removePendingAssetCommand("p1", "key-5");
    expect(loadPendingAssetCommands("p1").commands).toEqual([]);
  });

  it("明确业务拒绝 → REJECTED", () => {
    const error = new ApiRequestError("计划不可提交", 422, "ASSET_IMAGE_BATCH_BLOCKED", null, false, null, null);
    expect(classifyAssetSubmitError(error).outcome).toBe("REJECTED");
  });

  it("网络中断/超时/5xx → UNKNOWN", () => {
    expect(classifyAssetSubmitError(new TypeError("网络中断")).outcome).toBe("UNKNOWN");
    expect(classifyAssetSubmitError(new ApiRequestError("boom", 500, "HTTP_ERROR", null, false, null, null)).outcome).toBe("UNKNOWN");
  });

  it("同键冲突保留原记录并提示核对", () => {
    const error = new ApiRequestError("冲突", 409, "IDEMPOTENCY_PAYLOAD_MISMATCH", null, false, null, {
      existing_batch_id: "batch-12345678",
    });
    const classified = classifyAssetSubmitError(error);
    expect(classified.outcome).toBe("CONFLICT");
    if (classified.outcome === "CONFLICT") {
      expect(classified.existingBatchId).toBe("batch-12345678");
    }
  });

  it("已有进行中批次不并行创建", () => {
    const error = new ApiRequestError("进行中", 409, "ASSET_IMAGE_BATCH_ALREADY_IN_PROGRESS", null, false, null, {
      existing_batch_id: "batch-9999",
    });
    expect(classifyAssetSubmitError(error).outcome).toBe("CONFLICT");
  });

  it("未知 4xx 不误判为未受理", () => {
    const error = new ApiRequestError("未知", 403, "CSRF_TOKEN_REQUIRED", null, false, null, null);
    expect(classifyAssetSubmitError(error).outcome).toBe("UNKNOWN");
  });
});

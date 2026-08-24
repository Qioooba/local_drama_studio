import { afterEach, describe, expect, it, vi } from "vitest";
import {
  markEpisodePlanShotReady, runPerShot, setEpisodePlanShotAssetState,
} from "./episodePlanTableApi";

afterEach(() => vi.unstubAllGlobals());

describe("Episode Plan shot command routes", () => {
  it("uses the existing 0042 state command and real Production Ready route", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetch);
    await setEpisodePlanShotAssetState("shot-1", "asset-1", "state-1");
    await markEpisodePlanShotReady("shot-1");
    expect(fetch).toHaveBeenCalledWith("/api/v1/shots/shot-1/asset-state-bindings", expect.objectContaining({
      method: "POST", body: JSON.stringify({ asset_id: "asset-1", asset_state_id: "state-1" }),
    }));
    expect(fetch).toHaveBeenCalledWith("/api/v1/projects/shots/shot-1:mark-production-ready", expect.objectContaining({ method: "POST" }));
  });

  it("keeps per-shot partial success instead of pretending a batch is atomic", async () => {
    const results = await runPerShot(["shot-1", "shot-2"], async (shotId) => {
      if (shotId === "shot-2") throw new Error("not ready");
    });
    expect(results).toEqual([
      { shotId: "shot-1", ok: true, message: "已提交" },
      { shotId: "shot-2", ok: false, message: "not ready" },
    ]);
  });
});

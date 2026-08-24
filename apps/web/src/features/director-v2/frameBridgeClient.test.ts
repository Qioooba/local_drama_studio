import { afterEach, describe, expect, it, vi } from "vitest";
import { bootstrapLocalSession } from "../../generated/api";
import { setFrameBridgeSourceFrame } from "./frameBridgeClient";

vi.mock("../../generated/api", () => ({ bootstrapLocalSession: vi.fn() }));

describe("frameBridgeClient local write contract", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("adds the bootstrapped local instance token to semantic Frame Bridge writes", async () => {
    vi.mocked(bootstrapLocalSession).mockResolvedValue({ token: "local-test-token", mode: "LOCAL_ONLY" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ frame_bridge: { id: "transition-1", boundary_revision: 2 } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await setFrameBridgeSourceFrame("transition-1", 1, "anchor-1");

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/frame-bridges/transition-1/source-frame", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "X-Local-Instance-Token": "local-test-token" }),
    }));
  });
});

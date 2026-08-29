import { beforeEach, describe, expect, it, vi } from "vitest";

import { putGenerationPreference } from "./api";
import { apiJsonResponse } from "../../test/apiResponse";

describe("generation preference API mutation security", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("bootstraps and sends the local instance token on preference writes", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(apiJsonResponse({ token: "local-test-token", mode: "LOCAL_ONLY" }))
      .mockResolvedValueOnce(apiJsonResponse({ preference: { id: "pref-1" } }));
    vi.stubGlobal("fetch", fetchMock);

    await putGenerationPreference("project-1", {
      owner_type: "PROJECT",
      owner_id: "project-1",
      capability: "VIDEO_I2V",
      resolution_mode: "AUTO",
      execution_profile_version_id: null,
      settings: {},
      reason: "test",
      expected_revision: null,
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/session/bootstrap",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new Headers(init.headers).get("X-Local-Instance-Token")).toBe("local-test-token");
  });
});

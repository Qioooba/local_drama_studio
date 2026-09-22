import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cloneJob, startAutomationWorkflowRun } from "../generated/api";
import { submitAssetMultiView } from "../features/asset-bible-v2/multiviewClient";
import { runVisualLabNode } from "../features/visual-lab/client";
import { isCommandId } from "./commandId";

const fetchMock = vi.fn();

/**
 * FE-09: the product supports being served over plain HTTP on a LAN address, which
 * is not a secure browser context. There `crypto.getRandomValues` exists but
 * `crypto.randomUUID` is undefined. These are the real client entry points the
 * audit exercised; each must reach the network with a valid id instead of throwing
 * before any request is sent.
 */
function useLanInsecureContext() {
  let draw = 0;
  vi.stubGlobal("crypto", {
    subtle: undefined,
    getRandomValues: <T extends ArrayBufferView | null>(array: T): T => {
      if (array && "length" in array) {
        const view = array as unknown as Uint8Array;
        draw += 1;
        for (let index = 0; index < view.length; index += 1) view[index] = (draw * 17 + index * 53 + 7) % 256;
      }
      return array;
    },
  } as unknown as Crypto);
}

function apiResponse(body: unknown): Response {
  const headers = new Headers({ "Content-Type": "application/json", "X-API-Contract-Version": "localdrama.api.2026-08-29.3" });
  return new Response(JSON.stringify(body), { status: 200, headers });
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (path: string) => apiResponse(path.endsWith("/session/bootstrap") ? { token: "lan-token", mode: "LAN_SERVICE" } : { intent: { id: "intent-1" }, items: [] }));
  vi.stubGlobal("fetch", fetchMock);
  useLanInsecureContext();
});

afterEach(() => vi.unstubAllGlobals());

describe("LAN HTTP context without crypto.randomUUID", () => {
  it("submits multi-view generation with a request actually sent", async () => {
    const body: unknown = await submitAssetMultiView("asset-1", { asset_state_id: null, profile_version_id: null, consistency_strength: "HIGH", background: "CLEAN" }, "plan-hash")
      .then(() => JSON.parse(String(fetchMock.mock.calls.find(([path]) => String(path).endsWith("/generate-multiview"))?.[1]?.body)));
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith("/story-assets/asset-1/generate-multiview"))).toBe(true);
    expect(isCommandId(String((body as { idempotency_key: string }).idempotency_key))).toBe(true);
  });

  it("submits a Visual Lab node run with a request actually sent", async () => {
    await runVisualLabNode("node-1", "plan-hash");
    const call = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/visual-lab-nodes/node-1/runs"));
    const body = JSON.parse(String(call?.[1]?.body)) as { idempotency_key: string };
    expect(isCommandId(body.idempotency_key)).toBe(true);
  });

  it("clones a job with a valid Idempotency-Key header and stable retries", async () => {
    await cloneJob("job-1", { seed: 3 });
    await cloneJob("job-1", { seed: 3 });
    const calls = fetchMock.mock.calls.filter(([path]) => String(path).endsWith("/jobs/job-1:clone"));
    expect(calls).toHaveLength(2);
    const keys = calls.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"));
    expect(keys.every((key) => key !== null && isCommandId(key))).toBe(true);
    expect(keys[1]).toBe(keys[0]);

    await cloneJob("job-1", { seed: 4 });
    const changed = fetchMock.mock.calls.filter(([path]) => String(path).endsWith("/jobs/job-1:clone")).at(-1);
    expect(new Headers(changed?.[1]?.headers).get("Idempotency-Key")).not.toBe(keys[0]);
  });

  it("starts an automation workflow run with a valid Idempotency-Key header", async () => {
    await startAutomationWorkflowRun("workflow-1", "plan-hash");
    const call = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/automation-workflows/workflow-1/runs"));
    const key = new Headers(call?.[1]?.headers).get("Idempotency-Key");
    expect(key !== null && isCommandId(key)).toBe(true);
  });
});

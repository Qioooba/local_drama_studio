import { afterEach, describe, expect, it, vi } from "vitest";
import {
  canonicalRequest,
  completeOperation,
  isCommandId,
  newCommandId,
  operationIdempotencyKey,
  resetCommandIdCache,
  stableIdempotencyKey,
} from "./commandId";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * A plain-HTTP LAN address is not a secure browser context: `crypto` exists and
 * `getRandomValues` works, but `randomUUID` is undefined. Everything that mints a
 * command id must keep working in exactly that environment.
 */
function useLanInsecureContext() {
  let draw = 0;
  const getRandomValues = <T extends ArrayBufferView | null>(array: T): T => {
    if (array && "length" in array) {
      const view = array as unknown as Uint8Array;
      draw += 1;
      for (let index = 0; index < view.length; index += 1) view[index] = (draw * 31 + index * 37 + 11) % 256;
    }
    return array;
  };
  vi.stubGlobal("crypto", { getRandomValues, subtle: undefined } as unknown as Crypto);
}

afterEach(() => {
  vi.unstubAllGlobals();
  resetCommandIdCache();
});

describe("command ids without crypto.randomUUID", () => {
  it("still mints a valid v4 id from crypto.getRandomValues", () => {
    useLanInsecureContext();
    const id = newCommandId();
    expect(isCommandId(id)).toBe(true);
    expect(id).toMatch(UUID_V4);
  });

  it("mints distinct ids for distinct actions", () => {
    useLanInsecureContext();
    const ids = new Set(Array.from({ length: 32 }, () => newCommandId()));
    expect(ids.size).toBe(32);
  });

  it("keeps stable idempotency keys valid in the LAN context", () => {
    useLanInsecureContext();
    expect(isCommandId(stableIdempotencyKey("job-clone:job-1", { input_overrides: {} }))).toBe(true);
  });

  it("reuses the platform generator when randomUUID exists", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "11111111-2222-4333-8444-555555555555", getRandomValues: (array: Uint8Array) => array } as unknown as Crypto);
    expect(newCommandId()).toBe("11111111-2222-4333-8444-555555555555");
  });

  it("fails with an actionable message when the browser has no crypto at all", () => {
    vi.stubGlobal("crypto", undefined);
    expect(() => newCommandId()).toThrow(/安全随机数/);
  });
});

describe("idempotency-key lifetime", () => {
  it("reuses one key for a retry of the same operation and payload", () => {
    const payload = { input_overrides: { seed: 7 } };
    const first = stableIdempotencyKey("job-clone:job-1", payload);
    const retry = stableIdempotencyKey("job-clone:job-1", { input_overrides: { seed: 7 } });
    expect(retry).toBe(first);
  });

  it("rotates the key when the normalised payload changes", () => {
    const first = stableIdempotencyKey("job-clone:job-1", { input_overrides: { seed: 7 } });
    const changed = stableIdempotencyKey("job-clone:job-1", { input_overrides: { seed: 8 } });
    expect(changed).not.toBe(first);
  });

  it("ignores key order but not values when normalising the request", () => {
    expect(canonicalRequest({ b: 1, a: 2 })).toBe(canonicalRequest({ a: 2, b: 1 }));
    expect(canonicalRequest({ a: 2 })).not.toBe(canonicalRequest({ a: 3 }));
  });

  it("binds an operation key to the caller's session and retires it on completion", () => {
    const request = { endpoint_url: "http://127.0.0.1:8765/hooks", project_id: "p1" };
    const first = operationIdempotencyKey("webhook-subscription", request);
    expect(operationIdempotencyKey("webhook-subscription", { ...request })).toBe(first);
    expect(operationIdempotencyKey("webhook-subscription", { ...request, endpoint_url: "http://127.0.0.1:9999/hooks" })).not.toBe(first);
    const rotated = operationIdempotencyKey("webhook-subscription", request);
    completeOperation("webhook-subscription");
    expect(operationIdempotencyKey("webhook-subscription", request)).not.toBe(rotated);
  });

  it("keeps separate operation scopes independent", () => {
    expect(operationIdempotencyKey("scope-a", { value: 1 })).not.toBe(operationIdempotencyKey("scope-b", { value: 1 }));
  });
});

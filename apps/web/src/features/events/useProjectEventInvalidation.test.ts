import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createBatchedQueryInvalidator, EVENT_INVALIDATION_BATCH_MS } from "./useProjectEventInvalidation";

afterEach(() => vi.useRealTimers());

describe("createBatchedQueryInvalidator", () => {
  it("coalesces an event burst and keeps distinct query keys bounded", () => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const batch = createBatchedQueryInvalidator(client);

    for (let index = 0; index < 50; index += 1) batch.enqueue([["jobs", "p1"]]);
    batch.enqueue([["capacity", "p1"]]);
    expect(invalidate).not.toHaveBeenCalled();
    vi.advanceTimersByTime(EVENT_INVALIDATION_BATCH_MS);

    expect(invalidate).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["jobs", "p1"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["capacity", "p1"] });
    batch.dispose();
  });

  it("drops pending invalidations when the page unsubscribes", () => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const batch = createBatchedQueryInvalidator(client);
    batch.enqueue([["director-desk-v2", "p1"]]);
    batch.dispose();
    vi.runAllTimers();
    expect(invalidate).not.toHaveBeenCalled();
  });
});

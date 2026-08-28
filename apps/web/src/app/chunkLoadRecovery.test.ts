import { describe, expect, it, vi } from "vitest";
import { recoverFromPreloadError } from "./chunkLoadRecovery";

function memoryStorage(): Pick<Storage, "getItem" | "setItem"> {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

describe("chunk load recovery", () => {
  it("suppresses the stale chunk error and refreshes the current route once", () => {
    const storage = memoryStorage();
    const firstEvent = new Event("vite:preloadError", { cancelable: true });
    const firstReload = vi.fn();

    expect(recoverFromPreloadError({
      event: firstEvent,
      storage,
      path: "/projects/p1/story",
      reload: firstReload,
      now: 1_000,
    })).toBe(true);
    expect(firstEvent.defaultPrevented).toBe(true);
    expect(firstReload).toHaveBeenCalledOnce();

    const secondEvent = new Event("vite:preloadError", { cancelable: true });
    const secondReload = vi.fn();
    expect(recoverFromPreloadError({
      event: secondEvent,
      storage,
      path: "/projects/p1/story",
      reload: secondReload,
      now: 2_000,
    })).toBe(false);
    expect(secondEvent.defaultPrevented).toBe(false);
    expect(secondReload).not.toHaveBeenCalled();
  });

  it("permits another recovery after the cooldown", () => {
    const storage = memoryStorage();
    const reload = vi.fn();
    recoverFromPreloadError({ event: new Event("vite:preloadError", { cancelable: true }), storage, path: "/projects", reload, now: 1_000 });
    recoverFromPreloadError({ event: new Event("vite:preloadError", { cancelable: true }), storage, path: "/projects", reload, now: 31_001 });
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("does not risk a reload loop when session storage is unavailable", () => {
    const event = new Event("vite:preloadError", { cancelable: true });
    const reload = vi.fn();
    const storage = {
      getItem: () => null,
      setItem: () => { throw new Error("storage disabled"); },
    };

    expect(recoverFromPreloadError({ event, storage, path: "/projects/p1/story", reload, now: 1_000 })).toBe(false);
    expect(event.defaultPrevented).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { subscribeStudioEvents } from "./eventClient";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  closed = false;
  listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();

  constructor(url: string) { this.url = url; FakeEventSource.instances.push(this); }
  addEventListener(type: string, listener: EventListener) {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener as (event: MessageEvent<string>) => void);
    this.listeners.set(type, listeners);
  }
  close() { this.closed = true; }
  emit(type: string, eventId: number) {
    const message = { data: JSON.stringify({ event_id: eventId, type }), lastEventId: String(eventId) } as MessageEvent<string>;
    this.listeners.get(type)?.forEach((listener) => listener(message));
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  sessionStorage.clear();
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("subscribeStudioEvents", () => {
  it("deduplicates replay and reconnects from the persisted event cursor", () => {
    const onEvent = vi.fn();
    const stop = subscribeStudioEvents({ projectId: "p1", eventTypes: ["JOB_FINISHED"], onEvent });
    const first = FakeEventSource.instances[0]!;
    first.emit("JOB_FINISHED", 41);
    first.emit("JOB_FINISHED", 41);

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("local-drama:event-cursor:p1")).toBe("41");
    first.onerror?.();
    vi.advanceTimersByTime(500);

    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances[1]?.url).toContain("after_event_id=41");
    FakeEventSource.instances[1]?.emit("JOB_FINISHED", 42);
    expect(onEvent).toHaveBeenCalledTimes(2);
    stop();
  });

  it("does not listen for heartbeat unless the subscriber requested it", () => {
    const onEvent = vi.fn();
    const stop = subscribeStudioEvents({ projectId: "p1", eventTypes: ["JOB_FINISHED", "JOB_FINISHED"], onEvent });
    const source = FakeEventSource.instances[0]!;
    expect(source.listeners.get("JOB_FINISHED")).toHaveLength(1);
    expect(source.listeners.has("JOB_HEARTBEAT")).toBe(false);
    source.emit("JOB_HEARTBEAT", 5);
    expect(onEvent).not.toHaveBeenCalled();
    stop();
  });

  it("replaces a possibly stale connection after the browser comes online", () => {
    const stop = subscribeStudioEvents({ projectId: "p1", eventTypes: ["JOB_FINISHED"], onEvent: vi.fn() });
    const first = FakeEventSource.instances[0]!;
    first.emit("JOB_FINISHED", 9);
    window.dispatchEvent(new Event("online"));

    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances[1]?.url).toContain("after_event_id=9");
    stop();
  });
});

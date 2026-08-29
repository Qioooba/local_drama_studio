import { API_CONTRACT_VERSION } from "../../generated/api";

export type StudioEvent = {
  event_id: number;
  type: string;
  project_id?: string | null;
  subject_type?: string;
  subject_id?: string;
  payload?: Record<string, unknown>;
};

export type StudioEventSubscription = {
  projectId: string;
  eventTypes: readonly string[];
  onEvent: (event: StudioEvent) => void;
};

const cursorKey = (projectId: string) => `local-drama:event-cursor:${projectId || "all"}`;

function readCursor(projectId: string): number {
  try {
    const value = Number.parseInt(sessionStorage.getItem(cursorKey(projectId)) ?? "0", 10);
    return Number.isSafeInteger(value) && value >= 0 ? value : 0;
  } catch {
    return 0;
  }
}

function writeCursor(projectId: string, cursor: number): void {
  try { sessionStorage.setItem(cursorKey(projectId), String(cursor)); } catch {
    // Storage may be unavailable in a long-running private/local session; the in-memory cursor remains authoritative.
  }
}

/** Project-scoped SSE with explicit cursor reconnect; the server closes follow streams after 60s. */
export function subscribeStudioEvents({ projectId, eventTypes, onEvent }: StudioEventSubscription) {
  if (typeof EventSource === "undefined") return () => undefined;
  let source: EventSource | null = null;
  let stopped = false;
  let retryTimer: number | null = null;
  let retryMs = 500;
  let cursor = readCursor(projectId);

  const connect = () => {
    if (stopped) return;
    const query = new URLSearchParams({
      after_event_id: String(cursor),
      follow: "true",
      api_contract_version: API_CONTRACT_VERSION,
    });
    if (projectId) query.set("project_id", projectId);
    source = new EventSource(`/api/v1/events?${query}`);
    source.onopen = () => { retryMs = 500; };
    const receive = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as StudioEvent;
        const nextCursor = Number(message.lastEventId || event.event_id || 0);
        if (!Number.isSafeInteger(nextCursor) || nextCursor <= cursor || !event.type) return;
        cursor = nextCursor;
        writeCursor(projectId, cursor);
        onEvent(event);
      } catch {
        // A malformed event cannot advance the cursor or invalidate facts.
      }
    };
    new Set(eventTypes).forEach((eventType) => source?.addEventListener(eventType, receive as EventListener));
    source.onerror = () => {
      source?.close();
      source = null;
      if (stopped || retryTimer !== null) return;
      retryTimer = window.setTimeout(() => {
        retryTimer = null;
        connect();
      }, retryMs);
      retryMs = Math.min(retryMs * 2, 10_000);
    };
  };
  const reconnectNow = () => {
    if (stopped) return;
    if (retryTimer !== null) window.clearTimeout(retryTimer);
    retryTimer = null;
    source?.close();
    source = null;
    retryMs = 500;
    connect();
  };
  const resumeIfVisible = () => {
    if (document.visibilityState === "visible") reconnectNow();
  };
  window.addEventListener("online", reconnectNow);
  document.addEventListener("visibilitychange", resumeIfVisible);
  connect();
  return () => {
    stopped = true;
    window.removeEventListener("online", reconnectNow);
    document.removeEventListener("visibilitychange", resumeIfVisible);
    source?.close();
    if (retryTimer !== null) window.clearTimeout(retryTimer);
  };
}

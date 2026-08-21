import { useEffect } from "react";
import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { subscribeStudioEvents, type StudioEvent } from "./eventClient";

export const EVENT_INVALIDATION_BATCH_MS = 75;

export function createBatchedQueryInvalidator(queryClient: QueryClient, delay = EVENT_INVALIDATION_BATCH_MS) {
  const pending = new Map<string, QueryKey>();
  let timer: number | null = null;
  const flush = () => {
    timer = null;
    const keys = [...pending.values()];
    pending.clear();
    keys.forEach((queryKey) => { void queryClient.invalidateQueries({ queryKey }); });
  };
  return {
    enqueue(queryKeys: readonly QueryKey[]) {
      queryKeys.forEach((queryKey) => pending.set(JSON.stringify(queryKey), queryKey));
      if (timer === null) timer = window.setTimeout(flush, delay);
    },
    dispose() {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      pending.clear();
    },
  };
}

export function useProjectEventInvalidation(
  projectId: string,
  eventTypes: readonly string[],
  queryKeys: readonly QueryKey[],
  queryKeysForEvent?: (event: StudioEvent) => readonly QueryKey[],
) {
  const queryClient = useQueryClient();
  const eventSignature = eventTypes.join("|");
  const keySignature = queryKeys.map((key) => JSON.stringify(key)).join("|");
  useEffect(() => {
    const invalidator = createBatchedQueryInvalidator(queryClient);
    const unsubscribe = subscribeStudioEvents({
      projectId,
      eventTypes,
      onEvent: (event) => invalidator.enqueue(queryKeysForEvent?.(event) ?? queryKeys),
    });
    return () => { unsubscribe(); invalidator.dispose(); };
  // Stable signatures intentionally own subscription identity; callers may use inline arrays.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventSignature, keySignature, projectId, queryClient]);
}

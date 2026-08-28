const RELOAD_MARKER_KEY = "local-drama:chunk-load-recovery";
const RELOAD_COOLDOWN_MS = 30_000;

type ReloadMarker = {
  attemptedAt: number;
  path: string;
};

type StorageLike = Pick<Storage, "getItem" | "setItem">;

export type PreloadRecoveryOptions = {
  event: Event;
  storage: StorageLike;
  path: string;
  reload: () => void;
  now?: number;
};

function readMarker(storage: StorageLike): ReloadMarker | null {
  try {
    const raw = storage.getItem(RELOAD_MARKER_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ReloadMarker>;
    if (typeof parsed.attemptedAt !== "number" || typeof parsed.path !== "string") return null;
    return { attemptedAt: parsed.attemptedAt, path: parsed.path };
  } catch {
    return null;
  }
}

function writeMarker(storage: StorageLike, marker: ReloadMarker): boolean {
  try {
    storage.setItem(RELOAD_MARKER_KEY, JSON.stringify(marker));
    return true;
  } catch {
    return false;
  }
}

/**
 * Recover once from a stale Vite module graph after the app is rebuilt.
 * A second failure in the cooldown window is allowed through to the route
 * boundary so a genuine network/server problem cannot cause a reload loop.
 */
export function recoverFromPreloadError({
  event,
  storage,
  path,
  reload,
  now = Date.now(),
}: PreloadRecoveryOptions): boolean {
  const previous = readMarker(storage);
  if (previous?.path === path && now - previous.attemptedAt < RELOAD_COOLDOWN_MS) {
    return false;
  }

  // Without a durable marker a reload could repeat forever in privacy modes
  // that disable session storage, so let the route boundary handle the error.
  if (!writeMarker(storage, { attemptedAt: now, path })) return false;
  event.preventDefault();
  reload();
  return true;
}

export function installChunkLoadRecovery(target: Window = window): () => void {
  const handlePreloadError = (event: Event) => {
    let storage: Storage;
    try {
      storage = target.sessionStorage;
    } catch {
      return;
    }
    recoverFromPreloadError({
      event,
      storage,
      path: `${target.location.pathname}${target.location.search}${target.location.hash}`,
      reload: () => target.location.reload(),
    });
  };
  target.addEventListener("vite:preloadError", handlePreloadError);
  return () => target.removeEventListener("vite:preloadError", handlePreloadError);
}

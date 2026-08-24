const DIRECTOR_BATCH_STORAGE_PREFIX = "local-drama:director-batch:v1:";
const MAX_BATCH_SHOTS = 500;

export type DirectorBatchState = {
  episodeId: string;
  shotIds: string[];
  doneIds: string[];
  createdAt: string;
};

function uniqueIds(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  return values
    .map((value) => String(value ?? "").trim())
    .filter((value, index, all) => Boolean(value) && value.length <= 128 && all.indexOf(value) === index)
    .slice(0, MAX_BATCH_SHOTS);
}

function storageKey(reference: string) {
  return `${DIRECTOR_BATCH_STORAGE_PREFIX}${reference}`;
}

function makeReference() {
  try {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  } catch { /* use a local fallback below */ }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function persistDirectorBatch(episodeId: string, shotIds: string[]): string {
  const safeShotIds = uniqueIds(shotIds);
  if (!episodeId || safeShotIds.length === 0) return "";
  const reference = makeReference();
  const state: DirectorBatchState = { episodeId, shotIds: safeShotIds, doneIds: [], createdAt: new Date().toISOString() };
  try {
    window.localStorage.setItem(storageKey(reference), JSON.stringify(state));
    return `ref:${reference}`;
  } catch {
    // Storage can be unavailable in hardened/private browser contexts. Preserve
    // the legacy URL contract as an explicit, recoverable fallback.
    return safeShotIds.join(",");
  }
}

export function readDirectorBatch(batchParam: string, episodeId: string, legacyDoneParam = ""): DirectorBatchState {
  if (!batchParam.startsWith("ref:")) {
    const shotIds = uniqueIds(batchParam.split(","));
    return {
      episodeId,
      shotIds,
      doneIds: uniqueIds(legacyDoneParam.split(",")).filter((id) => shotIds.includes(id)),
      createdAt: "",
    };
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(storageKey(batchParam.slice(4))) || "null") as Partial<DirectorBatchState> | null;
    if (!parsed || parsed.episodeId !== episodeId) return { episodeId, shotIds: [], doneIds: [], createdAt: "" };
    const shotIds = uniqueIds(parsed.shotIds);
    return {
      episodeId,
      shotIds,
      doneIds: uniqueIds(parsed.doneIds).filter((id) => shotIds.includes(id)),
      createdAt: String(parsed.createdAt ?? ""),
    };
  } catch {
    return { episodeId, shotIds: [], doneIds: [], createdAt: "" };
  }
}

export function updateDirectorBatchDone(batchParam: string, episodeId: string, doneIds: string[]): boolean {
  if (!batchParam.startsWith("ref:")) return false;
  const current = readDirectorBatch(batchParam, episodeId);
  if (current.shotIds.length === 0) return false;
  try {
    window.localStorage.setItem(storageKey(batchParam.slice(4)), JSON.stringify({
      ...current,
      doneIds: uniqueIds(doneIds).filter((id) => current.shotIds.includes(id)),
    }));
    return true;
  } catch {
    return false;
  }
}

export function removeDirectorBatch(batchParam: string) {
  if (!batchParam.startsWith("ref:")) return;
  try { window.localStorage.removeItem(storageKey(batchParam.slice(4))); } catch { /* non-fatal cleanup */ }
}

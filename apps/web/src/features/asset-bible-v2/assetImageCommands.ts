import { ApiRequestError } from "../../generated/api";

export type FrozenAssetCommandRequest = {
  asset_kind: import("./assetImageBatchClient").AssetImageKind;
  asset_ids: string[];
  profile_version_id: string | null;
  mode: "MISSING_ONLY";
};

export type FrozenAssetCommand = {
  schemaVersion: 1;
  projectId: string;
  kind: import("./assetImageBatchClient").AssetImageKind;
  idempotencyKey: string;
  expectedPlanHash: string;
  request: FrozenAssetCommandRequest;
  submittedAt: string;
};

const STORAGE_PREFIX = "local-drama:asset-image-pending-commands:v1";
const STORAGE_MAX_COMMANDS = 20;

function storageKey(projectId: string): string {
  return `${STORAGE_PREFIX}:${projectId}`;
}

function deepCopy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function freezeAssetCommand(
  projectId: string,
  kind: FrozenAssetCommand["kind"],
  request: FrozenAssetCommandRequest,
  expectedPlanHash: string,
  idempotencyKey: string,
): FrozenAssetCommand {
  return {
    schemaVersion: 1,
    projectId,
    kind,
    idempotencyKey,
    expectedPlanHash,
    request: deepCopy(request),
    submittedAt: new Date().toISOString(),
  };
}

function readStored(projectId: string): FrozenAssetCommand[] {
  const raw = window.sessionStorage.getItem(storageKey(projectId));
  if (!raw) return [];
  const parsed = JSON.parse(raw) as { version?: number; commands?: FrozenAssetCommand[] };
  if (!Array.isArray(parsed.commands)) {
    throw new Error("待确认记录格式损坏");
  }
  return parsed.commands.filter(
    (command) =>
      command &&
      command.schemaVersion === 1 &&
      command.projectId === projectId &&
      typeof command.idempotencyKey === "string" &&
      command.request &&
      typeof command.expectedPlanHash === "string",
  );
}

export function loadPendingAssetCommands(projectId: string): {
  commands: FrozenAssetCommand[];
  readError: string | null;
} {
  try {
    return { commands: readStored(projectId), readError: null };
  } catch (error) {
    return {
      commands: [],
      readError:
        error instanceof Error ? error.message : String(error),
    };
  }
}

/**
 * Persist an UNKNOWN command for cross-refresh recovery. Throws on storage
 * failure so the caller can visibly block sends that would otherwise pretend
 * to be recoverable. Never silently evicts unresolved entries.
 */
export function savePendingAssetCommand(command: FrozenAssetCommand): void {
  const key = storageKey(command.projectId);
  let existing: FrozenAssetCommand[] = [];
  try {
    existing = readStored(command.projectId);
  } catch {
    // Corrupt record: do not merge; overwrite is also unsafe without a server
    // check, so surface the failure and let the caller verify the server.
    throw new Error("待确认记录读取失败，请先核对服务器回执，不要重复提交");
  }
  const deduped = existing.filter((item) => item.idempotencyKey !== command.idempotencyKey);
  const next = [...deduped, command].slice(-STORAGE_MAX_COMMANDS);
  // If bounding dropped an unresolved entry, refuse instead of silently deleting.
  const dropped = deduped.length + 1 - next.length;
  if (dropped > 0) {
    throw new Error("待确认记录已满且不能静默清理，请先处理最早的待确认命令");
  }
  try {
    window.sessionStorage.setItem(key, JSON.stringify({ version: 1, commands: next }));
  } catch {
    throw new Error("待确认记录写入失败（可能是容量或隐私设置限制）；已阻止发送需要跨刷新恢复的命令");
  }
}

export function removePendingAssetCommand(projectId: string, idempotencyKey: string): void {
  try {
    const existing = readStored(projectId);
    const next = existing.filter((item) => item.idempotencyKey !== idempotencyKey);
    if (next.length === existing.length) return;
    window.sessionStorage.setItem(storageKey(projectId), JSON.stringify({ version: 1, commands: next }));
  } catch {
    // Removal failures must not block the authoritative receipt; the entry
    // will be re-resolved via exact server query on next load.
  }
}

export type ClassifiedAssetError =
  | { outcome: "REJECTED"; code: string | null; message: string; existingBatchId?: string }
  | { outcome: "UNKNOWN"; code: string | null; message: string; existingBatchId?: string }
  | { outcome: "CONFLICT"; code: string; message: string; existingBatchId: string };

const REJECTED_CODES = new Set([
  "ASSET_IMAGE_BATCH_BLOCKED",
  "ASSET_IMAGE_BATCH_PLAN_STALE",
  "ASSET_IMAGE_KIND_UNSUPPORTED",
  "ASSET_IMAGE_MODE_UNSUPPORTED",
  "ASSET_IMAGE_BATCH_EMPTY",
  "ASSET_IMAGE_BATCH_TOO_LARGE",
  "ASSET_IMAGE_PROFILE_REQUIRED",
  "ASSET_IMAGE_PROFILE_NOT_PUBLISHED",
  "ASSET_IMAGE_PROFILE_CAPABILITY_MISMATCH",
  "ASSET_IMAGE_PROFILE_REQUIRES_REFERENCE_IMAGE",
  "ASSET_IMAGE_PROFILE_WORKFLOW_REQUIRED",
  "ASSET_IMAGE_WORKFLOW_UNAVAILABLE",
  "PROJECT_NOT_FOUND",
  "IDEMPOTENCY_KEY_REQUIRED",
  "VALIDATION_ERROR",
]);

export function errorText(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  return error instanceof Error ? error.message : String(error);
}

function existingBatchIdOf(error: unknown): string | undefined {
  if (error instanceof ApiRequestError) {
    const details = error.details as Record<string, unknown> | null;
    const candidate = details?.["existing_batch_id"];
    if (typeof candidate === "string" && candidate) return candidate;
  }
  return undefined;
}

/**
 * Classify submit-stage errors without inferring acceptance from HTTP status
 * alone. Network interruption, parse failure, timeout and 5xx default to
 * UNKNOWN (may have been accepted). Explicit business rejections map to
 * REJECTED. Same-key conflicts retain the original record for manual check.
 */
export function classifyAssetSubmitError(error: unknown): ClassifiedAssetError {
  const message = errorText(error);
  const existingBatchId = existingBatchIdOf(error);
  if (error instanceof ApiRequestError) {
    if (error.code === "IDEMPOTENCY_PAYLOAD_MISMATCH") {
      return {
        outcome: "CONFLICT",
        code: error.code,
        message: existingBatchId
          ? `同一命令键已有不同参数的批次（${existingBatchId.slice(0, 8)}），未自动换键，请核对后人工确认。${message}`
          : `同一命令键已有不同参数的请求，未自动换键，请核对后人工确认。${message}`,
        existingBatchId: existingBatchId ?? "",
      };
    }
    if (error.code === "ASSET_IMAGE_BATCH_ALREADY_IN_PROGRESS") {
      return {
        outcome: "CONFLICT",
        code: error.code,
        message: existingBatchId
          ? `已有进行中的相同补齐任务（批次 ${existingBatchId.slice(0, 8)}），未重复提交。${message}`
          : `已有进行中的相同补齐任务，未重复提交。${message}`,
        existingBatchId: existingBatchId ?? "",
      };
    }
    if (REJECTED_CODES.has(error.code)) {
      return { outcome: "REJECTED", code: error.code, message, existingBatchId };
    }
    if (error.status >= 500) {
      return { outcome: "UNKNOWN", code: error.code, message, existingBatchId };
    }
    // Other 4xx without an explicit acceptance-stage code stay UNKNOWN when
    // they may have been accepted (e.g. timeout-mapped 4xx, proxy errors).
    // Only the allowlisted business codes above are confirmed rejections.
    if (error.status >= 400 && error.status < 500) {
      return { outcome: "UNKNOWN", code: error.code, message, existingBatchId };
    }
  }
  if (error instanceof TypeError) {
    return { outcome: "UNKNOWN", code: null, message };
  }
  const text = message.toLowerCase();
  if (text.includes("timeout") || text.includes("超时") || text.includes("network") || text.includes("网络") || text.includes("fetch")) {
    return { outcome: "UNKNOWN", code: null, message };
  }
  return { outcome: "UNKNOWN", code: null, message };
}

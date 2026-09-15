/**
 * Bounded navigation-draft coordinator (repository integration).
 *
 * One bounded pass over the dirty set captured at entry. Never auto-saves
 * newly arriving edits, never clears the registry, never navigates. The
 * caller (AppShell) must recheck the live registry immediately before
 * invoking the live router blocker's proceed() in the same sync segment.
 */
import type {
  DraftDiscardResult,
  DraftSaveResult,
  MaybePromise,
} from "./draftRegistry";

export type DraftAction = "save" | "discard";
export type { DraftDiscardResult, DraftSaveResult };
export type MaybePromiseAlias<T> = MaybePromise<T>;

export interface DraftOwnerForSettle {
  readonly ownerId: string;
  readonly entityKey: string;
  readonly registrationToken: string;
  readonly version: number;
  readonly dirty: boolean;
  readonly save?: (expectedVersion: number) => MaybePromise<unknown>;
  readonly discard?: (expectedVersion: number) => MaybePromise<unknown>;
}

export interface SettleRegistryReader {
  get(ownerId: string): Readonly<DraftOwnerForSettle> | undefined;
  getDirty(): readonly Readonly<DraftOwnerForSettle>[];
}

export type SettleResult =
  | { allowed: true; completedOwnerIds: string[] }
  | { allowed: false; code: string; reason: string; completedOwnerIds: string[] };

function sameIdentity(
  a: Readonly<DraftOwnerForSettle>,
  b: Readonly<DraftOwnerForSettle>,
): boolean {
  return (
    a.ownerId === b.ownerId &&
    a.registrationToken === b.registrationToken &&
    a.version === b.version
  );
}

function normalizeSaveResult(
  raw: unknown,
  expectedVersion: number,
): DraftSaveResult | { status: "legacy-blocked"; reason: string } {
  if (raw === true) return { status: "saved", savedVersion: expectedVersion };
  if (raw === false || raw === null || raw === undefined) {
    return {
      status: "legacy-blocked",
      reason: "保存未完成，请处理页面中的错误后重试。",
    };
  }
  if (typeof raw === "object") {
    const candidate = raw as Partial<DraftSaveResult> & { status?: string };
    if (candidate.status === "saved" || candidate.status === "blocked") {
      return candidate as DraftSaveResult;
    }
  }
  return {
    status: "legacy-blocked",
    reason: "该草稿不支持在此处保存，请回到原编辑器完成保存。",
  };
}

function normalizeDiscardResult(
  raw: unknown,
  expectedVersion: number,
): DraftDiscardResult | { status: "legacy-blocked"; reason: string } {
  if (raw === true) return { status: "discarded", discardedVersion: expectedVersion };
  if (raw === false || raw === null || raw === undefined) {
    return {
      status: "legacy-blocked",
      reason: "未能放弃当前修改，请在页面内处理后重试。",
    };
  }
  if (typeof raw === "object") {
    const candidate = raw as Partial<DraftDiscardResult> & { status?: string };
    if (candidate.status === "discarded" || candidate.status === "blocked") {
      return candidate as DraftDiscardResult;
    }
  }
  return {
    status: "legacy-blocked",
    reason: "该草稿不支持在此处放弃，请回到原编辑器处理。",
  };
}

export async function settleDirtyDrafts(
  registry: SettleRegistryReader,
  action: DraftAction,
): Promise<SettleResult> {
  const targets = registry
    .getDirty()
    .map((owner) => ({ ...owner }));
  const completedOwnerIds: string[] = [];
  const blocked = (code: string, reason: string): SettleResult => ({
    allowed: false,
    code,
    reason,
    completedOwnerIds: [...completedOwnerIds],
  });

  for (const target of targets) {
    // Re-read the callback: a previous save may have refreshed the server
    // revision without changing this owner's local editable-payload version.
    const current = registry.get(target.ownerId);
    if (!current)
      return blocked(
        "DRAFT_OWNER_DISAPPEARED",
        `“${target.entityKey}”的编辑器已变化，请重新确认。`,
      );
    if (!sameIdentity(current, target))
      return blocked(
        "DRAFT_CHANGED",
        `“${target.entityKey}”产生了新修改，请重新确认。`,
      );
    if (!current.dirty) continue;

    let raw: unknown;
    try {
      if (action === "save") {
        if (!current.save)
          return blocked(
            "DRAFT_SAVE_UNSUPPORTED",
            `请回到“${target.entityKey}”完成保存。`,
          );
        raw = await current.save(target.version);
      } else {
        if (!current.discard)
          return blocked(
            "DRAFT_DISCARD_UNSUPPORTED",
            `请回到“${target.entityKey}”处理草稿。`,
          );
        raw = await current.discard(target.version);
      }
    } catch (error) {
      return blocked(
        "DRAFT_ACTION_FAILED",
        `“${target.entityKey}”处理失败：${error instanceof Error ? error.message : String(error)}`,
      );
    }

    const isLegacyBooleanSuccess = raw === true;

    if (action === "save") {
      const normalized = normalizeSaveResult(raw, target.version);
      if (normalized.status === "legacy-blocked" || normalized.status === "blocked") {
        return blocked("DRAFT_ACTION_BLOCKED", normalized.reason);
      }
      if (normalized.savedVersion !== target.version) {
        return blocked(
          "DRAFT_ACK_MISMATCH",
          `“${target.entityKey}”的处理回执版本不匹配。`,
        );
      }
    } else {
      const normalized = normalizeDiscardResult(raw, target.version);
      if (normalized.status === "legacy-blocked" || normalized.status === "blocked") {
        return blocked("DRAFT_ACTION_BLOCKED", normalized.reason);
      }
      if (normalized.discardedVersion !== target.version) {
        return blocked(
          "DRAFT_ACK_MISMATCH",
          `“${target.entityKey}”的处理回执版本不匹配。`,
        );
      }
    }

    // Legacy bridge: boolean-true producers predate the registry contract and
    // never clear themselves. Clear on their behalf only when identity is
    // unchanged; versioned producers must have cleared via registry.update.
    if (isLegacyBooleanSuccess) {
      const mutable = registry as {
        update?: (handle: { ownerId: string; token: string }, patch: { dirty: boolean }) => boolean;
      };
      if (typeof mutable.update === "function") {
        mutable.update(
          { ownerId: target.ownerId, token: target.registrationToken },
          { dirty: false },
        );
      }
    }

    const latest = registry.get(target.ownerId);
    if (!latest || !sameIdentity(latest, target)) {
      return blocked(
        "DRAFT_CHANGED_DURING_ACTION",
        `“${target.entityKey}”在处理期间发生变化，已保留新修改。`,
      );
    }
    if (latest.dirty)
      return blocked(
        "DRAFT_STILL_DIRTY",
        `“${target.entityKey}”仍有未保存内容。`,
      );
    completedOwnerIds.push(target.ownerId);
  }

  const remaining = registry.getDirty();
  if (remaining.length > 0) {
    return blocked(
      "NEW_DRAFT_PENDING",
      `仍有未处理内容：${remaining.map((owner) => owner.entityKey).join("、")}。`,
    );
  }
  return { allowed: true, completedOwnerIds };
}

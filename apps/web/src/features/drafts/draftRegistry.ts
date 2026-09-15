/**
 * Synchronous, observable draft registry.
 *
 * Contract (per reliability-closure spec R01):
 * - ownerId: logical identity of one editor region.
 * - entityKey: human-visible entity name (never used as technical id).
 * - registrationToken: one mount registration identity. Late updates/cleanups
 *   from an old handle must be ignored. Version comparison across tokens is
 *   meaningless; never use "different token but larger version overwrites".
 * - version: local editable-payload version, NOT a server revision_no.
 *   Bumped only when submittable content actually changes. Same token must
 *   never regress.
 * - dirty=false means "currently no unsaved edits", NOT unmounted. Mounted
 *   clean owners stay readable so the coordinator can verify receipts.
 *   Real unmount uses a separate unregister operation.
 * - Synchronous reads must immediately reflect edits from input events; do
 *   not rely on async React state/effect broadcasts.
 */

export type DraftSaveResult =
  | { status: "saved"; savedVersion: number }
  | { status: "blocked"; reason: string };

export type DraftDiscardResult =
  | { status: "discarded"; discardedVersion: number }
  | { status: "blocked"; reason: string };

export type MaybePromise<T> = T | Promise<T>;

/** Raw callback shape accepted from producers (new contract + legacy compat). */
export type DraftSaveCallback = (
  expectedVersion: number,
) => MaybePromise<unknown>;
export type DraftDiscardCallback = (
  expectedVersion: number,
) => MaybePromise<unknown>;

export interface DraftOwner {
  readonly ownerId: string;
  readonly entityKey: string;
  readonly registrationToken: string;
  readonly version: number;
  readonly dirty: boolean;
  readonly save?: DraftSaveCallback;
  readonly discard?: DraftDiscardCallback;
}

export interface DraftRegistryReader {
  get(ownerId: string): Readonly<DraftOwner> | undefined;
  getDirty(): readonly Readonly<DraftOwner>[];
}

export interface DraftHandle {
  readonly ownerId: string;
  readonly token: string;
}

export interface RegisterDraftInput {
  ownerId: string;
  entityKey: string;
  version: number;
  dirty: boolean;
  save?: DraftSaveCallback;
  discard?: DraftDiscardCallback;
}

export interface UpdateDraftInput {
  entityKey?: string;
  version?: number;
  dirty?: boolean;
  save?: DraftSaveCallback;
  discard?: DraftDiscardCallback;
}

function newToken(): string {
  try {
    const uuid = globalThis.crypto?.randomUUID?.();
    if (uuid) return uuid;
  } catch {
    /* fall through to Math.random fallback */
  }
  return `draft-${Date.now().toString(36)}-${Math.random().toString(16).slice(2)}`;
}

function freezeOwner(owner: DraftOwner): Readonly<DraftOwner> {
  return Object.freeze({ ...owner });
}

class DraftRegistry implements DraftRegistryReader {
  private owners = new Map<string, Readonly<DraftOwner>>();
  private listeners = new Set<() => void>();
  private snapshot: readonly Readonly<DraftOwner>[] = [];

  private emit(): void {
    this.snapshot = Object.freeze(Array.from(this.owners.values()).map(freezeOwner));
    for (const listener of Array.from(this.listeners)) {
      try {
        listener();
      } catch {
        /* listener errors must not corrupt the registry */
      }
    }
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Immutable snapshot; same reference while the set is unchanged. */
  getSnapshot = (): readonly Readonly<DraftOwner>[] => this.snapshot;

  getDirtySnapshot = (): readonly Readonly<DraftOwner>[] =>
    this.snapshot.filter((owner) => owner.dirty);

  get(ownerId: string): Readonly<DraftOwner> | undefined {
    return this.owners.get(ownerId);
  }

  getDirty(): readonly Readonly<DraftOwner>[] {
    return Array.from(this.owners.values()).filter((owner) => owner.dirty);
  }

  hasDirty(): boolean {
    for (const owner of this.owners.values()) {
      if (owner.dirty) return true;
    }
    return false;
  }

  register(input: RegisterDraftInput): DraftHandle {
    const token = newToken();
    const owner: Readonly<DraftOwner> = freezeOwner({
      ownerId: input.ownerId,
      entityKey: input.entityKey,
      registrationToken: token,
      version: Math.max(0, Math.floor(input.version)),
      dirty: input.dirty,
      save: input.save,
      discard: input.discard,
    });
    this.owners.set(input.ownerId, owner);
    this.emit();
    return { ownerId: input.ownerId, token };
  }

  /**
   * Update only with the current handle. Late updates from an old handle
   * (including old async cleanups) are ignored. Same-token version must
   * never regress; regressive updates are ignored.
   */
  update(handle: DraftHandle, patch: UpdateDraftInput): boolean {
    const current = this.owners.get(handle.ownerId);
    if (!current || current.registrationToken !== handle.token) return false;
    const nextVersion =
      patch.version === undefined ? current.version : Math.floor(patch.version);
    if (nextVersion < current.version) return false;
    const next: Readonly<DraftOwner> = freezeOwner({
      ownerId: current.ownerId,
      entityKey: patch.entityKey ?? current.entityKey,
      registrationToken: current.registrationToken,
      version: nextVersion,
      dirty: patch.dirty ?? current.dirty,
      save: patch.save !== undefined ? patch.save : current.save,
      discard: patch.discard !== undefined ? patch.discard : current.discard,
    });
    this.owners.set(handle.ownerId, next);
    this.emit();
    return true;
  }

  /** True unmount. Only the current handle may remove its registration. */
  unregister(handle: DraftHandle): boolean {
    const current = this.owners.get(handle.ownerId);
    if (!current || current.registrationToken !== handle.token) return false;
    this.owners.delete(handle.ownerId);
    this.emit();
    return true;
  }

  /**
   * Legacy bridge for `notifyDraftDirty(dirty, options)` event payloads.
   * dirty=false updates the entry to clean but keeps the registration;
   * it never deletes. Token mismatch on clean is ignored (old cleanup
   * must not clear a newer registration).
   */
  ingestLegacy(detail: {
    ownerId: string;
    entityKey: string;
    registrationToken: string;
    version: number;
    dirty: boolean;
    save?: DraftSaveCallback;
    discard?: DraftDiscardCallback;
  }): void {
    const current = this.owners.get(detail.ownerId);
    if (!detail.dirty) {
      if (!current || current.registrationToken === detail.registrationToken) {
        if (!current) return;
        this.owners.set(
          detail.ownerId,
          freezeOwner({ ...current, dirty: false }),
        );
        this.emit();
      }
      return;
    }
    if (!current) {
      this.owners.set(
        detail.ownerId,
        freezeOwner({
          ownerId: detail.ownerId,
          entityKey: detail.entityKey,
          registrationToken: detail.registrationToken,
          version: detail.version,
          dirty: true,
          save: detail.save,
          discard: detail.discard,
        }),
      );
      this.emit();
      return;
    }
    // Same token: accept (version monotonic). Different token: legacy rule
    // allowed overwrite only when incoming version is greater; the new
    // contract forbids cross-token version comparison, so a different token
    // replaces only when it carries callbacks for a genuinely new mount.
    // Here we preserve safety: different-token dirty events replace only if
    // they carry a save/discard callback (real new mount), never via a
    // bare version number.
    if (current.registrationToken === detail.registrationToken) {
      if (detail.version < current.version) return;
      this.owners.set(
        detail.ownerId,
        freezeOwner({
          ownerId: detail.ownerId,
          entityKey: detail.entityKey,
          registrationToken: detail.registrationToken,
          version: detail.version,
          dirty: true,
          save: detail.save ?? current.save,
          discard: detail.discard ?? current.discard,
        }),
      );
      this.emit();
      return;
    }
    if (detail.save !== undefined || detail.discard !== undefined) {
      this.owners.set(
        detail.ownerId,
        freezeOwner({
          ownerId: detail.ownerId,
          entityKey: detail.entityKey,
          registrationToken: detail.registrationToken,
          version: detail.version,
          dirty: true,
          save: detail.save,
          discard: detail.discard,
        }),
      );
      this.emit();
    }
  }

  /** Test-only reset. */
  clear(): void {
    if (this.owners.size === 0) return;
    this.owners.clear();
    this.emit();
  }
}

export const draftRegistry = new DraftRegistry();

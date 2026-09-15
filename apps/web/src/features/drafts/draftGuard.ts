import { draftRegistry, type DraftDiscardCallback, type DraftSaveCallback } from "./draftRegistry";

export type { DraftSaveResult } from "./draftRegistry";
export type DraftDiscardLegacy = boolean | void | Promise<boolean | void>;
export type { DraftDiscardCallback, DraftSaveCallback };

export interface DraftStateChange {
  ownerId: string;
  entityKey: string;
  registrationToken: string;
  version: number;
  dirty: boolean;
  href: string;
  save?: DraftSaveCallback;
  discard?: DraftDiscardCallback;
}

export const DRAFT_STATE_EVENT = "local-drama:draft-state-change";

type DraftRegistrationOptions = Partial<
  Pick<DraftStateChange, "ownerId" | "entityKey" | "registrationToken" | "version">
> &
  Pick<DraftStateChange, "save" | "discard">;

/**
 * Announces whether one entity draft has unsaved edits. Navigation ownership
 * stays in AppShell.
 *
 * The announcement is written synchronously into the shared draftRegistry so
 * coordinators observe input-event edits without waiting for React effects.
 * A window event is still dispatched for backward compatibility with mounted
 * AppShell listeners.
 */
export function notifyDraftDirty(
  dirty: boolean,
  options?: DraftRegistrationOptions,
  href?: string,
) {
  if (typeof window === "undefined") return;
  const resolvedHref =
    href ?? `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const detail: DraftStateChange = {
    ownerId: options?.ownerId ?? `legacy:${resolvedHref}`,
    entityKey: options?.entityKey ?? resolvedHref,
    registrationToken: options?.registrationToken ?? `legacy:${resolvedHref}`,
    version: options?.version ?? 0,
    dirty,
    href: resolvedHref,
    save: options?.save,
    discard: options?.discard,
  };
  // Synchronous registry write first; event listeners re-ingest idempotently.
  draftRegistry.ingestLegacy(detail);
  window.dispatchEvent(new CustomEvent<DraftStateChange>(DRAFT_STATE_EVENT, { detail }));
}

/** Removes persistence left by the retired tab-based workspace without touching entity drafts. */
export function retireWorkspaceTabPersistence() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem("local-drama:workspace-tabs:v1");
  window.localStorage.removeItem("local-drama:workspace-tabs:closed:v1");
}

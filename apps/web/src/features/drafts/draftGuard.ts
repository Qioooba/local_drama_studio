export type DraftSaveResult =
  | { status: "saved"; savedVersion: number }
  | { status: "blocked"; reason: string };

export interface DraftStateChange {
  ownerId: string;
  entityKey: string;
  registrationToken: string;
  version: number;
  dirty: boolean;
  href: string;
  save?: () => DraftSaveResult | boolean | void | Promise<DraftSaveResult | boolean | void>;
  discard?: () => boolean | void | Promise<boolean | void>;
}

export const DRAFT_STATE_EVENT = "local-drama:draft-state-change";

type DraftRegistrationOptions = Partial<Pick<DraftStateChange, "ownerId" | "entityKey" | "registrationToken" | "version">>
  & Pick<DraftStateChange, "save" | "discard">;

/** Announces whether one entity draft has unsaved edits. Navigation ownership stays in AppShell. */
export function notifyDraftDirty(dirty: boolean, options?: DraftRegistrationOptions, href?: string) {
  if (typeof window === "undefined") return;
  const resolvedHref = href ?? `${window.location.pathname}${window.location.search}${window.location.hash}`;
  window.dispatchEvent(new CustomEvent<DraftStateChange>(DRAFT_STATE_EVENT, {
    detail: {
      ownerId: options?.ownerId ?? `legacy:${resolvedHref}`,
      entityKey: options?.entityKey ?? resolvedHref,
      registrationToken: options?.registrationToken ?? `legacy:${resolvedHref}`,
      version: options?.version ?? 0,
      dirty,
      href: resolvedHref,
      save: options?.save,
      discard: options?.discard,
    },
  }));
}

/** Removes persistence left by the retired tab-based workspace without touching entity drafts. */
export function retireWorkspaceTabPersistence() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem("local-drama:workspace-tabs:v1");
  window.localStorage.removeItem("local-drama:workspace-tabs:closed:v1");
}

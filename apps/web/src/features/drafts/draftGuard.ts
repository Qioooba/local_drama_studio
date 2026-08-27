export interface DraftStateChange {
  dirty: boolean;
  href: string;
  save?: () => boolean | void | Promise<boolean | void>;
  discard?: () => boolean | void | Promise<boolean | void>;
}

export const DRAFT_STATE_EVENT = "local-drama:draft-state-change";

/** Announces whether the current entity draft has unsaved edits. Navigation ownership stays in AppShell. */
export function notifyDraftDirty(dirty: boolean, options?: Omit<DraftStateChange, "dirty" | "href">, href?: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<DraftStateChange>(DRAFT_STATE_EVENT, {
    detail: { dirty, href: href ?? `${window.location.pathname}${window.location.search}${window.location.hash}`, ...options },
  }));
}

/** Removes persistence left by the retired tab-based workspace without touching entity drafts. */
export function retireWorkspaceTabPersistence() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem("local-drama:workspace-tabs:v1");
  window.localStorage.removeItem("local-drama:workspace-tabs:closed:v1");
}

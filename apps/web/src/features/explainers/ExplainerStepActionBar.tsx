/**
 * The single sticky bottom action bar of the explainer workspace (design §B1.3,
 * §B1.2, and quality rule §E3 "exactly one primary action").
 *
 * The shell renders the bar **once**.  Pages never copy the 返回 / 保存 / 下一步
 * logic; they declare what their step needs through `useExplainerActionBar`.
 *
 * Registration contract:
 *
 * * The page's content object is kept in a ref and read at *click* time, so a
 *   button always calls the newest handler even if the shell has not re-rendered
 *   since the page's last render.  That removes the classic stale-closure trap
 *   without forcing every page to memoise its content object.
 * * The shell re-renders only when the *presentation signature* (labels, busy
 *   and disabled flags, summary) changes, which is what keeps this loop-free for
 *   pages that build a fresh object literal on every render.
 * * A page that never registers anything is fine: the bar then shows `上一步`
 *   plus the real next step derived from the step model.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import {
  EXPLAINER_PAGE_LABELS,
  routes,
  type ExplainerPage,
} from "../../app/routeRegistry";
import { nextExplainerPage, previousExplainerPage, type ExplainerStepStatusMap } from "./ExplainerSteps";
import "./explainers.css";

export type ExplainerActionBarPrimary = {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  /** Short reason shown next to a disabled primary action (never a dead button). */
  disabledReason?: string | null;
  busy?: boolean;
};

export type ExplainerActionBarSave = {
  label?: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
};

export type ExplainerActionBarDefer = {
  label?: string;
  onClick: () => void;
};

/** What a page declares for the shared bar.  Everything is optional. */
export type ExplainerActionBarContent = {
  primary?: ExplainerActionBarPrimary | null;
  save?: ExplainerActionBarSave | null;
  summary?: string | null;
  defer?: ExplainerActionBarDefer | null;
};

/** Everything a page must know in order to register its bar content. */
export type ExplainerActionBarHandlers = {
  /** Register (or replace) this page's bar content.  Returns an unregister function. */
  register: (content: ExplainerActionBarContent) => () => void;
};

/* -------------------------------------------------------------------------- */
/* store                                                                      */
/* -------------------------------------------------------------------------- */

type Provider = { key: string; read: () => ExplainerActionBarContent | null };

type ActionBarStore = {
  providers: Map<string, Provider>;
  listeners: Set<() => void>;
  version: number;
  activeOrder: string[];
};

function createActionBarStore(): ActionBarStore {
  return { providers: new Map(), listeners: new Set(), version: 0, activeOrder: [] };
}

const ActionBarContext = createContext<ActionBarStore | null>(null);

function notify(store: ActionBarStore) {
  store.version += 1;
  for (const listener of Array.from(store.listeners)) {
    try {
      listener();
    } catch {
      /* a failing listener must not break registration */
    }
  }
}

/** Presentation signature: only these fields can change what the bar shows. */
export function explainerActionBarSignature(content: ExplainerActionBarContent | null | undefined): string {
  if (!content) return "none";
  const primary = content.primary
    ? `${content.primary.label}|${content.primary.disabled ? 1 : 0}|${content.primary.busy ? 1 : 0}|${content.primary.disabledReason ?? ""}`
    : "-";
  const save = content.save ? `${content.save.label ?? "保存草稿"}|${content.save.disabled ? 1 : 0}|${content.save.busy ? 1 : 0}` : "-";
  const defer = content.defer ? content.defer.label ?? "稍后处理" : "-";
  return [primary, save, content.summary ?? "", defer].join("~");
}

/**
 * Register this page's bottom-bar content for as long as the component is
 * mounted.  Safe to call with a fresh object on every render.
 */
export function useExplainerActionBar(content: ExplainerActionBarContent | null | undefined): void {
  const store = useContext(ActionBarContext);
  const key = useId();
  const contentRef = useRef<ExplainerActionBarContent | null>(content ?? null);
  contentRef.current = content ?? null;
  const signature = explainerActionBarSignature(content);

  useEffect(() => {
    if (!store) return;
    // The provider reads the ref, so handlers are resolved at click time.
    store.providers.set(key, { key, read: () => contentRef.current });
    if (!store.activeOrder.includes(key)) store.activeOrder.push(key);
    notify(store);
    return () => {
      store.providers.delete(key);
      store.activeOrder = store.activeOrder.filter((item) => item !== key);
      notify(store);
    };
  }, [store, key]);

  useEffect(() => {
    if (!store) return;
    notify(store);
  }, [store, signature]);
}

/** Provider mounted by the shell around both the page outlet and the bar. */
export function ExplainerActionBarProvider({ children }: { children: ReactNode }) {
  const storeRef = useRef<ActionBarStore | null>(null);
  if (!storeRef.current) storeRef.current = createActionBarStore();
  return <ActionBarContext.Provider value={storeRef.current}>{children}</ActionBarContext.Provider>;
}

/** Current bar content, resolved from the newest registered page. */
export function useExplainerActionBarContent(): ExplainerActionBarContent | null {
  const store = useContext(ActionBarContext);
  const subscribe = useCallback(
    (listener: () => void) => {
      if (!store) return () => {};
      store.listeners.add(listener);
      return () => {
        store.listeners.delete(listener);
      };
    },
    [store],
  );
  const getSnapshot = useCallback(() => store?.version ?? 0, [store]);
  useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return readActiveContent(store);
}

function readActiveContent(store: ActionBarStore | null): ExplainerActionBarContent | null {
  if (!store) return null;
  for (let index = store.activeOrder.length - 1; index >= 0; index -= 1) {
    const provider = store.providers.get(store.activeOrder[index]);
    if (!provider) continue;
    const content = provider.read();
    if (content) return content;
  }
  return null;
}

/* -------------------------------------------------------------------------- */
/* bar                                                                        */
/* -------------------------------------------------------------------------- */

export function ExplainerStepActionBar({
  projectId,
  activePage,
  statuses,
  summary,
}: {
  projectId: string;
  activePage: ExplainerPage | null;
  statuses: ExplainerStepStatusMap;
  /** Shell-computed save/draft summary; a page summary always wins. */
  summary?: string | null;
}) {
  const content = useExplainerActionBarContent();
  const previous = activePage ? previousExplainerPage(activePage) : null;
  const next = activePage ? nextExplainerPage(activePage) : null;
  const done = Object.values(statuses).filter((status) => status === "DONE").length;

  // A page that registered nothing still gets the correct next step, derived
  // from the step model — never a disabled placeholder.
  const fallbackHref = !content?.primary && next ? routes.explainerPage(projectId, next) : null;
  const fallbackLabel = next ? `下一步：${EXPLAINER_PAGE_LABELS[next]}` : null;
  const primaryLabel = content?.primary?.label ?? fallbackLabel;
  const primaryDisabled = Boolean(content?.primary?.disabled);
  const primaryDisabledReason = content?.primary?.disabledReason ?? null;
  const primaryBusy = Boolean(content?.primary?.busy);
  const summaryText = content?.summary ?? summary ?? (done > 0 ? `${done} / 6 步已完成` : null);

  return <footer className="explainer-action-bar" aria-label="当前步骤操作">
    <div className="explainer-action-bar__start">
      {previous
        ? <Link className="explainer-action-bar__button" to={routes.explainerPage(projectId, previous)}>
          上一步
        </Link>
        : <Link className="explainer-action-bar__button" to={routes.explainers()}>
          作品列表
        </Link>}
      {content?.save
        ? <button
          type="button"
          className="explainer-action-bar__button"
          onClick={content.save.onClick}
          disabled={Boolean(content.save.disabled) || Boolean(content.save.busy)}
          aria-busy={content.save.busy ? "true" : undefined}
        >
          {content.save.busy ? "正在保存…" : (content.save.label ?? "保存草稿")}
        </button>
        : null}
    </div>

    <p className="explainer-action-bar__summary" role="status" aria-live="polite">
      {summaryText ?? ""}
    </p>

    <div className="explainer-action-bar__end">
      {content?.defer
        ? <button
          type="button"
          className="explainer-action-bar__defer"
          onClick={content.defer.onClick}
        >
          {content.defer.label ?? "稍后处理"}
        </button>
        : null}
      {primaryLabel
        ? fallbackHref
          ? <Link className="explainer-action-bar__primary" to={fallbackHref}>{primaryLabel}</Link>
          : <button
            type="button"
            className="explainer-action-bar__primary"
            onClick={() => content?.primary?.onClick()}
            disabled={primaryDisabled || primaryBusy}
            aria-busy={primaryBusy ? "true" : undefined}
            title={primaryDisabled && primaryDisabledReason ? primaryDisabledReason : undefined}
          >
            {primaryLabel}
          </button>
        : null}
      {primaryDisabled && primaryDisabledReason
        ? <small className="explainer-action-bar__reason">{primaryDisabledReason}</small>
        : null}
    </div>
  </footer>;
}

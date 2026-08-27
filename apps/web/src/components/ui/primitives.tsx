import {
  createContext,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import "./primitives.css";

export type Tone = "neutral" | "info" | "running" | "attention" | "success" | "danger";

let overlayLockDepth = 0;
let overlayPreviousHtmlOverflow = "";
let overlayPreviousBodyOverflow = "";

function useOverlayScrollLock(open: boolean) {
  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    if (overlayLockDepth === 0) {
      overlayPreviousHtmlOverflow = document.documentElement.style.overflow;
      overlayPreviousBodyOverflow = document.body.style.overflow;
      document.documentElement.style.overflow = "hidden";
      document.body.style.overflow = "hidden";
    }
    overlayLockDepth += 1;
    return () => {
      overlayLockDepth = Math.max(0, overlayLockDepth - 1);
      if (overlayLockDepth === 0) {
        document.documentElement.style.overflow = overlayPreviousHtmlOverflow;
        document.body.style.overflow = overlayPreviousBodyOverflow;
      }
    };
  }, [open]);
}

export function StatusBadge({ children, tone = "neutral" }: { children?: ReactNode; tone?: Tone }) {
  return <span className={`ui-status-badge ui-status-badge--${tone}`}>{children ?? tone}</span>;
}

export function MediaThumb({
  src,
  alt,
  emptyLabel = "暂无缩略图",
  aspectRatio = "16 / 9",
  objectFit = "contain",
  className = "",
}: {
  src?: string | null;
  alt: string;
  emptyLabel?: string;
  aspectRatio?: CSSProperties["aspectRatio"];
  objectFit?: CSSProperties["objectFit"];
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const isApiMedia = Boolean(src && /\/(?:media-versions|episode-renders)\//i.test(src));
  const safeSrc =
    src &&
    !/\/content(?:[/?#]|$)/i.test(src) &&
    (!isApiMedia || /\/thumbnail(?:[/?#]|$)/i.test(src))
      ? src
      : null;
  if (!safeSrc || failed) {
    return (
      <div
        className={`ui-media-thumb ui-media-thumb--empty ${className}`.trim()}
        style={{ aspectRatio }}
        role="img"
        aria-label={`${alt}：${emptyLabel}`}
      >
        <span aria-hidden="true">▧</span>
        <small>{emptyLabel}</small>
      </div>
    );
  }
  return (
    <img
      className={`ui-media-thumb ${className}`.trim()}
      src={safeSrc}
      alt={alt}
      loading="lazy"
      decoding="async"
      style={{ aspectRatio, objectFit }}
      onError={() => setFailed(true)}
    />
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  const titleId = useId();
  return (
    <section className="ui-state ui-state--empty" aria-labelledby={titleId}>
      <h3 id={titleId}>{title}</h3>
      {description ? <p>{description}</p> : null}
      {action ? <div className="ui-state__action">{action}</div> : null}
    </section>
  );
}

export function ErrorState({
  title = "暂时无法加载",
  description,
  onRetry,
}: {
  title?: string;
  description: string;
  onRetry?: () => void;
}) {
  return (
    <section className="ui-state ui-state--error" role="alert">
      <h3>{title}</h3>
      <p>{description}</p>
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          重试
        </button>
      ) : null}
    </section>
  );
}

export function Skeleton({
  label = "正在加载",
  lines = 1,
}: {
  label?: string;
  lines?: number;
}) {
  return (
    <div className="ui-skeleton" role="status" aria-label={label}>
      {Array.from({ length: Math.max(1, lines) }, (_, index) => (
        <span key={index} aria-hidden="true" />
      ))}
    </div>
  );
}

export function InspectorSection({
  title,
  summary,
  children,
  defaultOpen = true,
}: {
  title: string;
  summary?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="ui-inspector-section" open={defaultOpen}>
      <summary>
        <span>{title}</span>
        {summary ? <small>{summary}</small> : null}
      </summary>
      <div className="ui-inspector-section__body">{children}</div>
    </details>
  );
}

export function PropertyRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="ui-property-row">
      <div className="ui-property-row__label">
        <span>{label}</span>
        {hint ? <small>{hint}</small> : null}
      </div>
      <div className="ui-property-row__control">{children}</div>
    </div>
  );
}

export function Tooltip({
  content,
  children,
}: {
  content: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <span className="ui-tooltip">
      <span className="ui-tooltip__anchor" aria-describedby={id}>
        {children}
      </span>
      <span className="ui-tooltip__bubble" id={id} role="tooltip">
        {content}
      </span>
    </span>
  );
}

export function ConceptGuide({
  title = "名词说明",
  items,
}: {
  title?: string;
  items: Array<{ term: string; description: string }>;
}) {
  return (
    <details className="ui-concept-guide">
      <summary>{title}</summary>
      <dl>
        {items.map((item) => (
          <div key={item.term}>
            <dt>{item.term}</dt>
            <dd>{item.description}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export function Dialog({
  open,
  title,
  children,
  onClose,
  footer,
  dirtyGuard = false,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  dirtyGuard?: boolean;
}) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  useOverlayScrollLock(open);

  const safeClose = useCallback(() => {
    if (dirtyGuard && !window.confirm("当前有未保存的改动，确定要放弃并关闭吗？")) {
      return;
    }
    onClose();
  }, [dirtyGuard, onClose]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const handleKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") safeClose();
      if (event.key === "Tab") {
        const focusable = [
          ...(dialogRef.current?.querySelectorAll<HTMLElement>(
            "button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])"
          ) ?? []),
        ];
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      previous?.focus();
    };
  }, [open, safeClose]);

  if (!open) return null;
  return createPortal(
    <div
      className="ui-dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && safeClose()}
    >
      <section
        ref={dialogRef}
        className="ui-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header>
          <h2 id={titleId}>{title}</h2>
          <button ref={closeRef} type="button" onClick={safeClose} aria-label="关闭">
            ×
          </button>
        </header>
        <div className="ui-dialog__body">{children}</div>
        {footer ? <footer>{footer}</footer> : null}
      </section>
    </div>,
    document.body,
  );
}

/** Accessible sliding Drawer component with focus management and ARIA dialog semantics. */
export function Drawer({
  open,
  title,
  placement = "right",
  onClose,
  children,
  footer,
  width = 380,
  dirtyGuard = false,
}: {
  open: boolean;
  title: string;
  placement?: "left" | "right";
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  width?: string | number;
  dirtyGuard?: boolean;
}) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  useOverlayScrollLock(open);

  const safeClose = useCallback(() => {
    if (dirtyGuard && !window.confirm("当前有未保存的改动，确定要放弃并关闭吗？")) {
      return;
    }
    onClose();
  }, [dirtyGuard, onClose]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();

    const handleKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        safeClose();
      }
      if (event.key === "Tab") {
        const focusable = [
          ...(drawerRef.current?.querySelectorAll<HTMLElement>(
            "button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])"
          ) ?? []),
        ];
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      previous?.focus();
    };
  }, [open, safeClose]);

  if (!open) return null;
  return createPortal(
    <div
      className="ui-drawer-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && safeClose()}
    >
      <aside
        ref={drawerRef}
        className={`ui-drawer ui-drawer--${placement}`}
        style={{ width: typeof width === "number" ? `${width}px` : width }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="ui-drawer__header">
          <h2 id={titleId}>{title}</h2>
          <button
            ref={closeRef}
            type="button"
            className="ui-drawer__close"
            onClick={safeClose}
            aria-label="关闭抽屉"
          >
            ×
          </button>
        </header>
        <div className="ui-drawer__body">{children}</div>
        {footer ? <footer className="ui-drawer__footer">{footer}</footer> : null}
      </aside>
    </div>,
    document.body,
  );
}

export type TabItem = {
  id: string;
  label: string;
  icon?: ReactNode;
  badge?: ReactNode;
  disabled?: boolean;
};

const TabsIdContext = createContext<string | null>(null);

/** Accessible Compound Tabs with keyboard arrow navigation. */
export function Tabs({
  items,
  selectedId,
  onChange,
  ariaLabel = "选项卡",
  children,
}: {
  items: TabItem[];
  selectedId: string;
  onChange: (id: string) => void;
  ariaLabel?: string;
  children?: ReactNode;
}) {
  const tabsId = useId().replace(/:/g, "");
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const enabled = items.filter((item) => !item.disabled);
    const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)')];
    const focusedIndex = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const currentIndex = focusedIndex >= 0 ? focusedIndex : enabled.findIndex((item) => item.id === selectedId);
    if (currentIndex === -1) return;

    const activate = (index: number) => {
      const target = enabled[index];
      onChange(target.id);
      buttons[index]?.focus();
    };

    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      activate((currentIndex + 1) % enabled.length);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      activate((currentIndex - 1 + enabled.length) % enabled.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      activate(0);
    } else if (event.key === "End") {
      event.preventDefault();
      activate(enabled.length - 1);
    }
  };

  return (
    <TabsIdContext.Provider value={tabsId}>
    <div className="ui-tabs">
      <div
        className="ui-tabs__list"
        role="tablist"
        aria-label={ariaLabel}
        onKeyDown={handleKeyDown}
      >
        {items.map((item) => {
          const isSelected = item.id === selectedId;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              id={`${tabsId}-tab-${item.id}`}
              aria-selected={isSelected}
              aria-controls={`${tabsId}-tabpanel-${item.id}`}
              tabIndex={isSelected ? 0 : -1}
              disabled={item.disabled}
              className={`ui-tab ${isSelected ? "is-active" : ""}`}
              onClick={() => onChange(item.id)}
            >
              {item.icon ? <span className="ui-tab__icon">{item.icon}</span> : null}
              <span>{item.label}</span>
              {item.badge ? <span className="ui-tab__badge">{item.badge}</span> : null}
            </button>
          );
        })}
      </div>
      {children}
    </div>
    </TabsIdContext.Provider>
  );
}

export function TabPanel({
  id,
  selectedId,
  children,
}: {
  id: string;
  selectedId: string;
  children: ReactNode;
}) {
  const tabsId = useContext(TabsIdContext);
  if (id !== selectedId) return null;
  const prefix = tabsId ?? "standalone-tabs";
  return (
    <div
      role="tabpanel"
      id={`${prefix}-tabpanel-${id}`}
      aria-labelledby={`${prefix}-tab-${id}`}
      tabIndex={0}
      className="ui-tabpanel"
    >
      {children}
    </div>
  );
}

/** Accessible Popover anchored relative to a trigger button. */
export function Popover({
  open,
  onClose,
  children,
  placement = "bottom",
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  placement?: "top" | "bottom" | "left" | "right";
}) {
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target as Node)) {
        onClose();
      }
    };
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      ref={popoverRef}
      className={`ui-popover ui-popover--${placement}`}
      role="dialog"
      aria-modal="false"
    >
      {children}
    </div>
  );
}

export function IconButton({
  icon,
  label,
  onClick,
  tone = "neutral",
  variant = "ghost",
  disabled = false,
  shortcut,
  active = false,
  className = "",
}: {
  icon: ReactNode;
  label: string;
  onClick?: () => void;
  tone?: "neutral" | "primary" | "danger";
  variant?: "ghost" | "solid" | "outline";
  disabled?: boolean;
  shortcut?: string;
  active?: boolean;
  className?: string;
}) {
  const title = shortcut ? `${label} (${shortcut})` : label;
  return (
    <button
      type="button"
      className={`ui-icon-button ui-icon-button--${tone} ui-icon-button--${variant} ${active ? "is-active" : ""} ${className}`.trim()}
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={title}
    >
      <span aria-hidden="true">{icon}</span>
    </button>
  );
}

export function Field({
  label,
  hint,
  error,
  required = false,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
}) {
  const id = useId();
  const fieldId = htmlFor || id;
  const hintId = `${fieldId}-hint`;
  const errorId = `${fieldId}-error`;

  return (
    <div className={`ui-field ${error ? "has-error" : ""}`}>
      <label htmlFor={fieldId} className="ui-field__label">
        <span>{label}</span>
        {required ? <span className="ui-field__required" aria-hidden="true">*</span> : null}
      </label>
      <div className="ui-field__control">{children}</div>
      {hint && !error ? <small id={hintId} className="ui-field__hint">{hint}</small> : null}
      {error ? <div id={errorId} className="ui-field__error" role="alert">{error}</div> : null}
    </div>
  );
}

/* Toast notification system */
export interface ToastItem {
  id: string;
  title?: string;
  message: string;
  tone?: "info" | "success" | "warning" | "error";
  durationMs?: number;
}

interface ToastContextValue {
  toasts: ToastItem[];
  showToast: (toast: Omit<ToastItem, "id">) => void;
  dismissToast: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback((toast: Omit<ToastItem, "id">) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const item: ToastItem = { ...toast, id };
    setToasts((prev) => [...prev, item]);

    const duration = toast.durationMs ?? 4000;
    if (duration > 0) {
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, duration);
    }
  }, []);

  return (
    <ToastContext.Provider value={{ toasts, showToast, dismissToast }}>
      {children}
      <aside className="ui-toast-container" aria-label="通知信息" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.tone === "error" ? "alert" : "status"}
            className={`ui-toast ui-toast--${t.tone || "info"}`}
          >
            <div className="ui-toast__content">
              {t.title ? <strong>{t.title}</strong> : null}
              <span>{t.message}</span>
            </div>
            <button
              type="button"
              className="ui-toast__close"
              onClick={() => dismissToast(t.id)}
              aria-label="关闭通知"
            >
              ×
            </button>
          </div>
        ))}
      </aside>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    return {
      toasts: [],
      showToast: () => {},
      dismissToast: () => {},
    };
  }
  return ctx;
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : false,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, [query]);
  return matches;
}

/** Standard responsive 3-pane workbench layout with Collapsible Nav & Inspector. */
export function ThreePaneLayout({
  navRail,
  mainStage,
  inspector,
  navOpen = true,
  inspectorOpen = true,
  onToggleNav,
  onToggleInspector,
  navTitle = "导航",
  inspectorTitle = "检查器",
}: {
  navRail: ReactNode;
  mainStage: ReactNode;
  inspector: ReactNode;
  navOpen?: boolean;
  inspectorOpen?: boolean;
  onToggleNav?: () => void;
  onToggleInspector?: () => void;
  navTitle?: string;
  inspectorTitle?: string;
}) {
  const compactNav = useMediaQuery("(max-width: 960px)");
  const compactInspector = useMediaQuery("(max-width: 1280px)");
  const desktopNavOpen = navOpen && !compactNav;
  const desktopInspectorOpen = inspectorOpen && !compactInspector;
  return (
    <div className={`ui-threepane ${desktopNavOpen ? "has-nav" : ""} ${desktopInspectorOpen ? "has-inspector" : ""}`}>
      {desktopNavOpen ? <aside className="ui-threepane__nav is-open" aria-label={navTitle}>{navRail}</aside> : null}

      <section className="ui-threepane__stage" role="region" aria-label="工作区主舞台">{mainStage}</section>

      {desktopInspectorOpen ? <aside className="ui-threepane__inspector is-open" aria-label={inspectorTitle}>{inspector}</aside> : null}

      {compactNav ? <Drawer open={navOpen} onClose={onToggleNav ?? (() => {})} title={navTitle} placement="left" width={320}>{navRail}</Drawer> : null}
      {compactInspector ? <Drawer open={inspectorOpen} onClose={onToggleInspector ?? (() => {})} title={inspectorTitle} width={380}>{inspector}</Drawer> : null}
    </div>
  );
}

/** Inspector drawer/sidebar panel container. */
export function Inspector({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose?: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const titleId = useId();
  return (
    <div className="ui-inspector" role="region" aria-labelledby={titleId}>
      <header className="ui-inspector__header">
        <h3 id={titleId}>{title}</h3>
        {onClose ? (
          <button type="button" onClick={onClose} aria-label="关闭检查器">
            ×
          </button>
        ) : null}
      </header>
      <div className="ui-inspector__content">{children}</div>
      {footer ? <footer className="ui-inspector__footer">{footer}</footer> : null}
    </div>
  );
}

/** Media stage container for previewing image / video / canvas outputs with aspect ratio. */
export function MediaStage({
  media,
  controls,
  overlay,
  aspectRatio = "16 / 9",
  emptyState,
}: {
  media?: ReactNode;
  controls?: ReactNode;
  overlay?: ReactNode;
  aspectRatio?: CSSProperties["aspectRatio"];
  emptyState?: ReactNode;
}) {
  return (
    <section className="ui-media-stage" aria-label="媒体画面">
      <div className="ui-media-stage__viewport" style={{ aspectRatio }}>
        {media || emptyState || (
          <div className="ui-media-stage__empty">
            <span>▧ 暂无画面</span>
          </div>
        )}
        {overlay ? <div className="ui-media-stage__overlay">{overlay}</div> : null}
      </div>
      {controls ? <div className="ui-media-stage__controls">{controls}</div> : null}
    </section>
  );
}

export type EntityRailItem = {
  id: string;
  title: string;
  subtitle?: string;
  status?: Tone;
  badge?: ReactNode;
  onClick?: () => void;
};

/** List of entity items on a vertical rail (shots, scenes, assets). */
export function EntityRail({
  title,
  items,
  selectedId,
  onSelect,
  headerAction,
  emptyText = "暂无条目",
}: {
  title: string;
  items: EntityRailItem[];
  selectedId?: string | null;
  onSelect: (id: string) => void;
  headerAction?: ReactNode;
  emptyText?: string;
}) {
  return (
    <nav className="ui-entity-rail" aria-label={title}>
      <header className="ui-entity-rail__header">
        <h4>{title}</h4>
        {headerAction}
      </header>
      <div className="ui-entity-rail__list" role="list">
        {items.length === 0 ? (
          <p className="ui-entity-rail__empty">{emptyText}</p>
        ) : (
          items.map((item) => {
            const isSelected = item.id === selectedId;
            return (
              <button
                key={item.id}
                type="button"
                className={`ui-entity-card ${isSelected ? "is-selected" : ""}`}
                onClick={() => {
                  item.onClick?.();
                  onSelect(item.id);
                }}
                aria-current={isSelected ? "true" : undefined}
              >
                <div className="ui-entity-card__info">
                  <strong>{item.title}</strong>
                  {item.subtitle ? <small>{item.subtitle}</small> : null}
                </div>
                {item.badge || (item.status ? <StatusBadge tone={item.status} /> : null)}
              </button>
            );
          })
        )}
      </div>
    </nav>
  );
}

export type CandidateItem = {
  id: string;
  src?: string | null;
  alt?: string;
  title?: string;
  status?: Tone;
  selected?: boolean;
};

/** Responsive horizontal candidate tray for selecting generated variants. */
export function CandidateTray({
  candidates,
  selectedId,
  onSelect,
  onApprove,
  onReject,
  aspectRatio = "9 / 16",
  emptyLabel = "暂无候选",
}: {
  candidates: CandidateItem[];
  selectedId?: string | null;
  onSelect: (id: string) => void;
  onApprove?: (id: string) => void;
  onReject?: (id: string) => void;
  aspectRatio?: CSSProperties["aspectRatio"];
  emptyLabel?: string;
}) {
  return (
    <section className="ui-candidate-tray" aria-label="候选版本">
      <div className="ui-candidate-tray__scroll">
        {candidates.length === 0 ? (
          <div className="ui-candidate-tray__empty">{emptyLabel}</div>
        ) : (
          candidates.map((item) => {
            const isSelected = item.id === selectedId;
            return (
              <div
                key={item.id}
                className={`ui-candidate-card ${isSelected ? "is-selected" : ""}`}
                onClick={() => onSelect(item.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(item.id);
                  }
                }}
                aria-pressed={isSelected}
              >
                <MediaThumb
                  src={item.src}
                  alt={item.alt || item.title || "候选"}
                  aspectRatio={aspectRatio}
                  objectFit="contain"
                />
                <div className="ui-candidate-card__meta">
                  <span>{item.title || item.id}</span>
                  {item.status ? <StatusBadge tone={item.status} /> : null}
                </div>
                {isSelected && (onApprove || onReject) ? (
                  <div className="ui-candidate-card__actions" onClick={(e) => e.stopPropagation()}>
                    {onApprove ? (
                      <button
                        type="button"
                        className="ui-btn-approve"
                        onClick={() => onApprove(item.id)}
                      >
                        选用
                      </button>
                    ) : null}
                    {onReject ? (
                      <button
                        type="button"
                        className="ui-btn-reject"
                        onClick={() => onReject(item.id)}
                      >
                        废弃
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}

/** Sticky command bar pinned at bottom of editor pages for primary actions and status summaries. */
export function StickyCommandBar({
  primaryActions,
  secondaryActions,
  statusSummary,
  dirty = false,
  onSave,
  onReset,
  isSaving = false,
}: {
  primaryActions?: ReactNode;
  secondaryActions?: ReactNode;
  statusSummary?: ReactNode;
  dirty?: boolean;
  onSave?: () => void;
  onReset?: () => void;
  isSaving?: boolean;
}) {
  return (
    <footer className={`ui-sticky-bar ${dirty ? "is-dirty" : ""}`} aria-label="操作指令栏">
      <div className="ui-sticky-bar__status">
        {dirty ? <StatusBadge tone="attention">未保存改动</StatusBadge> : null}
        {statusSummary}
      </div>
      <div className="ui-sticky-bar__actions">
        {secondaryActions}
        {dirty && onReset ? (
          <button type="button" className="secondary" onClick={onReset} disabled={isSaving}>
            放弃修改
          </button>
        ) : null}
        {onSave ? (
          <button
            type="button"
            className="primary"
            onClick={onSave}
            disabled={isSaving || !dirty}
          >
            {isSaving ? "正在保存…" : "保存 (Ctrl+S)"}
          </button>
        ) : null}
        {primaryActions}
      </div>
    </footer>
  );
}

export type ContextMenuItem = {
  id: string;
  label: string;
  disabledReason?: string;
  danger?: boolean;
  onSelect: () => void;
};

export function ContextMenu({
  open,
  x,
  y,
  items,
  onClose,
  label = "快捷菜单",
}: {
  open: boolean;
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
  label?: string;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    const dismiss = () => onClose();
    window.addEventListener("pointerdown", dismiss);
    return () => window.removeEventListener("pointerdown", dismiss);
  }, [open, onClose]);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const enabled = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
    const current = enabled.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape") onClose();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      enabled[(current + direction + enabled.length) % enabled.length]?.focus();
    }
  };
  if (!open) return null;
  return (
    <div
      ref={menuRef}
      className="ui-context-menu"
      role="menu"
      aria-label={label}
      style={{ left: x, top: y }}
      onKeyDown={onKeyDown}
      onPointerDown={(event) => event.stopPropagation()}
    >
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="menuitem"
          className={item.danger ? "is-danger" : ""}
          disabled={Boolean(item.disabledReason)}
          title={item.disabledReason}
          onClick={() => {
            item.onSelect();
            onClose();
          }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function ResizablePane({
  primary,
  secondary,
  initial = 320,
  min = 220,
  max = 640,
  label = "调整面板宽度",
}: {
  primary: ReactNode;
  secondary: ReactNode;
  initial?: number;
  min?: number;
  max?: number;
  label?: string;
}) {
  const clamp = (value: number) => Math.min(max, Math.max(min, value));
  const [size, setSize] = useState(() => clamp(initial));
  const start = useRef<{ x: number; size: number } | null>(null);
  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    start.current = { x: event.clientX, size };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (start.current) setSize(clamp(start.current.size + event.clientX - start.current.x));
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (
      event.key !== "ArrowLeft" &&
      event.key !== "ArrowRight" &&
      event.key !== "Home" &&
      event.key !== "End"
    )
      return;
    event.preventDefault();
    if (event.key === "Home") setSize(min);
    else if (event.key === "End") setSize(max);
    else setSize((value) => clamp(value + (event.key === "ArrowRight" ? 16 : -16)));
  };
  return (
    <div
      className="ui-resizable-pane"
      style={{ gridTemplateColumns: `${size}px 8px minmax(0, 1fr)` }}
    >
      <div className="ui-resizable-pane__primary">{primary}</div>
      <div
        className="ui-resizable-pane__handle"
        role="separator"
        aria-label={label}
        aria-orientation="vertical"
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={size}
        tabIndex={0}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={(event) => {
          start.current = null;
          event.currentTarget.releasePointerCapture?.(event.pointerId);
        }}
        onKeyDown={handleKeyDown}
      />
      <div className="ui-resizable-pane__secondary">{secondary}</div>
    </div>
  );
}

/** Accessible VirtualList for efficiently rendering large lists of 50+ items. */
export function VirtualList<T>({
  items,
  itemHeight = 44,
  height = 400,
  renderItem,
  overscan = 5,
  className = "",
  keyExtractor,
  ariaLabel = "虚拟列表",
}: {
  items: T[];
  itemHeight?: number;
  height?: number | string;
  renderItem: (item: T, index: number) => ReactNode;
  overscan?: number;
  className?: string;
  keyExtractor?: (item: T, index: number) => string;
  ariaLabel?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [containerHeight, setContainerHeight] = useState(
    typeof height === "number" ? height : 400,
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.height > 0) {
          setContainerHeight(entry.contentRect.height);
        }
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const onScroll = useCallback(() => {
    if (containerRef.current) {
      setScrollTop(containerRef.current.scrollTop);
    }
  }, []);

  const totalCount = items.length;
  const totalHeight = totalCount * itemHeight;

  const startIndex = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan);
  const visibleCount = Math.ceil(containerHeight / itemHeight) + 2 * overscan;
  const endIndex = Math.min(totalCount, startIndex + visibleCount);

  const visibleItems = useMemo(() => {
    const slice: Array<{ item: T; index: number; top: number }> = [];
    for (let i = startIndex; i < endIndex; i++) {
      slice.push({ item: items[i], index: i, top: i * itemHeight });
    }
    return slice;
  }, [items, startIndex, endIndex, itemHeight]);

  return (
    <div
      ref={containerRef}
      className={`ui-virtual-list ${className}`.trim()}
      style={{
        height: typeof height === "number" ? `${height}px` : height,
        overflowY: "auto",
        position: "relative",
      }}
      onScroll={onScroll}
      role="list"
      aria-label={ariaLabel}
      tabIndex={0}
    >
      <div style={{ height: `${totalHeight}px`, width: "100%", position: "relative" }}>
        {visibleItems.map(({ item, index, top }) => (
          <div
            key={keyExtractor ? keyExtractor(item, index) : index}
            role="listitem"
            style={{
              position: "absolute",
              top: `${top}px`,
              left: 0,
              right: 0,
              height: `${itemHeight}px`,
            }}
          >
            {renderItem(item, index)}
          </div>
        ))}
      </div>
    </div>
  );
}

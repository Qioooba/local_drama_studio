/**
 * Explainer workspace UI primitives shared by the six pages.
 *
 * These exist so every page can express the eight documented states
 * (loading / empty / no capability / running / partial / failed / stale /
 * success) without inventing its own wording or its own idea of what "done"
 * means.
 */

import type { ReactNode } from "react";
import "./explainers.css";

export type PageState =
  | { kind: "loading"; message?: string }
  | { kind: "empty"; title: string; body: string; action?: ReactNode }
  | { kind: "no_capability"; title: string; body: string; action?: ReactNode }
  | { kind: "running"; title: string; body: string; progress?: number | null }
  | { kind: "partial"; title: string; body: string; action?: ReactNode }
  | { kind: "failed"; title: string; body: string; action?: ReactNode }
  | { kind: "stale"; title: string; body: string; action?: ReactNode };

const STATE_TONE: Record<PageState["kind"], string> = {
  loading: "",
  empty: "",
  no_capability: "warn",
  running: "",
  partial: "warn",
  failed: "danger",
  stale: "warn",
};

export function StateNotice({ state }: { state: PageState | null }) {
  if (!state) return null;
  const tone = STATE_TONE[state.kind];
  const title = state.kind === "loading" ? (state.message ?? "正在载入…") : state.title;
  const body = state.kind === "loading" ? null : state.body;
  const action = state.kind === "running" || state.kind === "loading" ? undefined : state.action;
  const progress = state.kind === "running" ? state.progress : null;
  return (
    <div className={`explainer-state${tone ? ` ${tone}` : ""}`} role={state.kind === "failed" ? "alert" : "status"}>
      <strong>{title}</strong>
      {body ? <p className="muted" style={{ marginTop: 6 }}>{body}</p> : null}
      {state.kind === "running" && progress !== null && progress !== undefined ? (
        <div className="explainer-progress" aria-hidden="true"><span style={{ width: `${Math.round(Math.max(0, Math.min(1, progress)) * 100)}%` }} /></div>
      ) : null}
      {action ? <div className="explainer-actions" style={{ marginTop: 10 }}>{action}</div> : null}
    </div>
  );
}

export function Panel({ title, subtitle, actions, children }: { title: string; subtitle?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="explainer-panel">
      <div className="explainer-header" style={{ marginBottom: 8 }}>
        <div>
          <h2>{title}</h2>
          {subtitle ? <p className="muted">{subtitle}</p> : null}
        </div>
        {actions ? <div className="explainer-actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function SettingRow({ label, value }: { label: string; value: ReactNode }) {
  return <div className="explainer-setting-row"><span>{label}</span><strong>{value}</strong></div>;
}

export function InlineError({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="explainer-inline-error" role="alert">{message}</p>;
}

export function InlineOk({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="explainer-ok" role="status">{message}</p>;
}

/**
 * A media slot that never pretends to hold media.  Every explainer preview goes
 * through this so an ungenerated asset is visibly ungenerated.
 */
export function MediaPlaceholder({ label, detail }: { label: string; detail?: string }) {
  return (
    <div className="explainer-placeholder" role="img" aria-label={`${label}（尚未生成媒体）`}>
      <div>
        <strong>{label}</strong>
        {detail ? <div>{detail}</div> : null}
        <div>尚未生成媒体</div>
      </div>
    </div>
  );
}

export type ExplainerTabOption = { value: string; label: string };

export function OptionTabs({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: ExplainerTabOption[];
  value: string | null;
  onChange: (next: string) => void;
}) {
  return (
    <div className="explainer-actions" role="tablist" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={value === option.value}
          className={value === option.value ? "primary-action" : undefined}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** Authority labels: machine acceptance is never rendered as human review. */
export function AuthorityBadge({ kind }: { kind: "machine" | "human" | "publication" | "none" }) {
  if (kind === "machine") return <span className="badge blue">自动检查结果 · 未人工审阅</span>;
  if (kind === "human") return <span className="badge green">已人工确认</span>;
  if (kind === "publication") return <span className="badge green">已记录发布授权</span>;
  return <span className="badge">尚无审批记录</span>;
}

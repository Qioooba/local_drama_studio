import { useMemo } from "react";
import { ACCELERATION_LABELS, PRODUCTION_TIER_LABELS, optionLabel } from "../shared/optionLabels";

type OverrideField = {
  type?: string;
  label?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  step?: number;
  options?: unknown[];
  scopes?: string[];
  visible_if?: { field?: string; equals?: unknown };
  editable?: boolean;
  locked_reason?: string;
};

const OVERRIDE_OPTION_LABELS: Record<string, string> = {
  ...PRODUCTION_TIER_LABELS,
  ...ACCELERATION_LABELS,
};

function overrideOptionLabel(value: unknown) {
  return optionLabel(OVERRIDE_OPTION_LABELS, String(value), String(value));
}

function fieldValue(value: unknown, field: OverrideField) {
  if (value === undefined || value === null) return field.default ?? "";
  return value;
}

export function ProfileOverrideFields({
  schema,
  value,
  sources,
  scope,
  onChange,
  disabled = false,
}: {
  schema?: Record<string, unknown>;
  value: Record<string, unknown>;
  sources?: Record<string, string>;
  scope: string;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
}) {
  const fields = useMemo(() => {
    const raw = schema?.fields;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    return Object.entries(raw as Record<string, unknown>).filter(([, item]) => item && typeof item === "object" && !Array.isArray(item)) as Array<[string, OverrideField]>;
  }, [schema]);

  if (!fields.length) return <p className="muted">当前 Profile 没有声明可覆盖的运行参数。</p>;

  return (
    <div className="profile-override-fields">
      {fields.map(([key, field]) => {
        if (Array.isArray(field.scopes) && !field.scopes.includes(scope)) return null;
        if (field.visible_if && value[field.visible_if.field ?? ""] !== field.visible_if.equals) return null;
        const current = fieldValue(value[key], field);
        const source = sources?.[key];
        const label = field.label ?? key;
        const setValue = (next: unknown) => onChange({ ...value, [key]: next });
        const clearValue = () => {
          const next = { ...value };
          delete next[key];
          onChange(next);
        };
        return (
          <label key={key} className="profile-override-field">
            <span><strong>{label}</strong><small>{key}{source ? ` · ${source === "PREFERENCE" ? "当前覆盖" : "Profile 默认"}` : " · 继承 Profile 默认"}{field.editable === false ? " · 已锁定" : ""}</small></span>
            {field.type === "enum" ? <select value={String(current)} disabled={disabled || field.editable === false} onChange={(event) => setValue(event.target.value)}>{(field.options ?? []).map((option) => <option key={String(option)} value={String(option)}>{overrideOptionLabel(option)}</option>)}</select> : field.type === "boolean" ? <input type="checkbox" checked={Boolean(current)} disabled={disabled || field.editable === false} onChange={(event) => setValue(event.target.checked)} /> : <input type={field.type === "number" || field.type === "integer" ? "number" : "text"} value={String(current)} min={field.minimum} max={field.maximum} step={field.step} disabled={disabled || field.editable === false} onChange={(event) => { if (field.type === "integer") setValue(event.target.value === "" ? undefined : Number.parseInt(event.target.value, 10)); else if (field.type === "number") setValue(event.target.value === "" ? undefined : Number(event.target.value)); else setValue(event.target.value); }} />}
            {field.editable === false && field.locked_reason ? <small className="profile-override-locked">{field.locked_reason}</small> : null}
            {value[key] !== undefined ? <button type="button" className="secondary profile-override-clear" onClick={clearValue} disabled={disabled || field.editable === false}>恢复继承</button> : null}
          </label>
        );
      })}
    </div>
  );
}

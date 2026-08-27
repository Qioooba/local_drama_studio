import { useEffect, useMemo, useState } from "react";
import {
  instantiateWorkflowDefinition,
  listWorkflowDefinitions,
  type WorkflowDefinition,
  type WorkflowDefinitionField,
} from "../../generated/api";

const DEFAULT_CODES: Record<string, string> = {
  H3_T2V: "h3-t2v",
  H3_I2V: "h3-i2v",
  H3_REF2V: "h3-ref2v",
  SDXL_T2I: "sdxl-t2i-keyframe",
};

function initialValues(definition: WorkflowDefinition) {
  return Object.fromEntries(Object.entries(definition.fields).map(([name, field]) => [name, field.default]));
}

function numberValue(field: WorkflowDefinitionField, value: string) {
  return field.type === "integer" ? Number.parseInt(value, 10) : Number.parseFloat(value);
}

export function WorkflowDefinitionForm({ disabled, onCreated }: { disabled: boolean; onCreated: () => void }) {
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [definitionCode, setDefinitionCode] = useState("H3_I2V");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [code, setCode] = useState(DEFAULT_CODES.H3_I2V);
  const [title, setTitle] = useState("H3 首帧生视频");
  const [open, setOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const selected = definitions.find((item) => item.code === definitionCode) ?? null;
  const regularFields = useMemo(() => selected ? Object.entries(selected.fields).filter(([, field]) => !field.advanced) : [], [selected]);
  const advancedFields = useMemo(() => selected ? Object.entries(selected.fields).filter(([, field]) => field.advanced) : [], [selected]);

  useEffect(() => {
    void listWorkflowDefinitions()
      .then((result) => {
        setDefinitions(result.items);
        const preferred = result.items.find((item) => item.code === "H3_I2V") ?? result.items[0];
        if (preferred) {
          setDefinitionCode(preferred.code);
          setValues(initialValues(preferred));
          setCode(DEFAULT_CODES[preferred.code] ?? preferred.code.toLowerCase());
          setTitle(preferred.title);
        }
      })
      .catch((caught) => setError(`工作流定义读取失败：${String(caught)}`));
  }, []);

  const chooseDefinition = (nextCode: string) => {
    const next = definitions.find((item) => item.code === nextCode);
    setDefinitionCode(nextCode);
    setError(null);
    setMessage(null);
    if (!next) return;
    setValues(initialValues(next));
    setCode(DEFAULT_CODES[next.code] ?? next.code.toLowerCase());
    setTitle(next.title);
    setAdvancedOpen(false);
  };

  const updateValue = (name: string, field: WorkflowDefinitionField, raw: string | boolean) => {
    setValues((current) => ({
      ...current,
      [name]: typeof raw === "boolean" ? raw : field.type === "integer" || field.type === "number" ? numberValue(field, raw) : raw,
    }));
  };

  const renderField = ([name, field]: [string, WorkflowDefinitionField]) => {
    const value = values[name] ?? field.default;
    const inputId = `workflow-definition-${definitionCode}-${name}`;
    return <label key={name} htmlFor={inputId} className={field.type === "textarea" ? "workflow-create-form__wide" : undefined}>
      {field.label}
      {field.type === "enum" ? (
        <select id={inputId} value={String(value)} onChange={(event) => updateValue(name, field, event.target.value)}>
          {(field.options ?? []).map((option) => <option key={String(option.value)} value={String(option.value)}>{option.label}</option>)}
        </select>
      ) : field.type === "boolean" ? (
        <input id={inputId} type="checkbox" checked={Boolean(value)} onChange={(event) => updateValue(name, field, event.target.checked)} />
      ) : field.type === "textarea" ? (
        <textarea id={inputId} value={String(value)} onChange={(event) => updateValue(name, field, event.target.value)} />
      ) : (
        <input id={inputId} type={field.type === "integer" || field.type === "number" ? "number" : "text"} value={String(value)} min={field.minimum} max={field.maximum} step={field.step} onChange={(event) => updateValue(name, field, event.target.value)} />
      )}
      {field.help_text && <small>{field.help_text}</small>}
    </label>;
  };

  const submit = async () => {
    if (!selected || !code.trim() || !title.trim()) {
      setError("请选择定义并填写技术标识和显示标题。");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await instantiateWorkflowDefinition(selected.code, { code: code.trim(), title: title.trim(), parameters: values });
      setMessage(`${result.workflow_version.code} v${result.workflow_version.version_no} 已由 ${selected.title} 定义编译为候选版本。`);
      onCreated();
    } catch (caught) {
      setError(`候选工作流创建失败：${String(caught)}`);
    } finally {
      setBusy(false);
    }
  };

  return <div className="workflow-definition-authoring">
    <div className="workflow-create-toolbar">
      <button type="button" className="primary-action" onClick={() => setOpen((current) => !current)} disabled={disabled || definitions.length === 0}>
        {open ? "收起定义表单" : "从工作流定义创建候选"}
      </button>
      <small>页面只提交参数；Comfy 图、语义绑定和运行契约由服务端定义统一生成。</small>
    </div>
    {open && selected ? <form className="workflow-create-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label htmlFor="workflow-definition-selector">工作流类型
        <select id="workflow-definition-selector" value={definitionCode} onChange={(event) => chooseDefinition(event.target.value)}>
          {definitions.map((item) => <option key={item.code} value={item.code} disabled={!item.available}>{item.title}{item.available ? "" : "（当前环境不可用）"}</option>)}
        </select>
      </label>
      <label htmlFor="workflow-definition-title">显示标题<input id="workflow-definition-title" value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      <label htmlFor="workflow-definition-code">技术标识<input id="workflow-definition-code" value={code} onChange={(event) => setCode(event.target.value)} /></label>
      <div className="workflow-create-form__wide workflow-auto-facts">
        <strong>{selected.description}</strong>
        <span>能力：{selected.capability} · 输出：{selected.output_kind}</span>
        <span>运行时语义槽：{Object.keys(selected.semantic_bindings).join("、") || "无"}</span>
      </div>
      {regularFields.map(renderField)}
      {advancedFields.length > 0 && <details className="workflow-create-form__wide" open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}>
        <summary>高级图参数（{advancedFields.length} 项）</summary>
        <div className="workflow-create-form">{advancedFields.map(renderField)}</div>
      </details>}
      <div className="workflow-create-form__wide profile-editor-actions">
        <button type="submit" className="primary-action" disabled={disabled || busy || !selected.available}>{busy ? "服务端编译中…" : "创建不可变候选版本"}</button>
      </div>
    </form> : null}
    {message && <p className="review-success" role="status">{message}</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </div>;
}

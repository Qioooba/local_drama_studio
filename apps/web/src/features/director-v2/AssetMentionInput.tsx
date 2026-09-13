import { useMemo, useState, type CompositionEvent, type KeyboardEvent } from "react";

export type AssetMentionOption = {
  assetId: string;
  bindingId: string;
  stateId?: string | null;
  name: string;
  kind: string;
  status: string;
};

export type AssetMentionReference = AssetMentionOption & { displayName: string };

export function AssetMentionInput({ value, references, options, onChange, disabled = false }: {
  value: string;
  references: AssetMentionReference[];
  options: AssetMentionOption[];
  onChange: (value: string, references: AssetMentionReference[]) => void;
  disabled?: boolean;
}) {
  const [composing, setComposing] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const query = /(?:^|\s)@([^\s@]*)$/.exec(value)?.[1] ?? null;
  const suggestions = useMemo(() => query === null ? [] : options.filter((option) => (
    option.name.toLocaleLowerCase().includes(query.toLocaleLowerCase())
    || option.assetId.toLocaleLowerCase().includes(query.toLocaleLowerCase())
  )).slice(0, 8), [options, query]);

  const select = (option: AssetMentionOption) => {
    if (option.status !== "ACTIVE") return;
    const nextValue = value.replace(/@[^\s@]*$/, `@${option.name} `);
    const nextReference = { ...option, displayName: option.name };
    onChange(nextValue, [...references.filter((item) => item.bindingId !== option.bindingId), nextReference]);
    setActiveIndex(0);
  };
  const remove = (bindingId: string) => onChange(value, references.filter((item) => item.bindingId !== bindingId));
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (composing || !suggestions.length) return;
    if (event.key === "ArrowDown") { event.preventDefault(); setActiveIndex((index) => Math.min(index + 1, suggestions.length - 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setActiveIndex((index) => Math.max(index - 1, 0)); }
    else if (event.key === "Enter") { event.preventDefault(); select(suggestions[activeIndex]); }
    else if (event.key === "Escape") { event.preventDefault(); onChange(value.replace(/@[^\s@]*$/, ""), references); }
  };

  return <div className="asset-mention-input">
    <label htmlFor="shot-asset-mention">资产引用补充（可选）</label>
    <textarea id="shot-asset-mention" value={value} disabled={disabled} rows={2} placeholder="输入 @ 选择本镜已绑定资产；粘贴文字不会自动建立引用" onChange={(event) => onChange(event.target.value, references)} onCompositionStart={() => setComposing(true)} onCompositionEnd={(event: CompositionEvent<HTMLTextAreaElement>) => { setComposing(false); onChange(event.currentTarget.value, references); }} onKeyDown={onKeyDown} aria-autocomplete="list" aria-controls="shot-asset-mention-options" aria-expanded={suggestions.length > 0} />
    {suggestions.length > 0 ? <div id="shot-asset-mention-options" role="listbox" aria-label="可引用资产">
      {suggestions.map((option, index) => <button type="button" role="option" aria-selected={index === activeIndex} key={option.bindingId} disabled={option.status !== "ACTIVE"} onMouseDown={(event) => event.preventDefault()} onClick={() => select(option)}><strong>{option.name}</strong><span>{option.kind} · {option.assetId.slice(0, 8)}{option.stateId ? ` · 状态 ${option.stateId.slice(0, 8)}` : ""}{option.status !== "ACTIVE" ? " · 已归档" : ""}</span></button>)}
    </div> : null}
    {references.length > 0 ? <div className="asset-mention-input__chips" aria-label="已选择资产引用">{references.map((reference) => {
      const current = options.find((option) => option.bindingId === reference.bindingId);
      const expired = !current || current.status !== "ACTIVE" || current.assetId !== reference.assetId || current.stateId !== reference.stateId;
      return <span key={reference.bindingId} data-expired={expired || undefined}>@{reference.displayName} · {reference.assetId.slice(0, 8)}{expired ? " · 已过期" : ""}<button type="button" aria-label={`移除 ${reference.displayName} 引用`} onClick={() => remove(reference.bindingId)}>×</button></span>;
    })}</div> : null}
    <small>显示名只用于阅读；保存的是资产、镜头绑定和可选状态 ID。正式生成仍由服务端校验当前镜头归属、身份包与版本。</small>
  </div>;
}

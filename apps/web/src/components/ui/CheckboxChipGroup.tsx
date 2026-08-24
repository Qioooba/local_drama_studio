type Option = { value: string; label: string; description?: string };

type Props = {
  legend: string;
  options: readonly Option[];
  value: string[];
  onChange: (value: string[]) => void;
  max?: number;
  required?: boolean;
};

export function CheckboxChipGroup({ legend, options, value, onChange, max = options.length, required = false }: Props) {
  const selected = new Set(value);
  const toggle = (option: string) => onChange(selected.has(option) ? value.filter((item) => item !== option) : [...value, option].slice(0, max));
  return <fieldset className="checkbox-chip-group">
    <legend>{legend}{required ? " *" : ""}</legend>
    <div className="checkbox-chip-options">
      {options.map((option) => <label className={selected.has(option.value) ? "selected" : ""} key={option.value} title={option.description}>
        <input type="checkbox" checked={selected.has(option.value)} disabled={!selected.has(option.value) && value.length >= max} onChange={() => toggle(option.value)} />
        <span>{option.label}</span><small>{option.value}</small>
      </label>)}
    </div>
    <div className="action-row"><button type="button" className="secondary" onClick={() => onChange(options.slice(0, max).map((option) => option.value))}>全选</button><button type="button" className="secondary" disabled={value.length === 0} onClick={() => onChange([])}>清空</button><span className="muted">已选 {value.length}/{max}</span></div>
  </fieldset>;
}

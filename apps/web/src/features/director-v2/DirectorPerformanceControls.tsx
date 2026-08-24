const EMOTIONS = [
  "平静", "喜悦", "愤怒", "悲伤", "恐惧", "冷峻", "慌乱", "震惊", "狠厉", "温柔", "阴险", "决绝", "崩溃", "疲惫", "傲慢",
] as const;

const MICRO_EXPRESSIONS = [
  "挑眉", "瞳孔震颤", "微眯双眼", "闭目沉思", "含泪凝视", "翻白眼",
  "嘴角上扬（冷笑）", "咬牙切齿", "倒吸凉气", "嘴角下沉（委屈）", "张口结舌",
  "面部抽搐", "肌肉紧绷", "脸颊泛红", "脸色煞白",
] as const;

const EYE_LINES = [
  ["CAMERA", "直视镜头"],
  ["LOOK_LEFT", "看向画左"],
  ["LOOK_RIGHT", "看向画右"],
  ["LOOK_UP", "仰视"],
  ["LOOK_DOWN", "俯视"],
  ["AVERTED", "眼神闪躲"],
  ["EYES_CLOSED", "闭目"],
] as const;

const splitExpressions = (value: string | null): string[] => value
  ? value.split(/[；;,，、]/).map((item) => item.trim()).filter(Boolean)
  : [];

export function EmotionPicker({ value, intensity, disabled, onChange }: {
  value: string | null;
  intensity: number;
  disabled?: boolean;
  onChange: (next: { emotion?: string | null; intensity?: number }) => void;
}) {
  const isPreset = EMOTIONS.includes(value as typeof EMOTIONS[number]);
  return <fieldset className="intent-performance-control">
    <legend>情绪与强度</legend>
    <div className="intent-tag-grid" aria-label="常用情绪">
      {EMOTIONS.map((emotion) => <button
        key={emotion}
        type="button"
        aria-pressed={value === emotion}
        className={value === emotion ? "selected" : ""}
        disabled={disabled}
        onClick={() => onChange({ emotion })}
      >{emotion}</button>)}
    </div>
    <label className="intent-custom-field">自定义情绪
      <input
        value={isPreset ? "" : value ?? ""}
        disabled={disabled}
        onChange={(event) => onChange({ emotion: event.target.value || null })}
        placeholder="预设不够用时输入，例如：强装镇定"
      />
    </label>
    <label className="intent-range-field">表演强度 <output>{Math.round(intensity * 100)}%</output>
      <input
        aria-label="表演强度"
        type="range"
        min="0"
        max="1"
        step="0.05"
        value={intensity}
        disabled={disabled}
        onChange={(event) => onChange({ intensity: Number(event.target.value) })}
      />
    </label>
  </fieldset>;
}

export function MicroExpressionSelect({ value, disabled, onChange }: {
  value: string | null;
  disabled?: boolean;
  onChange: (value: string | null) => void;
}) {
  const selected = splitExpressions(value);
  const custom = selected.filter((item) => !MICRO_EXPRESSIONS.includes(item as typeof MICRO_EXPRESSIONS[number])).join("；");
  const toggle = (expression: string) => {
    const next = selected.includes(expression)
      ? selected.filter((item) => item !== expression)
      : [...selected, expression];
    onChange(next.length ? next.join("；") : null);
  };
  const updateCustom = (nextCustom: string) => {
    const presets = selected.filter((item) => MICRO_EXPRESSIONS.includes(item as typeof MICRO_EXPRESSIONS[number]));
    const next = [...presets, ...splitExpressions(nextCustom)];
    onChange(next.length ? next.join("；") : null);
  };
  return <fieldset className="intent-performance-control">
    <legend>面部微表情 <small>可多选</small></legend>
    <div className="intent-tag-grid micro" aria-label="常用微表情">
      {MICRO_EXPRESSIONS.map((expression) => <button
        key={expression}
        type="button"
        aria-pressed={selected.includes(expression)}
        className={selected.includes(expression) ? "selected" : ""}
        disabled={disabled}
        onClick={() => toggle(expression)}
      >{expression}</button>)}
    </div>
    <label className="intent-custom-field">自定义微表情
      <input
        value={custom}
        disabled={disabled}
        onChange={(event) => updateCustom(event.target.value)}
        placeholder="多个动作可用分号分隔"
      />
    </label>
  </fieldset>;
}

export function EyeLineControl({ value, disabled, onChange }: {
  value: string | null;
  disabled?: boolean;
  onChange: (value: string | null) => void;
}) {
  const isPreset = EYE_LINES.some(([code]) => code === value);
  return <fieldset className="intent-performance-control">
    <legend>视线方向</legend>
    <div className="intent-segmented" aria-label="视线方向">
      {EYE_LINES.map(([code, label]) => <button
        key={code}
        type="button"
        aria-pressed={value === code}
        className={value === code ? "selected" : ""}
        disabled={disabled}
        onClick={() => onChange(code)}
      ><span aria-hidden="true" className={`eye-line-symbol ${code.toLowerCase()}`} /><span>{label}</span></button>)}
    </div>
    <label className="intent-custom-field">自定义视线
      <input
        value={isPreset ? "" : value ?? ""}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value || null)}
        placeholder="例如：越过对手肩膀看向门外"
      />
    </label>
  </fieldset>;
}

import { useId, useMemo } from "react";
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
  description?: string;
  multiple_of?: number;
};

const OVERRIDE_OPTION_LABELS: Record<string, string> = {
  ...PRODUCTION_TIER_LABELS,
  ...ACCELERATION_LABELS,
  simple: "简单调度",
  normal: "标准调度",
  karras: "Karras 调度",
  exponential: "指数调度",
  sgm_uniform: "SGM 均匀调度",
  beta: "Beta 调度",
  ddim_uniform: "DDIM 均匀调度",
  res_multistep: "RES 多步采样",
  euler: "Euler 采样",
  euler_ancestral: "Euler Ancestral 采样",
  dpmpp_2m: "DPM++ 2M 采样",
  dpmpp_sde: "DPM++ SDE 采样",
};

const FIELD_HELP: Record<string, string> = {
  production_tier: "决定速度、显存占用和质量的整体档位。日常测试通常选“日常生成”，正式镜头再提高档位。",
  sigma_points: "H3 生成过程使用的采样点数量；数值越大通常越慢，不代表质量一定线性提高。",
  acceleration: "决定是否使用加速模型。启用后会缩短生成时间，但需要对应 LoRA 已安装且验证通过。",
  lora_strength: "只在启用 Turbo LoRA 时生效，用于控制加速 LoRA 对结果的影响强度。",
  native_audio: "决定视频生成阶段是否同时产生模型原生音轨；部分参考视频能力会强制开启。",
  take_count: "一次生成任务创建的独立候选数量，会直接影响耗时和显存任务排队数量。",
  width: "生成媒体的像素宽度；必须满足模型声明的步进约束。",
  height: "生成媒体的像素高度；必须满足模型声明的步进约束。",
  frame_count: "模型实际生成的总帧数，与帧率共同决定最终时长。",
  fps: "成片播放帧率；只改变播放速度，不会增加模型实际生成的画面帧。",
  steps: "扩散采样步数；更高通常更慢，具体质量收益取决于模型和采样器。",
  cfg: "提示词遵循强度；过高可能导致画面生硬、过饱和或细节失真。",
  sampler_name: "扩散采样算法。不同算法会改变速度、稳定性和画面质感。",
  scheduler: "控制采样过程中噪声变化的调度方式，需要与采样器和模型匹配。",
  denoise: "输入画面保留程度。数值越低越接近输入，越高允许模型改动越大。",
};

const SCOPE_LABELS: Record<string, string> = {
  PROJECT: "项目默认",
  SHOT: "镜头设置",
  RUN: "本次生成",
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
  context = "override",
}: {
  schema?: Record<string, unknown>;
  value: Record<string, unknown>;
  sources?: Record<string, string>;
  scope: string;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
  context?: "override" | "profile-default";
}) {
  const helperPrefix = useId();
  const fields = useMemo(() => {
    const raw = schema?.fields;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    return Object.entries(raw as Record<string, unknown>).filter(([, item]) => item && typeof item === "object" && !Array.isArray(item)) as Array<[string, OverrideField]>;
  }, [schema]);

  if (!fields.length) return <p className="muted">当前生成配置没有可调整的运行参数。</p>;

  return (
    <div className="profile-override-fields">
      {fields.map(([key, field]) => {
        if (Array.isArray(field.scopes) && !field.scopes.includes(scope)) return null;
        if (field.visible_if && value[field.visible_if.field ?? ""] !== field.visible_if.equals) return null;
        const current = fieldValue(value[key], field);
        const source = sources?.[key];
        const label = field.label ?? key;
        const helperId = `${helperPrefix}-profile-override-${key}-help`;
        const description = field.description ?? FIELD_HELP[key];
        const scopeText = Array.isArray(field.scopes) && field.scopes.length
          ? `可在${field.scopes.map((item) => SCOPE_LABELS[item] ?? item).join("、")}覆盖`
          : "仅由当前能力版本决定";
        const limits = [
          field.minimum !== undefined || field.maximum !== undefined ? `范围 ${field.minimum ?? "—"}–${field.maximum ?? "—"}` : "",
          field.step !== undefined ? `步进 ${field.step}` : "",
          field.multiple_of !== undefined ? `需为 ${field.multiple_of} 的倍数` : "",
        ].filter(Boolean).join(" · ");
        const setValue = (next: unknown) => onChange({ ...value, [key]: next });
        const clearValue = () => {
          const next = { ...value };
          delete next[key];
          onChange(next);
        };
        return (
          <label key={key} className="profile-override-field">
            <span><strong>{label}</strong><small>{key}{context === "profile-default" ? (value[key] === undefined ? " · 使用模型声明值" : " · 当前版本默认值") : source ? ` · ${source === "PREFERENCE" ? "当前覆盖" : "Profile 默认"}` : " · 继承 Profile 默认"}{field.editable === false ? " · 已锁定" : ""}</small></span>
            {field.type === "enum" ? <select aria-describedby={helperId} value={value[key] === undefined ? "" : String(current)} disabled={disabled || field.editable === false} onChange={(event) => event.target.value === "" ? clearValue() : setValue(event.target.value)}><option value="">{context === "profile-default" ? "使用模型声明值" : "使用模型默认"}</option>{(field.options ?? []).map((option) => <option key={String(option)} value={String(option)}>{overrideOptionLabel(option)}</option>)}</select> : field.type === "boolean" ? <input aria-describedby={helperId} type="checkbox" checked={Boolean(current)} disabled={disabled || field.editable === false} onChange={(event) => setValue(event.target.checked)} /> : <input aria-describedby={helperId} type={field.type === "number" || field.type === "integer" ? "number" : "text"} value={value[key] === undefined ? "" : String(current)} placeholder={field.default === undefined ? (context === "profile-default" ? "使用模型声明值" : "使用模型默认") : `默认 ${String(field.default)}`} min={field.minimum} max={field.maximum} step={field.step} disabled={disabled || field.editable === false} onChange={(event) => { if (event.target.value === "") clearValue(); else if (field.type === "integer") setValue(Number.parseInt(event.target.value, 10)); else if (field.type === "number") setValue(Number(event.target.value)); else setValue(event.target.value); }} />}
            <small id={helperId} className="muted">{description ?? "该字段由模型能力契约声明。"} {limits ? `${limits}。` : ""}{scopeText}。</small>
            {field.editable === false && field.locked_reason ? <small className="profile-override-locked">{field.locked_reason}</small> : null}
            {value[key] !== undefined ? <button type="button" className="secondary profile-override-clear" onClick={clearValue} disabled={disabled || field.editable === false}>{context === "profile-default" ? "使用模型声明值" : "恢复继承"}</button> : null}
          </label>
        );
      })}
    </div>
  );
}

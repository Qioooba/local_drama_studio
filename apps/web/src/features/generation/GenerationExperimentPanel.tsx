import { useEffect, useState } from "react";
import { cancelRemainingGenerationExperiment, confirmGenerationExperiment, createGenerationExperiment, estimateGenerationExperiment, expandGenerationExperiment, getGenerationExperiment, type GenerationExperiment, type GenerationExperimentEstimate } from "../../generated/api";

type AxisKey = "seed" | "motion" | "guidance_scale" | "steps" | "denoise_strength";
type ExperimentAxis = { key: AxisKey; values: string[] };

const AXIS_DEFINITIONS: Record<AxisKey, { label: string; valueType: "NUMBER" | "TEXT"; options: Array<{ value: string; label: string }> }> = {
  seed: { label: "随机种子", valueType: "NUMBER", options: ["42", "43", "44", "45"].map((value) => ({ value, label: `Seed ${value}` })) },
  motion: { label: "运动节奏", valueType: "TEXT", options: [{ value: "slow", label: "舒缓" }, { value: "medium", label: "均衡" }, { value: "fast", label: "快速" }] },
  guidance_scale: { label: "提示词遵循程度", valueType: "NUMBER", options: [{ value: "3.5", label: "自由 3.5" }, { value: "5", label: "自然 5" }, { value: "7.5", label: "准确 7.5" }, { value: "10", label: "严格 10" }] },
  steps: { label: "生成精细度", valueType: "NUMBER", options: [{ value: "12", label: "快速 12" }, { value: "20", label: "均衡 20" }, { value: "28", label: "精细 28" }, { value: "40", label: "高精 40" }] },
  denoise_strength: { label: "重绘幅度", valueType: "NUMBER", options: [{ value: "0.25", label: "轻微" }, { value: "0.5", label: "均衡" }, { value: "0.75", label: "明显" }, { value: "1", label: "完全重绘" }] },
};

const DEFAULT_VALUES: Record<AxisKey, string[]> = {
  seed: ["42", "43", "44"], motion: ["slow", "fast"], guidance_scale: ["5", "7.5"], steps: ["20", "28"], denoise_strength: ["0.5", "0.75"],
};

function structuredAxes(items: ExperimentAxis[]): Record<string, unknown[]> {
  const result: Record<string, unknown[]> = {};
  for (const item of items) {
    if (!item.values.length) throw new Error(`请至少选择一个“${AXIS_DEFINITIONS[item.key].label}”候选值`);
    result[item.key] = item.values.map((value) => AXIS_DEFINITIONS[item.key].valueType === "NUMBER" ? Number(value) : value);
  }
  if (!Object.keys(result).length) throw new Error("至少需要一个实验维度");
  return result;
}

const randomSeeds = () => Array.from({ length: 4 }, () => String(Math.floor(Math.random() * 900_000) + 100_000));

export function GenerationExperimentPanel({ intentId }: { intentId: string | null }) {
  const [title, setTitle] = useState("同图同词参数实验");
  const [axes, setAxes] = useState<ExperimentAxis[]>([{ key: "seed", values: DEFAULT_VALUES.seed }, { key: "motion", values: DEFAULT_VALUES.motion }]);
  const [confirmLarge, setConfirmLarge] = useState(false);
  const [experiment, setExperiment] = useState<GenerationExperiment | null>(null);
  const [estimate, setEstimate] = useState<GenerationExperimentEstimate | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setExperiment(null); setEstimate(null); setMessage(null); setError(null); }, [intentId]);
  const refresh = async (id: string) => {
    const result = await getGenerationExperiment(id); setExperiment(result.experiment);
    const estimateResult = await estimateGenerationExperiment(id); setEstimate(estimateResult.estimate);
  };
  const run = async (action: "create" | "confirm" | "expand" | "cancel") => {
    setBusy(action); setError(null); setMessage(null);
    try {
      if (action === "create") {
        if (!intentId) throw new Error("请先完成生成预检，锁定生成意图");
        const result = await createGenerationExperiment({ intent_id: intentId, title: title.trim(), axes: structuredAxes(axes), max_parallel: 1, resource_estimate: { cpu_seconds_per_cell: 1, disk_bytes_per_cell: 0, gpu_slots: 0 } });
        await refresh(result.experiment.id); setMessage("实验计划已保存为草稿；系统会串行执行，尚未创建任务。");
      } else if (!experiment) throw new Error("请先创建实验计划");
      else if (action === "confirm") {
        if (!estimate) throw new Error("请先读取实验估算");
        const result = await confirmGenerationExperiment(experiment.id, { plan_hash: estimate.plan_hash, limit: 20, confirm_large_matrix: confirmLarge });
        await refresh(experiment.id); setMessage(`实验已确认并创建 ${Number((result.result as Record<string, unknown>).expanded_count ?? 0)} 个组合；其余组合继续按需创建。`);
      } else if (action === "expand") {
        const result = await expandGenerationExperiment(experiment.id, 20); await refresh(experiment.id);
        setMessage(`本次新增 ${Number((result.result as Record<string, unknown>).expanded_count ?? 0)} 个实验组合。`);
      } else { await cancelRemainingGenerationExperiment(experiment.id); await refresh(experiment.id); setMessage("已取消未完成实验项；已完成项保持不变。"); }
    } catch (caught) { setError(String(caught)); } finally { setBusy(null); }
  };
  const patchAxis = (index: number, patch: Partial<ExperimentAxis>) => setAxes((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  const toggleValue = (index: number, value: string) => setAxes((items) => items.map((item, itemIndex) => itemIndex !== index ? item : ({ ...item, values: item.values.includes(value) ? item.values.filter((entry) => entry !== value) : [...item.values, value] })));

  return <section className="panel generation-experiment-panel" aria-labelledby="generation-experiment-title">
    <div className="panel-heading"><div><p className="eyebrow">参数矩阵安全</p><h3 id="generation-experiment-title">参数实验矩阵</h3></div><span className="status-pill neutral">系统限流 · 显式确认</span></div>
    <p className="muted">选择想比较的维度和值即可。系统固定单任务执行、每批最多展开 20 个组合，避免参数实验挤占生产队列。</p>
    <div className="field-grid"><label>实验标题<input value={title} onChange={(event) => setTitle(event.target.value)} disabled={Boolean(experiment)} placeholder="例如：比较运动节奏与精细度" /></label><div className="configuration-card"><small>执行策略</small><strong>单任务串行</strong><span>每批最多 20 个组合，由系统控制</span></div></div>
    <fieldset disabled={Boolean(experiment)}><legend>要比较什么？</legend><div className="structured-control-list">{axes.map((axis, index) => {
      const definition = AXIS_DEFINITIONS[axis.key];
      return <div className="structured-control-row experiment-axis-row" key={`${axis.key}-${index}`}><label>比较维度<select value={axis.key} onChange={(event) => { const key = event.target.value as AxisKey; patchAxis(index, { key, values: DEFAULT_VALUES[key] }); }}>{Object.entries(AXIS_DEFINITIONS).map(([key, item]) => <option key={key} value={key} disabled={axes.some((entry, itemIndex) => itemIndex !== index && entry.key === key)}>{item.label}</option>)}</select></label><fieldset className="experiment-value-picker"><legend>{definition.label}候选值</legend><div>{definition.options.map((option) => <label key={option.value} className={axis.values.includes(option.value) ? "selected" : ""}><input type="checkbox" checked={axis.values.includes(option.value)} onChange={() => toggleValue(index, option.value)} />{option.label}</label>)}</div>{axis.key === "seed" && <button type="button" className="secondary" onClick={() => patchAxis(index, { values: randomSeeds() })}>换一组随机 Seed</button>}</fieldset><button type="button" className="secondary" disabled={axes.length === 1} onClick={() => setAxes((items) => items.filter((_, itemIndex) => itemIndex !== index))}>删除维度</button></div>;
    })}</div><button type="button" className="secondary" disabled={axes.length >= Object.keys(AXIS_DEFINITIONS).length} onClick={() => { const key = (Object.keys(AXIS_DEFINITIONS) as AxisKey[]).find((candidate) => !axes.some((item) => item.key === candidate)); if (key) setAxes((items) => [...items, { key, values: DEFAULT_VALUES[key] }]); }}>添加比较维度</button></fieldset>
    {!intentId && <p id="generation-experiment-guidance" className="review-guidance">先回到“预检与确认”完成只读预检，锁定生成意图后才能保存实验计划。</p>}
    <div className="action-row"><button type="button" className="secondary" aria-describedby={!intentId ? "generation-experiment-guidance" : undefined} onClick={() => void run("create")} disabled={busy !== null || Boolean(experiment) || !intentId || !title.trim()}>{busy === "create" ? "保存中…" : "保存实验计划"}</button>{experiment && <><button type="button" className="secondary" onClick={() => void refresh(experiment.id)} disabled={busy !== null}>{busy ? "读取中…" : "刷新估算"}</button><button type="button" className="primary-action" onClick={() => void run("confirm")} disabled={busy !== null || experiment.status !== "DRAFT" || !estimate}>{busy === "confirm" ? "确认中…" : "确认并展开首批"}</button><button type="button" className="secondary" onClick={() => void run("expand")} disabled={busy !== null || experiment.status !== "CONFIRMED"}>{busy === "expand" ? "展开中…" : "继续展开下一批"}</button><button type="button" className="secondary" onClick={() => void run("cancel")} disabled={busy !== null || ["CANCELLED", "COMPLETED"].includes(experiment.status)}>{busy === "cancel" ? "取消中…" : "取消剩余"}</button></>}</div>
    {estimate && <div className="configuration-grid capacity-grid"><div className="configuration-card"><small>矩阵规模</small><strong>{estimate.cell_count}</strong><span>已展开 {estimate.expanded_count} · 剩余 {estimate.remaining_count}</span></div><div className="configuration-card"><small>预计 CPU</small><strong>{estimate.estimated_cpu_seconds}s</strong><span>只读估算</span></div><div className="configuration-card"><small>计划状态</small><strong>{estimate.status}</strong><span>计划已固定</span></div></div>}
    {estimate?.large_matrix_confirmation_required && <label className="check-row"><input type="checkbox" checked={confirmLarge} onChange={(event) => setConfirmLarge(event.target.checked)} />我确认组合数量较多，允许系统按每批 20 个逐批创建。</label>}
    {experiment && <p className="muted">当前实验：{experiment.status} · 已创建 {experiment.expanded_count} 个组合。</p>}
    {message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">实验操作失败：{error}</p>}
  </section>;
}

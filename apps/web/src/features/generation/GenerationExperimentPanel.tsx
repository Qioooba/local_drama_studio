import { useEffect, useState } from "react";
import { cancelRemainingGenerationExperiment, confirmGenerationExperiment, createGenerationExperiment, estimateGenerationExperiment, expandGenerationExperiment, getGenerationExperiment, type GenerationExperiment, type GenerationExperimentEstimate } from "../../generated/api";

function parseAxes(value: string): Record<string, unknown[]> {
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error("实验轴必须是有效 JSON 对象"); }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("实验轴必须是对象，例如 {\"seed\":[42,43]}");
  const axes = parsed as Record<string, unknown>;
  if (!Object.keys(axes).length || Object.values(axes).some((values) => !Array.isArray(values) || values.length === 0)) throw new Error("每个实验轴都必须是非空数组");
  return axes as Record<string, unknown[]>;
}

export function GenerationExperimentPanel({ intentId }: { intentId: string | null }) {
  const [title, setTitle] = useState("同图同词参数实验");
  const [axesText, setAxesText] = useState('{"seed":[42,43,44],"motion":["slow","fast"]}');
  const [maxParallel, setMaxParallel] = useState("1");
  const [expandLimit, setExpandLimit] = useState("20");
  const [confirmLarge, setConfirmLarge] = useState(false);
  const [experiment, setExperiment] = useState<GenerationExperiment | null>(null);
  const [estimate, setEstimate] = useState<GenerationExperimentEstimate | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setExperiment(null); setEstimate(null); setMessage(null); setError(null); }, [intentId]);
  const refresh = async (id: string) => {
    const result = await getGenerationExperiment(id);
    setExperiment(result.experiment);
    const estimateResult = await estimateGenerationExperiment(id);
    setEstimate(estimateResult.estimate);
  };
  const run = async (action: "create" | "confirm" | "expand" | "cancel") => {
    setBusy(action); setError(null); setMessage(null);
    try {
      if (action === "create") {
        if (!intentId) throw new Error("请先完成生成预检，锁定 GenerationIntent");
        const result = await createGenerationExperiment({ intent_id: intentId, title: title.trim(), axes: parseAxes(axesText), max_parallel: Number(maxParallel), resource_estimate: { cpu_seconds_per_cell: 1, disk_bytes_per_cell: 0, gpu_slots: 0 } });
        await refresh(result.experiment.id);
        setMessage("实验计划已保存为 DRAFT；尚未创建 Job。");
      } else if (!experiment) {
        throw new Error("请先创建实验计划");
      } else if (action === "confirm") {
        if (!estimate) throw new Error("请先读取实验估算");
        const result = await confirmGenerationExperiment(experiment.id, { plan_hash: estimate.plan_hash, limit: Number(expandLimit), confirm_large_matrix: confirmLarge });
        await refresh(experiment.id);
        setMessage(`实验已确认并展开 ${Number((result.result as Record<string, unknown>).expanded_count ?? 0)} 个 cell；其余项保持懒展开。`);
      } else if (action === "expand") {
        const result = await expandGenerationExperiment(experiment.id, Number(expandLimit));
        await refresh(experiment.id);
        setMessage(`本次新增 ${Number((result.result as Record<string, unknown>).expanded_count ?? 0)} 个已持久化 cell。`);
      } else {
        await cancelRemainingGenerationExperiment(experiment.id);
        await refresh(experiment.id);
        setMessage("已取消未完成实验项；已完成项保持不变。");
      }
    } catch (caught) { setError(String(caught)); }
    finally { setBusy(null); }
  };
  return <section className="panel generation-experiment-panel" aria-labelledby="generation-experiment-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-GEN-008 · MATRIX SAFETY</p><h3 id="generation-experiment-title">参数实验矩阵</h3></div><span className="status-pill neutral">懒展开 · 显式确认</span></div>
    <p className="muted">实验只绑定当前 GenerationIntent；先估算，再确认，按上限懒展开 Job。大矩阵超过 24 格需要额外确认，不会一次性占满队列。</p>
    <div className="field-grid"><label>实验标题<input value={title} onChange={(event) => setTitle(event.target.value)} disabled={Boolean(experiment)} /></label><label>最大并发<input type="number" min="1" max="64" value={maxParallel} onChange={(event) => setMaxParallel(event.target.value)} disabled={Boolean(experiment)} /></label><label>本次展开上限<input type="number" min="1" max="500" value={expandLimit} onChange={(event) => setExpandLimit(event.target.value)} /></label></div>
    <label>实验轴 JSON<textarea value={axesText} onChange={(event) => setAxesText(event.target.value)} disabled={Boolean(experiment)} spellCheck={false} /></label>
    <div className="action-row"><button type="button" className="secondary" onClick={() => void run("create")} disabled={busy !== null || Boolean(experiment)}>{busy === "create" ? "保存中…" : "保存实验计划"}</button>{experiment && <><button type="button" className="secondary" onClick={() => void refresh(experiment.id)} disabled={busy !== null}>{busy ? "读取中…" : "刷新估算"}</button><button type="button" className="primary-action" onClick={() => void run("confirm")} disabled={busy !== null || experiment.status !== "DRAFT" || !estimate}>{busy === "confirm" ? "确认中…" : "确认并展开首批"}</button><button type="button" className="secondary" onClick={() => void run("expand")} disabled={busy !== null || experiment.status !== "CONFIRMED"}>{busy === "expand" ? "展开中…" : "继续展开"}</button><button type="button" className="secondary" onClick={() => void run("cancel")} disabled={busy !== null || ["CANCELLED", "COMPLETED"].includes(experiment.status)}>{busy === "cancel" ? "取消中…" : "取消剩余"}</button></>}</div>
    {estimate && <div className="configuration-grid capacity-grid"><div className="configuration-card"><small>矩阵规模</small><strong>{estimate.cell_count}</strong><span>已展开 {estimate.expanded_count} · 剩余 {estimate.remaining_count}</span></div><div className="configuration-card"><small>预计 CPU</small><strong>{estimate.estimated_cpu_seconds}s</strong><span>只读估算</span></div><div className="configuration-card"><small>计划状态</small><strong>{estimate.status}</strong><span>hash {estimate.plan_hash.slice(0, 12)}…</span></div></div>}
    {estimate?.large_matrix_confirmation_required && <label className="check-row"><input type="checkbox" checked={confirmLarge} onChange={(event) => setConfirmLarge(event.target.checked)} />我确认该矩阵超过安全阈值，允许按上限逐批展开。</label>}
    {experiment && <p className="muted">实验 {experiment.id.slice(0, 12)}… · {experiment.status} · 已持久化 {experiment.expanded_count} 个 cell。</p>}
    {message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">实验操作失败：{error}</p>}
  </section>;
}

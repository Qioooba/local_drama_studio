import { useEffect, useMemo, useState } from "react";
import type { StoryAssetReference, StoryAssetState } from "./api";
import { CapabilityPicker, useCapabilityOptions } from "../model-config/CapabilityPicker";
import type { MultiViewSettings } from "./multiviewClient";
import { bindExpressionReference, getAssetExpressionHistory, isExpressionBatchActive, preflightAssetExpression, submitAssetExpression, type ExpressionBatch, type ExpressionKind, type ExpressionPreflight } from "./expressionClient";
import "./GenerateMultiViewPanel.css";
import { normalizeGenerationPercent } from "./progress";

const SLOTS: Array<{ kind: ExpressionKind; label: string }> = [
  { kind: "NEUTRAL", label: "平静" }, { kind: "HAPPY", label: "开心" }, { kind: "SAD", label: "悲伤" },
  { kind: "ANGRY", label: "愤怒" }, { kind: "SURPRISED", label: "惊讶" }, { kind: "FEARFUL", label: "恐惧" },
  { kind: "DISGUSTED", label: "厌恶" }, { kind: "DETERMINED", label: "坚定" }, { kind: "CRYING", label: "哭泣" },
];
const terminalFailures = new Set(["FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"]);
const thumbnailUrl = (id: string) => `/api/v1/media-versions/${encodeURIComponent(id)}/thumbnail?size=small&frame=poster`;

type Props = {
  projectId: string; assetId: string; assetStatus: string; states: StoryAssetState[]; baseReferences: StoryAssetReference[];
  initialBatches: ExpressionBatch[]; onReferencesChanged: () => Promise<void>;
};

export function GenerateExpressionPanel({ projectId, assetId, assetStatus, states, baseReferences, initialBatches, onReferencesChanged }: Props) {
  const [assetStateId, setAssetStateId] = useState("");
  const [profileVersionId, setProfileVersionId] = useState("");
  const profileOptions = useCapabilityOptions("IMAGE_EXPRESSION", { projectId });
  const [preflight, setPreflight] = useState<ExpressionPreflight | null>(null);
  const [batches, setBatches] = useState(initialBatches);
  const [busy, setBusy] = useState<"preflight" | "submit" | string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedSlots, setSelectedSlots] = useState<ExpressionKind[]>(SLOTS.map((slot) => slot.kind));
  const [consistency, setConsistency] = useState<MultiViewSettings["consistency_strength"]>("HIGH");
  const [background, setBackground] = useState<MultiViewSettings["background"]>("CLEAN");
  useEffect(() => { setBatches(initialBatches); }, [initialBatches]);
  useEffect(() => { setAssetStateId(""); setProfileVersionId(""); setPreflight(null); setError(null); }, [assetId]);
  const settings = useMemo<MultiViewSettings>(() => ({ asset_state_id: assetStateId || null, profile_version_id: profileVersionId || null, consistency_strength: consistency, background, requested_slots: selectedSlots }), [assetStateId, background, consistency, profileVersionId, selectedSlots]);
  useEffect(() => { setPreflight(null); }, [settings]);
  const active = batches.some(isExpressionBatchActive);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => { void getAssetExpressionHistory(assetId).then(setBatches).catch((reason) => setError(String(reason))); }, 2_000);
    return () => window.clearInterval(timer);
  }, [active, assetId]);

  const selectedReferences = assetStateId ? states.find((item) => item.id === assetStateId)?.references ?? [] : baseReferences;
  const allReferences = [...baseReferences, ...states.flatMap((item) => item.references)];
  const hasHero = selectedReferences.some((item) => item.reference_kind === "HERO") || Boolean(assetStateId && baseReferences.some((item) => item.reference_kind === "HERO"));
  const runPreflight = async () => {
    setBusy("preflight"); setError(null);
    try { setPreflight((await preflightAssetExpression(assetId, settings)).preflight); } catch (reason) { setError(String(reason)); } finally { setBusy(null); }
  };
  const submit = async () => {
    if (!preflight?.ready) return;
    setBusy("submit"); setError(null);
    try { await submitAssetExpression(assetId, settings, preflight.plan_hash); setBatches(await getAssetExpressionHistory(assetId)); setPreflight(null); } catch (reason) { setError(String(reason)); } finally { setBusy(null); }
  };
  const bind = async (batchId: string, kind: ExpressionKind, mediaVersionId: string) => {
    setBusy(`${batchId}:${kind}`); setError(null);
    try { await bindExpressionReference(assetId, assetStateId || null, kind, mediaVersionId); await onReferencesChanged(); } catch (reason) { setError(String(reason)); } finally { setBusy(null); }
  };

  return <section className="panel multiview-panel" aria-labelledby={`expression-title-${assetId}`}>
    <div className="panel-heading"><div><p className="eyebrow">角色表情</p><h4 id={`expression-title-${assetId}`}>生成表情九宫格</h4></div><span className={`status-pill ${hasHero ? "state-ready" : "state-blocked"}`}>{hasHero ? "主参考已就绪" : "缺少主参考"}</span></div>
    <p className="muted">九个表情会分别生成并保留各自历史，单项失败不会丢失其他成功结果。提交时会固定当前主参考、剧情状态和生成配置版本。</p>
    <div className="multiview-controls">
      <label>造型状态<select value={assetStateId} onChange={(event) => setAssetStateId(event.target.value)}><option value="">基础角色</option>{states.map((state) => <option value={state.id} key={state.id}>{state.label}</option>)}</select></label>
      <CapabilityPicker capability="IMAGE_EXPRESSION" label="表情生成方式" value={profileVersionId} onChange={setProfileVersionId} query={profileOptions} disabled={busy !== null} migrationBusinessSurface="assets" />
      <label>一致性<select value={consistency} onChange={(event) => setConsistency(event.target.value as typeof consistency)}><option value="HIGH">高</option><option value="MEDIUM">中</option><option value="LOW">低</option></select></label><label>背景<select value={background} onChange={(event) => setBackground(event.target.value as typeof background)}><option value="CLEAN">干净背景</option><option value="TRANSPARENT">透明背景</option><option value="ORIGINAL">保留原背景</option></select></label>
    </div>
    <fieldset><legend>本批次表情槽</legend><div className="action-row">{SLOTS.map((slot) => <label key={slot.kind}><input type="checkbox" checked={selectedSlots.includes(slot.kind)} onChange={(event) => setSelectedSlots((current) => event.target.checked ? [...current, slot.kind] : current.filter((item) => item !== slot.kind))} />{slot.label}</label>)}</div></fieldset>
    <div className="multiview-actions"><button className="secondary" type="button" disabled={busy !== null || assetStatus !== "ACTIVE" || selectedSlots.length === 0} onClick={() => void runPreflight()}>{busy === "preflight" ? "预检中…" : `预检 ${selectedSlots.length} 个表情槽`}</button><button className="primary-action" type="button" disabled={!preflight?.ready || busy !== null} onClick={() => void submit()}>{busy === "submit" ? "提交中…" : `确认生成 ${selectedSlots.length} 个独立槽`}</button></div>
    {preflight && <div className={`multiview-preflight ${preflight.ready ? "ready" : "blocked"}`} role="status"><strong>{preflight.ready ? `预检通过 · 将创建 ${preflight.would_create_jobs} 个任务` : `预检阻挡 · ${preflight.blockers.length} 项`}</strong>{preflight.blockers.map((item) => <div className="multiview-blocker" key={item.code}><span>{item.message}</span><code>{item.code}</code></div>)}</div>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div className="multiview-history" aria-label="表情九宫格生成历史">{batches.length === 0 ? <p className="empty-state">尚未提交表情九宫格生成。</p> : batches.map((batch, index) => <details key={batch.intent_id} open={index === 0}><summary><span>{index === 0 ? "最新批次" : `历史批次 ${batches.length - index}`}</span><span className={`status-pill state-${batch.status.toLowerCase()}`}>{batch.completed_count}/{batch.total_count} 完成{batch.failed_count ? ` · ${batch.failed_count} 失败` : ""}</span></summary><div className="multiview-slots">{SLOTS.map((slot) => {
      const item = batch.items.find((candidate) => candidate.slot_kind === slot.kind); const output = item?.outputs.at(-1); const key = `${batch.intent_id}:${slot.kind}`; const failed = Boolean(item?.job_state && terminalFailures.has(item.job_state));
      const alreadyBound = Boolean(output && allReferences.some((reference) => reference.reference_kind === "EXPRESSION_GRID" && reference.media_version_id === output.media_version_id && (reference.asset_state_id ?? "") === assetStateId));
      return <article className={`multiview-slot${failed ? " failed" : ""}`} key={slot.kind}><div className="multiview-slot-head"><strong>{slot.label}</strong><span>{slot.kind}</span></div>{output ? <img src={thumbnailUrl(output.media_version_id)} alt={`${slot.label}表情结果缩略图`} loading="eager" decoding="async" onError={(e) => { e.currentTarget.style.display = "none"; }} /> : <div className="multiview-slot-placeholder"><span>{normalizeGenerationPercent(item?.progress?.percent)}%</span></div>}<small>{item?.job_state ?? "等待任务"}</small>{item?.error && <p className="inline-error">{item.error.detail ?? item.error.code}</p>}{output && <button type="button" className="secondary" disabled={busy !== null || alreadyBound} onClick={() => void bind(batch.intent_id, slot.kind, output.media_version_id)}>{alreadyBound ? "已绑定" : busy === key ? "绑定中…" : "绑定为表情参考"}</button>}</article>;
    })}</div></details>)}</div>
  </section>;
}

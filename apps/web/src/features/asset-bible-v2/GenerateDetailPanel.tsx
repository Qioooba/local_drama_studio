import { useEffect, useMemo, useState } from "react";
import type { StoryAssetReference, StoryAssetState } from "./api";
import { CapabilityPicker, useCapabilityOptions } from "../model-config/CapabilityPicker";
import type { MultiViewSettings } from "./multiviewClient";
import { bindDetailReference, getAssetDetailHistory, isDetailBatchActive, preflightAssetDetail, submitAssetDetail, type DetailBatch, type DetailKind, type DetailPreflight } from "./detailClient";
import "./GenerateMultiViewPanel.css";

const SLOTS: Array<{ kind: DetailKind; label: string; reference: "CLOSEUP" | "DETAIL" }> = [
  { kind: "FACE_CLOSEUP", label: "面部近景", reference: "CLOSEUP" },
  { kind: "COSTUME_DETAIL", label: "服装细节", reference: "DETAIL" },
  { kind: "DISTINCTIVE_DETAIL", label: "识别性细节", reference: "DETAIL" },
];
const terminalFailures = new Set(["FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"]);
const thumbnailUrl = (id: string) => `/api/v1/media-versions/${encodeURIComponent(id)}/thumbnail?size=small&frame=poster`;
type Props = { projectId: string; assetId: string; assetStatus: string; states: StoryAssetState[]; baseReferences: StoryAssetReference[]; initialBatches: DetailBatch[]; onReferencesChanged: () => Promise<void> };

export function GenerateDetailPanel({ projectId, assetId, assetStatus, states, baseReferences, initialBatches, onReferencesChanged }: Props) {
  const [assetStateId, setAssetStateId] = useState("");
  const [profileVersionId, setProfileVersionId] = useState("");
  const profileOptions = useCapabilityOptions("IMAGE_EDIT", { projectId });
  const [preflight, setPreflight] = useState<DetailPreflight | null>(null);
  const [batches, setBatches] = useState(initialBatches);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedSlots, setSelectedSlots] = useState<DetailKind[]>(SLOTS.map((slot) => slot.kind));
  const [consistency, setConsistency] = useState<MultiViewSettings["consistency_strength"]>("HIGH");
  const [background, setBackground] = useState<MultiViewSettings["background"]>("CLEAN");
  useEffect(() => { setBatches(initialBatches); }, [initialBatches]);
  useEffect(() => { setAssetStateId(""); setProfileVersionId(""); setPreflight(null); setError(null); }, [assetId]);
  const settings = useMemo<MultiViewSettings>(() => ({ asset_state_id: assetStateId || null, profile_version_id: profileVersionId || null, consistency_strength: consistency, background, requested_slots: selectedSlots }), [assetStateId, background, consistency, profileVersionId, selectedSlots]);
  useEffect(() => { setPreflight(null); }, [settings]);
  const active = batches.some(isDetailBatchActive);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => { void getAssetDetailHistory(assetId).then(setBatches).catch((reason) => setError(String(reason))); }, 2_000);
    return () => window.clearInterval(timer);
  }, [active, assetId]);
  const selectedReferences = assetStateId ? states.find((item) => item.id === assetStateId)?.references ?? [] : baseReferences;
  const allReferences = [...baseReferences, ...states.flatMap((item) => item.references)];
  const hasHero = selectedReferences.some((item) => item.reference_kind === "HERO") || Boolean(assetStateId && baseReferences.some((item) => item.reference_kind === "HERO"));
  const runPreflight = async () => { setBusy("preflight"); setError(null); try { setPreflight((await preflightAssetDetail(assetId, settings)).preflight); } catch (reason) { setError(String(reason)); } finally { setBusy(null); } };
  const submit = async () => { if (!preflight?.ready) return; setBusy("submit"); setError(null); try { await submitAssetDetail(assetId, settings, preflight.plan_hash); setBatches(await getAssetDetailHistory(assetId)); setPreflight(null); } catch (reason) { setError(String(reason)); } finally { setBusy(null); } };
  const bind = async (batchId: string, kind: DetailKind, mediaVersionId: string) => { setBusy(`${batchId}:${kind}`); setError(null); try { await bindDetailReference(assetId, assetStateId || null, kind, mediaVersionId); await onReferencesChanged(); } catch (reason) { setError(String(reason)); } finally { setBusy(null); } };

  return <section className="panel multiview-panel" aria-labelledby={`detail-title-${assetId}`}>
    <div className="panel-heading"><div><p className="eyebrow">角色细节</p><h4 id={`detail-title-${assetId}`}>生成近景细节</h4></div><span className={`status-pill ${hasHero ? "state-ready" : "state-blocked"}`}>{hasHero ? "主参考已就绪" : "缺少主参考"}</span></div>
    <p className="muted">系统会以锁定的主参考分别生成面部、服装和识别性细节。每项都是独立任务并保留各自历史，单项失败不会丢失其他成功结果，也不会覆盖旧参考。</p>
    <div className="multiview-controls"><label>造型状态<select value={assetStateId} onChange={(event) => setAssetStateId(event.target.value)}><option value="">基础角色</option>{states.map((state) => <option value={state.id} key={state.id}>{state.label}</option>)}</select></label><CapabilityPicker capability="IMAGE_EDIT" label="近景细节生成方式" value={profileVersionId} onChange={setProfileVersionId} query={profileOptions} disabled={busy !== null} migrationBusinessSurface="assets" /><label>一致性<select value={consistency} onChange={(event) => setConsistency(event.target.value as typeof consistency)}><option value="HIGH">高</option><option value="MEDIUM">中</option><option value="LOW">低</option></select></label><label>背景<select value={background} onChange={(event) => setBackground(event.target.value as typeof background)}><option value="CLEAN">干净背景</option><option value="TRANSPARENT">透明背景</option><option value="ORIGINAL">保留原背景</option></select></label></div>
    <fieldset><legend>本批次细节槽</legend><div className="action-row">{SLOTS.map((slot) => <label key={slot.kind}><input type="checkbox" checked={selectedSlots.includes(slot.kind)} onChange={(event) => setSelectedSlots((current) => event.target.checked ? [...current, slot.kind] : current.filter((item) => item !== slot.kind))} />{slot.label}</label>)}</div></fieldset>
    <div className="multiview-actions"><button className="secondary" type="button" disabled={busy !== null || assetStatus !== "ACTIVE" || selectedSlots.length === 0} onClick={() => void runPreflight()}>{busy === "preflight" ? "预检中…" : `预检 ${selectedSlots.length} 个近景细节`}</button><button className="primary-action" type="button" disabled={!preflight?.ready || busy !== null} onClick={() => void submit()}>{busy === "submit" ? "提交中…" : `确认生成 ${selectedSlots.length} 个独立槽`}</button></div>
    {preflight && <div className={`multiview-preflight ${preflight.ready ? "ready" : "blocked"}`} role="status"><strong>{preflight.ready ? `预检通过 · 将创建 ${preflight.would_create_jobs} 个任务` : `预检阻挡 · ${preflight.blockers.length} 项`}</strong>{preflight.blockers.map((item) => <div className="multiview-blocker" key={item.code}><span>{item.message}</span><code>{item.code}</code></div>)}</div>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div className="multiview-history" aria-label="近景细节生成历史">{batches.length === 0 ? <p className="empty-state">尚未提交近景细节生成。</p> : batches.map((batch, index) => <details key={batch.intent_id} open={index === 0}><summary><span>{index === 0 ? "最新批次" : `历史批次 ${batches.length - index}`}</span><span className={`status-pill state-${batch.status.toLowerCase()}`}>{batch.completed_count}/{batch.total_count} 完成{batch.failed_count ? ` · ${batch.failed_count} 失败` : ""}</span></summary><div className="multiview-slots">{SLOTS.map((slot) => {
      const item = batch.items.find((candidate) => candidate.slot_kind === slot.kind); const output = item?.outputs.at(-1); const key = `${batch.intent_id}:${slot.kind}`; const failed = Boolean(item?.job_state && terminalFailures.has(item.job_state));
      const alreadyBound = Boolean(output && allReferences.some((reference) => reference.reference_kind === slot.reference && reference.media_version_id === output.media_version_id && (reference.asset_state_id ?? "") === assetStateId));
      return <article className={`multiview-slot${failed ? " failed" : ""}`} key={slot.kind}><div className="multiview-slot-head"><strong>{slot.label}</strong><span>{slot.reference}</span></div>{output ? <img src={thumbnailUrl(output.media_version_id)} alt={`${slot.label}结果缩略图`} loading="lazy" decoding="async" /> : <div className="multiview-slot-placeholder"><span>{Number(item?.progress?.percent ?? 0)}%</span></div>}<small>{item?.job_state ?? "等待任务"}</small>{item?.error && <p className="inline-error">{item.error.detail ?? item.error.code}</p>}{output && <button type="button" className="secondary" disabled={busy !== null || alreadyBound} onClick={() => void bind(batch.intent_id, slot.kind, output.media_version_id)}>{alreadyBound ? "已绑定" : busy === key ? "绑定中…" : `绑定为 ${slot.reference}`}</button>}</article>;
    })}</div></details>)}</div>
  </section>;
}

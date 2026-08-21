import { useEffect, useMemo, useState } from "react";
import { listProfiles, type Profile } from "../../generated/api";
import type { StoryAssetReference, StoryAssetState } from "./api";
import type { MultiViewSettings } from "./multiviewClient";
import { bindExpressionReference, getAssetExpressionHistory, isExpressionBatchActive, preflightAssetExpression, submitAssetExpression, type ExpressionBatch, type ExpressionKind, type ExpressionPreflight } from "./expressionClient";
import "./GenerateMultiViewPanel.css";

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
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [preflight, setPreflight] = useState<ExpressionPreflight | null>(null);
  const [batches, setBatches] = useState(initialBatches);
  const [busy, setBusy] = useState<"preflight" | "submit" | string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setBatches(initialBatches); }, [initialBatches]);
  useEffect(() => { setAssetStateId(""); setProfileVersionId(""); setPreflight(null); setError(null); }, [assetId]);
  useEffect(() => {
    let cancelled = false;
    void listProfiles().then(({ items }) => {
      if (!cancelled) setProfiles(items.filter((item) => item.capability === "IMAGE_EXPRESSION" && item.status === "PUBLISHED"));
    }).catch(() => { if (!cancelled) setProfiles([]); });
    return () => { cancelled = true; };
  }, [projectId]);
  const settings = useMemo<MultiViewSettings>(() => ({ asset_state_id: assetStateId || null, profile_version_id: profileVersionId || null, consistency_strength: "HIGH", background: "CLEAN" }), [assetStateId, profileVersionId]);
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
    <div className="panel-heading"><div><p className="eyebrow">角色表情</p><h4 id={`expression-title-${assetId}`}>生成表情九宫格</h4></div><span className={`status-pill ${hasHero ? "state-ready" : "state-blocked"}`}>{hasHero ? "HERO 已就绪" : "缺少 HERO"}</span></div>
    <p className="muted">九个表情是独立 Variant 与 Job；单槽失败不会丢失成功结果。提交冻结当前 HERO、状态和 IMAGE_EXPRESSION Profile。</p>
    <div className="multiview-controls">
      <label>造型状态<select value={assetStateId} onChange={(event) => setAssetStateId(event.target.value)}><option value="">基础角色</option>{states.map((state) => <option value={state.id} key={state.id}>{state.label}</option>)}</select></label>
      <label>语义 Profile<select aria-label="表情生成 Profile" value={profileVersionId} onChange={(event) => setProfileVersionId(event.target.value)}><option value="">AUTO · 项目偏好解析</option>{profiles.map((profile) => <option value={profile.version_id} key={profile.version_id}>{profile.title} · v{profile.version_no ?? "?"}</option>)}</select></label>
    </div>
    <div className="multiview-actions"><button className="secondary" type="button" disabled={busy !== null || assetStatus !== "ACTIVE"} onClick={() => void runPreflight()}>{busy === "preflight" ? "预检中…" : "运行只读预检"}</button><button className="primary-action" type="button" disabled={!preflight?.ready || busy !== null} onClick={() => void submit()}>{busy === "submit" ? "提交中…" : "确认生成九个独立槽"}</button></div>
    {preflight && <div className={`multiview-preflight ${preflight.ready ? "ready" : "blocked"}`} role="status"><strong>{preflight.ready ? `预检通过 · 将创建 ${preflight.would_create_jobs} 个任务` : `预检阻挡 · ${preflight.blockers.length} 项`}</strong>{preflight.blockers.map((item) => <div className="multiview-blocker" key={item.code}><span>{item.message}</span><code>{item.code}</code></div>)}</div>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div className="multiview-history" aria-label="表情九宫格生成历史">{batches.length === 0 ? <p className="empty-state">尚未提交表情九宫格生成。</p> : batches.map((batch, index) => <details key={batch.intent_id} open={index === 0}><summary><span>{index === 0 ? "最新批次" : `历史批次 ${batches.length - index}`}</span><span className={`status-pill state-${batch.status.toLowerCase()}`}>{batch.completed_count}/{batch.total_count} 完成{batch.failed_count ? ` · ${batch.failed_count} 失败` : ""}</span></summary><div className="multiview-slots">{SLOTS.map((slot) => {
      const item = batch.items.find((candidate) => candidate.slot_kind === slot.kind); const output = item?.outputs.at(-1); const key = `${batch.intent_id}:${slot.kind}`; const failed = Boolean(item?.job_state && terminalFailures.has(item.job_state));
      const alreadyBound = Boolean(output && allReferences.some((reference) => reference.reference_kind === "EXPRESSION_GRID" && reference.media_version_id === output.media_version_id && (reference.asset_state_id ?? "") === assetStateId));
      return <article className={`multiview-slot${failed ? " failed" : ""}`} key={slot.kind}><div className="multiview-slot-head"><strong>{slot.label}</strong><span>{slot.kind}</span></div>{output ? <img src={thumbnailUrl(output.media_version_id)} alt={`${slot.label}表情结果缩略图`} width="240" height="180" loading="lazy" decoding="async" /> : <div className="multiview-slot-placeholder"><span>{Number(item?.progress?.percent ?? 0)}%</span></div>}<small>{item?.job_state ?? "等待任务"}</small>{item?.error && <p className="inline-error">{item.error.detail ?? item.error.code}</p>}{output && <button type="button" className="secondary" disabled={busy !== null || alreadyBound} onClick={() => void bind(batch.intent_id, slot.kind, output.media_version_id)}>{alreadyBound ? "已绑定" : busy === key ? "绑定中…" : "绑定为表情参考"}</button>}</article>;
    })}</div></details>)}</div>
  </section>;
}

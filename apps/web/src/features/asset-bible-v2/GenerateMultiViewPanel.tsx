import { useEffect, useMemo, useRef, useState } from "react";
import { retryJob } from "../../generated/api";
import { MediaThumb } from "../../components/ui";
import type { StoryAssetReference, StoryAssetState } from "./api";
import { CapabilityPicker, useCapabilityOptions } from "../model-config/CapabilityPicker";
import {
  bindMultiViewReference,
  collectMultiViewOutput,
  draftAssetMultiViewPrompts,
  getAssetMultiViewHistory,
  isMultiViewBatchActive,
  preflightAssetMultiView,
  submitAssetMultiView,
  type MultiViewBatch,
  type MultiViewKind,
  type MultiViewPromptBundle,
  type MultiViewPreflight,
  type MultiViewSettings,
} from "./multiviewClient";
import "./GenerateMultiViewPanel.css";
import { getDirectorRecipeBinding } from "../recipes-v2/api";
import { normalizeGenerationPercent } from "./progress";

const VIEWS: Array<{ kind: MultiViewKind; label: string; angle: string }> = [
  { kind: "FRONT", label: "正面", angle: "0°" },
  { kind: "LEFT", label: "左侧", angle: "−90°" },
  { kind: "RIGHT", label: "右侧", angle: "+90°" },
  { kind: "BACK", label: "背面", angle: "180°" },
  { kind: "TOP", label: "顶部", angle: "俯视" },
  { kind: "BOTTOM", label: "底部", angle: "仰视" },
];
const TERMINAL_FAILURES = new Set(["FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"]);

type Props = {
  projectId: string;
  assetId: string;
  assetKind: string;
  assetStatus: string;
  states: StoryAssetState[];
  baseReferences: StoryAssetReference[];
  initialBatches: MultiViewBatch[];
  onReferencesChanged: () => Promise<void>;
};

function percentOf(item: MultiViewBatch["items"][number] | undefined): number {
  if (!item) return 0;
  if (item.job_state === "SUCCEEDED") return 100;
  return normalizeGenerationPercent(item.progress?.percent);
}

function stateLabel(state: string | null | undefined): string {
  const labels: Record<string, string> = {
    QUEUED: "排队中", CLAIMED: "已领取", RUNNING: "生成中", SUCCEEDED: "已完成",
    FAILED: "失败", CANCELLED: "已取消", NEEDS_ATTENTION: "需要处理", ORPHANED: "执行中断",
  };
  return labels[state ?? ""] ?? state ?? "等待任务";
}

function thumbnailUrl(mediaVersionId: string): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=small&frame=poster`;
}

export function GenerateMultiViewPanel({ projectId, assetId, assetKind, assetStatus, states, baseReferences, initialBatches, onReferencesChanged }: Props) {
  const [assetStateId, setAssetStateId] = useState("");
  const [profileVersionId, setProfileVersionId] = useState("");
  const profileOptions = useCapabilityOptions("IMAGE_MULTI_VIEW", { projectId });
  const [consistency, setConsistency] = useState<MultiViewSettings["consistency_strength"]>("HIGH");
  const [background, setBackground] = useState<MultiViewSettings["background"]>("CLEAN");
  const [preflight, setPreflight] = useState<MultiViewPreflight | null>(null);
  const [promptBundle, setPromptBundle] = useState<MultiViewPromptBundle | null>(null);
  const [seedOffset, setSeedOffset] = useState(0);
  const [revisionGuidance, setRevisionGuidance] = useState("");
  const [batches, setBatches] = useState(initialBatches);
  const [busy, setBusy] = useState<"prompts" | "preflight" | "submit" | null>(null);
  const [bindingKey, setBindingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryingJobId, setRetryingJobId] = useState<string | null>(null);
  const collectingJobs = useRef(new Set<string>());
  const [batchBindReport, setBatchBindReport] = useState<{ batchId: string; succeeded: number; failed: Array<{ kind: MultiViewKind; reason: string }> } | null>(null);
  const [policyViews, setPolicyViews] = useState<MultiViewKind[]>(["FRONT", "LEFT", "RIGHT"]);
  const [regenerateExisting, setRegenerateExisting] = useState(false);
  const [autoBind, setAutoBind] = useState(true);
  const [oneClickActive, setOneClickActive] = useState(false);
  const pendingAutoBind = useRef(new Set<string>());
  const applyPreset = (views: MultiViewKind[]) => { setPolicyViews(views); setPromptBundle(null); setPreflight(null); };

  useEffect(() => { setBatches(initialBatches); }, [initialBatches]);
  useEffect(() => {
    pendingAutoBind.current.clear();
    setAssetStateId(""); setProfileVersionId(""); setPromptBundle(null); setSeedOffset(0); setPreflight(null); setBatches(initialBatches); setError(null); setBatchBindReport(null); setRegenerateExisting(false); setRevisionGuidance("");
  }, [assetId]); // initialBatches is intentionally synchronized by the effect above.

  useEffect(() => {
    let cancelled = false;
    void getDirectorRecipeBinding(projectId).then((binding) => {
      if (cancelled || !binding) return;
      const allowed = new Set(VIEWS.map((item) => item.kind));
      const configured = binding.recipe.asset_policy.character_required_refs.filter((item): item is MultiViewKind => allowed.has(item as MultiViewKind));
      if (configured.length) setPolicyViews(configured);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [projectId]);

  const selectedReferences = assetStateId
    ? states.find((state) => state.id === assetStateId)?.references ?? []
    : baseReferences;
  const allReferences = [...baseReferences, ...states.flatMap((state) => state.references)];
  const missingViews = useMemo(
    () => regenerateExisting ? policyViews : policyViews.filter((kind) => !selectedReferences.some((reference) => reference.reference_kind === kind)),
    [policyViews, selectedReferences, regenerateExisting],
  );
  const hasHero = selectedReferences.some((reference) => reference.reference_kind === "HERO")
    || (assetStateId !== "" && baseReferences.some((reference) => reference.reference_kind === "HERO"));

  const settings = useMemo<MultiViewSettings>(() => ({
    asset_state_id: assetStateId || null,
    profile_version_id: profileVersionId.trim() || null,
    consistency_strength: consistency,
    background,
    requested_slots: missingViews.length ? missingViews : policyViews,
    prompt_bundle: promptBundle,
    seed_offset: seedOffset,
  }), [assetStateId, profileVersionId, consistency, background, missingViews, policyViews, promptBundle, seedOffset]);

  useEffect(() => { setPreflight(null); }, [settings]);

  useEffect(() => { setPromptBundle(null); }, [assetStateId, consistency, background, policyViews]);

  const active = batches.some(isMultiViewBatchActive);
  const collectOutput = async (jobId: string) => {
    try {
      await collectMultiViewOutput(jobId);
      setBatches(await getAssetMultiViewHistory(assetId));
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : String(requestError)); }
  };
  useEffect(() => {
    for (const item of batches.flatMap((batch) => batch.items)) {
      if (item.job_id && item.job_state === "SUCCEEDED" && item.outputs.length === 0 && !collectingJobs.current.has(item.job_id)) {
        collectingJobs.current.add(item.job_id);
        void collectOutput(item.job_id);
      }
    }
  }, [batches]);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const next = await getAssetMultiViewHistory(assetId);
        if (!cancelled) setBatches(next);
      } catch (pollError) {
        if (!cancelled) setError(pollError instanceof Error ? pollError.message : String(pollError));
      }
    };
    const timer = window.setInterval(() => { void poll(); }, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [active, assetId]);

  const retryFailedView = async (jobId: string) => {
    setRetryingJobId(jobId); setError(null);
    try {
      await retryJob(jobId);
      setBatches(await getAssetMultiViewHistory(assetId));
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : String(requestError)); }
    finally { setRetryingJobId(null); }
  };

  const generatePrompts = async () => {
    setBusy("prompts"); setError(null); setPreflight(null);
    try {
      const result = await draftAssetMultiViewPrompts(assetId, {
        asset_state_id: assetStateId || null,
        consistency_strength: consistency,
        background,
        requested_slots: missingViews.length ? missingViews : policyViews,
        revision_guidance: revisionGuidance.trim(),
      });
      setPromptBundle(result.prompt_bundle);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : String(requestError)); }
    finally { setBusy(null); }
  };

  const runPreflight = async () => {
    if (!promptBundle) { setError("请先通过页面调用本机大模型生成并审核本批正反提示词。"); return; }
    setBusy("preflight"); setError(null);
    try { setPreflight((await preflightAssetMultiView(assetId, settings)).preflight); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : String(requestError)); }
    finally { setBusy(null); }
  };

  const submit = async () => {
    if (!preflight?.ready) return;
    setBusy("submit"); setError(null);
    try {
      const result = await submitAssetMultiView(assetId, settings, preflight.plan_hash);
      if (autoBind && result.intent.id) pendingAutoBind.current.add(result.intent.id);
      setBatches(await getAssetMultiViewHistory(assetId));
      setPreflight(null);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : String(requestError)); }
    finally { setBusy(null); }
  };

  const runOneClick = async () => {
    if (missingViews.length === 0 || assetStatus !== "ACTIVE" || busy !== null) return;
    setOneClickActive(true); setError(null); setPreflight(null);
    try {
      setBusy("prompts");
      const promptResult = await draftAssetMultiViewPrompts(assetId, {
        asset_state_id: assetStateId || null,
        consistency_strength: consistency,
        background,
        requested_slots: missingViews,
        revision_guidance: revisionGuidance.trim(),
      });
      const nextSettings: MultiViewSettings = {
        asset_state_id: assetStateId || null,
        profile_version_id: profileVersionId.trim() || null,
        consistency_strength: consistency,
        background,
        requested_slots: missingViews,
        prompt_bundle: promptResult.prompt_bundle,
        seed_offset: seedOffset,
      };
      setPromptBundle(promptResult.prompt_bundle);
      setBusy("preflight");
      const preflightResult = await preflightAssetMultiView(assetId, nextSettings);
      setPreflight(preflightResult.preflight);
      if (!preflightResult.preflight.ready) throw new Error(preflightResult.preflight.blockers.map((item) => item.message).join("；") || "预检未通过");
      setBusy("submit");
      const result = await submitAssetMultiView(assetId, nextSettings, preflightResult.preflight.plan_hash);
      if (autoBind && result.intent.id) pendingAutoBind.current.add(result.intent.id);
      setBatches(await getAssetMultiViewHistory(assetId));
      setPreflight(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    } finally {
      setBusy(null);
      setOneClickActive(false);
    }
  };

  const bind = async (batchId: string, kind: MultiViewKind, mediaVersionId: string) => {
    const key = `${batchId}:${kind}:${mediaVersionId}`;
    setBindingKey(key); setError(null);
    try { await bindMultiViewReference(assetId, assetStateId || null, kind, mediaVersionId); await onReferencesChanged(); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : String(requestError)); }
    finally { setBindingKey(null); }
  };

  const bindableOutputs = (batch: MultiViewBatch) => batch.items.flatMap((item) => {
    const output = item.outputs.at(-1);
    if (!output) return [];
    const alreadyBound = allReferences.some((reference) => reference.reference_kind === item.reference_kind && reference.media_version_id === output.media_version_id && (reference.asset_state_id ?? "") === assetStateId);
    return alreadyBound ? [] : [{ kind: item.reference_kind, mediaVersionId: output.media_version_id }];
  });

  const bindBatchOutputs = async (batch: MultiViewBatch) => {
    const outputs = bindableOutputs(batch);
    if (!outputs.length) return;
    setBindingKey(`batch:${batch.intent_id}`);
    setError(null);
    const failed: Array<{ kind: MultiViewKind; reason: string }> = [];
    let succeeded = 0;
    for (const output of outputs) {
      try {
        await bindMultiViewReference(assetId, assetStateId || null, output.kind, output.mediaVersionId);
        succeeded += 1;
      } catch (requestError) {
        failed.push({ kind: output.kind, reason: requestError instanceof Error ? requestError.message : String(requestError) });
      }
    }
    if (succeeded) await onReferencesChanged();
    setBatchBindReport({ batchId: batch.intent_id, succeeded, failed });
    setBindingKey(null);
  };

  useEffect(() => {
    for (const batch of batches) {
      if (!pendingAutoBind.current.has(batch.intent_id) || isMultiViewBatchActive(batch)) continue;
      pendingAutoBind.current.delete(batch.intent_id);
      if (batch.completed_count > 0) void bindBatchOutputs(batch);
    }
  }, [batches]);

  if (assetKind !== "CHARACTER") return null;

  return <section className="panel multiview-panel" aria-labelledby={`multiview-title-${assetId}`}>
    <div className="panel-heading multiview-heading">
      <div><p className="eyebrow">角色一致性</p><h4 id={`multiview-title-${assetId}`}>生成导演配方要求的角色视图</h4></div>
      <span className={`status-pill ${hasHero ? "state-ready" : "state-blocked"}`}>{hasHero ? "HERO 已就绪" : "缺少 HERO"}</span>
    </div>
    <p className="muted">默认只生成当前造型仍缺失的视图；每个槽仍是独立任务。成功后可一次回绑全部结果，失败项不会隐藏。</p>
    <label><input type="checkbox" checked={regenerateExisting} disabled={busy !== null} onChange={(event) => { setRegenerateExisting(event.target.checked); setPromptBundle(null); setPreflight(null); }} />重新生成所选视图（保留旧版本，生成后再选择采用）</label>
    <label className="multiview-revision">本批修订要求（可选）<textarea rows={3} maxLength={2000} value={revisionGuidance} disabled={busy !== null} placeholder="描述需要纠正的外观或朝向，以及应保持不变的特征" onChange={(event) => { setRevisionGuidance(event.target.value); setPromptBundle(null); setPreflight(null); }} /><small className="muted">要求会交给本机大模型，并随提示词冻结到本批生成计划。修改后需重新生成提示词和预检。</small></label>

    <div className="multiview-controls">
      <label>造型状态<select value={assetStateId} onChange={(event) => setAssetStateId(event.target.value)}><option value="">基础角色</option>{states.map((state) => <option key={state.id} value={state.id}>{state.label}</option>)}</select></label>
      <label>一致性<select value={consistency} onChange={(event) => setConsistency(event.target.value as typeof consistency)}><option value="HIGH">高</option><option value="MEDIUM">中</option><option value="LOW">低</option></select></label>
      <label>背景<select value={background} onChange={(event) => setBackground(event.target.value as typeof background)}><option value="CLEAN">干净背景</option><option value="TRANSPARENT">透明背景</option><option value="ORIGINAL">保留原背景</option></select></label>
    </div>
    <fieldset><legend>视图预设</legend><div className="action-row"><button type="button" className={policyViews.length === 3 && !policyViews.includes("BACK") ? "secondary selected" : "secondary"} onClick={() => applyPreset(["FRONT", "LEFT", "RIGHT"])}>三视图</button><button type="button" className={policyViews.length === 4 && policyViews.includes("BACK") ? "secondary selected" : "secondary"} onClick={() => applyPreset(["FRONT", "LEFT", "RIGHT", "BACK"])}>四视图</button><button type="button" className={policyViews.length === 6 ? "secondary selected" : "secondary"} onClick={() => applyPreset(["FRONT", "LEFT", "RIGHT", "BACK", "TOP", "BOTTOM"])}>六视图</button></div><small className="muted">预设会明确决定本批次槽位；仍可在下方逐项调整。</small></fieldset>
    <fieldset><legend>本批次需要的视图</legend><div className="action-row">{VIEWS.map((view) => <label key={view.kind}><input type="checkbox" checked={policyViews.includes(view.kind)} onChange={(event) => setPolicyViews((current) => event.target.checked ? [...new Set([...current, view.kind])] : current.filter((item) => item !== view.kind))} />{view.label}</label>)}</div><small className="muted">初始值来自当前项目绑定的导演配方；可为本批次显式覆盖。</small></fieldset>
    <details className="multiview-advanced">
      <summary>指定生成模型（可选）</summary>
      <CapabilityPicker
        capability="IMAGE_MULTI_VIEW"
        label="多视图生成方式"
        description="自动使用项目偏好；指定版本只覆盖本批次，并会冻结到每个独立任务。"
        value={profileVersionId}
        onChange={setProfileVersionId}
        query={profileOptions}
        migrationBusinessSurface="assets"
        disabled={busy !== null}
      />
    </details>

    <div className="multiview-actions">
      <button type="button" className="primary-action" disabled={busy !== null || assetStatus !== "ACTIVE" || missingViews.length === 0} onClick={() => void runOneClick()}>
        {oneClickActive && busy === "prompts" ? "AI 正在生成本视图提示词…" : oneClickActive && busy === "preflight" ? "正在预检视图任务…" : oneClickActive && busy === "submit" ? "正在提交视图任务…" : missingViews.length ? `一键生成并回绑 ${missingViews.length} 个缺失视图` : "所选视图已齐全"}
      </button>
      <label><input type="checkbox" checked={autoBind} disabled={busy !== null} onChange={(event) => setAutoBind(event.target.checked)} />成功后自动回绑新视图</label>
      <button type="button" className="secondary" disabled={busy !== null || missingViews.length === 0} onClick={() => { setSeedOffset((current) => current + 1009); setPreflight(null); }}>换一组可复现种子</button>
      <small className="muted">种子批次偏移：{seedOffset}（会冻结到任务，可严格重放）</small>
      {assetStatus !== "ACTIVE" && <small className="blocker-text">归档角色不能生成。</small>}
      <details className="multiview-manual">
        <summary>分步手动生成</summary>
        <div className="action-row">
          <button type="button" className="secondary" disabled={busy !== null || assetStatus !== "ACTIVE" || missingViews.length === 0} onClick={() => void generatePrompts()}>{busy === "prompts" ? "本机大模型生成中…" : missingViews.length ? `AI 生成本批 ${missingViews.length} 组正反提示词` : "所选视图已齐全"}</button>
          <button type="button" className="secondary" disabled={busy !== null || assetStatus !== "ACTIVE" || missingViews.length === 0 || !promptBundle} onClick={() => void runPreflight()}>{busy === "preflight" ? "预检中…" : missingViews.length ? `预检 ${missingViews.length} 个缺失视图` : "所选视图已齐全"}</button>
          <button type="button" className="primary-action" disabled={!preflight?.ready || busy !== null || assetStatus !== "ACTIVE"} onClick={() => void submit()}>{busy === "submit" ? "提交任务…" : (preflight?.would_create_jobs ?? missingViews.length) > 0 ? `确认生成 ${preflight?.would_create_jobs ?? missingViews.length} 个缺失视图` : "没有缺失视图"}</button>
        </div>
        <small className="muted">手动流程会保留提示词审核和预检确认；一键流程使用同一冻结计划。</small>
      </details>
    </div>

    {promptBundle && <section className="multiview-prompts" aria-label="本机大模型正反提示词审核">
      <div className="multiview-prompt-heading"><div><strong>本批提示词已由本机大模型生成</strong><small>{promptBundle.provider || "本机服务"} · {promptBundle.model || "当前模型"}</small></div><code>{promptBundle.content_hash?.slice(0, 12) ?? "已冻结"}…</code></div>
      {(missingViews.length ? missingViews : policyViews).map((kind) => {
        const item = promptBundle.items[kind];
        return item ? <details key={kind} open><summary>{kind} · 正向 / 反向</summary><dl><dt>正向提示词</dt><dd>{item.positive_prompt}</dd><dt>反向提示词</dt><dd>{item.negative_prompt}</dd></dl></details> : null;
      })}
    </section>}

    {preflight && <div className={`multiview-preflight ${preflight.ready ? "ready" : "blocked"}`} role="status">
      <strong>{preflight.ready ? `预检通过 · 将创建 ${preflight.would_create_jobs} 个任务` : `预检阻塞 · ${preflight.blockers.length} 项`}</strong>
      {preflight.hero && <small>HERO：{preflight.hero.media_version_id.slice(0, 12)}… · 输入角色 {String(preflight.profile_resolution.input_role ?? "未解析")}</small>}
      {preflight.blockers.map((blocker) => <div className="multiview-blocker" key={blocker.code}><span>{blocker.message}</span>{blocker.suggested_action && <small>{blocker.suggested_action}</small>}<code>{blocker.code}</code></div>)}
    </div>}
    {error && <p className="inline-error" role="alert">{error}</p>}

    <div className="multiview-history" aria-label="三视图生成历史">
      {batches.length === 0 ? <p className="empty-state">尚未提交三视图生成。</p> : batches.map((batch, batchIndex) => <details key={batch.intent_id} open={batchIndex === 0}>
        <summary><span>{batchIndex === 0 ? "最新批次" : `历史批次 ${batches.length - batchIndex}`}</span><span className={`status-pill state-${batch.status.toLowerCase().replaceAll("_", "-")}`}>{batch.completed_count}/{batch.total_count} 完成{batch.failed_count ? ` · ${batch.failed_count} 失败` : ""}</span><time dateTime={batch.created_at}>{new Date(batch.created_at).toLocaleString()}</time></summary>
        {bindableOutputs(batch).length > 0 && <button type="button" className="primary-action multiview-bind-all" disabled={bindingKey !== null} onClick={() => void bindBatchOutputs(batch)}>{bindingKey === `batch:${batch.intent_id}` ? "正在回绑…" : `一键回绑 ${bindableOutputs(batch).length} 个成功视图`}</button>}
        {batchBindReport?.batchId === batch.intent_id && <div className={batchBindReport.failed.length ? "multiview-preflight blocked" : "multiview-preflight ready"} role="status"><strong>回绑结果：成功 {batchBindReport.succeeded} · 失败 {batchBindReport.failed.length}</strong>{batchBindReport.failed.map((item) => <span key={item.kind}>{item.kind}：{item.reason}</span>)}</div>}
        <div className="multiview-slots">
          {VIEWS.filter((view) => batch.items.some((item) => item.reference_kind === view.kind)).map((view) => {
            const item = batch.items.find((candidate) => candidate.reference_kind === view.kind);
            const output = item?.outputs.at(-1);
            const failed = Boolean(item?.job_state && TERMINAL_FAILURES.has(item.job_state));
            const bindKey = output ? `${batch.intent_id}:${view.kind}:${output.media_version_id}` : "";
            const alreadyBound = Boolean(output && allReferences.some((reference) => reference.reference_kind === view.kind && reference.media_version_id === output.media_version_id && (reference.asset_state_id ?? "") === assetStateId));
            const percent = percentOf(item);
            return <article className={`multiview-slot${failed ? " failed" : item?.job_state === "SUCCEEDED" ? " succeeded" : ""}`} key={view.kind}>
              <div className="multiview-slot-head"><strong>{view.label}</strong><span>{view.kind} · {view.angle}</span></div>
              {output ? <MediaThumb src={thumbnailUrl(output.media_version_id)} alt={`${view.label}生成结果缩略图`} loading="eager" aspectRatio="3 / 4" /> : <div className="multiview-slot-placeholder" aria-hidden="true"><span>{percent}%</span></div>}
              <div className="multiview-progress"><progress value={percent} max="100" aria-label={`${view.label}生成进度`} /><span>{stateLabel(item?.job_state)}</span></div>
              {item?.progress?.phase && <small>{item.progress.phase === "WAITING_FOR_GPU" ? "等待显卡空闲，任务会自动继续" : String(item.progress.phase)}{item.progress.node ? ` · ${String(item.progress.node)}` : ""}</small>}
              {item?.error && <p className="inline-error" role="alert">{item.error.detail || item.error.code || "任务失败"}</p>}
              {item?.job_id && <a href={`/system/jobs?project=${encodeURIComponent(projectId)}&job=${encodeURIComponent(item.job_id)}`}>查看{view.label}任务与原图</a>}
              {item?.job_id && item.job_state === "SUCCEEDED" && !output && <button type="button" onClick={() => void collectOutput(item.job_id!)}>收取已完成视图</button>}
              {item?.job_id && ["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(item.job_state ?? "") && <button type="button" disabled={retryingJobId !== null} onClick={() => void retryFailedView(item.job_id!)}>{retryingJobId === item.job_id ? "正在重试…" : `重试${view.label}任务`}</button>}
              {output && <><code title={output.media_version_id}>{output.media_version_id.slice(0, 12)}…</code><button type="button" className="secondary" disabled={alreadyBound || bindingKey !== null} onClick={() => void bind(batch.intent_id, view.kind, output.media_version_id)}>{alreadyBound ? "已绑定此结果" : bindingKey === bindKey ? "绑定中…" : `绑定为 ${view.kind}`}</button></>}
            </article>;
          })}
        </div>
      </details>)}
    </div>
  </section>;
}

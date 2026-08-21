import { useEffect, useMemo, useState } from "react";
import { listProfiles, type Profile } from "../../generated/api";
import type { StoryAssetReference, StoryAssetState } from "./api";
import {
  bindMultiViewReference,
  getAssetMultiViewHistory,
  isMultiViewBatchActive,
  preflightAssetMultiView,
  submitAssetMultiView,
  type MultiViewBatch,
  type MultiViewKind,
  type MultiViewPreflight,
  type MultiViewSettings,
} from "./multiviewClient";
import "./GenerateMultiViewPanel.css";

const VIEWS: Array<{ kind: MultiViewKind; label: string; angle: string }> = [
  { kind: "FRONT", label: "正面", angle: "0°" },
  { kind: "LEFT", label: "左侧", angle: "−90°" },
  { kind: "RIGHT", label: "右侧", angle: "+90°" },
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
  const value = Number(item.progress?.percent ?? 0);
  return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0;
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
  const [consistency, setConsistency] = useState<MultiViewSettings["consistency_strength"]>("HIGH");
  const [background, setBackground] = useState<MultiViewSettings["background"]>("CLEAN");
  const [preflight, setPreflight] = useState<MultiViewPreflight | null>(null);
  const [batches, setBatches] = useState(initialBatches);
  const [busy, setBusy] = useState<"preflight" | "submit" | null>(null);
  const [bindingKey, setBindingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profilesState, setProfilesState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => { setBatches(initialBatches); }, [initialBatches]);
  useEffect(() => {
    setAssetStateId(""); setProfileVersionId(""); setPreflight(null); setBatches(initialBatches); setError(null);
  }, [assetId]); // initialBatches is intentionally synchronized by the effect above.

  useEffect(() => {
    let cancelled = false;
    setProfilesState("loading");
    void listProfiles()
      .then(({ items }) => {
        if (cancelled) return;
        const matching = items
          .filter((profile) => profile.capability === "IMAGE_MULTI_VIEW" && profile.status === "PUBLISHED")
          .sort((left, right) => left.title.localeCompare(right.title, "zh-CN") || (right.version_no ?? 0) - (left.version_no ?? 0));
        setProfiles(matching);
        setProfileVersionId((current) => current && !matching.some((profile) => profile.version_id === current) ? "" : current);
        setProfilesState("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setProfiles([]);
        setProfileVersionId("");
        setProfilesState("error");
      });
    return () => { cancelled = true; };
  }, [projectId]);

  const settings = useMemo<MultiViewSettings>(() => ({
    asset_state_id: assetStateId || null,
    profile_version_id: profileVersionId.trim() || null,
    consistency_strength: consistency,
    background,
  }), [assetStateId, profileVersionId, consistency, background]);

  useEffect(() => { setPreflight(null); }, [settings]);

  const active = batches.some(isMultiViewBatchActive);
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

  const selectedReferences = assetStateId
    ? states.find((state) => state.id === assetStateId)?.references ?? []
    : baseReferences;
  const allReferences = [...baseReferences, ...states.flatMap((state) => state.references)];
  const hasHero = selectedReferences.some((reference) => reference.reference_kind === "HERO")
    || (assetStateId !== "" && baseReferences.some((reference) => reference.reference_kind === "HERO"));

  const runPreflight = async () => {
    setBusy("preflight"); setError(null);
    try { setPreflight((await preflightAssetMultiView(assetId, settings)).preflight); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : String(requestError)); }
    finally { setBusy(null); }
  };

  const submit = async () => {
    if (!preflight?.ready) return;
    setBusy("submit"); setError(null);
    try {
      await submitAssetMultiView(assetId, settings, preflight.plan_hash);
      setBatches(await getAssetMultiViewHistory(assetId));
      setPreflight(null);
    } catch (requestError) { setError(requestError instanceof Error ? requestError.message : String(requestError)); }
    finally { setBusy(null); }
  };

  const bind = async (batchId: string, kind: MultiViewKind, mediaVersionId: string) => {
    const key = `${batchId}:${kind}:${mediaVersionId}`;
    setBindingKey(key); setError(null);
    try { await bindMultiViewReference(assetId, assetStateId || null, kind, mediaVersionId); await onReferencesChanged(); }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : String(requestError)); }
    finally { setBindingKey(null); }
  };

  if (assetKind !== "CHARACTER") return null;

  return <section className="panel multiview-panel" aria-labelledby={`multiview-title-${assetId}`}>
    <div className="panel-heading multiview-heading">
      <div><p className="eyebrow">角色一致性</p><h4 id={`multiview-title-${assetId}`}>生成 FRONT / LEFT / RIGHT</h4></div>
      <span className={`status-pill ${hasHero ? "state-ready" : "state-blocked"}`}>{hasHero ? "HERO 已就绪" : "缺少 HERO"}</span>
    </div>
    <p className="muted">一次预检后提交三个独立任务。成功视图可单独绑定；重做会保留此前批次和媒体历史。</p>

    <div className="multiview-controls">
      <label>造型状态<select value={assetStateId} onChange={(event) => setAssetStateId(event.target.value)}><option value="">基础角色</option>{states.map((state) => <option key={state.id} value={state.id}>{state.label}</option>)}</select></label>
      <label>一致性<select value={consistency} onChange={(event) => setConsistency(event.target.value as typeof consistency)}><option value="HIGH">高</option><option value="MEDIUM">中</option><option value="LOW">低</option></select></label>
      <label>背景<select value={background} onChange={(event) => setBackground(event.target.value as typeof background)}><option value="CLEAN">干净背景</option><option value="TRANSPARENT">透明背景</option><option value="ORIGINAL">保留原背景</option></select></label>
    </div>
    <details className="multiview-advanced">
      <summary>指定能力 Profile（可选）</summary>
      <label htmlFor={`multiview-profile-${assetId}`}>已发布的三视图 Profile</label>
      <select
        id={`multiview-profile-${assetId}`}
        value={profileVersionId}
        disabled={profilesState === "loading"}
        aria-describedby={`multiview-profile-help-${assetId}`}
        onChange={(event) => setProfileVersionId(event.target.value)}
      >
        <option value="">AUTO · 按项目偏好解析</option>
        {profiles.map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.title} · {profile.code} · v{profile.version_no ?? "?"}</option>)}
      </select>
      <small id={`multiview-profile-help-${assetId}`} className={`multiview-profile-help${profilesState === "error" ? " is-error" : ""}`} role={profilesState === "loading" ? "status" : profilesState === "error" ? "alert" : undefined}>
        {profilesState === "loading" && "正在读取已发布的 IMAGE_MULTI_VIEW Profiles…"}
        {profilesState === "error" && "Profile 目录暂不可用；AUTO 仍可运行只读预检，由服务端返回真实解析结果。"}
        {profilesState === "ready" && profiles.length === 0 && "暂无已发布的 IMAGE_MULTI_VIEW Profile；AUTO 仍可运行预检。"}
        {profilesState === "ready" && profiles.length > 0 && "只列出能力匹配且已发布的不可变版本；AUTO 仍为默认。"}
      </small>
    </details>

    <div className="multiview-actions">
      <button type="button" className="secondary" disabled={busy !== null || assetStatus !== "ACTIVE"} onClick={() => void runPreflight()}>{busy === "preflight" ? "预检中…" : "运行只读预检"}</button>
      <button type="button" className="primary-action" disabled={!preflight?.ready || busy !== null || assetStatus !== "ACTIVE"} onClick={() => void submit()}>{busy === "submit" ? "提交三个任务…" : "确认生成三视图"}</button>
      {assetStatus !== "ACTIVE" && <small className="blocker-text">归档角色不能生成。</small>}
    </div>

    {preflight && <div className={`multiview-preflight ${preflight.ready ? "ready" : "blocked"}`} role="status">
      <strong>{preflight.ready ? `预检通过 · 将创建 ${preflight.would_create_jobs} 个任务` : `预检阻塞 · ${preflight.blockers.length} 项`}</strong>
      {preflight.hero && <small>HERO：{preflight.hero.media_version_id.slice(0, 12)}… · 输入角色 {String(preflight.profile_resolution.input_role ?? "未解析")}</small>}
      {preflight.blockers.map((blocker) => <div className="multiview-blocker" key={blocker.code}><span>{blocker.message}</span>{blocker.suggested_action && <small>{blocker.suggested_action}</small>}<code>{blocker.code}</code></div>)}
    </div>}
    {error && <p className="inline-error" role="alert">{error}</p>}

    <div className="multiview-history" aria-label="三视图生成历史">
      {batches.length === 0 ? <p className="empty-state">尚未提交三视图生成。</p> : batches.map((batch, batchIndex) => <details key={batch.intent_id} open={batchIndex === 0}>
        <summary><span>{batchIndex === 0 ? "最新批次" : `历史批次 ${batches.length - batchIndex}`}</span><span className={`status-pill state-${batch.status.toLowerCase().replaceAll("_", "-")}`}>{batch.completed_count}/{batch.total_count} 完成{batch.failed_count ? ` · ${batch.failed_count} 失败` : ""}</span><time dateTime={batch.created_at}>{new Date(batch.created_at).toLocaleString()}</time></summary>
        <div className="multiview-slots">
          {VIEWS.map((view) => {
            const item = batch.items.find((candidate) => candidate.reference_kind === view.kind);
            const output = item?.outputs.at(-1);
            const failed = Boolean(item?.job_state && TERMINAL_FAILURES.has(item.job_state));
            const bindKey = output ? `${batch.intent_id}:${view.kind}:${output.media_version_id}` : "";
            const alreadyBound = Boolean(output && allReferences.some((reference) => reference.reference_kind === view.kind && reference.media_version_id === output.media_version_id && (reference.asset_state_id ?? "") === assetStateId));
            const percent = percentOf(item);
            return <article className={`multiview-slot${failed ? " failed" : item?.job_state === "SUCCEEDED" ? " succeeded" : ""}`} key={view.kind}>
              <div className="multiview-slot-head"><strong>{view.label}</strong><span>{view.kind} · {view.angle}</span></div>
              {output ? <img src={thumbnailUrl(output.media_version_id)} alt={`${view.label}生成结果缩略图`} width="240" height="180" loading="lazy" decoding="async" /> : <div className="multiview-slot-placeholder" aria-hidden="true"><span>{percent}%</span></div>}
              <div className="multiview-progress"><progress value={percent} max="100" aria-label={`${view.label}生成进度`} /><span>{stateLabel(item?.job_state)}</span></div>
              {item?.progress?.phase && <small>{String(item.progress.phase)}{item.progress.node ? ` · ${String(item.progress.node)}` : ""}</small>}
              {item?.error && <p className="inline-error" role="alert">{item.error.detail || item.error.code || "任务失败"}</p>}
              {output && <><code title={output.media_version_id}>{output.media_version_id.slice(0, 12)}…</code><button type="button" className="secondary" disabled={alreadyBound || bindingKey !== null} onClick={() => void bind(batch.intent_id, view.kind, output.media_version_id)}>{alreadyBound ? "已绑定此结果" : bindingKey === bindKey ? "绑定中…" : `绑定为 ${view.kind}`}</button></>}
            </article>;
          })}
        </div>
      </details>)}
    </div>
  </section>;
}

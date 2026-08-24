import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listQcEpisodes, listQcPolicies, listQcSeasons, listQcShots, putQcPolicy, QcPolicyApiError, resolveQcPolicy } from "./api";
import type { QcCategory, QcOwnerType, QcPolicy, QcStage } from "./types";
import "./qc-policy.css";

const STAGES: Array<[QcStage, string]> = [["IMAGE", "图像"], ["VIDEO", "视频"], ["AUDIO", "声音"], ["CONTINUITY", "连续性"], ["DELIVERY", "交付"]];
const CATEGORIES: Array<[QcCategory, string]> = [["VISUAL", "画面"], ["FACE", "面部"], ["IDENTITY", "身份"], ["COMPOSITION", "构图"], ["MOTION", "运动"], ["CONTINUITY", "连续性"], ["AUDIO", "声音"], ["TECHNICAL", "技术规格"]];
const OWNER_LABEL: Record<QcOwnerType, string> = { PROJECT: "项目默认", EPISODE: "分集覆盖", SHOT: "镜头覆盖" };

const findPolicy = (items: QcPolicy[], ownerType: QcOwnerType, ownerId: string, stage: QcStage) => items.find((item) => item.owner_type === ownerType && item.owner_id === ownerId && item.stage === stage);
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export function QcPolicyManager({ projectId, initialEpisodeId = "", initialShotId = "" }: { projectId: string; initialEpisodeId?: string; initialShotId?: string }) {
  const cache = useQueryClient();
  const [seasonId, setSeasonId] = useState("");
  const [episodeId, setEpisodeId] = useState(initialEpisodeId);
  const [shotId, setShotId] = useState(initialShotId);
  const [ownerType, setOwnerType] = useState<QcOwnerType>(initialShotId ? "SHOT" : initialEpisodeId ? "EPISODE" : "PROJECT");
  const [stage, setStage] = useState<QcStage>("VIDEO");
  const [thresholds, setThresholds] = useState<Partial<Record<QcCategory, number>>>({ IDENTITY: .85, CONTINUITY: .8 });
  const [autoCategories, setAutoCategories] = useState<QcCategory[]>([]);
  const [maxRerolls, setMaxRerolls] = useState(0);
  const [reason, setReason] = useState("");
  const [expectedRevision, setExpectedRevision] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const seasons = useQuery({ queryKey: ["qc-policy", "seasons", projectId], queryFn: () => listQcSeasons(projectId), enabled: Boolean(projectId) });
  const episodes = useQuery({ queryKey: ["qc-policy", "episodes", seasonId], queryFn: () => listQcEpisodes(seasonId), enabled: Boolean(seasonId) });
  const shots = useQuery({ queryKey: ["qc-policy", "shots", episodeId], queryFn: () => listQcShots(episodeId), enabled: Boolean(episodeId) });
  const policies = useQuery({ queryKey: ["qc-policy", "items", projectId], queryFn: () => listQcPolicies(projectId), enabled: Boolean(projectId) });
  const resolution = useQuery({ queryKey: ["qc-policy", "resolution", projectId, stage, episodeId, shotId], queryFn: () => resolveQcPolicy(projectId, stage, episodeId || undefined, shotId || undefined), enabled: Boolean(projectId) });

  useEffect(() => { if (!seasonId && seasons.data?.[0]) setSeasonId(seasons.data[0].id); }, [seasonId, seasons.data]);
  useEffect(() => { if (!episodeId && episodes.data?.[0]) setEpisodeId(episodes.data[0].id); }, [episodeId, episodes.data]);
  useEffect(() => { if (!shotId && shots.data?.[0]) setShotId(shots.data[0].id); }, [shotId, shots.data]);
  const ownerId = ownerType === "PROJECT" ? projectId : ownerType === "EPISODE" ? episodeId : shotId;
  const current = useMemo(() => findPolicy(policies.data ?? [], ownerType, ownerId, stage), [ownerId, ownerType, policies.data, stage]);
  const currentKey = current ? `${current.policy_version_id}:${current.revision}` : `new:${ownerType}:${ownerId}:${stage}`;
  const loadEditor = (source: QcPolicy | undefined, nextNotice: string | null = null) => {
    setThresholds(source?.policy.thresholds ?? {});
    setMaxRerolls(source?.max_auto_rerolls ?? 0);
    setAutoCategories(source?.auto_reroll_categories ?? []);
    setReason("");
    setExpectedRevision(source?.revision ?? null);
    setNotice(nextNotice);
  };
  useEffect(() => {
    loadEditor(current);
  }, [currentKey]);

  const save = useMutation({
    mutationFn: () => {
      if (!ownerId) throw new Error("请先选择完整的项目、分集或镜头范围");
      if (maxRerolls === 0 && autoCategories.length) throw new Error("自动重抽上限为 0 时不能选择自动类别");
      const checks = CATEGORIES.map(([id]) => id).filter((id) => thresholds[id] !== undefined);
      return putQcPolicy(projectId, { owner_type: ownerType, owner_id: ownerId, stage, policy: { checks, thresholds, attention_selection: "REQUIRE_CONFIRMATION" }, max_auto_rerolls: maxRerolls, auto_reroll_categories: autoCategories, reason: reason.trim(), expected_revision: expectedRevision });
    },
    onMutate: () => setNotice(null),
    onSuccess: async (saved) => { setNotice(`已创建不可变版本 v${saved.version_no}，policy revision ${saved.revision}`); await cache.invalidateQueries({ queryKey: ["qc-policy"] }); },
    onError: (error) => setNotice(error instanceof QcPolicyApiError && error.status === 409 ? `版本冲突：${error.message}。刷新后再保存。` : `保存失败：${message(error)}`),
  });
  const toggleThreshold = (category: QcCategory, enabled: boolean) => setThresholds((old) => { const next = { ...old }; if (enabled) next[category] = .8; else delete next[category]; return next; });
  const toggleAuto = (category: QcCategory, enabled: boolean) => setAutoCategories((old) => enabled ? [...new Set([...old, category])] : old.filter((item) => item !== category));
  const adjustThreshold = (category: QcCategory, direction: number) => setThresholds((old) => ({ ...old, [category]: Math.max(0, Math.min(1, Number(((old[category] ?? .8) + direction * .05).toFixed(2)))) }));
  const setRerollLimit = (value: number) => { const next = Math.max(0, Math.min(10, Math.round(value))); setMaxRerolls(next); if (!next) setAutoCategories([]); };
  const refreshEditor = async () => {
    const refreshed = await policies.refetch();
    loadEditor(findPolicy(refreshed.data ?? [], ownerType, ownerId, stage), "已从持久化版本重新载入，未保存修改已放弃。");
  };
  const hierarchy = (["PROJECT", "EPISODE", "SHOT"] as QcOwnerType[]).map((type) => ({ type, id: type === "PROJECT" ? projectId : type === "EPISODE" ? episodeId : shotId }));

  const initialPending = seasons.isPending || policies.isPending || resolution.isPending;
  const initialErrors = [seasons.error, policies.error, resolution.error].filter(Boolean);
  if (initialPending) return <section className="panel" role="status" aria-live="polite"><p className="empty-state">正在读取 QC 策略、继承范围与当前生效版本…</p></section>;
  if (initialErrors.length > 0) return <section className="panel" role="alert"><div className="panel-heading"><div><p className="eyebrow">读取失败</p><h3>QC 策略暂时无法打开</h3></div></div><p className="muted">已保留页面上下文，没有写入任何策略。{initialErrors.map(message).join("；")}</p><button type="button" className="secondary" onClick={() => void Promise.all([seasons.refetch(), policies.refetch(), resolution.refetch()])}>重新读取</button></section>;

  return <div className="qc-policy-manager">
    <section className="panel qc-context" aria-labelledby="qc-context-title"><div className="panel-heading"><div><p className="eyebrow">质量策略</p><h3 id="qc-context-title">生产范围与继承解析</h3></div><span className="status-pill neutral">项目 → 分集 → 镜头</span></div>
      <div className="qc-context-grid"><label>季度<select value={seasonId} onChange={(e) => { setSeasonId(e.target.value); setEpisodeId(""); setShotId(""); }}><option value="">选择季度</option>{seasons.data?.map((item) => <option key={item.id} value={item.id}>{item.code} · {item.title}</option>)}</select></label><label>分集<select value={episodeId} onChange={(e) => { setEpisodeId(e.target.value); setShotId(""); }}><option value="">仅项目级</option>{episodes.data?.map((item) => <option key={item.id} value={item.id}>{item.code} · {item.title}</option>)}</select></label><label>镜头<select value={shotId} onChange={(e) => setShotId(e.target.value)}><option value="">仅分集级</option>{shots.data?.map((item) => <option key={item.id} value={item.id}>{item.code}</option>)}</select></label></div>
      <div className="qc-stage-tabs" role="tablist" aria-label="QC 阶段">{STAGES.map(([id, label]) => <button type="button" role="tab" aria-selected={stage === id} className={stage === id ? "selected" : ""} key={id} onClick={() => setStage(id)}>{label}<small>{id}</small></button>)}</div>
    </section>
    <div className="qc-workspace">
      <section className="panel qc-resolution" aria-labelledby="qc-resolution-title"><div className="panel-heading"><div><p className="eyebrow">当前生效策略</p><h3 id="qc-resolution-title">{stage}</h3></div><span className={`status-pill ${resolution.data ? "state-active" : "neutral"}`}>{resolution.data ? OWNER_LABEL[resolution.data.source] : "未配置"}</span></div>
        <ol className="qc-inheritance">{hierarchy.map(({ type, id }) => { const item = id ? findPolicy(policies.data ?? [], type, id, stage) : undefined; return <li className={resolution.data?.source === type ? "effective" : ""} key={type}><span>{type === "PROJECT" ? "1" : type === "EPISODE" ? "2" : "3"}</span><div><strong>{OWNER_LABEL[type]}</strong><small>{!id ? "未选择" : item ? `v${item.version_no} · revision ${item.revision}` : "继承上级"}</small></div></li>; })}</ol>
        {resolution.data ? <dl className="qc-resolution-facts"><div><dt>生效版本</dt><dd>v{resolution.data.version_no} · {resolution.data.policy_version_id.slice(0, 8)}…</dd></div><div><dt>自动上限</dt><dd>{resolution.data.max_auto_rerolls} 次</dd></div><div><dt>自动类别</dt><dd>{resolution.data.auto_reroll_categories.join("、") || "无，失败进入人工门禁"}</dd></div></dl> : <p className="empty-state">该上下文没有可继承策略，生产前应先配置项目默认。</p>}
      </section>
      <form className="panel qc-editor" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}><div className="panel-heading"><div><p className="eyebrow">版本化编辑</p><h3>{OWNER_LABEL[ownerType]} · {stage}</h3></div><span className="status-pill neutral">{current ? `revision ${current.revision}` : "新策略"}</span></div>
        <fieldset className="qc-owner"><legend>写入层级</legend>{(["PROJECT", "EPISODE", "SHOT"] as QcOwnerType[]).map((type) => { const disabledReason = type === "EPISODE" && !episodeId ? "请先选择分集" : type === "SHOT" && !shotId ? "请先选择镜头" : null; return <button type="button" key={type} disabled={Boolean(disabledReason)} title={disabledReason ?? undefined} aria-pressed={ownerType === type} className={ownerType === type ? "selected" : ""} onClick={() => setOwnerType(type)}>{OWNER_LABEL[type]}</button>; })}</fieldset>
        <fieldset className="qc-thresholds"><legend>检查类别与通过阈值</legend>{CATEGORIES.map(([id, label]) => { const active = thresholds[id] !== undefined; return <div key={id}><label><input type="checkbox" checked={active} onChange={(e) => toggleThreshold(id, e.target.checked)} />{label}<small>{id}</small></label><label className="threshold-value"><span className="sr-only">{label}阈值</span><input type="number" min="0" max="1" step="0.05" value={thresholds[id] ?? .8} disabled={!active} onKeyDown={(e) => { if (["ArrowUp", "ArrowRight"].includes(e.key)) { e.preventDefault(); adjustThreshold(id, 1); } else if (["ArrowDown", "ArrowLeft"].includes(e.key)) { e.preventDefault(); adjustThreshold(id, -1); } }} onChange={(e) => setThresholds((old) => ({ ...old, [id]: Math.max(0, Math.min(1, Number(e.target.value))) }))} /><output>{Math.round((thresholds[id] ?? .8) * 100)}%</output></label></div>; })}</fieldset>
        <label className="qc-reroll-limit">最大自动重抽次数 <output>{maxRerolls}</output><input type="range" min="0" max="10" step="1" value={maxRerolls} onKeyDown={(e) => { if (["ArrowUp", "ArrowRight"].includes(e.key)) { e.preventDefault(); setRerollLimit(maxRerolls + 1); } else if (["ArrowDown", "ArrowLeft"].includes(e.key)) { e.preventDefault(); setRerollLimit(maxRerolls - 1); } else if (e.key === "Home") { e.preventDefault(); setRerollLimit(0); } else if (e.key === "End") { e.preventDefault(); setRerollLimit(10); } }} onChange={(e) => setRerollLimit(Number(e.target.value))} /><small>0 表示任何失败都进入人工门禁；系统绝不会无限重抽。</small></label>
        <fieldset className="qc-auto-categories" disabled={maxRerolls === 0}><legend>允许自动重抽的失败类别</legend>{CATEGORIES.map(([id, label]) => <label key={id}><input type="checkbox" checked={autoCategories.includes(id)} onChange={(e) => toggleAuto(id, e.target.checked)} />{label}</label>)}</fieldset>
        <label>变更原因<textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="为什么调整阈值或自动重抽范围？" /></label>
        {notice && <p className={notice.startsWith("已创建") ? "qc-notice success" : "qc-notice error"} role="status">{notice}</p>}
        <div className="qc-actions"><button type="button" className="secondary" onClick={() => void refreshEditor()}>刷新版本</button><button type="submit" className="primary-action" disabled={save.isPending || !ownerId} title={save.isPending ? "正在创建不可变策略版本" : !ownerId ? "请先选择完整的写入范围" : undefined}>{save.isPending ? "保存中…" : current ? `创建 v${current.version_no + 1}` : "创建策略 v1"}</button></div>
      </form>
    </div>
    <section className="panel qc-dispositions" aria-labelledby="qc-dispositions-title"><div className="panel-heading"><div><p className="eyebrow">机器决定的含义</p><h3 id="qc-dispositions-title">Disposition 不是人工批准</h3></div><span className="status-pill state-warning">机器证据 ≠ 人工批准</span></div><div>{[["PASS", "机器检查通过", "只证明本次检查通过，候选仍需人工选择/批准。"], ["ATTENTION", "需要显式确认", "机器提示风险；UI 必须让人确认，不静默批准。"], ["AUTO_REROLL_ALLOWED", "允许有限重抽", "仅对允许类别且未达到上限创建子 Variant。"], ["WAITING_GATE", "等待人工门禁", "达到上限或类别不可自动修复，停止自动流程。"]].map(([code, title, copy]) => <article key={code} className={`qc-disposition-card disposition-${code.toLowerCase()}`}><code>{code}</code><strong>{title}</strong><p>{copy}</p></article>)}</div></section>
  </div>;
}

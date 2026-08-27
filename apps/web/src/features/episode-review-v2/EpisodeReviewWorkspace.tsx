import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import {
  createReviewDecisionV2,
  getEpisodePostOverviewV2,
  listEpisodeReviewTargetsV2,
  revokeReviewDecisionV2,
  type EpisodeReviewTarget,
  type ReviewTargetKind,
} from "../../generated/api";
import { VideoAnnotations } from "../reviews/VideoAnnotations";

const KIND_LABELS: Record<ReviewTargetKind, string> = {
  MEDIA_VERSION: "镜头与音频",
  EPISODE_RENDER_VERSION: "整集成片",
};
const DECISION_LABELS = { APPROVED: "批准", NEEDS_CHANGES: "需修改", REJECTED: "拒绝" } as const;
type Decision = keyof typeof DECISION_LABELS;

function newCommandKey(prefix: string) {
  return `${prefix}:${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

function Summary({ label, value, detail, tone = "neutral" }: { label: string; value: string | number; detail: string; tone?: string }) {
  return <article className={`post-review-summary tone-${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function TargetRow({ item, selected, onSelect }: { item: EpisodeReviewTarget; selected: boolean; onSelect: () => void }) {
  const needsAttention = item.blocker_codes.length > 0 || item.latest_decision_stale;
  return <button className={`post-review-target${selected ? " is-selected" : ""}`} type="button" onClick={onSelect} aria-pressed={selected}>
    <span className="post-review-target__title"><strong>{item.label}</strong><span className={`status-pill ${needsAttention ? "warning" : "neutral"}`}>{needsAttention ? "需处理" : "待决定"}</span></span>
    <span>{item.media_kind ? `${item.media_kind} · ${item.stage}` : "整集成片"}</span>
    <small>{item.machine_status ? `机器证据：${item.machine_status}` : `完整性：${item.integrity_status}`}</small>
  </button>;
}

export function EpisodeReviewWorkspace({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedKind = searchParams.get("targetKind") as ReviewTargetKind | null;
  const legacyMedia = searchParams.get("media");
  const requestedId = searchParams.get("targetId") ?? legacyMedia;
  const initialKind: ReviewTargetKind = requestedKind === "EPISODE_RENDER_VERSION" ? requestedKind : "MEDIA_VERSION";
  const [kind, setKind] = useState<ReviewTargetKind>(initialKind);
  const [includeResolved, setIncludeResolved] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(requestedId);
  const [decision, setDecision] = useState<Decision>("APPROVED");
  const [comment, setComment] = useState("");
  const [checks, setChecks] = useState<Record<string, boolean>>({});

  const overview = useQuery({ queryKey: ["post-v2", episodeId, "overview"], queryFn: () => getEpisodePostOverviewV2(episodeId) });
  const targets = useQuery({
    queryKey: ["post-v2", episodeId, "review-targets", kind, includeResolved],
    queryFn: () => listEpisodeReviewTargetsV2(episodeId, { targetKinds: [kind], includeResolved, limit: 100 }),
  });
  const items = targets.data?.items ?? [];
  const selected = items.find((item) => item.target_id === selectedId) ?? null;
  const resolvingDeepLink = Boolean(requestedId && targets.isFetching && !selected);

  useEffect(() => {
    if (requestedId && items.some((item) => item.target_id === requestedId)) {
      setSelectedId(requestedId);
      return;
    }
    if (resolvingDeepLink || items.some((item) => item.target_id === selectedId)) return;
    setSelectedId(items[0]?.target_id ?? null);
  }, [items, requestedId, resolvingDeepLink, selectedId]);

  useEffect(() => {
    if (!selected) return;
    setChecks(Object.fromEntries(selected.template_items.map((item) => [item.id, false])));
    setDecision("APPROVED");
    setComment("");
  }, [selected?.target_id]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["post-v2", episodeId, "overview"] }),
      queryClient.invalidateQueries({ queryKey: ["post-v2", episodeId, "review-targets"] }),
    ]);
  };
  const createDecision = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error("请先选择审核目标");
      return createReviewDecisionV2({
        target_kind: selected.target_kind,
        target_id: selected.target_id,
        template_version_id: selected.template_version_id,
        expected_revision: selected.subject_revision,
        decision,
        checks: selected.template_items.map((item) => ({ item_id: item.id, result: checks[item.id] ? "PASS" : "FAIL" })),
        comment: comment.trim() || null,
        idempotency_key: newCommandKey("review"),
      });
    },
    onSuccess: refresh,
  });
  const revokeDecision = useMutation({
    mutationFn: async () => {
      if (!selected?.latest_decision_id || !selected.latest_decision_revision) throw new Error("没有可撤回的审核决定");
      return revokeReviewDecisionV2(selected.latest_decision_id, {
        expected_revision: selected.latest_decision_revision,
        reason: comment.trim() || "审核人撤回决定",
        idempotency_key: newCommandKey("review-revoke"),
      });
    },
    onSuccess: refresh,
  });

  const requiredComplete = useMemo(
    () => selected?.template_items.filter((item) => item.required).every((item) => checks[item.id]) ?? false,
    [checks, selected],
  );
  const submitDisabled = !selected || createDecision.isPending || (decision === "APPROVED" && (!requiredComplete || selected.blocker_codes.length > 0));
  const summary = overview.data?.overview;

  const selectKind = (nextKind: ReviewTargetKind) => {
    setKind(nextKind);
    setSelectedId(null);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("targetKind", nextKind);
      next.delete("targetId");
      next.delete("media");
      return next;
    }, { replace: true });
  };
  const selectTarget = (item: EpisodeReviewTarget) => {
    setSelectedId(item.target_id);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("targetKind", item.target_kind);
      next.set("targetId", item.target_id);
      next.delete("media");
      return next;
    }, { replace: true });
  };

  return <div className="episode-review-workspace post-review-v2">
    <section className="post-review-summary-grid" aria-label="本集审核摘要">
      <Summary label="待审核" value={summary?.review.pending_count ?? "—"} detail="当前分集全部目标" tone={summary?.review.pending_count ? "warning" : "ok"} />
      <Summary label="阻塞" value={summary?.review.blocked_count ?? "—"} detail="需先补齐机器或文件证据" tone={summary?.review.blocked_count ? "danger" : "ok"} />
      <Summary label="已失效" value={summary?.review.stale_count ?? "—"} detail="上游变化后重新决定" tone={summary?.review.stale_count ? "warning" : "ok"} />
      <Summary label="整集批准" value={summary?.review.approved_render_id ? "已完成" : "未完成"} detail="交付只消费有效批准" tone={summary?.review.approved_render_id ? "ok" : "neutral"} />
    </section>

    <section className="post-review-toolbar" aria-label="审核目标筛选">
      <div className="segmented-control" role="group" aria-label="审核目标类型">
        {(Object.keys(KIND_LABELS) as ReviewTargetKind[]).map((value) => <button key={value} type="button" className={kind === value ? "is-active" : ""} onClick={() => selectKind(value)}>{KIND_LABELS[value]}</button>)}
      </div>
      <label className="post-review-resolved"><input type="checkbox" checked={includeResolved} onChange={(event) => setIncludeResolved(event.target.checked)} />显示已解决</label>
    </section>

    {overview.isError || targets.isError ? <div className="inline-error" role="alert">审核工作台读取失败。<button className="secondary" type="button" onClick={() => { void overview.refetch(); void targets.refetch(); }}>重试</button></div> : null}
    {targets.isPending || resolvingDeepLink ? <p className="empty-state" role="status">正在读取审核目标…</p> : null}

    <div className="post-review-layout">
      <section className="post-review-list" aria-label="审核目标列表">
        <header><div><p className="eyebrow">审核队列</p><h3>{KIND_LABELS[kind]}</h3></div><span>{targets.data?.total ?? 0} 项</span></header>
        {!targets.isPending && items.length === 0 ? <p className="empty-state">当前筛选下没有待处理目标。</p> : items.map((item) => <TargetRow key={item.target_id} item={item} selected={item.target_id === selectedId} onSelect={() => selectTarget(item)} />)}
      </section>

      <section className="post-review-inspector" aria-label="审核决定">
        {!selected ? <p className="empty-state">从左侧选择一个目标，查看证据并记录人工决定。</p> : <>
          <header><div><p className="eyebrow">人工决定</p><h3>{selected.label}</h3></div><span className="status-pill neutral">revision {selected.subject_revision}</span></header>
          <div className="post-review-preview">
            {selected.target_kind === "EPISODE_RENDER_VERSION"
              ? <video key={selected.target_id} ref={videoRef} controls preload="none" poster={`/api/v1/episode-renders/${encodeURIComponent(selected.target_id)}/thumbnail?size=medium&frame=poster`} src={`/api/v1/episode-renders/${encodeURIComponent(selected.target_id)}/content`} />
              : selected.media_kind === "VIDEO"
              ? <video key={selected.target_id} ref={videoRef} controls preload="none" poster={`/api/v1/media-versions/${encodeURIComponent(selected.target_id)}/thumbnail?size=medium&frame=poster`} src={`/api/v1/media-versions/${encodeURIComponent(selected.target_id)}/proxy`} />
              : selected.media_kind === "AUDIO"
              ? <audio key={selected.target_id} controls preload="metadata" src={`/api/v1/media-versions/${encodeURIComponent(selected.target_id)}/content`} />
              : <img src={`/api/v1/media-versions/${encodeURIComponent(selected.target_id)}/thumbnail?size=medium&frame=poster`} alt={`${selected.label} 审核预览`} />}
          </div>
          <dl className="post-review-facts"><div><dt>完整性</dt><dd>{selected.integrity_status}</dd></div><div><dt>机器证据</dt><dd>{selected.machine_status ?? "不适用"}</dd></div><div><dt>最近决定</dt><dd>{selected.latest_decision ?? "尚无"}{selected.latest_decision_stale ? " · 已失效" : ""}</dd></div></dl>
          {selected.allowed_actions.includes("CREATE_FRAME_ANNOTATION") && selected.duration_ms ? <VideoAnnotations mediaVersionId={selected.target_id} expectedRevision={selected.subject_revision} durationMs={selected.duration_ms} getCurrentTimeMs={() => (videoRef.current?.currentTime ?? 0) * 1000} onSeek={(timecodeMs) => { if (videoRef.current) videoRef.current.currentTime = timecodeMs / 1000; }} /> : null}
          {selected.blocker_codes.length > 0 && <div className="post-review-blockers" role="note"><strong>批准前需处理</strong><ul>{selected.blocker_codes.map((code) => <li key={code}>{code}</li>)}</ul></div>}
          <fieldset className="post-review-checks"><legend>{selected.template_code} 检查表</legend>{selected.template_items.map((item) => <label key={item.id}><input type="checkbox" checked={Boolean(checks[item.id])} onChange={(event) => setChecks((current) => ({ ...current, [item.id]: event.target.checked }))} /><span>{item.label}{item.required ? " *" : ""}</span></label>)}</fieldset>
          <label className="post-review-field">审核结论<select value={decision} onChange={(event) => setDecision(event.target.value as Decision)}>{(Object.keys(DECISION_LABELS) as Decision[]).map((value) => <option key={value} value={value}>{DECISION_LABELS[value]}</option>)}</select></label>
          <label className="post-review-field">备注<textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder={decision === "REJECTED" ? "拒绝时必须填写具体原因" : "记录修改建议或批准说明"} rows={3} /></label>
          {createDecision.error && <p className="inline-error" role="alert">提交失败：{String(createDecision.error)}</p>}
          {createDecision.isSuccess && <p className="inline-success" role="status">审核决定已保存并写入审计记录。</p>}
          {revokeDecision.error && <p className="inline-error" role="alert">撤回失败：{String(revokeDecision.error)}</p>}
          <div className="post-review-actions">
            {selected.latest_decision_id && selected.latest_decision !== "VOIDED" && <button className="secondary" type="button" disabled={revokeDecision.isPending} onClick={() => revokeDecision.mutate()}>撤回最近决定</button>}
            <button className="primary-action" type="button" disabled={submitDisabled} onClick={() => createDecision.mutate()}>{createDecision.isPending ? "正在保存…" : `保存${DECISION_LABELS[decision]}`}</button>
          </div>
        </>}
      </section>
    </div>
    <footer className="creative-task-gateway post-review-handoff"><div><strong>审核完成后</strong><p>冻结剪辑负责生成 Review Proxy；交付只消费有效的整集人工批准。</p></div><div className="creative-task-gateway__actions"><Link className="secondary" to={routes.postEdit(projectId, episodeId)}>检查剪辑</Link><Link className="secondary" to={routes.delivery(projectId, episodeId)}>查看交付</Link></div></footer>
  </div>;
}

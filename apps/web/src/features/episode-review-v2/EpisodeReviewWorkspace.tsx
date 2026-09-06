import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import {
  createReviewDecisionV2,
  getEpisodePostOverviewV2,
  listEpisodeReviewTargetsV2,
  revokeReviewDecisionV2,
  runMachineCheck,
  type MachineCheck,
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

function latestDecisionLabel(decision: string | null): string | null {
  return decision && decision in DECISION_LABELS ? DECISION_LABELS[decision as Decision] : null;
}

function newCommandKey(prefix: string) {
  return `${prefix}:${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

function Summary({ label, value, detail, tone = "neutral" }: { label: string; value: string | number; detail: string; tone?: string }) {
  return <article className={`post-review-summary tone-${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function TargetRow({ item, selected, onSelect }: { item: EpisodeReviewTarget; selected: boolean; onSelect: () => void }) {
  const needsAttention = item.blocker_codes.length > 0 || item.latest_decision_stale;
  const decisionLabel = latestDecisionLabel(item.latest_decision);
  return <button className={`post-review-target${selected ? " is-selected" : ""}`} type="button" onClick={onSelect} aria-pressed={selected}>
    <span className="post-review-target__title"><strong>{item.label}</strong><span className={`status-pill ${needsAttention ? "warning" : "neutral"}`}>{decisionLabel ?? "待决定"}</span></span>
    <span>{item.media_kind ? `${item.media_kind} · ${item.stage}` : "整集成片"}</span>
    <small>{item.machine_status ? `机器证据：${item.machine_status}` : `完整性：${item.integrity_status}`}</small>
  </button>;
}

const MACHINE_RESULT_LABELS: Record<string, string> = {
  duration: "时长",
  codec: "编码",
  sample_rate: "采样率",
  channels: "声道",
  integrated_loudness: "综合响度",
  true_peak: "True Peak",
  peak: "峰值",
  clipping: "削波",
  silence: "静音段",
  file_integrity: "文件完整性",
  decode: "解码",
};

function machineResultValue(item: MachineCheck["results"][number]): string {
  const details = item.details ?? {};
  if (item.item_id === "duration") return `${details.duration_ms ?? "—"} ms`;
  if (item.item_id === "codec") return String(details.codec_name ?? "—");
  if (item.item_id === "sample_rate") return `${details.sample_rate_hz ?? "—"} Hz`;
  if (item.item_id === "channels") return `${details.channels ?? "—"}${details.channel_layout ? ` · ${details.channel_layout}` : ""}`;
  if (item.item_id === "integrated_loudness") return `${details.value_lufs ?? "—"} LUFS`;
  if (item.item_id === "true_peak" || item.item_id === "peak") return `${details.value_dbfs ?? "—"} dBFS`;
  if (item.item_id === "clipping") return details.detected ? "检测到削波" : "未检测到削波";
  if (item.item_id === "silence") {
    const count = details.segment_count;
    return `${details.detected ? "检测到" : "未检测到"}${count === undefined ? "" : ` · ${count} 段`}`;
  }
  if (item.item_id === "file_integrity") return details.exists === false ? "文件缺失" : `文件 ${details.byte_size ?? "—"} bytes`;
  if (item.item_id === "decode") return String(details.probe_status ?? "—");
  return Object.entries(details).map(([key, value]) => `${key}=${String(value)}`).join(" · ") || "—";
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
  const [machineReport, setMachineReport] = useState<MachineCheck | null>(null);

  const overview = useQuery({ queryKey: ["post-v2", episodeId, "overview"], queryFn: () => getEpisodePostOverviewV2(episodeId) });
  const targets = useInfiniteQuery({
    queryKey: ["post-v2", episodeId, "review-targets", kind, includeResolved],
    queryFn: ({ pageParam }) => listEpisodeReviewTargetsV2(episodeId, {
      targetKinds: [kind],
      includeResolved,
      limit: 100,
      ...(pageParam > 0 ? { cursor: pageParam } : {}),
    }),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    maxPages: 10,
  });
  const items = targets.data?.pages.flatMap((page) => page.items) ?? [];
  const deepLinkInPage = Boolean(requestedId && items.some((item) => item.target_id === requestedId));
  const deepLinkTarget = useQuery({
    queryKey: ["post-v2", episodeId, "review-target", kind, requestedId],
    queryFn: () => listEpisodeReviewTargetsV2(episodeId, {
      targetKinds: [kind],
      includeResolved,
      targetId: requestedId ?? undefined,
      limit: 1,
    }),
    enabled: Boolean(requestedId && !deepLinkInPage),
  });
  const deepLinkItem = deepLinkTarget.data?.items.find((item) => item.target_id === requestedId) ?? null;
  const selected = items.find((item) => item.target_id === selectedId) ?? (selectedId === requestedId ? deepLinkItem : null);
  const resolvingDeepLink = Boolean(requestedId && !selected && (targets.isFetching || deepLinkTarget.isFetching));
  const deepLinkNotFound = Boolean(requestedId && !resolvingDeepLink && !selected && deepLinkTarget.isFetched && !deepLinkTarget.isError);
  const displayItems = useMemo(() => {
    if (!deepLinkItem || items.some((item) => item.target_id === deepLinkItem.target_id)) return items;
    return [deepLinkItem, ...items];
  }, [deepLinkItem, items]);

  useEffect(() => {
    if (requestedId && items.some((item) => item.target_id === requestedId)) {
      setSelectedId(requestedId);
      return;
    }
    if (requestedId) {
      if (deepLinkItem) {
        setSelectedId(requestedId);
      } else if (resolvingDeepLink) {
        return;
      } else {
        setSelectedId(null);
      }
      return;
    }
    if (items.some((item) => item.target_id === selectedId)) return;
    setSelectedId(items[0]?.target_id ?? null);
  }, [deepLinkItem, items, requestedId, resolvingDeepLink, selectedId]);

  useEffect(() => {
    if (!selected) return;
    setChecks(Object.fromEntries(selected.template_items.map((item) => [item.id, false])));
    setDecision("APPROVED");
    setComment("");
    setMachineReport(null);
  }, [selected?.target_id]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["post-v2", episodeId, "overview"] }),
      queryClient.invalidateQueries({ queryKey: ["post-v2", episodeId, "review-targets"] }),
    ]);
  };
  const canRunMachineCheck = Boolean(
    selected?.target_kind === "MEDIA_VERSION"
      && selected.media_kind === "AUDIO"
      && selected.stage === "FORMAL"
      && selected.integrity_status === "VERIFIED"
      && selected.is_adopted === true,
  );
  const effectiveMachineStatus = machineReport?.status ?? selected?.machine_status ?? "NOT_RUN";
  const effectiveBlockers = selected?.blocker_codes.filter((code) => !(code === "MACHINE_QC_REQUIRED" && effectiveMachineStatus === "PASS")) ?? [];
  const audioChecklistLocked = selected?.media_kind === "AUDIO" && effectiveMachineStatus !== "PASS";
  const runAudioMachineCheck = useMutation({
    mutationFn: async () => {
      if (!selected || !canRunMachineCheck) throw new Error("只有已采用且完整性已验证的正式对白音频才能运行机器 QC");
      return runMachineCheck("MEDIA_VERSION", selected.target_id, { policy_version: "g8_audio_qc_v1" });
    },
    onSuccess: async ({ machine_check }) => {
      setMachineReport(machine_check);
      await refresh();
    },
  });
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
  const submitDisabled = !selected || createDecision.isPending || (decision === "APPROVED" && (!requiredComplete || effectiveBlockers.length > 0));
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

    {overview.isError || targets.isError || deepLinkTarget.isError ? <div className="inline-error" role="alert">审核工作台读取失败。<button className="secondary" type="button" onClick={() => { void overview.refetch(); void targets.refetch(); void deepLinkTarget.refetch(); }}>重试</button></div> : null}
    {targets.isPending || resolvingDeepLink ? <p className="empty-state" role="status">正在读取审核目标…</p> : null}
    {deepLinkNotFound ? <p className="empty-state" role="status">审核目标不存在或不属于本集：{requestedId}</p> : null}

    <div className="post-review-layout">
      <section className="post-review-list" aria-label="审核目标列表">
        <header><div><p className="eyebrow">审核队列</p><h3>{KIND_LABELS[kind]}</h3></div><span>{targets.data?.pages[0]?.total ?? 0} 项</span></header>
        {!targets.isPending && displayItems.length === 0 ? <p className="empty-state">当前筛选下没有待处理目标。</p> : displayItems.map((item) => <TargetRow key={item.target_id} item={item} selected={item.target_id === selectedId} onSelect={() => selectTarget(item)} />)}
        {targets.hasNextPage ? <button className="secondary list-more" type="button" onClick={() => void targets.fetchNextPage()} disabled={targets.isFetchingNextPage}>{targets.isFetchingNextPage ? "读取中…" : `加载更多审核目标（已加载 ${items.length}）`}</button> : null}
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
              : <img loading="eager" decoding="async" src={`/api/v1/media-versions/${encodeURIComponent(selected.target_id)}/thumbnail?size=medium&frame=poster`} alt={`${selected.label} 审核预览`} onError={(e) => { e.currentTarget.style.display = "none"; }} />}
          </div>
          <dl className="post-review-facts"><div><dt>完整性</dt><dd>{selected.integrity_status}</dd></div><div><dt>机器证据</dt><dd>{selected.machine_status ?? "不适用"}</dd></div><div><dt>最近决定</dt><dd>{selected.latest_decision ?? "尚无"}{selected.latest_decision_stale ? " · 已失效" : ""}</dd></div></dl>
          {selected.allowed_actions.includes("CREATE_FRAME_ANNOTATION") && selected.duration_ms ? <VideoAnnotations mediaVersionId={selected.target_id} expectedRevision={selected.subject_revision} durationMs={selected.duration_ms} getCurrentTimeMs={() => (videoRef.current?.currentTime ?? 0) * 1000} onSeek={(timecodeMs) => { if (videoRef.current) videoRef.current.currentTime = timecodeMs / 1000; }} /> : null}
          {canRunMachineCheck ? <section className="post-review-machine-qc" aria-label="音频机器质检"><header><div><p className="eyebrow">机器质检</p><h4>对白音频技术证据</h4></div><span className={`status-pill ${effectiveMachineStatus === "PASS" ? "success" : effectiveMachineStatus === "FAIL" ? "danger" : "warning"}`}>{effectiveMachineStatus}</span></header><p className="muted">仅读取当前已采用、完整性 VERIFIED 的正式对白音频；不会修改媒体内容。</p><button className="secondary" type="button" disabled={runAudioMachineCheck.isPending} onClick={() => runAudioMachineCheck.mutate()}>{runAudioMachineCheck.isPending ? "正在运行机器 QC…" : effectiveMachineStatus === "PASS" ? "重新运行机器 QC" : "运行机器 QC"}</button>{runAudioMachineCheck.error ? <p className="inline-error" role="alert">机器 QC 失败：{String(runAudioMachineCheck.error)}</p> : null}{machineReport ? <dl className="post-review-machine-qc__results" aria-label="音频机器质检结果">{machineReport.results.map((item) => <div key={item.item_id}><dt>{MACHINE_RESULT_LABELS[item.item_id] ?? item.item_id}</dt><dd><span className={`status-pill ${item.result === "PASS" ? "success" : "danger"}`}>{item.result}</span>{machineResultValue(item)}</dd></div>)}</dl> : null}</section> : null}
          {effectiveBlockers.length > 0 && <div className="post-review-blockers" role="note"><strong>批准前需处理</strong><ul>{effectiveBlockers.map((code) => <li key={code}>{code}</li>)}</ul></div>}
          <fieldset className="post-review-checks"><legend>{selected.template_code} 检查表</legend>{audioChecklistLocked ? <p className="muted">音频须先运行并通过机器 QC；通过后才可勾选人工检查项。</p> : null}{selected.template_items.map((item) => <label key={item.id}><input type="checkbox" disabled={audioChecklistLocked} checked={Boolean(checks[item.id])} onChange={(event) => setChecks((current) => ({ ...current, [item.id]: event.target.checked }))} /><span>{item.label}{item.required ? " *" : ""}</span></label>)}</fieldset>
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

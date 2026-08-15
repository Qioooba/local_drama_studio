import { useEffect, useRef, useState } from "react";
import { commitReviewBatch, getReviewContext, preflightReviewBatch, submitReview, type ReviewBatchPlan, type ReviewInboxItem, type ReviewTemplate } from "../../generated/api";
import { INITIAL_LIST_WINDOW, progressiveSlice } from "../shared/progressive";
import { VideoAnnotations } from "./VideoAnnotations";

export function ReviewInboxPanel({
  items,
  templates,
  selectedVersionId,
  context,
  onSelect,
  onPromote,
  selecting,
  onMachineCheck,
  machineChecking,
  machineCheckError,
  onSubmit,
  submitting,
  submitError,
  submitSucceeded,
}: {
  items: ReviewInboxItem[];
  templates: ReviewTemplate[];
  selectedVersionId: string | null;
  context: Awaited<ReturnType<typeof getReviewContext>> | undefined;
  onSelect: (id: string) => void;
  onPromote: (mediaVersionId: string, selectionType: string) => void;
  selecting: boolean;
  onMachineCheck: (mediaVersionId: string) => void;
  machineChecking: boolean;
  machineCheckError: string | null;
  onSubmit: (mediaVersionId: string, payload: Parameters<typeof submitReview>[1]) => void;
  submitting: boolean;
  submitError: string | null;
  submitSucceeded: boolean;
}) {
  const [checks, setChecks] = useState<Record<string, "PASS" | "FAIL">>({});
  const [decision, setDecision] = useState<"APPROVED" | "REJECTED" | "NEEDS_CHANGES">("APPROVED");
  const [reviewComment, setReviewComment] = useState("");
  const [compareVersionId, setCompareVersionId] = useState<string | null>(null);
  const [referencePinned, setReferencePinned] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [loopPreview, setLoopPreview] = useState(false);
  const [mutedPreview, setMutedPreview] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [syncVideoIds, setSyncVideoIds] = useState<string[]>([]);
  const syncVideoRefs = useRef<Record<string, HTMLVideoElement | null>>({});
  const [batchSelectedIds, setBatchSelectedIds] = useState<Set<string>>(new Set());
  const [batchPlan, setBatchPlan] = useState<ReviewBatchPlan | null>(null);
  const [batchDecision, setBatchDecision] = useState<"APPROVED" | "REJECTED" | "NEEDS_CHANGES">("APPROVED");
  const [batchChecks, setBatchChecks] = useState<Record<string, "PASS" | "FAIL">>({});
  const [batchComment, setBatchComment] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [batchNotice, setBatchNotice] = useState<string | null>(null);
  const [mediaFilter, setMediaFilter] = useState("ALL");
  const [reviewFilter, setReviewFilter] = useState("PENDING");
  const [visibleCount, setVisibleCount] = useState(INITIAL_LIST_WINDOW);
  const selectedItem = items.find((item) => item.media_version_id === selectedVersionId);
  const selectedIndex = items.findIndex((item) => item.media_version_id === selectedVersionId);
  const filteredItems = items.filter((item) => (mediaFilter === "ALL" || item.media_kind === mediaFilter) && (reviewFilter === "PENDING" ? item.decision !== "APPROVED" || Boolean(item.is_stale) : reviewFilter === "STALE" ? Boolean(item.is_stale) : true));
  const filteredSelectedIndex = filteredItems.findIndex((item) => item.media_version_id === selectedVersionId);
  const visibleItems = progressiveSlice(filteredItems, visibleCount, filteredSelectedIndex);
  const selectedMediaKind = selectedItem?.media_kind ?? String(context?.media_version.media_kind ?? "");
  const selectedStage = selectedItem?.stage ?? String(context?.media_version.stage ?? "");
  const videoCandidates = items.filter((item) => item.media_kind === "VIDEO" && item.stage === selectedStage).slice(0, 8);
  const supportsThumbnail = (mediaKind: string | undefined) => mediaKind === "IMAGE" || mediaKind === "VIDEO";
  const selectionType = selectedStage === "FORMAL" ? "FORMAL_SELECTION" : selectedStage === "PROXY" ? "PROXY_WINNER" : "KEYFRAME";
  const currentSelection = context?.selections.find((item) => item["media_version_id"] === selectedVersionId && item["selection_type"] === selectionType);
  const currentApproval = context?.reviews.find((item) => item["decision"] === "APPROVED" && !item["is_stale"]);
  const latestMachineCheck = context?.machine_checks[0];
  const machineResults = Array.isArray(latestMachineCheck?.["results"]) ? latestMachineCheck["results"] as Array<Record<string, unknown>> : [];
  const audioQcPassed = selectedMediaKind !== "AUDIO" || latestMachineCheck?.["status"] === "PASS";
  const templateCodeForItem = (item: ReviewInboxItem) => item.media_kind === "AUDIO" ? "audio_mix" : item.media_kind === "VIDEO" && item.stage === "FORMAL" ? "formal_video" : item.media_kind === "VIDEO" ? "proxy_video" : "image_asset";
  const batchItems = items.filter((item) => batchSelectedIds.has(item.media_version_id));
  const batchTemplate = batchItems.length > 0 ? templates.find((template) => template.code === templateCodeForItem(batchItems[0])) : undefined;
  const batchCompatible = Boolean(batchTemplate) && batchItems.every((item) => templateCodeForItem(item) === batchTemplate?.code && item.project_id === batchItems[0]?.project_id);
  const batchChecksComplete = batchTemplate?.items.every((item) => Boolean(batchChecks[item.id])) ?? false;
  useEffect(() => { setChecks({}); setDecision("APPROVED"); setReviewComment(""); setCompareVersionId(null); setPlaybackRate(1); setLoopPreview(false); setMutedPreview(false); setSyncVideoIds(selectedVersionId && selectedMediaKind === "VIDEO" ? [selectedVersionId] : []); syncVideoRefs.current = {}; }, [selectedVersionId, selectedMediaKind]);
  useEffect(() => { if (videoRef.current) videoRef.current.playbackRate = playbackRate; }, [playbackRate, selectedVersionId]);
  useEffect(() => { setVisibleCount(INITIAL_LIST_WINDOW); }, [items]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!selectedVersionId || (event.target as HTMLElement | null)?.closest("input,select,textarea,button")) return;
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      const next = selectedIndex + (event.key === "ArrowRight" ? 1 : -1);
      if (next >= 0 && next < items.length) { event.preventDefault(); onSelect(items[next].media_version_id); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, onSelect, selectedIndex, selectedVersionId]);
  const requiredComplete = context?.template.items.every((item) => !item.required || Boolean(checks[item.id])) ?? false;
  const missingRequired = currentApproval ? [] : context?.template.items.filter((item) => item.required && !checks[item.id]) ?? [];
  const submitCurrentReview = () => {
    if (!context || !selectedVersionId) return;
    let comment = reviewComment.trim();
    if (decision === "REJECTED" && !comment) {
      comment = window.prompt("请输入拒绝原因")?.trim() ?? "";
      if (!comment) return;
      setReviewComment(comment);
    }
    onSubmit(selectedVersionId, {
      template_version_id: context.template.id,
      decision,
      expected_subject_revision: context.subject_revision,
      checks: context.template.items.map((item) => ({ item_id: item.id, result: checks[item.id] })),
      comment: comment || undefined,
    });
  };
  const toggleBatchItem = (mediaVersionId: string) => {
    setBatchNotice(null); setBatchError(null); setBatchPlan(null);
    setBatchSelectedIds((current) => { const next = new Set(current); if (next.has(mediaVersionId)) next.delete(mediaVersionId); else next.add(mediaVersionId); return next; });
  };
  const preflightBatch = async () => {
    if (!batchCompatible || batchItems.length === 0 || !batchTemplate) return;
    setBatchBusy(true); setBatchError(null); setBatchNotice(null);
    try {
      const result = await preflightReviewBatch(batchItems[0].project_id, batchItems.map((item) => ({ media_version_id: item.media_version_id, template_version_id: batchTemplate.id })));
      setBatchPlan(result.plan);
      setBatchChecks(Object.fromEntries(batchTemplate.items.map((item) => [item.id, "PASS" as const])));
    } catch (error) { setBatchError(String(error)); }
    finally { setBatchBusy(false); }
  };
  const commitBatch = async () => {
    if (!batchPlan || !batchTemplate || !batchChecksComplete || (batchDecision === "REJECTED" && !batchComment.trim())) return;
    setBatchBusy(true); setBatchError(null); setBatchNotice(null);
    try {
      const result = await commitReviewBatch(batchPlan.plan_token, { decision: batchDecision, checks: batchTemplate.items.map((item) => ({ item_id: item.id, result: batchChecks[item.id] })), comment: batchComment.trim() || undefined });
      setBatchNotice(`批量审核已提交：${result.result.items.length} 项，计划 ${result.result.plan_id.slice(0, 12)}。`);
      setBatchPlan(null); setBatchSelectedIds(new Set());
    } catch (error) { setBatchError(String(error)); }
    finally { setBatchBusy(false); }
  };
  const toggleSyncVideo = (mediaVersionId: string) => {
    setSyncVideoIds((current) => current.includes(mediaVersionId) ? current.filter((id) => id !== mediaVersionId) : current.length < 4 ? [...current, mediaVersionId] : current);
  };
  const syncVideoAction = (action: "play" | "pause") => {
    syncVideoIds.forEach((id) => { const video = syncVideoRefs.current[id]; if (!video) return; if (action === "play") void video.play().catch(() => undefined); else video.pause(); });
  };
  const syncVideoStep = (delta: number) => {
    const current = syncVideoIds.map((id) => syncVideoRefs.current[id]?.currentTime ?? 0)[0] ?? 0;
    syncVideoIds.forEach((id) => { const video = syncVideoRefs.current[id]; if (video) video.currentTime = Math.max(0, current + delta); });
  };
  return <section className="panel"><div className="panel-heading"><div><p className="eyebrow">G4 REVIEW INBOX</p><h3>媒体版本审核与选择</h3></div><span className="status-pill">{items.length} 个待处理</span></div>
    <p className="muted">审核模板、机器检查和选择指针均来自本地 API；selection 与 approval 分离，旧 revision 会显示 stale。</p>
    <div className="review-filters" role="search" aria-label="审核收件箱筛选"><label>媒体类型<select value={mediaFilter} onChange={(event) => { setMediaFilter(event.target.value); setVisibleCount(INITIAL_LIST_WINDOW); }}><option value="ALL">全部</option><option value="IMAGE">IMAGE</option><option value="VIDEO">VIDEO</option><option value="AUDIO">AUDIO</option></select></label><label>审核状态<select value={reviewFilter} onChange={(event) => { setReviewFilter(event.target.value); setVisibleCount(INITIAL_LIST_WINDOW); }}><option value="PENDING">待处理 / 非批准</option><option value="STALE">STALE</option><option value="ALL">全部历史</option></select></label><span className="muted">显示 {filteredItems.length}/{items.length}</span></div>
    <section className="batch-review-panel" aria-label="批量审核保护"><div className="panel-heading"><div><p className="eyebrow">EXPLICIT BATCH REVIEW</p><strong>批量审核保护</strong></div><span className="status-pill">已选 {batchItems.length} 项</span></div><p className="muted">必须显式勾选；同一项目、同一审核模板；先预检锁定 revision，再提交，不允许静默部分成功。</p><div className="batch-review-picker">{items.slice(0, 40).map((item) => <label key={item.media_version_id}><input type="checkbox" checked={batchSelectedIds.has(item.media_version_id)} onChange={() => toggleBatchItem(item.media_version_id)} /><span>{item.stage} · {item.media_kind} · {item.media_version_id.slice(0, 10)}</span></label>)}</div>{batchItems.length > 0 && !batchCompatible && <p className="review-guidance">批量项必须属于同一项目且使用同一模板（不能混合图片、代理视频、正式视频或音频）。</p>}<div className="action-row"><button className="secondary" onClick={() => void preflightBatch()} disabled={!batchCompatible || batchBusy}>{batchBusy ? "处理中…" : batchPlan ? "重新预检" : "预检批量审核"}</button>{batchPlan && <span className="ok-text">预检通过 · {batchPlan.items.length} 项 · 5 分钟内有效</span>}</div>{batchPlan && batchTemplate && <div className="batch-review-submit"><div className="review-checklist">{batchTemplate.items.map((item) => <fieldset className="review-check" key={item.id}><legend>{item.label} *</legend><label><input type="radio" name={`batch-check-${item.id}`} checked={batchChecks[item.id] === "PASS"} onChange={() => setBatchChecks((value) => ({ ...value, [item.id]: "PASS" }))} />通过</label><label><input type="radio" name={`batch-check-${item.id}`} checked={batchChecks[item.id] === "FAIL"} onChange={() => setBatchChecks((value) => ({ ...value, [item.id]: "FAIL" }))} />不通过</label></fieldset>)}</div><label>批量决定<select value={batchDecision} onChange={(event) => setBatchDecision(event.target.value as typeof batchDecision)}><option value="APPROVED">批准</option><option value="NEEDS_CHANGES">需要修改</option><option value="REJECTED">拒绝</option></select></label>{batchDecision === "REJECTED" && <label>拒绝原因<textarea value={batchComment} onChange={(event) => setBatchComment(event.target.value)} placeholder="填写批量拒绝原因" /></label>}<button className="primary-action" onClick={() => void commitBatch()} disabled={batchBusy || !batchChecksComplete || (batchDecision === "REJECTED" && !batchComment.trim())}>{batchBusy ? "提交中…" : "提交批量审核"}</button></div>}{batchError && <p className="inline-error" role="alert">批量审核失败：{batchError}</p>}{batchNotice && <p className="review-success" role="status">{batchNotice}</p>}</section>
    {selectedMediaKind === "VIDEO" && videoCandidates.length > 0 && <section className="video-sync-compare" aria-label="视频候选同步比较"><div className="panel-heading"><div><p className="eyebrow">SYNC COMPARE</p><strong>多候选同步比较（最多 4 路）</strong></div><span className="muted">{syncVideoIds.length} 路</span></div><div className="video-sync-picker">{videoCandidates.map((item) => <label key={item.media_version_id}><input type="checkbox" checked={syncVideoIds.includes(item.media_version_id)} disabled={!syncVideoIds.includes(item.media_version_id) && syncVideoIds.length >= 4} onChange={() => toggleSyncVideo(item.media_version_id)} /><span>{item.stage} · {item.media_version_id.slice(0, 10)}</span></label>)}</div><div className="video-sync-controls"><button className="secondary" onClick={() => syncVideoAction("play")} disabled={syncVideoIds.length < 2}>同步播放</button><button className="secondary" onClick={() => syncVideoAction("pause")} disabled={syncVideoIds.length < 2}>同步暂停</button><button className="secondary" onClick={() => syncVideoStep(-0.1)} disabled={syncVideoIds.length < 2}>全部短退 0.1s</button><button className="secondary" onClick={() => syncVideoStep(0.1)} disabled={syncVideoIds.length < 2}>全部短进 0.1s</button></div><div className="video-sync-grid">{syncVideoIds.map((id) => <figure key={id}><video ref={(node) => { syncVideoRefs.current[id] = node; }} controls preload="metadata" muted src={`/api/v1/media-versions/${encodeURIComponent(id)}/content`} /><figcaption>{id.slice(0, 16)} · Range</figcaption></figure>)}</div></section>}
    <div className="review-layout"><div className="review-list">{filteredItems.length === 0 ? <p className="empty-state">当前筛选没有待审核媒体版本。</p> : <>{visibleItems.map((item) => <button className={`project-row review-row progressive-row${item.media_version_id === selectedVersionId ? " selected" : ""}`} key={item.media_version_id} onClick={() => onSelect(item.media_version_id)}>{supportsThumbnail(item.media_kind) ? <img src={`/api/v1/media-versions/${encodeURIComponent(item.media_version_id)}/thumbnail?size=small&frame=poster`} alt="" width="80" height="45" loading="lazy" decoding="async" /> : <span className="media-kind-placeholder" aria-hidden="true">{item.media_kind}</span>}<span><strong>{item.stage} · {item.media_kind}</strong><small>{item.media_version_id.slice(0, 12)} · {item.decision ?? "未审核"}</small></span><span className={item.is_stale ? "blocker-text" : "status-pill"}>{item.is_stale ? "STALE" : "待处理"}</span></button>)}{visibleItems.length < filteredItems.length && <button className="secondary list-more" onClick={() => setVisibleCount((count) => count + INITIAL_LIST_WINDOW)}>继续显示审核项（{visibleItems.length}/{filteredItems.length}）</button>}</>}</div>
      <div className="review-detail">{!context ? <p className="empty-state">选择一个媒体版本读取审核上下文。</p> : <>{selectedMediaKind === "IMAGE" && <div className="image-compare-toolbar" aria-label="图片缩略图比较"><label>比较对象<select aria-label="图片比较对象" value={compareVersionId ?? ""} onChange={(event) => setCompareVersionId(event.target.value || null)}><option value="">不比较</option>{items.filter((item) => item.media_kind === "IMAGE" && item.media_version_id !== selectedVersionId).map((item) => <option key={item.media_version_id} value={item.media_version_id}>{item.stage} · {item.media_version_id.slice(0, 12)}</option>)}</select></label><button className="secondary" onClick={() => setReferencePinned((value) => !value)}>{referencePinned ? "取消置顶参考图" : "置顶当前参考图"}</button><span className="muted">←/→ 键切换候选；仅加载派生缩略图</span></div>}{selectedMediaKind === "IMAGE" && compareVersionId ? <div className="image-compare-grid" aria-label="图片 A/B 缩略图比较"><figure><img src={`/api/v1/media-versions/${encodeURIComponent(referencePinned ? compareVersionId : selectedVersionId ?? "")}/thumbnail?size=small&frame=poster`} alt="参考图缩略图" width="320" height="180" decoding="async" /><figcaption>参考 · {referencePinned ? compareVersionId.slice(0, 12) : "当前"}</figcaption></figure><figure><img src={`/api/v1/media-versions/${encodeURIComponent(referencePinned ? selectedVersionId ?? "" : compareVersionId)}/thumbnail?size=small&frame=poster`} alt="比较图缩略图" width="320" height="180" decoding="async" /><figcaption>比较 · {(referencePinned ? selectedVersionId : compareVersionId)?.slice(0, 12)}</figcaption></figure></div> : selectedMediaKind === "VIDEO" ? <div className="review-video-preview"><video ref={videoRef} controls preload="metadata" loop={loopPreview} muted={mutedPreview} src={`/api/v1/media-versions/${encodeURIComponent(selectedVersionId ?? "")}/content`} /><div className="video-preview-controls" aria-label="视频预览控制"><button className="secondary" onClick={() => { if (videoRef.current) videoRef.current.currentTime = Math.max(0, videoRef.current.currentTime - 0.1); }}>短退 0.1s</button><button className="secondary" onClick={() => { if (videoRef.current) videoRef.current.currentTime += 0.1; }}>短进 0.1s</button><button className="secondary" onClick={() => setLoopPreview((value) => !value)}>{loopPreview ? "关闭循环" : "循环播放"}</button><button className="secondary" onClick={() => setMutedPreview((value) => !value)}>{mutedPreview ? "打开声音" : "静音"}</button><label>倍速<select aria-label="播放倍速" value={playbackRate} onChange={(event) => setPlaybackRate(Number(event.target.value))}><option value="0.5">0.5×</option><option value="1">1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label><span className="muted">Range 本地预览 · 原片不加载到内存</span></div></div> : <div className="review-preview">{selectedMediaKind === "AUDIO" ? <img src={`/api/v1/media-versions/${encodeURIComponent(selectedVersionId ?? "")}/waveform`} alt="当前音频的 640 像素派生波形" width="640" height="128" decoding="async" /> : supportsThumbnail(selectedMediaKind) ? <img src={`/api/v1/media-versions/${encodeURIComponent(selectedVersionId ?? "")}/thumbnail?size=small&frame=poster`} alt="当前审核版本缩略图" width="320" height="180" decoding="async" /> : <strong>{selectedMediaKind || "MEDIA"} 暂无视觉缩略图</strong>}<span>{selectedMediaKind === "AUDIO" ? "固定请求 640×128 派生波形；不加载原音频" : supportsThumbnail(selectedMediaKind) ? "固定请求 320px small 缩略图；此处不加载原片" : "不探测或下载原始媒体"}</span></div>}<div className="review-meta image-metadata"><span>媒体版本：{selectedVersionId?.slice(0, 16)}</span><span>阶段：{selectedStage}</span><span>类型：{selectedMediaKind}</span><span>源 hash：{String(context.media_version.sha256 ?? "未提供").slice(0, 16)}…</span></div><div className="panel-heading"><div><p className="eyebrow">{context.template.code}</p><h3>{String(context.media_version.stage)} · {String(context.media_version.mime_type)}</h3></div><button className="secondary" onClick={() => selectedVersionId && onPromote(selectedVersionId, selectionType)} disabled={!selectedVersionId || selecting || Boolean(currentSelection)}>{selecting ? "保存中…" : currentSelection ? `已选择 ${selectionType}` : `选择为 ${selectionType}`}</button></div>{selectedMediaKind === "AUDIO" && <section className="audio-qc-panel"><div className="panel-heading"><strong>LUFS / True Peak / 削波机器检查</strong><button className="secondary" onClick={() => selectedVersionId && onMachineCheck(selectedVersionId)} disabled={!selectedVersionId || machineChecking}>{machineChecking ? "检查中…" : "运行音频 QC"}</button></div><div className="review-meta">{machineResults.filter((item) => ["integrated_loudness", "true_peak", "peak", "clipping"].includes(String(item["item_id"]))).map((item) => <span key={String(item["item_id"])}>{String(item["item_id"])}：{String(item["result"])} · {JSON.stringify(item["details"] ?? {})}</span>)}</div>{!audioQcPassed && <p className="review-guidance">最新音频 QC 未 PASS，正式批准被领域规则阻塞。</p>}{machineCheckError && <p className="inline-error" role="alert">音频 QC 失败：{machineCheckError}</p>}</section>}{currentApproval && <p className="review-success" role="status">当前版本已批准，审核记录 {String(currentApproval["id"]).slice(0, 12)}。如需改变结论，请先撤回当前审核；重复点击不会新增记录。</p>}<div className="review-checklist">{context.template.items.map((item) => <fieldset className="review-check" key={item.id} disabled={Boolean(currentApproval)}><legend>{item.label}{item.required ? " *" : ""}</legend><label><input type="radio" name={`check-${item.id}`} checked={checks[item.id] === "PASS"} onChange={() => setChecks((value) => ({ ...value, [item.id]: "PASS" }))} />通过</label><label><input type="radio" name={`check-${item.id}`} checked={checks[item.id] === "FAIL"} onChange={() => setChecks((value) => ({ ...value, [item.id]: "FAIL" }))} />不通过</label></fieldset>)}</div>{missingRequired.length > 0 && <p className="review-guidance" aria-live="polite">还需完成 {missingRequired.length} 个必填检查：{missingRequired.map((item) => item.label).join("、")}。选择“批准”不会自动提交。</p>}<div className="review-submit"><label htmlFor="review-decision">审核决定<select id="review-decision" value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)} disabled={Boolean(currentApproval)}><option value="APPROVED">批准</option><option value="NEEDS_CHANGES">需要修改</option><option value="REJECTED">拒绝</option></select></label><button className="primary-action" onClick={submitCurrentReview} disabled={Boolean(currentApproval) || !requiredComplete || submitting || (decision === "APPROVED" && !audioQcPassed)}>{currentApproval ? "已批准" : submitting ? "提交中…" : "提交审核"}</button></div>{submitError && <p className="inline-error" role="alert">提交失败：{submitError}</p>}{submitSucceeded && context.reviews.length > 0 && <p className="review-success" role="status">审核已保存：{String(context.reviews[0]?.decision ?? decision)}。选择指针仍需通过上方独立按钮确认。</p>}<div className="review-meta"><span>机器检查：{String(context.machine_checks[0]?.status ?? "未运行")}</span><span>审核记录：{context.reviews.length}</span><span>选择指针：{currentSelection ? selectionType : "未选择"}</span><span>subject revision：{context.subject_revision}</span><span>候选模板：{templates.length}</span></div></>}</div></div>
    {context && selectedMediaKind === "VIDEO" && Number(context.media_version.duration_ms) > 0 && <VideoAnnotations mediaVersionId={selectedVersionId ?? ""} durationMs={Number(context.media_version.duration_ms)} />}
  </section>;
}

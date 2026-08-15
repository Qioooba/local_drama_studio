import { useEffect, useState } from "react";
import { getReviewContext, submitReview, type ReviewInboxItem, type ReviewTemplate } from "../../generated/api";
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
  onSubmit: (mediaVersionId: string, payload: Parameters<typeof submitReview>[1]) => void;
  submitting: boolean;
  submitError: string | null;
  submitSucceeded: boolean;
}) {
  const [checks, setChecks] = useState<Record<string, "PASS" | "FAIL">>({});
  const [decision, setDecision] = useState<"APPROVED" | "REJECTED" | "NEEDS_CHANGES">("APPROVED");
  const [visibleCount, setVisibleCount] = useState(INITIAL_LIST_WINDOW);
  const selectedItem = items.find((item) => item.media_version_id === selectedVersionId);
  const selectedIndex = items.findIndex((item) => item.media_version_id === selectedVersionId);
  const visibleItems = progressiveSlice(items, visibleCount, selectedIndex);
  const selectedMediaKind = selectedItem?.media_kind ?? String(context?.media_version.media_kind ?? "");
  const selectedStage = selectedItem?.stage ?? String(context?.media_version.stage ?? "");
  const supportsThumbnail = (mediaKind: string | undefined) => mediaKind === "IMAGE" || mediaKind === "VIDEO";
  const selectionType = selectedStage === "FORMAL" ? "FORMAL_SELECTION" : selectedStage === "PROXY" ? "PROXY_WINNER" : "KEYFRAME";
  const currentSelection = context?.selections.find((item) => item["media_version_id"] === selectedVersionId && item["selection_type"] === selectionType);
  const currentApproval = context?.reviews.find((item) => item["decision"] === "APPROVED" && !item["is_stale"]);
  useEffect(() => { setChecks({}); setDecision("APPROVED"); }, [selectedVersionId]);
  useEffect(() => { setVisibleCount(INITIAL_LIST_WINDOW); }, [items]);
  const requiredComplete = context?.template.items.every((item) => !item.required || Boolean(checks[item.id])) ?? false;
  const missingRequired = currentApproval ? [] : context?.template.items.filter((item) => item.required && !checks[item.id]) ?? [];
  const submitCurrentReview = () => {
    if (!context || !selectedVersionId) return;
    onSubmit(selectedVersionId, {
      template_version_id: context.template.id,
      decision,
      expected_subject_revision: context.subject_revision,
      checks: context.template.items.map((item) => ({ item_id: item.id, result: checks[item.id] })),
    });
  };
  return <section className="panel"><div className="panel-heading"><div><p className="eyebrow">G4 REVIEW INBOX</p><h3>媒体版本审核与选择</h3></div><span className="status-pill">{items.length} 个待处理</span></div>
    <p className="muted">审核模板、机器检查和选择指针均来自本地 API；selection 与 approval 分离，旧 revision 会显示 stale。</p>
    <div className="review-layout"><div className="review-list">{items.length === 0 ? <p className="empty-state">当前项目没有待审核媒体版本。</p> : <>{visibleItems.map((item) => <button className={`project-row review-row progressive-row${item.media_version_id === selectedVersionId ? " selected" : ""}`} key={item.media_version_id} onClick={() => onSelect(item.media_version_id)}>{supportsThumbnail(item.media_kind) ? <img src={`/api/v1/media-versions/${encodeURIComponent(item.media_version_id)}/thumbnail?size=small&frame=poster`} alt="" width="80" height="45" loading="lazy" decoding="async" /> : <span className="media-kind-placeholder" aria-hidden="true">{item.media_kind}</span>}<span><strong>{item.stage} · {item.media_kind}</strong><small>{item.media_version_id.slice(0, 12)} · {item.decision ?? "未审核"}</small></span><span className={item.is_stale ? "blocker-text" : "status-pill"}>{item.is_stale ? "STALE" : "待处理"}</span></button>)}{visibleItems.length < items.length && <button className="secondary list-more" onClick={() => setVisibleCount((count) => count + INITIAL_LIST_WINDOW)}>继续显示审核项（{visibleItems.length}/{items.length}）</button>}</>}</div>
      <div className="review-detail">{!context ? <p className="empty-state">选择一个媒体版本读取审核上下文。</p> : <><div className="review-preview">{supportsThumbnail(selectedMediaKind) ? <img src={`/api/v1/media-versions/${encodeURIComponent(selectedVersionId ?? "")}/thumbnail?size=small&frame=poster`} alt="当前审核版本缩略图" width="320" height="180" decoding="async" /> : <strong>{selectedMediaKind || "MEDIA"} 暂无视觉缩略图</strong>}<span>{supportsThumbnail(selectedMediaKind) ? "固定请求 320px small 缩略图；此处不加载原片" : "不探测或下载原始媒体"}</span></div><div className="panel-heading"><div><p className="eyebrow">{context.template.code}</p><h3>{String(context.media_version.stage)} · {String(context.media_version.mime_type)}</h3></div><button className="secondary" onClick={() => selectedVersionId && onPromote(selectedVersionId, selectionType)} disabled={!selectedVersionId || selecting || Boolean(currentSelection)}>{selecting ? "保存中…" : currentSelection ? `已选择 ${selectionType}` : `选择为 ${selectionType}`}</button></div>{currentApproval && <p className="review-success" role="status">当前版本已批准，审核记录 {String(currentApproval["id"]).slice(0, 12)}。如需改变结论，请先撤回当前审核；重复点击不会新增记录。</p>}<div className="review-checklist">{context.template.items.map((item) => <fieldset className="review-check" key={item.id} disabled={Boolean(currentApproval)}><legend>{item.label}{item.required ? " *" : ""}</legend><label><input type="radio" name={`check-${item.id}`} checked={checks[item.id] === "PASS"} onChange={() => setChecks((value) => ({ ...value, [item.id]: "PASS" }))} />通过</label><label><input type="radio" name={`check-${item.id}`} checked={checks[item.id] === "FAIL"} onChange={() => setChecks((value) => ({ ...value, [item.id]: "FAIL" }))} />不通过</label></fieldset>)}</div>{missingRequired.length > 0 && <p className="review-guidance" aria-live="polite">还需完成 {missingRequired.length} 个必填检查：{missingRequired.map((item) => item.label).join("、")}。选择“批准”不会自动提交。</p>}<div className="review-submit"><label htmlFor="review-decision">审核决定<select id="review-decision" value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)} disabled={Boolean(currentApproval)}><option value="APPROVED">批准</option><option value="NEEDS_CHANGES">需要修改</option><option value="REJECTED">拒绝</option></select></label><button className="primary-action" onClick={submitCurrentReview} disabled={Boolean(currentApproval) || !requiredComplete || submitting}>{currentApproval ? "已批准" : submitting ? "提交中…" : "提交审核"}</button></div>{submitError && <p className="inline-error" role="alert">提交失败：{submitError}</p>}{submitSucceeded && context.reviews.length > 0 && <p className="review-success" role="status">审核已保存：{String(context.reviews[0]?.decision ?? decision)}。选择指针仍需通过上方独立按钮确认。</p>}<div className="review-meta"><span>机器检查：{String(context.machine_checks[0]?.status ?? "未运行")}</span><span>审核记录：{context.reviews.length}</span><span>选择指针：{currentSelection ? selectionType : "未选择"}</span><span>subject revision：{context.subject_revision}</span><span>候选模板：{templates.length}</span></div></>}</div></div>
    {context && selectedMediaKind === "VIDEO" && Number(context.media_version.duration_ms) > 0 && <VideoAnnotations mediaVersionId={selectedVersionId ?? ""} durationMs={Number(context.media_version.duration_ms)} />}
  </section>;
}

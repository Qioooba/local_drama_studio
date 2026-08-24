import { useEffect, useMemo, useState } from "react";
import { submitEpisodeRenderReview, type ReviewTemplate } from "../../generated/api";

const EPISODE_REVIEW_PHRASES = ["节奏需要调整", "字幕与对白不同步", "响度或混音需修正", "发现黑帧或异常画面", "头尾或平台规格不符合"];

function appendReviewPhrase(current: string, phrase: string) {
  const normalized = current.trim();
  if (!normalized) return phrase;
  if (normalized.includes(phrase)) return current;
  return `${normalized}；${phrase}`;
}

export function EpisodeReviewPanel({ render, templates, onChanged }: { render: Record<string, unknown> | null; templates: ReviewTemplate[]; onChanged?: () => void }) {
  const template = useMemo(() => templates.find((item) => item.subject_type === "EPISODE_RENDER_VERSION"), [templates]);
  const [checks, setChecks] = useState<Record<string, "PASS" | "FAIL">>({});
  const [decision, setDecision] = useState<"APPROVED" | "REJECTED" | "NEEDS_CHANGES">("APPROVED");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setChecks({}); setDecision("APPROVED"); setComment(""); setMessage(null); setError(null); }, [render?.id, template?.id]);
  useEffect(() => {
    if (!message) return undefined;
    const timeout = window.setTimeout(() => setMessage(null), 3_000);
    return () => window.clearTimeout(timeout);
  }, [message]);
  if (!render) return <section className="panel"><p className="empty-state">暂无整集渲染版本；先从冻结 TimelineRevision 登记渲染。</p></section>;
  if (!template) return <section className="panel"><p className="empty-state">整集审核模板尚未初始化。</p></section>;
  const required = template.items.filter((item) => item.required);
  const complete = required.every((item) => checks[item.id]);
  const failures = required.filter((item) => checks[item.id] === "FAIL");
  const missing = required.filter((item) => !checks[item.id]);
  const submitBlockReason = missing.length
    ? `还需完成 ${missing.length} 个必填检查：${missing.map((item) => item.label).join("、")}`
    : decision === "APPROVED" && failures.length
      ? `存在 ${failures.length} 个未通过检查项，不能批准整集渲染`
      : decision === "REJECTED" && !comment.trim()
        ? "拒绝整集渲染必须填写原因"
        : null;
  const review = async () => {
    if (!complete) { setError("请完成全部整集审核检查项"); return; }
    if (decision === "APPROVED" && failures.length) { setError("存在未通过检查项，不能批准整集渲染"); return; }
    if (decision === "REJECTED" && !comment.trim()) { setError("拒绝整集渲染必须填写原因"); return; }
    setBusy(true); setError(null); setMessage(null);
    try { const result = await submitEpisodeRenderReview(String(render.id), { template_version_id: template.id, decision, expected_subject_revision: Number(render.revision ?? 1), checks: required.map((item) => ({ item_id: item.id, result: checks[item.id] })), comment: comment.trim() || undefined }); setMessage(`整集审核已保存：${String(result.review.decision ?? decision)}`); onChanged?.(); }
    catch (caught) { setError(String(caught)); }
    finally { setBusy(false); }
  };
  const renderId = encodeURIComponent(String(render.id));
  return <section className="panel episode-review-panel" aria-labelledby="episode-review-title"><div className="panel-heading"><div><p className="eyebrow">成片质检</p><h3 id="episode-review-title">整集渲染审核</h3></div><span className="status-pill neutral">逐项确认</span></div><p className="muted">当前 render 只读预览；画面、对白、字幕、响度、节奏、黑帧、头尾和平台规格必须逐项记录，人工批准不会被机器状态替代。页面只加载派生缩略海报；点击播放后才按需读取本机 Range 视频。</p><video className="episode-review-video" controls preload="none" poster={`/api/v1/episode-renders/${renderId}/thumbnail?size=medium&frame=poster`} src={`/api/v1/episode-renders/${renderId}/content`} /><div className="review-checklist">{required.map((item) => <fieldset className="review-check" key={item.id}><legend>{item.label} *</legend><label><input type="radio" name={`episode-check-${item.id}`} checked={checks[item.id] === "PASS"} onChange={() => setChecks((current) => ({ ...current, [item.id]: "PASS" }))} />通过</label><label><input type="radio" name={`episode-check-${item.id}`} checked={checks[item.id] === "FAIL"} onChange={() => setChecks((current) => ({ ...current, [item.id]: "FAIL" }))} />不通过</label></fieldset>)}</div><div className="review-submit"><label>审核决定<select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)}><option value="APPROVED">批准</option><option value="NEEDS_CHANGES">需要修改</option><option value="REJECTED">拒绝</option></select></label><label>备注 / 拒绝原因<textarea value={comment} onChange={(event) => setComment(event.target.value)} placeholder="记录节奏、头尾、黑帧或平台规格问题…" /></label><div className="action-row" aria-label="常用审核备注">{EPISODE_REVIEW_PHRASES.map((phrase) => <button key={phrase} type="button" className="secondary" onClick={() => setComment((current) => appendReviewPhrase(current, phrase))}>{phrase}</button>)}</div>{submitBlockReason && <p id="episode-review-guidance" className="review-guidance">{submitBlockReason}。</p>}<button className="primary-action" type="button" aria-describedby={submitBlockReason ? "episode-review-guidance" : undefined} onClick={() => void review()} disabled={busy || Boolean(submitBlockReason)}>{busy ? "提交中…" : "提交整集审核"}</button></div>{message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">整集审核失败：{error}</p>}</section>;
}

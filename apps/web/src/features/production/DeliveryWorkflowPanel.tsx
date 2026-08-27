import { useEffect, useState } from "react";
import { getBackgroundOperation, listBrandControls, listEpisodeDeliveryPackages, reviewDeliveryPackage, submitDeliveryPackageBuild, submitEpisodeCompose, verifyDeliveryPackage, withdrawDeliveryPackage, type DeliveryPackage } from "../../generated/api";
import { statusLabel } from "../shared/optionLabels";
import { routes } from "../../app/routeRegistry";

type DeliveryWorkflowFocus = "COMPOSE" | "REVIEW" | "PACKAGE";

export function DeliveryWorkflowPanel({ projectId, episodeId, timelineRevisionId, renderId, targetVersionId, deliveryId, onRenderCreated, onDeliveryCreated, onChanged, focus }: { projectId?: string; episodeId: string; timelineRevisionId: string | null; renderId: string | null; targetVersionId: string | null; deliveryId: string | null; onRenderCreated?: (renderId: string) => void; onDeliveryCreated?: (deliveryId: string) => void; onChanged?: () => void; focus?: DeliveryWorkflowFocus }) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [composeJobId, setComposeJobId] = useState<string | null>(null);
  const [composeJobState, setComposeJobState] = useState<string | null>(null);
  const [deliveryJobId, setDeliveryJobId] = useState<string | null>(null);
  const [deliveryJobState, setDeliveryJobState] = useState<string | null>(null);
  const [history, setHistory] = useState<DeliveryPackage[]>([]);
  const [brandControls, setBrandControls] = useState<{ brand_kits: Array<Record<string, unknown>>; watermark_profiles: Array<Record<string, unknown>>; compliance_policies: Array<Record<string, unknown>> }>({ brand_kits: [], watermark_profiles: [], compliance_policies: [] });
  const [brandKitId, setBrandKitId] = useState("");
  const [watermarkProfileId, setWatermarkProfileId] = useState("");
  const [compliancePolicyId, setCompliancePolicyId] = useState("");
  const [noteContext, setNoteContext] = useState<null | { purpose: "review"; reviewerType: "HUMAN" | "PLATFORM" } | { purpose: "withdraw" }>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const refreshHistory = async () => {
    try { setHistory((await listEpisodeDeliveryPackages(episodeId)).items); } catch (caught) { setError(`读取交付历史失败：${String(caught)}`); }
  };
  useEffect(() => {
    if (!focus || focus === "PACKAGE") void refreshHistory();
  }, [episodeId, focus]);
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    void listBrandControls(projectId).then((result) => {
      if (cancelled) return;
      setBrandControls(result);
      const active = (items: Array<Record<string, unknown>>) => String((items.find((item) => item.status === "ACTIVE") ?? items[0])?.id ?? "NONE");
      setBrandKitId(active(result.brand_kits));
      setWatermarkProfileId(active(result.watermark_profiles));
      setCompliancePolicyId(active(result.compliance_policies));
    }).catch((caught) => { if (!cancelled) setError(`读取品牌与合规版本失败：${String(caught)}`); });
    return () => { cancelled = true; };
  }, [projectId]);
  useEffect(() => {
    if (!success) return;
    const timer = window.setTimeout(() => setSuccess(null), 3_000);
    return () => window.clearTimeout(timer);
  }, [success]);
  useEffect(() => {
    setComposeJobId(null);
    setComposeJobState(null);
  }, [timelineRevisionId]);
  useEffect(() => {
    setDeliveryJobId(null);
    setDeliveryJobState(null);
  }, [renderId, targetVersionId]);
  useEffect(() => {
    const activeOperations = [
      composeJobId && !["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(String(composeJobState)) ? { kind: "compose", id: composeJobId } : null,
      deliveryJobId && !["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(String(deliveryJobState)) ? { kind: "delivery", id: deliveryJobId } : null,
    ].filter((item): item is { kind: string; id: string } => Boolean(item));
    if (!activeOperations.length) return;
    let cancelled = false;
    const poll = async () => {
      for (const operation of activeOperations) {
        try {
          const result = await getBackgroundOperation(operation.id);
          if (cancelled) return;
          const state = result.job.state;
          if (operation.kind === "compose") setComposeJobState(state);
          else setDeliveryJobState(state);
          if (state === "SUCCEEDED" && result.result && operation.kind === "compose" && result.result_type === "EPISODE_RENDER") {
            const id = typeof result.result.id === "string" ? result.result.id : null;
            if (id) onRenderCreated?.(id);
            setSuccess("整集渲染已完成，可以继续创建交付候选。");
            onChanged?.();
          } else if (state === "SUCCEEDED" && result.result && operation.kind === "delivery" && result.result_type === "DELIVERY") {
            const id = typeof result.result.id === "string" ? result.result.id : null;
            if (id) onDeliveryCreated?.(id);
            setSuccess("交付候选已创建，下一步请校验 manifest 并记录人工审核。");
            onChanged?.();
            void refreshHistory();
          } else if (["FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(state)) {
            setError(`后台${operation.kind === "compose" ? "渲染" : "交付"}任务需要处理：${result.job.last_error_detail_redacted ?? result.job.last_error_code ?? state}。请在任务中心查看并重试。`);
          }
        } catch (caught) {
          if (!cancelled) setError(`读取后台任务进度失败：${String(caught)}`);
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [composeJobId, composeJobState, deliveryJobId, deliveryJobState, onChanged, onDeliveryCreated, onRenderCreated]);
  const review = async (reviewerType: "HUMAN" | "PLATFORM") => {
    if (!deliveryId) { setError("请先创建交付候选"); return; }
    setNoteContext({ purpose: "review", reviewerType });
    setNoteDraft("");
  };
  const withdraw = async () => {
    if (!deliveryId) { setError("请先创建交付候选"); return; }
    setNoteContext({ purpose: "withdraw" });
    setNoteDraft("");
  };
  const confirmNote = async () => {
    if (!noteContext || !deliveryId) return;
    const note = noteDraft.trim();
    if (!note) return;
    setPending(noteContext.purpose === "review" ? noteContext.reviewerType : "withdraw");
    setError(null); setSuccess(null); setNoteContext(null); setNoteDraft("");
    try {
      if (noteContext.purpose === "review") {
        const result = await reviewDeliveryPackage(deliveryId, { reviewer_type: noteContext.reviewerType, decision: "APPROVED", note });
        setSuccess(`${noteContext.reviewerType === "HUMAN" ? "人工" : "平台"}审核已记录：${String(result.delivery[noteContext.reviewerType === "HUMAN" ? "human_review_status" : "platform_review_status"] ?? "APPROVED")}`);
      } else {
        const result = await withdrawDeliveryPackage(deliveryId, note);
        setSuccess(`交付包已撤回：${result.delivery.status} · ${note}`);
      }
      onChanged?.();
      void refreshHistory();
    } catch (caught) { setError(`操作失败：${String(caught)}`); }
    finally { setPending(null); }
  };
  const run = async (action: "render" | "build" | "verify") => {
    setPending(action); setError(null); setSuccess(null);
    try {
      if (action === "render") {
        if (!timelineRevisionId) throw new Error("请先创建并选择冻结 TimelineRevision");
        const result = await submitEpisodeCompose(timelineRevisionId, { force_rerender: false });
        const reusedRenderId = typeof result.render?.id === "string" ? result.render.id : null;
        if (reusedRenderId) {
          const renderStatus = String(result.render?.integrity_status ?? result.render?.status ?? "可用");
          onRenderCreated?.(reusedRenderId);
          setSuccess(`已复用相同时间线的整集渲染 · ${renderStatus}`);
        } else if (result.job?.id) {
          setComposeJobId(result.job.id);
          setComposeJobState(result.job.state);
          setSuccess("整集渲染已进入后台队列。现在可以离开本页，任务完成后再回来继续创建交付候选。");
        } else {
          throw new Error("后台任务未返回可跟踪的任务编号");
        }
      } else if (action === "build") {
        if (!renderId || !targetVersionId) throw new Error("必须同时拥有整集 render 和项目显式交付目标");
        const result = await submitDeliveryPackageBuild({ episode_render_version_id: renderId, target_version_id: targetVersionId, brand_kit_id: brandKitId || "NONE", watermark_profile_id: watermarkProfileId || "NONE", compliance_policy_id: compliancePolicyId || "NONE" });
        setDeliveryJobId(result.job.id);
        setDeliveryJobState(result.job.state);
        setSuccess("交付候选构建已进入后台队列。可以离开本页，完成后再进行 manifest 校验。");
      } else {
        if (!deliveryId) throw new Error("请先创建交付候选");
        const result = await verifyDeliveryPackage(deliveryId);
        setSuccess(`交付 manifest 校验完成：${result.delivery.status}`);
      }
      onChanged?.();
      void refreshHistory();
    } catch (caught) { setError(`操作失败：${String(caught)}`); }
    finally { setPending(null); }
  };
  /** One-click "verify then approve" for solo local creators: machine verify,
   *  then record a human approval with a default shorthand note, keeping the
   *  manual reason entry available as an expanding detail. */
  const verifyAndApprove = async () => {
    if (!deliveryId) { setError("请先创建交付候选"); return; }
    setPending("verify"); setError(null); setSuccess(null);
    let verifiedStatus: string | null = null;
    try {
      const verifyResult = await verifyDeliveryPackage(deliveryId);
      verifiedStatus = verifyResult.delivery.status;
      await reviewDeliveryPackage(deliveryId, {
        reviewer_type: "HUMAN",
        decision: "APPROVED",
        note: "本机创作者一键批准（交付清单与文件指纹校验通过）",
      });
      setSuccess(`已一键验证并批准：${verifiedStatus} · 人工审核已记录。`);
      onChanged?.();
      void refreshHistory();
    } catch (caught) {
      setError(verifiedStatus
        ? `交付文件已完成验证（${statusLabel(verifiedStatus)}），但人工批准未写入：${String(caught)}。请点击“记录人工批准”从当前阶段继续，无需重复构建交付包。`
        : `一键验证并批准失败：${String(caught)}`);
    }
    finally { setPending(null); }
  };
  const title = focus === "COMPOSE" ? "创建整集渲染与交付候选" : focus === "REVIEW" ? "校验并记录交付审核" : focus === "PACKAGE" ? "复验交付包与历史证据" : "整集渲染与本地交付候选";
  const showCompose = !focus || focus === "COMPOSE";
  const showReview = !focus || focus === "REVIEW";
  const showPackage = !focus || focus === "PACKAGE";
  const composeBlocker = !timelineRevisionId
    ? "请先在时间线创建并冻结一个版本，然后返回本步骤。"
    : composeJobId && composeJobState !== "SUCCEEDED"
      ? "整集渲染任务正在后台处理；可到任务中心查看或恢复。"
      : null;
  const buildBlocker = !renderId
    ? "请先完成整集渲染。"
    : !targetVersionId
      ? "请先在项目设置中选择交付目标。"
      : deliveryJobId && deliveryJobState !== "SUCCEEDED"
        ? "交付候选任务正在后台处理；可到任务中心查看或恢复。"
        : null;
  const reviewBlocker = !deliveryId ? "请先完成交付候选构建，才能校验和记录审核。" : null;
  return <section className="panel delivery-workflow-panel" aria-labelledby="delivery-workflow-title">
    <div className="panel-heading"><div><p className="eyebrow">交付证据链</p><h3 id="delivery-workflow-title">{title}</h3></div><span className="status-pill neutral">不覆盖</span></div>
    <p className="muted">只使用冻结时间线和已选择的交付目标。整集渲染、交付清单、文件校验指纹、机器验证与人工决定都会分别留痕，不会覆盖原始输入。</p>
    {noteContext && <div className="inline-note-box" role="dialog" aria-label={noteContext.purpose === "review" ? "填写审核说明" : "填写撤回原因"}><strong>{noteContext.purpose === "review" ? `${noteContext.reviewerType === "HUMAN" ? "人工" : "平台"}审核说明（不会由机器结果自动代填）` : "撤回原因（会写入交付事件历史）"}</strong><textarea autoFocus value={noteDraft} onChange={(event) => setNoteDraft(event.target.value)} placeholder={noteContext.purpose === "review" ? "说明审核依据与结论" : "说明撤回原因"} /><div className="action-row"><button className="primary-action" type="button" onClick={() => void confirmNote()} disabled={!noteDraft.trim() || pending !== null}>{pending === "withdraw" || (noteContext.purpose === "review" && pending === noteContext.reviewerType) ? "提交中…" : noteContext.purpose === "review" ? "记录审核" : "确认撤回"}</button><button className="secondary" type="button" onClick={() => { setNoteContext(null); setNoteDraft(""); }} disabled={pending !== null}>取消</button></div></div>}
    {showCompose && <><fieldset><legend>本次交付使用的版本化规则</legend><div className="field-grid"><label>品牌规范<select value={brandKitId} onChange={(event) => setBrandKitId(event.target.value)}><option value="NONE">本次不绑定品牌规范</option>{brandControls.brand_kits.map((item) => <option key={String(item.id)} value={String(item.id)}>{String(item.title ?? item.code)} · 第 {String(item.version_no ?? "?")} 版{item.status === "ACTIVE" ? "（当前）" : ""}</option>)}</select></label><label>水印规则<select value={watermarkProfileId} onChange={(event) => setWatermarkProfileId(event.target.value)}><option value="NONE">本次不应用水印</option>{brandControls.watermark_profiles.map((item) => <option key={String(item.id)} value={String(item.id)}>{String(item.title ?? item.code)} · 第 {String(item.version_no ?? "?")} 版{item.status === "ACTIVE" ? "（当前）" : ""}</option>)}</select></label><label>合规检查<select value={compliancePolicyId} onChange={(event) => setCompliancePolicyId(event.target.value)}><option value="NONE">仅执行机器基础校验</option>{brandControls.compliance_policies.map((item) => <option key={String(item.id)} value={String(item.id)}>{String(item.title ?? item.code)} · 第 {String(item.version_no ?? "?")} 版{item.status === "ACTIVE" ? "（当前）" : ""}</option>)}</select></label></div><small className="muted">缺省选择当前生效版本；提交后 ID 和配置快照会冻结在交付任务中。</small></fieldset><div className="action-row" aria-label="合成候选操作"><button type="button" className="secondary" aria-describedby={composeBlocker ? "delivery-compose-blocker" : undefined} onClick={() => void run("render")} disabled={pending !== null || !timelineRevisionId || composeJobId !== null}>{pending === "render" ? "提交后台任务中…" : composeJobState === "SUCCEEDED" ? "整集渲染已完成" : composeJobId ? "整集渲染已排队" : "提交整集渲染任务"}</button><button type="button" className="primary-action" aria-describedby={buildBlocker ? "delivery-build-blocker" : undefined} onClick={() => void run("build")} disabled={pending !== null || !renderId || !targetVersionId || deliveryJobId !== null}>{pending === "build" ? "提交后台任务中…" : deliveryJobState === "SUCCEEDED" ? "交付候选已创建" : deliveryJobId ? "交付候选已排队" : "提交交付候选任务"}</button></div>{composeBlocker && <p id="delivery-compose-blocker" className="muted" role="note">整集渲染暂不可用：{composeBlocker}</p>}{buildBlocker && <p id="delivery-build-blocker" className="muted" role="note">交付候选暂不可用：{buildBlocker}</p>}{(composeJobId || deliveryJobId) && <p className="muted" role="note">后台任务已记录，可在<a href={routes.systemJobs(projectId)}>任务中心</a>查看进度、失败原因和重试入口。</p>}</>}
    {showReview && <>{reviewBlocker && <p id="delivery-review-blocker" className="muted" role="note">审核暂不可用：{reviewBlocker}</p>}<div className="action-row" aria-label="交付审核操作"><button type="button" className="primary-action" aria-describedby={reviewBlocker ? "delivery-review-blocker" : undefined} onClick={() => void run("verify")} disabled={pending !== null || !deliveryId}>{pending === "verify" ? "校验中…" : "验证交付文件"}</button><button type="button" className="primary-action" aria-describedby={reviewBlocker ? "delivery-review-blocker" : undefined} onClick={() => void verifyAndApprove()} disabled={pending !== null || !deliveryId}>{pending === "verify" ? "处理中…" : "一键验证并批准"}</button><button type="button" className="secondary" aria-describedby={reviewBlocker ? "delivery-review-blocker" : undefined} onClick={() => void review("HUMAN")} disabled={pending !== null || !deliveryId}>{pending === "HUMAN" ? "记录人工审核中…" : "记录人工批准"}</button><button type="button" className="secondary" aria-describedby={reviewBlocker ? "delivery-review-blocker" : undefined} onClick={() => void review("PLATFORM")} disabled={pending !== null || !deliveryId}>{pending === "PLATFORM" ? "记录平台审核中…" : "记录平台批准"}</button><button type="button" className="secondary" aria-describedby={reviewBlocker ? "delivery-review-blocker" : undefined} onClick={() => void withdraw()} disabled={pending !== null || !deliveryId}>{pending === "withdraw" ? "撤回中…" : "撤回交付包"}</button></div></>}
    {showPackage && <>{reviewBlocker && <p id="delivery-package-blocker" className="muted" role="note">复验暂不可用：{reviewBlocker}</p>}<div className="action-row" aria-label="交付包复验操作"><button type="button" className="primary-action" aria-describedby={reviewBlocker ? "delivery-package-blocker" : undefined} onClick={() => void run("verify")} disabled={pending !== null || !deliveryId}>{pending === "verify" ? "校验中…" : "重新验证交付文件"}</button><button type="button" className="secondary" aria-describedby={reviewBlocker ? "delivery-package-blocker" : undefined} onClick={() => void withdraw()} disabled={pending !== null || !deliveryId}>{pending === "withdraw" ? "撤回中…" : "撤回交付包"}</button></div></>}
    <details className="job-input-snapshot"><summary>高级：查看交付对象标识</summary><div className="review-meta"><span>分集：{episodeId.slice(0, 12)}</span><span>时间线：{timelineRevisionId?.slice(0, 12) ?? "缺失"}</span><span>渲染成片：{renderId?.slice(0, 12) ?? "缺失"}</span><span>交付目标：{targetVersionId?.slice(0, 12) ?? "缺失"}</span><span>交付包：{deliveryId?.slice(0, 12) ?? "缺失"}</span></div></details><div className="review-meta"><span>机器验证通过不等于人工或平台批准</span><button className="secondary" type="button" onClick={() => void refreshHistory()} disabled={pending !== null}>刷新交付历史</button></div>
    {showPackage && history.length > 0 && <div className="table-wrap"><table><caption className="sr-only">交付包历史</caption><thead><tr><th>状态</th><th>目标版本</th><th>文件校验指纹</th><th>保存位置</th><th>下载与操作记录</th><th>撤回原因</th></tr></thead><tbody>{history.map((item) => { const downloadCount = (item.events ?? []).filter((event) => event.action === "DOWNLOAD").length; return <tr key={item.id}><td>{statusLabel(item.status)}</td><td>{String(item.target_version_id).slice(0, 12)}</td><td><code>{String(item.manifest_sha256 ?? "").slice(0, 16)}…</code></td><td><code>{String(item.rel_path ?? "")}</code></td><td><a className="secondary" href={`/api/v1/delivery-packages/${encodeURIComponent(item.id)}/download`} download>{downloadCount > 0 ? `下载 MP4 · 已记录 ${downloadCount} 次` : "下载 MP4"}</a></td><td>{String(item.withdrawn_reason ?? "—")}</td></tr>; })}</tbody></table></div>}
    {error && <p className="inline-error" role="alert">{error}</p>}{success && <p className="review-success" role="status">{success}</p>}
  </section>;
}

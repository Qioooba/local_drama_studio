import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Dialog, TabPanel, Tabs, type TabItem } from "../components/ui";
import { commitEpisodeTimelineRefresh, getEpisodeTimelineStatus, getG8Readiness, getProjectConfiguration, planEpisodeTimelineRefresh, reviewInbox } from "../generated/api";
import { DeliveryWorkflowPanel } from "../features/production/DeliveryWorkflowPanel";
import { EpisodeContactSheetAction } from "../features/production/EpisodeContactSheetAction";
import { PostProcessPanel } from "../features/generation/PostProcessPanel";
import { G8ReadinessPanel } from "../features/status/ReadinessPanels";
import "./creative-workspaces.css";

type DeliveryStep = "preflight" | "compose" | "review" | "package";

const DELIVERY_STEPS = new Set<DeliveryStep>(["preflight", "compose", "review", "package"]);
const DELIVERY_TABS: TabItem[] = [
  { id: "preflight", label: "1 交付检查" },
  { id: "compose", label: "2 合成候选" },
  { id: "review", label: "3 审核成片" },
  { id: "package", label: "4 打包交付" },
];

export function DeliveryPage() {
  const { projectId, episodeId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [createdRenderId, setCreatedRenderId] = useState<string | null>(null);
  const [createdDeliveryId, setCreatedDeliveryId] = useState<string | null>(null);
  const [refreshConfirmOpen, setRefreshConfirmOpen] = useState(false);
  const [refreshFeedback, setRefreshFeedback] = useState("");
  const requestedStep = searchParams.get("view") as DeliveryStep | null;
  const activeStep: DeliveryStep = requestedStep && DELIVERY_STEPS.has(requestedStep) ? requestedStep : "preflight";
  const selectStep = (step: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (step === "preflight") next.delete("view");
      else next.set("view", step);
      return next;
    }, { replace: true });
  };
  const status = useQuery({ queryKey: ["episode", episodeId, "timeline-status"], queryFn: () => getEpisodeTimelineStatus(episodeId as string), enabled: Boolean(episodeId), refetchInterval: 10_000 });
  const configuration = useQuery({ queryKey: ["project", projectId, "configuration"], queryFn: () => getProjectConfiguration(projectId as string), enabled: Boolean(projectId) });
  const reviewVideos = useQuery({ queryKey: ["delivery", projectId, episodeId, "review-videos"], queryFn: () => reviewInbox(projectId, "", { episode_id: episodeId, media_kind: "VIDEO" }), enabled: Boolean(projectId && episodeId) && activeStep === "package" });
  const g8Readiness = useQuery({ queryKey: ["delivery", projectId, episodeId, "g8-readiness"], queryFn: () => getG8Readiness(projectId as string, episodeId as string), enabled: Boolean(projectId && episodeId) && activeStep === "preflight" });
  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  const snapshot = status.data?.status;
  const timelineIsStale = String(snapshot?.timeline?.latest?.status ?? "").toUpperCase() === "STALE";
  const refreshPlan = useQuery({ queryKey: ["delivery", episodeId, "timeline-refresh-plan"], queryFn: () => planEpisodeTimelineRefresh(episodeId), enabled: activeStep === "preflight" && timelineIsStale });
  const refreshTimeline = useMutation({
    mutationFn: () => {
      const planHash = refreshPlan.data?.plan.plan_hash;
      if (!planHash) throw new Error("恢复预检尚未就绪");
      return commitEpisodeTimelineRefresh(episodeId, planHash);
    },
    onSuccess: async (result) => {
      setRefreshConfirmOpen(false);
      setRefreshFeedback(`已从最新采用事实创建冻结时间线 v${result.timeline.revision_no}；可以继续交付预检。`);
      await Promise.all([status.refetch(), g8Readiness.refetch()]);
    },
    onError: (error) => setRefreshFeedback(`恢复未完成：${error instanceof Error ? error.message : String(error)}`),
  });
  const activeError = status.error ?? configuration.error ?? (activeStep === "preflight" ? g8Readiness.error : null) ?? (activeStep === "package" ? reviewVideos.error : null);
  const timelineRevisionId = snapshot?.timeline?.latest?.id && !timelineIsStale ? String(snapshot.timeline.latest.id) : null;
  const renderId = createdRenderId ?? (snapshot?.renders?.latest?.id ? String(snapshot.renders.latest.id) : null);
  const targetVersionId = configuration.data?.configuration?.selected_delivery_target_version_id ? String(configuration.data.configuration.selected_delivery_target_version_id) : null;
  const deliveryId = createdDeliveryId ?? (snapshot?.delivery?.latest?.id ? String(snapshot.delivery.latest.id) : null);
  const workflowProps = {
    episodeId,
    timelineRevisionId,
    renderId,
    targetVersionId,
    deliveryId,
    onRenderCreated: setCreatedRenderId,
    onDeliveryCreated: setCreatedDeliveryId,
    onChanged: () => void status.refetch(),
  };
  return <div className="v2-page creative-task-page delivery-workspace-v2">
    <div className="panel-heading"><div><p className="eyebrow">合成与交付</p><h2>从冻结时间线创建可验证成片</h2></div><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episodeId}/timeline`}>返回时间线</Link></div>
    <p className="muted">合成成片、核对交付文件与人工批准是分开的步骤；机器检查通过不等于人工批准。</p>
    {activeError && <p className="inline-error" role="alert">当前交付步骤读取失败：{String(activeError)}</p>}
    <Tabs items={DELIVERY_TABS} selectedId={activeStep} onChange={selectStep} ariaLabel="交付四步检查">
      <TabPanel id="preflight" selectedId={activeStep}>
        <section className="creative-task-stage" aria-labelledby="delivery-preflight-title">
          <div className="panel-heading"><div><p className="eyebrow">步骤 1 · 交付检查</p><h3 id="delivery-preflight-title">确认冻结输入、交付目标与必要证据</h3></div><span className="status-pill neutral">只读检查</span></div>
          {g8Readiness.isLoading && <p className="loading-state" role="status">正在读取正式退出证据…</p>}
          {g8Readiness.data?.readiness && <G8ReadinessPanel readiness={g8Readiness.data.readiness} />}
          {timelineIsStale && <section className="delivery-stale-recovery" aria-labelledby="delivery-stale-title">
            <div className="panel-heading"><div><p className="eyebrow">过期输入恢复</p><h4 id="delivery-stale-title">同步最新采用事实并重新冻结</h4></div><span className={`status-pill ${refreshPlan.data?.plan.status === "READY" ? "success" : "warning"}`}>{refreshPlan.isLoading ? "复检中" : refreshPlan.data?.plan.status === "READY" ? "可恢复" : "需处理"}</span></div>
            <p className="muted">先只读复检当前采用视频、音轨、字幕与文件完整性；确认后才会创建新的不可变冻结版本，旧版本和审计记录都会保留。</p>
            {refreshPlan.isLoading && <p className="loading-state" role="status">正在复检最新采用事实…</p>}
            {refreshPlan.isError && <div className="inline-error" role="alert"><span>恢复预检失败：{String(refreshPlan.error)}</span><button type="button" className="secondary" onClick={() => void refreshPlan.refetch()}>重新复检</button></div>}
            {refreshPlan.data && <>
              <dl className="delivery-refresh-summary"><div><dt>镜头</dt><dd>{refreshPlan.data.plan.summary.video_count}/{refreshPlan.data.plan.summary.shot_count}</dd></div><div><dt>音轨</dt><dd>{refreshPlan.data.plan.summary.audio_count}</dd></div><div><dt>字幕</dt><dd>{refreshPlan.data.plan.summary.subtitle_count ? "已包含" : "未包含"}</dd></div><div><dt>新版本</dt><dd>冻结且可追溯</dd></div></dl>
              {refreshPlan.data.plan.blockers.length > 0 && <div className="delivery-refresh-findings blockers" role="alert"><strong>请先处理以下阻断</strong><ul>{refreshPlan.data.plan.blockers.map((item, index) => <li key={`${item.code}-${index}`}>{item.message}</li>)}</ul></div>}
              {refreshPlan.data.plan.warnings.length > 0 && <div className="delivery-refresh-findings warnings"><strong>冻结后仍需人工复核</strong><ul>{refreshPlan.data.plan.warnings.map((item, index) => <li key={`${item.code}-${index}`}>{item.message}</li>)}</ul></div>}
              <div className="delivery-refresh-confirm"><span>检查通过后，系统会基于当前采用的视频、音轨和字幕创建一个新冻结版本，旧版本仍会保留。</span><button type="button" className="primary-action" disabled={refreshPlan.data.plan.status !== "READY" || refreshTimeline.isPending} onClick={() => setRefreshConfirmOpen(true)}>准备重新冻结</button></div>
            </>}
          </section>}
          {refreshFeedback && <p className={refreshFeedback.startsWith("恢复未完成") ? "inline-error" : "review-success"} role="status">{refreshFeedback}</p>}
          {!g8Readiness.isLoading && <div className="creative-task-command"><span>{timelineIsStale ? "当前冻结时间线已过期；请先完成上方复检与重新冻结。" : !timelineRevisionId ? <>尚无可用的冻结时间线；请先<Link to={`/projects/${projectId}/episodes/${episodeId}/timeline`}>打开时间线并冻结</Link>一个版本。</> : !targetVersionId ? <>尚未选择交付目标；请先到<Link to={`/projects/${projectId}/production-settings?view=delivery`}>生产设置</Link>选择本地目标版本。</> : "预检不会创建 render 或交付包。"}</span><button className="primary-action" type="button" aria-describedby={!timelineRevisionId || !targetVersionId ? "delivery-preflight-blocker" : undefined} disabled={!timelineRevisionId || !targetVersionId} onClick={() => selectStep("compose")}>继续到合成候选</button>{(!timelineRevisionId || !targetVersionId) && <span id="delivery-preflight-blocker" className="sr-only">冻结时间线与交付目标就绪后才能继续</span>}</div>}
        </section>
      </TabPanel>
      <TabPanel id="compose" selectedId={activeStep}>
        <DeliveryWorkflowPanel {...workflowProps} focus="COMPOSE" />
        <div className="creative-task-command"><span>{deliveryId ? "交付候选已创建，可以进入成片审核。" : "先完成整集渲染并创建交付候选；后台任务完成后本页会恢复结果。"}</span><button className="secondary" type="button" disabled={!deliveryId} onClick={() => selectStep("review")}>下一步：审核成片 →</button></div>
      </TabPanel>
      <TabPanel id="review" selectedId={activeStep}>
        <DeliveryWorkflowPanel {...workflowProps} focus="REVIEW" />
        <div className="creative-task-command"><span>审核确认后可进入打包交付。</span><button className="secondary" type="button" onClick={() => selectStep("package")}>下一步：打包交付 →</button></div>
      </TabPanel>
      <TabPanel id="package" selectedId={activeStep}>
        <DeliveryWorkflowPanel {...workflowProps} focus="PACKAGE" />
        <section className="panel creative-task-stage" aria-labelledby="delivery-local-tools-title">
          <div className="panel-heading"><div><p className="eyebrow">本地交付工具</p><h3 id="delivery-local-tools-title">增强与联系表</h3></div><span className="status-pill neutral">不覆盖输入</span></div>
          <p className="muted">只列出当前集审核收件箱中的真实视频；增强先只读预检再显式执行，联系表只在点击后导出。</p>
          {reviewVideos.isLoading && <p className="loading-state" role="status">正在读取当前集视频…</p>}
          <EpisodeContactSheetAction episodeId={episodeId} />
        </section>
        {!reviewVideos.isLoading && <PostProcessPanel videos={(reviewVideos.data?.items ?? []).filter((item) => item.media_kind === "VIDEO")} />}
      </TabPanel>
    </Tabs>
    <Dialog
      open={refreshConfirmOpen}
      title="确认创建新的冻结时间线"
      onClose={() => !refreshTimeline.isPending && setRefreshConfirmOpen(false)}
      footer={<><button type="button" className="secondary" disabled={refreshTimeline.isPending} onClick={() => setRefreshConfirmOpen(false)}>返回检查</button><button type="button" className="primary-action" disabled={refreshTimeline.isPending} onClick={() => refreshTimeline.mutate()}>{refreshTimeline.isPending ? "正在同步并冻结…" : "确认同步并冻结"}</button></>}
    >
      <p>这会把当前采用的视频、音轨和字幕同步到一个新的不可变版本。旧冻结版本与审计记录不会被删除，但后续渲染将以新版本为准。</p>
      {refreshPlan.data && <dl className="delivery-refresh-summary"><div><dt>镜头</dt><dd>{refreshPlan.data.plan.summary.video_count}/{refreshPlan.data.plan.summary.shot_count}</dd></div><div><dt>音轨</dt><dd>{refreshPlan.data.plan.summary.audio_count}</dd></div><div><dt>字幕</dt><dd>{refreshPlan.data.plan.summary.subtitle_count ? "已包含" : "未包含"}</dd></div></dl>}
    </Dialog>
  </div>;
}

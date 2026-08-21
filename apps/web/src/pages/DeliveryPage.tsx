import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { TabPanel, Tabs, type TabItem } from "../components/ui";
import { getEpisodeTimelineStatus, getG8Readiness, getProjectConfiguration, reviewInbox } from "../generated/api";
import { DeliveryWorkflowPanel } from "../features/production/DeliveryWorkflowPanel";
import { EpisodeContactSheetAction } from "../features/production/EpisodeContactSheetAction";
import { PostProcessPanel } from "../features/generation/PostProcessPanel";
import { G8ReadinessPanel } from "../features/status/ReadinessPanels";
import "./creative-workspaces.css";

type DeliveryStep = "preflight" | "compose" | "review" | "package";

const DELIVERY_STEPS = new Set<DeliveryStep>(["preflight", "compose", "review", "package"]);
const DELIVERY_TABS: TabItem[] = [
  { id: "preflight", label: "1 准备预检" },
  { id: "compose", label: "2 合成候选" },
  { id: "review", label: "3 审核证据" },
  { id: "package", label: "4 打包交付" },
];

export function DeliveryPage() {
  const { projectId, episodeId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
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
  const status = useQuery({ queryKey: ["episode", episodeId, "timeline-status"], queryFn: () => getEpisodeTimelineStatus(episodeId as string), enabled: Boolean(episodeId) });
  const configuration = useQuery({ queryKey: ["project", projectId, "configuration"], queryFn: () => getProjectConfiguration(projectId as string), enabled: Boolean(projectId) });
  const reviewVideos = useQuery({ queryKey: ["delivery", projectId, episodeId, "review-videos"], queryFn: () => reviewInbox(projectId, "", { episode_id: episodeId, media_kind: "VIDEO" }), enabled: Boolean(projectId && episodeId) && activeStep === "package" });
  const g8Readiness = useQuery({ queryKey: ["delivery", projectId, episodeId, "g8-readiness"], queryFn: () => getG8Readiness(projectId as string, episodeId as string), enabled: Boolean(projectId && episodeId) && activeStep === "preflight" });
  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  const snapshot = status.data?.status;
  const activeError = status.error ?? configuration.error ?? (activeStep === "preflight" ? g8Readiness.error : null) ?? (activeStep === "package" ? reviewVideos.error : null);
  const workflowProps = {
    episodeId,
    timelineRevisionId: snapshot?.timeline?.latest?.id ? String(snapshot.timeline.latest.id) : null,
    renderId: snapshot?.renders?.latest?.id ? String(snapshot.renders.latest.id) : null,
    targetVersionId: configuration.data?.configuration?.selected_delivery_target_version_id ? String(configuration.data.configuration.selected_delivery_target_version_id) : null,
    deliveryId: snapshot?.delivery?.latest?.id ? String(snapshot.delivery.latest.id) : null,
    onChanged: () => void status.refetch(),
  };
  return <div className="v2-page creative-task-page delivery-workspace-v2">
    <div className="panel-heading"><div><p className="eyebrow">合成与交付</p><h2>从冻结时间线创建可验证成片</h2></div><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episodeId}/timeline`}>返回时间线</Link></div>
    <p className="muted">渲染、交付候选、manifest 校验与人工批准是分开的证据步骤；机器通过不等于人工批准。</p>
    {activeError && <p className="inline-error" role="alert">当前交付步骤读取失败：{String(activeError)}</p>}
    <Tabs items={DELIVERY_TABS} selectedId={activeStep} onChange={selectStep} ariaLabel="交付四步检查">
      <TabPanel id="preflight" selectedId={activeStep}>
        <section className="creative-task-stage" aria-labelledby="delivery-preflight-title">
          <div className="panel-heading"><div><p className="eyebrow">步骤 1 · Preflight</p><h3 id="delivery-preflight-title">确认冻结输入、目标与退出证据</h3></div><span className="status-pill neutral">只读</span></div>
          {g8Readiness.isLoading && <p className="loading-state" role="status">正在读取 G8 正式退出证据…</p>}
          {g8Readiness.data?.readiness && <G8ReadinessPanel readiness={g8Readiness.data.readiness} />}
          {!g8Readiness.isLoading && <div className="creative-task-command"><span>预检不会创建 render 或交付包。</span><button className="primary-action" type="button" onClick={() => selectStep("compose")}>继续到合成候选</button></div>}
        </section>
      </TabPanel>
      <TabPanel id="compose" selectedId={activeStep}>
        <DeliveryWorkflowPanel {...workflowProps} focus="COMPOSE" />
      </TabPanel>
      <TabPanel id="review" selectedId={activeStep}>
        <DeliveryWorkflowPanel {...workflowProps} focus="REVIEW" />
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
  </div>;
}

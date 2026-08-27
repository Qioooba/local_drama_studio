import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { StoryboardBatchWorkbench } from "../features/projects/StoryboardBatchWorkbench";
import { ShotGroupPlanner } from "../features/episode-plan-v2/ShotGroupPlanner";
import { SelectedBeatReplanPanel } from "../features/episode-plan-v2/SelectedBeatReplanPanel";
import { EpisodeSourcePassage } from "../features/source-passage/EpisodeSourcePassage";
import { EpisodeSceneRanges } from "../features/projects/EpisodeSceneRanges";
import { ErrorBoundary } from "../components/ui/ErrorBoundary";
import { Drawer, TabPanel, Tabs } from "../components/ui";
import { routes } from "../app/routeRegistry";
import "./episode-plan.css";

type PlanTask = "storyboard" | "scenes" | "source";

const PLAN_TASKS = new Set<PlanTask>(["storyboard", "scenes", "source"]);
const PLAN_TABS = [
  { id: "storyboard", label: "镜头分镜板" },
  { id: "scenes", label: "场景与分组" },
  { id: "source", label: "原文证据" },
];

/** Episode plan: source evidence → reviewed AI draft → versioned shot plan. */
export function EpisodePlanPage() {
  const { projectId = "", episodeId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTask = searchParams.get("view") as PlanTask | null;
  const activeTab: PlanTask = requestedTask && PLAN_TASKS.has(requestedTask) ? requestedTask : "storyboard";
  const [replanDrawerOpen, setReplanDrawerOpen] = useState(false);
  const [sourceDrawerOpen, setSourceDrawerOpen] = useState(false);

  const selectTab = (task: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (task === "storyboard") next.delete("view");
      else next.set("view", task);
      return next;
    }, { replace: true });
  };

  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;

  return (
    <ErrorBoundary projectId={projectId} fallbackTitle="分集策划工作区异常">
      <div className="v2-page episode-plan-page">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">分集策划</p>
            <h2>从原文证据到可生产镜头</h2>
          </div>
          <div className="episode-plan-actions">
            <button
              type="button"
              className="secondary btn-sm"
              onClick={() => setSourceDrawerOpen(true)}
            >
              对照原文
            </button>
            <button
              type="button"
              className="secondary btn-sm"
              onClick={() => setReplanDrawerOpen(true)}
              aria-label="打开重排抽屉"
            >
              选定 Beat 重排
            </button>
            <Link
              className="secondary v2-inline-link"
              to={routes.storyWorkspace(projectId)}
            >
              故事工作区
            </Link>
            <Link
              className="primary-action v2-inline-link"
              to={routes.shotStudio(projectId, episodeId)}
            >
              进入镜头工作台
            </Link>
          </div>
        </div>
        <p className="muted">
          项目长文导入与 AI 拆解在故事工作区统一管理；此处展示当前集引用的原文范围与分镜头编排。
        </p>

        <ol className="episode-plan-flow" aria-label="分集策划数据流">
          <li><span>1</span>引用原文证据</li>
          <li><span>2</span>编排场景与镜头</li>
          <li><span>3</span>冻结生产快照</li>
        </ol>

        <div className="episode-plan-tabs">
          <Tabs items={PLAN_TABS} selectedId={activeTab} onChange={selectTab} ariaLabel="分集策划任务" />
        </div>

        {/* Tab 1: Storyboard */}
        <TabPanel id="storyboard" selectedId={activeTab}>
          <ErrorBoundary projectId={projectId} fallbackTitle="分镜批量编排异常">
            <StoryboardBatchWorkbench projectId={projectId} episodeId={episodeId} />
          </ErrorBoundary>
        </TabPanel>

        {/* Tab 2: Scenes & Groups */}
        <TabPanel id="scenes" selectedId={activeTab}>
          <div className="episode-plan-stack">
          <ErrorBoundary projectId={projectId} fallbackTitle="镜头组规划异常">
            <ShotGroupPlanner episodeId={episodeId} />
          </ErrorBoundary>
          <ErrorBoundary projectId={projectId} fallbackTitle="分集场景范围异常">
            <EpisodeSceneRanges projectId={projectId} episodeId={episodeId} />
          </ErrorBoundary>
          </div>
        </TabPanel>

        {/* Tab 3: source evidence only; AI draft ownership remains in Story. */}
        <TabPanel id="source" selectedId={activeTab}>
          <ErrorBoundary projectId={projectId} fallbackTitle="原文定位异常">
            <div className="subpanel episode-plan-source" id="plan-source">
              <div className="section-title">
                <span>故事原稿与段落引用</span>
                <Link
                  className="secondary"
                  to={`${routes.storyWorkspace(projectId)}#story-review`}
                >
                  前往故事工作区审核或重新拆解 →
                </Link>
              </div>
              <p className="muted" style={{ fontSize: "13px", margin: "4px 0 12px" }}>
                本集镜头所绑定的来源段落与上下文（Unicode 字符偏移范围与原文比对）：
              </p>
              <EpisodeSourcePassage projectId={projectId} episodeId={episodeId} />
            </div>
          </ErrorBoundary>

        </TabPanel>

        {/* Replan Drawer */}
        <Drawer
          open={sourceDrawerOpen}
          onClose={() => setSourceDrawerOpen(false)}
          title="原文证据对照"
          width={640}
        >
          <div className="episode-plan-source-drawer">
            <p className="muted">在不离开镜头分镜板的情况下核对本集来源段落；修改原稿或重新拆解仍由故事工作区负责。</p>
            <EpisodeSourcePassage projectId={projectId} episodeId={episodeId} />
            <Link className="secondary v2-inline-link" to={`${routes.storyWorkspace(projectId)}#story-review`}>前往故事工作区审核或重新拆解</Link>
          </div>
        </Drawer>

        <Drawer
          open={replanDrawerOpen}
          onClose={() => setReplanDrawerOpen(false)}
          title="选定 Beat 重排与对比"
          width={600}
        >
          <ErrorBoundary projectId={projectId} fallbackTitle="选定 Beat 重排异常">
            <SelectedBeatReplanPanel projectId={projectId} episodeId={episodeId} />
          </ErrorBoundary>
        </Drawer>
      </div>
    </ErrorBoundary>
  );
}

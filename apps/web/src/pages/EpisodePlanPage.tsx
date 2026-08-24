import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { getEpisodeProduction, listProfiles } from "../generated/api";
import { StoryboardBatchWorkbench } from "../features/projects/StoryboardBatchWorkbench";
import { ShotGroupPlanner } from "../features/episode-plan-v2/ShotGroupPlanner";
import { SelectedBeatReplanPanel } from "../features/episode-plan-v2/SelectedBeatReplanPanel";
import { EpisodeSourcePassage } from "../features/source-passage/EpisodeSourcePassage";
import { EpisodeSceneRanges } from "../features/projects/EpisodeSceneRanges";
import { PromptTemplatePanel } from "../features/production/PromptTemplatePanel";
import { ErrorBoundary } from "../components/ui/ErrorBoundary";
import { Drawer, TabPanel, Tabs } from "../components/ui";
import { routes } from "../app/routeRegistry";
import { STATUS_LABELS, optionLabel } from "../features/shared/optionLabels";
import "./episode-plan.css";

type PlanTask = "storyboard" | "scenes" | "source" | "prompts";

const PLAN_TASKS = new Set<PlanTask>(["storyboard", "scenes", "source", "prompts"]);
const PLAN_TABS = [
  { id: "storyboard", label: "镜头分镜板" },
  { id: "scenes", label: "场景与分组" },
  { id: "source", label: "原文证据" },
  { id: "prompts", label: "提示词快照" },
];

/** Episode plan: source evidence → reviewed AI draft → versioned shot plan. */
export function EpisodePlanPage() {
  const { projectId = "", episodeId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTask = searchParams.get("view") as PlanTask | null;
  const activeTab: PlanTask = requestedTask && PLAN_TASKS.has(requestedTask) ? requestedTask : "storyboard";
  const [replanDrawerOpen, setReplanDrawerOpen] = useState(false);

  const selectTab = (task: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (task === "storyboard") next.delete("view");
      else next.set("view", task);
      return next;
    }, { replace: true });
  };

  const production = useQuery({
    queryKey: ["episode", episodeId, "production", "prompt-tools"],
    queryFn: () => getEpisodeProduction(episodeId),
    enabled: Boolean(episodeId),
  });
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: () => listProfiles(),
    enabled: Boolean(projectId),
  });

  const shots = useMemo(() => production.data?.items ?? [], [production.data?.items]);
  const [selectedShotId, setSelectedShotId] = useState("");

  useEffect(() => {
    if (!shots.some((shot) => String(shot.id) === selectedShotId)) {
      setSelectedShotId(shots[0] ? String(shots[0].id) : "");
    }
  }, [selectedShotId, shots]);

  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  const selectedShot = shots.find((shot) => String(shot.id) === selectedShotId);
  const publishedProfiles = (profiles.data?.items ?? []).filter(
    (profile) => profile.status === "PUBLISHED"
  );

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
              to={routes.directorDesk(projectId, episodeId)}
            >
              进入导演台
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

        {/* Tab 4: Prompts Snapshot */}
        <TabPanel id="prompts" selectedId={activeTab}>
          <div className="episode-plan-stack">
          <section className="panel" aria-labelledby="episode-prompt-tools-title">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">生成准备</p>
                <h3 id="episode-prompt-tools-title">镜头提示词快照</h3>
              </div>
            </div>
            <p className="muted">
              按镜头编号选择，不需要粘贴技术 ID；冻结结果保留镜头字段和已发布 Profile 版本来源。
            </p>
            {production.isLoading || profiles.isLoading ? (
              <p className="loading-state" role="status">
                正在读取镜头与已发布 Profile…
              </p>
            ) : null}
            {(production.isError || profiles.isError) && (
              <p className="inline-error" role="alert">
                生成准备读取失败：{String(production.error ?? profiles.error)}
              </p>
            )}
            {!production.isLoading && shots.length === 0 ? (
              <p className="empty-state">当前集还没有镜头，请先完成镜头编排。</p>
            ) : (
              <label htmlFor="episode-prompt-shot">
                目标镜头
                <select
                  id="episode-prompt-shot"
                  value={selectedShotId}
                  onChange={(event) => setSelectedShotId(event.target.value)}
                >
                  <option value="">请选择镜头</option>
                  {shots.map((shot, index) => (
                    <option key={String(shot.id)} value={String(shot.id)}>
                      {String(shot.code ?? `镜头 ${index + 1}`)} · {optionLabel(STATUS_LABELS, typeof shot.status === "string" ? shot.status : undefined, "未导演")}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {shots.length > 0 && publishedProfiles.length === 0 && !profiles.isLoading ? (
              <p className="inline-warning" role="status">
                暂无已发布 Profile；可先查看镜头，但冻结提示词前需在模型页发布可用版本。
              </p>
            ) : null}
          </section>
          <PromptTemplatePanel projectId={projectId} shot={selectedShot} profiles={publishedProfiles} />
          </div>
        </TabPanel>

        {/* Replan Drawer */}
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

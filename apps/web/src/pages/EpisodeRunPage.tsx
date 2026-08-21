import { useCallback, useEffect } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { TabPanel, Tabs, type TabItem } from "../components/ui";
import { EpisodeRunPanel } from "../features/episode-run-v2/EpisodeRunPanel";
import { EpisodeCockpit } from "../features/episode-cockpit/EpisodeCockpit";
import { FreshnessPanel } from "../features/freshness/FreshnessPanel";
import "./creative-workspaces.css";

type RunView = "run" | "cockpit" | "freshness";

const RUN_VIEWS = new Set<RunView>(["run", "cockpit", "freshness"]);
const RUN_TABS: TabItem[] = [
  { id: "run", label: "生产运行" },
  { id: "cockpit", label: "关卡总览" },
  { id: "freshness", label: "失效与影响" },
];

/** Creator-facing seven-stage facade for a durable episode automation run. */
export function EpisodeRunPage() {
  const { projectId, episodeId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const requestedView = params.get("view") as RunView | null;
  const activeView: RunView = requestedView && RUN_VIEWS.has(requestedView) ? requestedView : "run";
  const selectView = useCallback((view: string) => {
    const next = new URLSearchParams(location.search);
    if (view === "run") next.delete("view");
    else next.set("view", view);
    const search = next.toString();
    navigate({ pathname: location.pathname, search: search ? `?${search}` : "", hash: view === "run" ? location.hash : "" }, { replace: true });
  }, [location.hash, location.pathname, location.search, navigate]);

  useEffect(() => {
    if (location.hash === "#episode-production-controls" && activeView !== "run") selectView("run");
  }, [activeView, location.hash, selectView]);
  useEffect(() => {
    if (activeView !== "run" || location.hash !== "#episode-production-controls") return;
    const frame = window.requestAnimationFrame(() => document.getElementById("episode-production-controls")?.scrollIntoView({ block: "start" }));
    return () => window.cancelAnimationFrame(frame);
  }, [activeView, location.hash]);

  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  return <div className="v2-page creative-task-page episode-run-page-v2">
    <div className="panel-heading"><div><p className="eyebrow">本集生产</p><h2>运行、关卡与失效处置</h2></div><span className="status-pill neutral">本机持久化事实</span></div>
    <p className="muted">一次只处理一个生产任务；运行控制、只读关卡事实与 stale 影响保持各自唯一 owner。</p>
    <Tabs items={RUN_TABS} selectedId={activeView} onChange={selectView} ariaLabel="整集生产任务">
      <TabPanel id="run" selectedId={activeView}>
        <div id="episode-production-controls"><EpisodeRunPanel projectId={projectId} episodeId={episodeId} /></div>
      </TabPanel>
      <TabPanel id="cockpit" selectedId={activeView}>
        <EpisodeCockpit projectId={projectId} episodeId={episodeId} />
      </TabPanel>
      <TabPanel id="freshness" selectedId={activeView}>
        <FreshnessPanel projectId={projectId} scopeType="EPISODE" scopeId={episodeId} />
      </TabPanel>
    </Tabs>
  </div>;
}

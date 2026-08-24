import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { BreadcrumbSeparatorIcon, StudioMarkIcon } from "../components/icons";
import { buildBreadcrumbs } from "../app/routeRegistry";
import { CommandPalette } from "../features/commands/CommandPalette";
import type { StudioCommand } from "../features/commands/commandRegistry";
import { getEpisodeCockpit } from "../features/episode-cockpit/api";
import { loadHealth, LocalRuntimeIndicator } from "../features/status-v2/LocalRuntimeIndicator";
import { getEpisodeTimelineStatus, getProjectCreatorSetup, getProjectEpisodeCatalog, getStoryboardWorkspace, listProjects } from "../generated/api";
import { queryKeys } from "../query/queryKeys";

const navigationCommands: StudioCommand[] = [
  { id: "nav.projects", label: "打开全部项目", group: "导航", keywords: ["首页", "项目列表"], run: ({ navigate }) => navigate("/projects") },
  { id: "nav.project", label: "打开项目总览", group: "导航", enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}`) },
  { id: "nav.story", label: "打开故事工作区", group: "创作", keywords: ["长文", "剧本", "拆解", "故事圣经"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/story`) },
  { id: "nav.assets", label: "打开资产圣经", group: "创作", keywords: ["角色", "场景", "三视图"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/assets`) },
  { id: "nav.qc-policy", label: "打开质量策略", group: "创作", keywords: ["自动重抽", "质量", "质检"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/qc-policies`) },
  { id: "nav.director-recipes", label: "打开导演配方", group: "创作", keywords: ["Recipe", "版本", "生产策略"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/director-recipes`) },
  { id: "nav.production-settings", label: "打开生产设置", group: "创作", keywords: ["配置", "偏好", "QC", "配方", "容量"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/production-settings`) },
  { id: "nav.operations", label: "打开项目维护", group: "系统", keywords: ["迁移", "授权", "自动化", "健康", "审计"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/operations`) },
  { id: "nav.canvas", label: "打开高级画布", group: "创作", keywords: ["DAG", "依赖", "分支实验"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/canvas`) },
  { id: "nav.lab", label: "打开素材实验室", group: "工具", keywords: ["Comfy", "沙盒", "工作流"], run: ({ navigate, projectId }) => navigate(`/lab${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`) },
  { id: "nav.episode-plan", label: "打开分集策划", group: "创作", keywords: ["原文", "拆解", "镜头计划"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/plan`) },
  { id: "nav.director", label: "打开导演台", group: "创作", keywords: ["镜头", "候选", "首尾帧"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId, shotId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/direct${shotId ? `/${shotId}` : ""}`) },
  { id: "nav.manual-generation", label: "打开镜头生成", group: "创作", keywords: ["生成", "候选", "镜头", "视频", "图片"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId, shotId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/generation${shotId ? `/${shotId}` : ""}`) },
  { id: "nav.episode-run", label: "打开整集生产", group: "创作", keywords: ["生成剩余", "暂停", "恢复"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/run`) },
  { id: "nav.review", label: "打开本集审核", group: "创作", enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/review`) },
  { id: "nav.audio", label: "打开声音工作区", group: "创作", keywords: ["TTS", "BGM", "SFX"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/audio`) },
  { id: "nav.timeline", label: "打开时间线", group: "创作", enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/timeline`) },
  { id: "nav.delivery", label: "打开交付与导出", group: "创作", enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/delivery`) },
  { id: "nav.models", label: "打开模型与能力", group: "系统", run: ({ navigate, projectId }) => navigate(`/models${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`) },
  { id: "nav.jobs", label: "打开任务与机器", group: "系统", run: ({ navigate, projectId }) => navigate(`/jobs${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`) },
  { id: "nav.diagnostics", label: "打开诊断", group: "系统", run: ({ navigate, projectId }) => navigate(`/diagnostics${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`) },
];

function NavFact({ tone = "neutral", children }: { tone?: "neutral" | "ready" | "attention" | "danger"; children: ReactNode }) {
  return <span className={`nav-fact ${tone}`} aria-label={`状态：${String(children)}`}>{children}</span>;
}

/**
 * V2 AppShell: production-semantics navigation + compact local runtime
 * indicator.  See docs/xinjihua/01_竞品研究与产品_UI_UX_总设计.md §27.
 */
export function AppShell() {
  const { projectId: routeProjectId, episodeId, shotId } = useParams();
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const projectId = routeProjectId ?? searchParams.get("project") ?? undefined;
  const navigate = useNavigate();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const mobileNavTriggerRef = useRef<HTMLButtonElement>(null);
  const mobileNavRef = useRef<HTMLElement>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const previousPathRef = useRef(location.pathname);
  const projects = useQuery({ queryKey: queryKeys.projects.list({ limit: 100 }), queryFn: () => listProjects({ limit: 100 }) });
  const episodeCatalog = useQuery({
    queryKey: queryKeys.seasons.catalog(projectId ?? ""),
    queryFn: () => getProjectEpisodeCatalog(projectId!),
    enabled: Boolean(projectId),
  });
  const creatorSetup = useQuery({
    queryKey: queryKeys.projects.creatorSetup(projectId ?? ""),
    queryFn: () => getProjectCreatorSetup(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: 30_000,
  });
  const episodeGroups = episodeCatalog.data?.catalog.seasons ?? [];
  const selectedSeasonId = episodeGroups.find((group) => group.episodes.some((episode) => episode.id === episodeId))?.id
    ?? (episodeId ? "" : episodeGroups[0]?.id ?? "");
  const visibleEpisodes = episodeGroups.find((group) => group.id === selectedSeasonId)?.episodes ?? [];
  const storyboard = useQuery({
    queryKey: ["storyboard", episodeId],
    queryFn: () => getStoryboardWorkspace(episodeId!),
    enabled: Boolean(episodeId),
  });
  const episodeCockpit = useQuery({
    queryKey: queryKeys.episodes.cockpit(episodeId ?? ""),
    queryFn: () => getEpisodeCockpit(episodeId!),
    enabled: Boolean(episodeId),
    refetchInterval: 30_000,
  });
  const timelineStatus = useQuery({
    queryKey: ["episode", episodeId, "timeline-status"],
    queryFn: () => getEpisodeTimelineStatus(episodeId!),
    enabled: Boolean(episodeId),
    refetchInterval: 10_000,
  });
  const workerHealth = useQuery({ queryKey: ["system-health", "dependencies"], queryFn: () => loadHealth("dependencies"), refetchInterval: 30_000 });
  const workerReady = workerHealth.data?.checks.worker_supervisor?.startsWith("ready:") ?? false;
  // Remember the last episode a project was worked on so switching projects
  // does not silently drop the creation context a supervisor was in (C-09).
  const lastEpisodeKey = "local-drama:last-episode-by-project:v1";
  const rememberLastEpisode = (projectId: string, episodeId: string) => {
    if (!projectId || !episodeId) return;
    try {
      const memo: Record<string, string> = JSON.parse(window.localStorage.getItem(lastEpisodeKey) || "{}");
      memo[projectId] = episodeId;
      window.localStorage.setItem(lastEpisodeKey, JSON.stringify(memo));
    } catch { /* non-fatal */ }
  };
  const lastEpisodeForProject = (projectId: string): string | null => {
    try {
      const memo: Record<string, string> = JSON.parse(window.localStorage.getItem(lastEpisodeKey) || "{}");
      return memo[projectId] ?? null;
    } catch { return null; }
  };
  useEffect(() => {
    if (projectId && episodeId) rememberLastEpisode(projectId, episodeId);
  }, [projectId, episodeId]);
  const prefix = projectId ? `/projects/${projectId}` : "";
  const episodePrefix = episodeId ? `${prefix}/episodes/${episodeId}` : null;
  const milestones = creatorSetup.data?.setup.milestones;
  const configurationMissing = milestones
    ? Number(!milestones.production_plan_count.ready) + Number(!milestones.published_profile_binding_count.ready)
    : null;
  const cockpit = episodeCockpit.data;
  const selectedProject = projects.data?.items.find((item) => item.id === projectId);
  const selectedSeason = episodeGroups.find((group) => group.id === selectedSeasonId);
  const selectedEpisode = selectedSeason?.episodes.find((item) => item.id === episodeId);
  const selectedShot = storyboard.data?.storyboard.items.find((item) => item.id === shotId);
  const breadcrumbs = buildBreadcrumbs({
    pathname: location.pathname,
    projectTitle: selectedProject?.title,
    seasonTitle: selectedSeason?.title,
    episodeTitle: selectedEpisode?.title,
    shotCode: selectedShot?.code,
  });
  const latestTimeline = timelineStatus.data?.status?.timeline?.latest;
  const latestTimelineState = String(latestTimeline?.status ?? "").toUpperCase();
  const latestDelivery = timelineStatus.data?.status?.delivery?.latest;
  const latestDeliveryState = String(latestDelivery?.status ?? "").toUpperCase();
  const latestHumanReviewState = String(latestDelivery?.human_review_status ?? "").toUpperCase();
  const timelineIsStale = latestTimelineState === "STALE";
  const episodeBlockerCount = cockpit?.blockers.reduce((total, blocker) => total + blocker.count, 0) ?? 0;
  const currentEpisodeTask = location.pathname.match(/\/episodes\/[^/]+\/(plan|direct|generation|run|review|audio|timeline|delivery)(?:\/|$)/)?.[1] ?? "plan";
  const episodeTaskPath = (nextEpisodeId: string, nextShotId?: string) => {
    const base = `/projects/${encodeURIComponent(projectId ?? "")}/episodes/${encodeURIComponent(nextEpisodeId)}`;
    if (currentEpisodeTask === "direct" || currentEpisodeTask === "generation") {
      return `${base}/${currentEpisodeTask}${nextShotId ? `/${encodeURIComponent(nextShotId)}` : ""}`;
    }
    return `${base}/${currentEpisodeTask}`;
  };
  const commandContext = useMemo(() => ({ projectId, episodeId, shotId, navigate }), [episodeId, navigate, projectId, shotId]);
  useEffect(() => { setMobileNavOpen(false); }, [location.pathname, location.search]);
  useEffect(() => {
    if (previousPathRef.current === location.pathname) return;
    previousPathRef.current = location.pathname;
    workspaceRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);
  useEffect(() => {
    if (!mobileNavOpen) return;
    const previousBodyOverflow = document.body.style.overflow;
    const previousRootOverflow = document.documentElement.style.overflow;
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    const nav = mobileNavRef.current;
    const getFocusable = () => Array.from(nav?.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter((element) => element.getClientRects().length > 0);
    const initialFocus = nav?.querySelector<HTMLElement>(".nav-item.active") ?? getFocusable()[0];
    initialFocus?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobileNavOpen(false);
        mobileNavTriggerRef.current?.focus();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = getFocusable();
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousBodyOverflow;
      document.documentElement.style.overflow = previousRootOverflow;
    };
  }, [mobileNavOpen]);
  return (
    <main className="shell">
      <a className="skip-link" href="#v2-workspace-content">跳到工作区内容</a>
      <header className="topbar">
        <Link className="brand-lockup brand-link" to="/projects" aria-label="返回项目列表">
          <span className="brand-mark" aria-hidden="true"><StudioMarkIcon /></span>
          <div><p className="eyebrow">本地制片系统</p><h1>LocalDramaStudio</h1></div>
        </Link>
        <div className="topbar-context">
          <button ref={mobileNavTriggerRef} type="button" className="mobile-nav-toggle" aria-label="打开项目导航" aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen((value) => !value)}>菜单</button>
          <CommandPalette context={commandContext} baseCommands={navigationCommands} />
          <div className="context-selectors">
            <label>当前项目<select aria-label="当前项目" value={projectId ?? ""} onChange={(event) => {
              const nextProjectId = event.target.value;
              if (!nextProjectId) { navigate("/projects"); return; }
              const rememberedEpisode = lastEpisodeForProject(nextProjectId);
              navigate(rememberedEpisode ? `/projects/${nextProjectId}/episodes/${rememberedEpisode}/plan` : `/projects/${nextProjectId}`);
            }}>
              <option value="">所有项目</option>
              {projects.data?.items.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
            </select></label>
            {projectId && <label>季度<select aria-label="当前季度" value={selectedSeasonId} disabled={episodeCatalog.isPending || episodeGroups.length === 0} onChange={(event) => {
              const group = episodeGroups.find((item) => item.id === event.target.value);
              const nextEpisode = group?.episodes[0];
              if (nextEpisode) {
                rememberLastEpisode(projectId, nextEpisode.id);
                navigate(episodeTaskPath(nextEpisode.id));
              }
            }}>
              {episodeGroups.length === 0 && <option value="">无季度</option>}
              {episodeGroups.map((season) => <option key={season.id} value={season.id}>{season.title}</option>)}
            </select></label>}
            {projectId && <label>分集<select aria-label="当前分集" value={episodeId ?? ""} disabled={episodeCatalog.isPending || visibleEpisodes.length === 0} onChange={(event) => {
              const nextEpisodeId = event.target.value;
              if (!nextEpisodeId) return;
              rememberLastEpisode(projectId, nextEpisodeId);
              navigate(episodeTaskPath(nextEpisodeId));
            }}>
              {!episodeId && <option value="">选择分集</option>}
              {visibleEpisodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.title}</option>)}
            </select></label>}
            {episodeId && <label>镜头<select aria-label="当前镜头" value={shotId ?? ""} disabled={storyboard.isPending || (storyboard.data?.storyboard.items.length ?? 0) === 0} onChange={(event) => {
              const nextShotId = event.target.value;
              if (!nextShotId) return;
              navigate(
                currentEpisodeTask === "direct" || currentEpisodeTask === "generation"
                  ? episodeTaskPath(episodeId, nextShotId)
                  : `/projects/${encodeURIComponent(projectId ?? "")}/episodes/${encodeURIComponent(episodeId)}/direct/${encodeURIComponent(nextShotId)}`,
              );
            }}>
              {!shotId && <option value="">选择镜头</option>}
              {storyboard.data?.storyboard.items.map((shot) => <option key={shot.id} value={shot.id}>{shot.code}</option>)}
            </select></label>}
          </div>
          <LocalRuntimeIndicator />
        </div>
      </header>

      <div className="layout">
        {mobileNavOpen && <div className="mobile-nav-backdrop" role="presentation" onMouseDown={() => { setMobileNavOpen(false); mobileNavTriggerRef.current?.focus(); }} />}
        <nav ref={mobileNavRef} className={`sidebar${mobileNavOpen ? " mobile-open" : ""}`} aria-label="项目导航" onClick={(event) => { if ((event.target as Element).closest("a")) setMobileNavOpen(false); }}>
          <div className="sidebar-scroll">
          <span className="nav-title">工作空间</span>
          <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to="/projects" end>全部项目</NavLink>
          {projectId ? <>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={prefix} end>项目总览</NavLink>
            <span className="nav-title nav-section">创作区</span>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/story`}>故事{milestones && <NavFact tone={milestones.reviewable_story_draft_count.ready ? "ready" : "attention"}>{milestones.reviewable_story_draft_count.ready ? "已拆解" : "待拆解"}</NavFact>}</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/assets`}>资产圣经{milestones && <NavFact tone={milestones.active_story_asset_count.ready ? "ready" : "attention"}>{milestones.active_story_asset_count.count || "待建档"}</NavFact>}</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/qc-policies`}>质量策略</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/director-recipes`}>导演配方</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/production-settings`}>生产设置{configurationMissing !== null && <NavFact tone={configurationMissing === 0 ? "ready" : "attention"}>{configurationMissing === 0 ? "已配置" : `${configurationMissing} 待办`}</NavFact>}</NavLink>
            <span className="nav-title nav-section">工具区</span>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/canvas`}>高级画布</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/operations`}>项目维护</NavLink>
          </> : <p className="sidebar-context-hint">选择项目后显示资产圣经与分集生产导航。</p>}
          {episodePrefix ? <>
            <span className="nav-title nav-section">本集生产</span>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/plan`}>分集策划{cockpit && <NavFact tone={cockpit.shots.total > 0 ? "neutral" : "attention"}>{cockpit.shots.total > 0 ? `${cockpit.shots.total} 镜` : "待规划"}</NavFact>}</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/direct`}>导演台{cockpit && <NavFact tone={cockpit.shots.directed === cockpit.shots.total && cockpit.shots.total > 0 ? "ready" : "attention"}>{`${cockpit.shots.directed}/${cockpit.shots.total}`}</NavFact>}</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/generation${shotId ? `/${shotId}` : ""}`}>镜头生成{cockpit && <NavFact tone={cockpit.shots.failed > 0 ? "danger" : cockpit.shots.with_candidates === cockpit.shots.total && cockpit.shots.total > 0 ? "ready" : "attention"}>{cockpit.shots.failed > 0 ? `${cockpit.shots.failed} 失败` : `${cockpit.shots.with_candidates}/${cockpit.shots.total}`}</NavFact>}</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/run`}>整集生产{cockpit && <NavFact tone={episodeBlockerCount > 0 ? "danger" : cockpit.shots.total > 0 ? "ready" : "attention"}>{episodeBlockerCount > 0 ? `${episodeBlockerCount} 阻塞` : cockpit.shots.total > 0 ? "无阻塞" : "待规划"}</NavFact>}</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/review`}>审核{cockpit && <NavFact tone={cockpit.qc.failed > 0 ? "danger" : cockpit.qc.passed > 0 ? "ready" : "attention"}>{cockpit.qc.failed > 0 ? `${cockpit.qc.failed} 失败` : `${cockpit.qc.passed} 通过`}</NavFact>}</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/audio`}>声音{cockpit && <NavFact tone={cockpit.audio.bindings > 0 && cockpit.audio.verified === cockpit.audio.bindings ? "ready" : "attention"}>{cockpit.audio.bindings > 0 ? `${cockpit.audio.verified}/${cockpit.audio.bindings}` : "未配置"}</NavFact>}</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/timeline`}>时间线{timelineStatus.data && <NavFact tone={timelineIsStale ? "danger" : latestTimelineState === "FROZEN" ? "ready" : "attention"}>{timelineIsStale ? "已过期" : latestTimelineState === "FROZEN" ? "已冻结" : latestTimeline ? "待冻结" : "未创建"}</NavFact>}</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/delivery`}>交付{timelineStatus.data && <NavFact tone={["CORRUPT", "WITHDRAWN"].includes(latestDeliveryState) ? "danger" : latestHumanReviewState === "APPROVED" ? "ready" : "attention"}>{latestHumanReviewState === "APPROVED" ? "已批准" : latestDeliveryState === "VERIFIED" ? "待批准" : latestDelivery ? latestDeliveryState || "待处理" : "未创建"}</NavFact>}</NavLink>
          </> : <p className="sidebar-context-hint">进入具体分集后显示策划、导演、审核、声音、时间线与交付。</p>}
          <span className="nav-title nav-section">系统区</span>
          <div className="sidebar-system-links">
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`/lab${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`}>素材实验室</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`/models${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`}>模型与能力</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`/jobs${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`}>任务与机器</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`/diagnostics${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`}>诊断与审计</NavLink>
          </div>
          </div>
          <div className={`sidebar-foot${workerReady ? "" : " warning"}`} aria-live="polite">
            <span>{workerReady ? "本地 Worker 已常驻" : "本地 Worker 未运行"}</span>
            <small>{workerReady ? "关闭浏览器不会中断任务" : "运行 scripts/start-worker.ps1 恢复处理"}</small>
          </div>
        </nav>

        <section ref={workspaceRef} className="content v2-content" id="v2-workspace-content" tabIndex={-1}>
          {breadcrumbs.length > 1 && <nav className="workspace-breadcrumbs" aria-label="当前位置">
            <ol>{breadcrumbs.map((item, index) => <li key={`${item.label}-${index}`}>{index > 0 && <BreadcrumbSeparatorIcon />}{item.to && !item.isCurrent ? <Link to={item.to}>{item.label}</Link> : <span aria-current={item.isCurrent ? "page" : undefined}>{item.label}</span>}</li>)}</ol>
          </nav>}
          {timelineIsStale && episodePrefix && <aside className="workspace-stale-banner" role="alert">
            <div><strong>本集冻结时间线已过期</strong><span>采用镜头、声音或字幕已变化；旧冻结版本仍保留，但交付前必须基于最新事实复检。</span></div>
            <Link className="secondary" to={`${episodePrefix}/delivery`}>同步、复检并重新冻结</Link>
          </aside>}
          <Outlet />
        </section>
      </div>
    </main>
  );
}

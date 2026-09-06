import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useBlocker, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { BreadcrumbSeparatorIcon, StudioIcon, StudioMarkIcon, type StudioIconName } from "../components/icons";
import { buildBreadcrumbs, parseRouteContext, routes } from "../app/routeRegistry";
import { isTextEntryTarget, type StudioCommand } from "../features/commands/commandRegistry";
import { getProjectEpisodeCatalog, listProjects } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import { Dialog } from "../components/ui";
import { DRAFT_STATE_EVENT, retireWorkspaceTabPersistence, type DraftStateChange } from "../features/drafts/draftGuard";
import { ShellToolbar } from "./ShellToolbar";
import { EpisodeContextBar } from "./EpisodeContextBar";

const shellActions: StudioCommand[] = [
  { id: "action.quick-create", label: "快速生成", description: "生成独立画面或视频，不创建项目", group: "当前页面", keywords: ["一句话", "视频", "画面"], run: ({ navigate }) => navigate(routes.quickCreate()) },
  { id: "action.new-project", label: "新建项目", group: "当前页面", keywords: ["创建", "快速成片", "专业制片"], run: ({ navigate }) => navigate("/projects?create=1") },
  { id: "action.project-settings", label: "项目设置", group: "当前页面", enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(routes.settings(projectId!)) },
  { id: "action.system-jobs", label: "查看后台任务", group: "系统", run: ({ navigate, projectId }) => navigate(routes.systemJobs(projectId)) },
];

function StudioNavLink({ icon, to, end, children }: { icon: StudioIconName; to: string; end?: boolean; children: ReactNode }) {
  return <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={to} end={end}>
    <StudioIcon name={icon} className="nav-item__icon" />
    <span className="nav-item__label">{children}</span>
  </NavLink>;
}

const SIDEBAR_COLLAPSED_KEY = "local-drama:sidebar-collapsed:v1";
function readSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

const LAST_EPISODE_KEY = "local-drama:last-episode-by-project:v2";
function rememberEpisode(projectId: string, episodeId: string) {
  try {
    const memo: Record<string, string> = JSON.parse(window.localStorage.getItem(LAST_EPISODE_KEY) || "{}");
    memo[projectId] = episodeId;
    window.localStorage.setItem(LAST_EPISODE_KEY, JSON.stringify(memo));
  } catch { /* optional local history */ }
}

function episodeStagePath(projectId: string, episodeId: string, pathname: string) {
  const routeId = parseRouteContext(pathname).routeId;
  if (routeId === "shotStudio" || routeId === "shotStudioShot") return routes.shotStudio(projectId, episodeId);
  if (routeId === "episodeProduction") return routes.episodePlan(projectId, episodeId);
  if (routeId === "postAudio") return routes.postAudio(projectId, episodeId);
  if (routeId === "postEdit") return routes.postEdit(projectId, episodeId);
  if (routeId === "postReview") return routes.postReview(projectId, episodeId);
  if (routeId === "delivery") return routes.delivery(projectId, episodeId);
  return routes.episodePlan(projectId, episodeId);
}

export function AppShell() {
  const { projectId: routeProjectId } = useParams();
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  // A parent route's useParams can retain child params while an SPA
  // transition is being committed.  The pathname is the canonical route
  // context; only the project query fallback is allowed for global pages.
  const routeContext = parseRouteContext(location.pathname);
  const projectId = routeContext.projectId ?? routeProjectId ?? searchParams.get("project") ?? undefined;
  const episodeId = routeContext.episodeId ?? undefined;
  const shotId = routeContext.shotId ?? undefined;
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);
  const [draftDirty, setDraftDirty] = useState(false);
  const [draftActionPending, setDraftActionPending] = useState<"save" | "discard" | null>(null);
  const [draftActionError, setDraftActionError] = useState<string | null>(null);
  const draftStateRef = useRef<DraftStateChange | null>(null);
  const mobileNavTriggerRef = useRef<HTMLButtonElement>(null);
  const mobileNavRef = useRef<HTMLElement>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const previousPathRef = useRef(location.pathname);

  const toggleSidebar = () => {
    if (typeof window !== "undefined" && window.innerWidth <= 760) {
      setMobileNavOpen((open) => !open);
    } else {
      setSidebarCollapsed((collapsed) => {
        const next = !collapsed;
        try {
          window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
        } catch { /* optional local history */ }
        return next;
      });
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "b") {
        if (!isTextEntryTarget(event.target)) {
          event.preventDefault();
          toggleSidebar();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const currentShellActions = useMemo<StudioCommand[]>(() => [
    ...shellActions,
    {
      id: "action.toggle-sidebar",
      label: sidebarCollapsed ? "展开侧边栏" : "收起侧边栏",
      description: sidebarCollapsed ? "展开左侧主导航菜单" : "收起左侧主导航菜单以获得更大工作空间",
      group: "导航",
      shortcut: "Ctrl+B",
      keywords: ["菜单", "侧边栏", "收起", "展开", "主导航"],
      run: () => toggleSidebar(),
    },
  ], [sidebarCollapsed]);

  const projects = useQuery({ queryKey: queryKeys.projects.list({ limit: 100 }), queryFn: () => listProjects({ limit: 100 }) });
  const episodeCatalog = useQuery({
    queryKey: queryKeys.seasons.catalog(projectId ?? ""),
    queryFn: () => getProjectEpisodeCatalog(projectId!),
    enabled: Boolean(projectId),
  });
  const seasons = episodeCatalog.data?.catalog.seasons ?? [];
  const selectedProject = projects.data?.items.find((project) => project.id === projectId);
  const selectedSeason = seasons.find((season) => season.episodes.some((episode) => episode.id === episodeId));
  const selectedEpisode = selectedSeason?.episodes.find((episode) => episode.id === episodeId);
  const episodePrefix = projectId && episodeId ? `/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}` : null;
  const breadcrumbs = buildBreadcrumbs({
    pathname: location.pathname,
    projectTitle: selectedProject?.title,
    seasonTitle: selectedSeason?.title,
    episodeTitle: selectedEpisode?.title,
    shotCode: shotId ? `镜头 ${shotId}` : undefined,
  });
  const routeId = routeContext.routeId;
  const isStudioShotDesk = routeId === "shotStudioShot";
  const advancedNavigationActive = routeId === "settings" || routeId === "visualLabs" || routeId === "visualLab" || routeId?.startsWith("system") === true;
  const commandContext = useMemo(() => ({ projectId, episodeId, shotId, navigate }), [episodeId, navigate, projectId, shotId]);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => draftDirty && `${currentLocation.pathname}${currentLocation.search}${currentLocation.hash}` !== `${nextLocation.pathname}${nextLocation.search}${nextLocation.hash}`);

  useEffect(() => { retireWorkspaceTabPersistence(); }, []);
  useEffect(() => {
    const onDraftState = (event: Event) => {
      const detail = (event as CustomEvent<DraftStateChange>).detail;
      draftStateRef.current = detail.dirty ? detail : null;
      setDraftDirty(detail.dirty);
      if (!detail.dirty) setDraftActionError(null);
    };
    window.addEventListener(DRAFT_STATE_EVENT, onDraftState);
    return () => window.removeEventListener(DRAFT_STATE_EVENT, onDraftState);
  }, []);
  useEffect(() => {
    if (!draftDirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [draftDirty]);
  useEffect(() => { if (projectId && episodeId) rememberEpisode(projectId, episodeId); }, [episodeId, projectId]);
  useEffect(() => { setMobileNavOpen(false); }, [location.pathname, location.search]);
  useEffect(() => {
    if (previousPathRef.current === location.pathname) return;
    previousPathRef.current = location.pathname;
    workspaceRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);
  useEffect(() => {
    if (!mobileNavOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const nav = mobileNavRef.current;
    const focusable = () => Array.from(nav?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])') ?? []).filter((element) => element.getClientRects().length > 0);
    (nav?.querySelector<HTMLElement>(".nav-item.active") ?? focusable()[0])?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setMobileNavOpen(false); mobileNavTriggerRef.current?.focus(); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => { window.removeEventListener("keydown", onKeyDown); document.body.style.overflow = previousOverflow; };
  }, [mobileNavOpen]);

  const finishBlockedNavigation = async (action: "save" | "discard") => {
    const callback = action === "save" ? draftStateRef.current?.save : draftStateRef.current?.discard;
    setDraftActionPending(action);
    setDraftActionError(null);
    try {
      const result = await callback?.();
      if (result === false) {
        setDraftActionError(action === "save" ? "保存未完成，请处理页面中的错误后重试。" : "未能放弃当前修改，请在页面内处理后重试。");
        return;
      }
      draftStateRef.current = null;
      setDraftDirty(false);
      blocker.proceed?.();
    } catch (error) {
      setDraftActionError(`${action === "save" ? "保存" : "放弃"}失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setDraftActionPending(null);
    }
  };

  return <main className="shell">
    <a className="skip-link" href="#v2-workspace-content">跳到工作区内容</a>
    <header className="topbar">
      <div className="topbar-brand-section">
        <button
          type="button"
          className="sidebar-toggle-btn"
          aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
          title={sidebarCollapsed ? "展开侧边栏 (Ctrl+B)" : "收起侧边栏 (Ctrl+B)"}
          aria-expanded={!sidebarCollapsed}
          aria-controls="studio-sidebar-nav"
          onClick={toggleSidebar}
        >
          <StudioIcon name="sidebar" />
        </button>
        <Link className="brand-lockup brand-link" to={routes.home()} aria-label="返回全局工作台">
          <span className="brand-mark" aria-hidden="true"><StudioMarkIcon /></span>
          <div><p className="eyebrow">本地短剧制作</p><h1>AI 导演工作室</h1></div>
        </Link>
      </div>
      <ShellToolbar
        baseCommands={currentShellActions}
        commandContext={commandContext}
        episodeCatalogPending={episodeCatalog.isPending}
        episodeId={episodeId}
        mobileNavOpen={mobileNavOpen}
        mobileNavTriggerRef={mobileNavTriggerRef}
        onEpisodeChange={(nextEpisodeId) => {
          if (!projectId || !nextEpisodeId) return;
          rememberEpisode(projectId, nextEpisodeId);
          navigate(episodeStagePath(projectId, nextEpisodeId, location.pathname));
        }}
        onProjectChange={(nextProjectId) => navigate(nextProjectId ? routes.projectHome(nextProjectId) : routes.projects())}
        onToggleMobileNav={() => setMobileNavOpen((open) => !open)}
        projectId={projectId}
        projects={projects.data?.items ?? []}
        seasons={seasons}
        showEpisodeSwitcher={!episodeId}
      />
    </header>
    <div
      className={`layout${sidebarCollapsed ? " sidebar-collapsed" : ""}`}
      style={{ "--sidebar-width": sidebarCollapsed ? "0px" : "232px" } as React.CSSProperties}
    >
      {mobileNavOpen && <div className="mobile-nav-backdrop" role="presentation" onMouseDown={() => { setMobileNavOpen(false); mobileNavTriggerRef.current?.focus(); }} />}
      <nav
        ref={mobileNavRef}
        id="studio-sidebar-nav"
        className={`sidebar${mobileNavOpen ? " mobile-open" : ""}${sidebarCollapsed ? " collapsed" : ""}`}
        aria-label="主导航"
        aria-hidden={sidebarCollapsed && !mobileNavOpen ? "true" : undefined}
        onClick={(event) => { if ((event.target as Element).closest("a")) setMobileNavOpen(false); }}
      >
        {mobileNavOpen && (
          <div className="mobile-nav-header">
            <strong>导航</strong>
            <button type="button" aria-label="关闭主导航" onClick={() => { setMobileNavOpen(false); mobileNavTriggerRef.current?.focus(); }}>×</button>
          </div>
        )}
        <div className="sidebar-scroll">
          <span className="nav-title">工作区</span>
          <StudioNavLink icon="home" to={routes.home()} end>全局工作台</StudioNavLink>
          <StudioNavLink icon="sparkles" to={routes.quickCreate()} end>快速生成</StudioNavLink>
          <StudioNavLink icon="grid" to={routes.projects()} end>全部项目</StudioNavLink>
          {projectId ? <>
            <span className="nav-title nav-section">当前项目</span>
            <StudioNavLink icon="clapperboard" to={routes.projectHome(projectId)} end>项目首页</StudioNavLink>
            <StudioNavLink icon="book" to={routes.story(projectId)}>AI 制作</StudioNavLink>
            <StudioNavLink icon="assets" to={routes.assets(projectId)}>核心资产</StudioNavLink>
          </> : <p className="sidebar-context-hint">选择项目后显示 AI 制作、核心资产与本集制作流程。</p>}
          {projectId && !episodePrefix && <p className="sidebar-context-hint">从上方“分集”进入某一集，生成、修镜、后期和交付会始终显示在内容区顶部。</p>}
          <details className="sidebar-system" open={advancedNavigationActive || undefined}>
            <summary>高级设置与系统</summary>
            <div className="sidebar-system-links">
              {projectId && <>
                <StudioNavLink icon="sliders" to={routes.settings(projectId)}>项目设置</StudioNavLink>
                <StudioNavLink icon="workflow" to={routes.visualLabs(projectId)}>Visual Lab</StudioNavLink>
              </>}
              <StudioNavLink icon="cpu" to={routes.systemCapabilities(projectId)}>能力与模型</StudioNavLink>
              <StudioNavLink icon="activity" to={routes.systemJobs(projectId)}>后台任务</StudioNavLink>
              <StudioNavLink icon="shield" to={routes.systemDiagnostics(projectId)}>诊断与审计</StudioNavLink>
              <StudioNavLink icon="workflow" to={routes.systemWorkflows(projectId)}>工作流与环境</StudioNavLink>
            </div>
          </details>
        </div>
      </nav>
      <section ref={workspaceRef} className={`content v2-content${isStudioShotDesk ? " studio-desk-mode" : ""}`} id="v2-workspace-content" tabIndex={-1}>
        {breadcrumbs.length > 1 && <nav className="workspace-breadcrumbs" aria-label="当前位置"><ol>{breadcrumbs.map((item, index) => <li key={`${item.label}-${index}`}>{index > 0 && <BreadcrumbSeparatorIcon />}{item.to && !item.isCurrent ? <Link to={item.to} title={item.label}>{item.label}</Link> : <span aria-current={item.isCurrent ? "page" : undefined} title={item.label}>{item.label}</span>}</li>)}</ol></nav>}
        {projectId && episodeId && <EpisodeContextBar projectId={projectId} episodeId={episodeId} seasons={seasons} pathname={location.pathname} onEpisodeChange={(nextEpisodeId) => {
          rememberEpisode(projectId, nextEpisodeId);
          navigate(episodeStagePath(projectId, nextEpisodeId, location.pathname));
        }} />}
        <Outlet key={`${location.pathname}${location.search}`} />
      </section>
    </div>
    <Dialog open={blocker.state === "blocked"} title="当前页面有未保存内容" onClose={() => blocker.reset?.()} footer={<><button type="button" className="secondary" disabled={draftActionPending !== null} onClick={() => blocker.reset?.()}>取消切换</button><button type="button" className="secondary danger-outline" disabled={draftActionPending !== null} onClick={() => void finishBlockedNavigation("discard")}>{draftActionPending === "discard" ? "正在放弃…" : "放弃并切换"}</button><button type="button" className="primary-action" disabled={draftActionPending !== null} onClick={() => void finishBlockedNavigation("save")}>{draftActionPending === "save" ? "正在保存…" : "保存并切换"}</button></>}>
      <p>“保存并切换”会先调用当前工作台的正式保存动作；“放弃并切换”会清理当前实体的本地草稿并恢复最近一次正式版本。</p>
      {draftActionError && <p className="inline-error" role="alert">{draftActionError}</p>}
    </Dialog>
  </main>;
}

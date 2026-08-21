import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { StudioMarkIcon } from "../components/icons";
import { CommandPalette } from "../features/commands/CommandPalette";
import type { StudioCommand } from "../features/commands/commandRegistry";
import { LocalRuntimeIndicator } from "../features/status-v2/LocalRuntimeIndicator";
import { listProjects } from "../generated/api";

const navigationCommands: StudioCommand[] = [
  { id: "nav.projects", label: "打开全部项目", group: "导航", keywords: ["首页", "项目列表"], run: ({ navigate }) => navigate("/projects") },
  { id: "nav.project", label: "打开项目总览", group: "导航", enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}`) },
  { id: "nav.story", label: "打开故事工作区", group: "创作", keywords: ["长文", "剧本", "拆解", "故事圣经"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/story`) },
  { id: "nav.assets", label: "打开资产圣经", group: "创作", keywords: ["角色", "场景", "三视图"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/assets`) },
  { id: "nav.qc-policy", label: "打开 QC 策略", group: "创作", keywords: ["自动重抽", "质量"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/qc-policies`) },
  { id: "nav.director-recipes", label: "打开导演配方", group: "创作", keywords: ["Recipe", "版本", "生产策略"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/director-recipes`) },
  { id: "nav.production-settings", label: "打开生产设置", group: "创作", keywords: ["配置", "偏好", "QC", "配方", "容量"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/production-settings`) },
  { id: "nav.operations", label: "打开项目维护", group: "系统", keywords: ["迁移", "授权", "自动化", "健康", "审计"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/operations`) },
  { id: "nav.canvas", label: "打开高级画布", group: "创作", keywords: ["DAG", "依赖", "分支实验"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/canvas`) },
  { id: "nav.lab", label: "打开素材实验室", group: "工具", keywords: ["Comfy", "沙盒", "工作流"], enabled: ({ projectId }) => Boolean(projectId), run: ({ navigate, projectId }) => navigate(`/projects/${projectId}/lab`) },
  { id: "nav.episode-plan", label: "打开分集策划", group: "创作", keywords: ["原文", "拆解", "镜头计划"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/plan`) },
  { id: "nav.director", label: "打开导演台", group: "创作", keywords: ["镜头", "候选", "首尾帧"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId, shotId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/direct${shotId ? `/${shotId}` : ""}`) },
  { id: "nav.manual-generation", label: "打开手动生成", group: "工具", keywords: ["preflight", "Variant", "Job", "高级"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId, shotId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/generation${shotId ? `/${shotId}` : ""}`) },
  { id: "nav.episode-run", label: "打开整集生产", group: "创作", keywords: ["生成剩余", "暂停", "恢复"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/run`) },
  { id: "nav.review", label: "打开本集审核", group: "创作", enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/review`) },
  { id: "nav.audio", label: "打开声音工作区", group: "创作", keywords: ["TTS", "BGM", "SFX"], enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/audio`) },
  { id: "nav.timeline", label: "打开时间线", group: "创作", enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/timeline`) },
  { id: "nav.delivery", label: "打开交付与导出", group: "创作", enabled: ({ projectId, episodeId }) => Boolean(projectId && episodeId), run: ({ navigate, projectId, episodeId }) => navigate(`/projects/${projectId}/episodes/${episodeId}/delivery`) },
  { id: "nav.models", label: "打开模型与能力", group: "系统", run: ({ navigate, projectId }) => navigate(`/models${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`) },
  { id: "nav.jobs", label: "打开任务与机器", group: "系统", run: ({ navigate, projectId }) => navigate(`/jobs${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`) },
  { id: "nav.diagnostics", label: "打开诊断", group: "系统", run: ({ navigate, projectId }) => navigate(`/diagnostics${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`) },
];

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
  const projects = useQuery({ queryKey: ["projects", "shell-switcher"], queryFn: () => listProjects({ limit: 100 }) });
  const prefix = projectId ? `/projects/${projectId}` : "";
  const episodePrefix = episodeId ? `${prefix}/episodes/${episodeId}` : null;
  const commandContext = useMemo(() => ({ projectId, episodeId, shotId, navigate }), [episodeId, navigate, projectId, shotId]);
  return (
    <main className="shell">
      <a className="skip-link" href="#v2-workspace-content">跳到工作区内容</a>
      <header className="topbar">
        <Link className="brand-lockup brand-link" to="/projects" aria-label="返回项目列表">
          <span className="brand-mark" aria-hidden="true"><StudioMarkIcon /></span>
          <div><p className="eyebrow">本地制片系统</p><h1>LocalDramaStudio</h1></div>
        </Link>
        <div className="topbar-context">
          <CommandPalette context={commandContext} baseCommands={navigationCommands} />
          <div className="context-selectors">
            <label>当前项目<select aria-label="当前项目" value={projectId ?? ""} onChange={(event) => navigate(event.target.value ? `/projects/${event.target.value}` : "/projects")}>
              <option value="">所有项目</option>
              {projects.data?.items.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
            </select></label>
          </div>
          <LocalRuntimeIndicator />
        </div>
      </header>

      <div className="layout">
        <nav className="sidebar" aria-label="项目导航">
          <span className="nav-title">创作区</span>
          <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to="/projects" end>全部项目</NavLink>
          {projectId ? <>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={prefix} end>项目总览</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/story`}>故事</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/assets`}>资产圣经</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/qc-policies`}>QC 策略</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/director-recipes`}>导演配方</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/production-settings`}>生产设置</NavLink>
            <span className="nav-title nav-section">工具区</span>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/canvas`}>高级画布</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/lab`}>素材实验室</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${prefix}/operations`}>项目维护</NavLink>
          </> : <p className="sidebar-context-hint">选择项目后显示资产圣经与分集生产导航。</p>}
          {episodePrefix ? <>
            <span className="nav-title nav-section">本集生产</span>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/plan`}>分集策划</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/direct`}>导演台</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/generation${shotId ? `/${shotId}` : ""}`}>手动生成</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/run`}>整集生产</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/review`}>审核</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/audio`}>声音</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/timeline`}>时间线</NavLink>
            <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`${episodePrefix}/delivery`}>交付</NavLink>
          </> : <p className="sidebar-context-hint">进入具体分集后显示策划、导演、审核、声音、时间线与交付。</p>}
          <details className="sidebar-system" open={/^\/(models|jobs|diagnostics)(?:\/|$)/.test(location.pathname) || undefined}>
            <summary className="nav-title nav-section">系统区</summary>
            <div className="sidebar-system-links">
              <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`/models${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`}>模型与能力</NavLink>
              <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`/jobs${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`}>任务与机器</NavLink>
              <NavLink className={({ isActive }) => `nav-item${isActive ? " active" : ""}`} to={`/diagnostics${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`}>诊断与审计</NavLink>
            </div>
          </details>
          <div className="sidebar-foot"><span>本地任务持续运行</span><small>关闭浏览器不会中断 Worker</small></div>
        </nav>

        <section className="content v2-content" id="v2-workspace-content" aria-live="polite" tabIndex={-1}>
          <Outlet />
        </section>
      </div>
    </main>
  );
}

import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { routes } from "../app/routeRegistry";
import { StudioIcon, type StudioIconName } from "../components/icons";
import { MediaThumb } from "../components/ui";
import { ProjectCreateWizard } from "../features/projects/ProjectCreateWizard";
import { isProductionRuntimeHealthy, loadRuntimeHealth, runtimeHealthQueryKeys } from "../features/status-v2/runtimeHealth";
import { getCapacitySnapshot, listProfiles, listProjects, listWorkflowVersions } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import { partitionRecentProjects, type ProjectWithUpdatedAt } from "./projectRecency";
import "./home-workspace.css";

const PROJECT_STATUS_LABELS: Record<string, string> = {
  ACTIVE: "生产中",
  DRAFT: "草稿",
  PAUSED: "已暂停",
  ARCHIVED: "已归档",
};

type HomeProject = ProjectWithUpdatedAt & {
  preview_media_version_id?: string | null;
  preview_media_kind?: "IMAGE" | "VIDEO" | null;
  preview_has_thumbnail?: boolean;
};

function ProjectPreview({ project }: { project: HomeProject }) {
  const mediaVersionId = project.preview_has_thumbnail ? project.preview_media_version_id : null;
  const src = mediaVersionId
    ? `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=small&frame=poster`
    : null;
  return <div className="home-project-row__preview">
    <MediaThumb
      src={src}
      alt={`${project.title} 项目缩略图`}
      emptyLabel="暂无图片或视频"
      objectFit="cover"
      className="home-project-row__media"
    />
    {src && project.preview_media_kind === "VIDEO" ? <small className="home-project-row__media-kind">视频</small> : null}
  </div>;
}

function formatBytes(bytes: number | null | undefined) {
  if (bytes === null || bytes === undefined) return "—";
  const gib = bytes / 1024 ** 3;
  return `${gib >= 100 ? gib.toFixed(0) : gib.toFixed(1)} GB`;
}

function formatActivity(value: string | null | undefined) {
  if (!value) return "暂无更新时间";
  const parsed = new Date(value.includes("T") ? value : `${value.replace(" ", "T")}Z`);
  if (Number.isNaN(parsed.getTime())) return "暂无更新时间";
  return `${new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(parsed)} 更新`;
}

function SystemEntry({ icon, title, description, fact, tone = "neutral", to }: {
  icon: StudioIconName;
  title: string;
  description: string;
  fact: string;
  tone?: "neutral" | "ready" | "attention";
  to: string;
}) {
  return <Link className="home-system-entry" to={to}>
    <span className="home-system-entry__icon" aria-hidden="true"><StudioIcon name={icon} /></span>
    <span className="home-system-entry__body"><strong>{title}</strong><small>{description}</small></span>
    <span className={`home-system-entry__fact ${tone}`}>{fact}</span>
  </Link>;
}

/** Global entry for creation, recent work and machine-level configuration. */
export function HomePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const projects = useQuery({ queryKey: queryKeys.projects.list({ limit: 100 }), queryFn: () => listProjects({ limit: 100 }) });
  const profiles = useQuery({ queryKey: queryKeys.profiles.list(), queryFn: () => listProfiles() });
  const workflows = useQuery({ queryKey: queryKeys.workflows.versions(), queryFn: () => listWorkflowVersions() });
  const capacity = useQuery({ queryKey: queryKeys.capacity.scope(), queryFn: () => getCapacitySnapshot(), refetchInterval: 5_000 });
  const ready = useQuery({ queryKey: runtimeHealthQueryKeys.ready, queryFn: () => loadRuntimeHealth("ready"), refetchInterval: 30_000 });
  const dependencies = useQuery({ queryKey: runtimeHealthQueryKeys.dependencies, queryFn: () => loadRuntimeHealth("dependencies"), refetchInterval: 30_000 });

  const projectItems = (projects.data?.items ?? []) as HomeProject[];
  const recentProjects = useMemo(() => partitionRecentProjects(projectItems, 4).recentProjects, [projectItems]);
  const activeProjectCount = projectItems.filter((project) => project.status === "ACTIVE").length;
  const publishedProfileCount = (profiles.data?.items ?? []).filter((profile) => profile.status === "PUBLISHED").length;
  const publishedWorkflowCount = (workflows.data?.items ?? []).filter((workflow) => workflow.status === "PUBLISHED").length;
  const runtimePending = ready.isPending || dependencies.isPending;
  const runtimeUnavailable = ready.isError || dependencies.isError;
  const runtimeHealthy = !runtimeUnavailable && isProductionRuntimeHealthy(ready.data, dependencies.data);
  const runtimeFact = runtimePending ? "检查中" : runtimeUnavailable ? "状态不可用" : runtimeHealthy ? "生产环境正常" : "需要处理";
  const runtimeTone = runtimePending ? "neutral" : runtimeHealthy ? "ready" : "attention";

  return <div className="v2-page home-workspace">
    <header className="home-overview" aria-labelledby="home-title">
      <div>
        <p className="eyebrow">全局工作台</p>
        <h2 id="home-title">今天从哪里继续？</h2>
        <p className="muted">先看项目进展与本机生产状态，再进入具体创作环节。系统配置和运行维护始终可以从这里到达。</p>
      </div>
      <div className="home-overview__actions">
        <ProjectCreateWizard
          onCreated={(created) => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.projects.lists() });
            navigate(routes.story(created.id));
          }}
        />
        <Link className="secondary" to={routes.projects()}>查看全部项目</Link>
      </div>
    </header>

    <section className="home-metrics" aria-label="制作与机器概览">
      <dl>
        <div><dt>项目</dt><dd>{projects.isPending ? "—" : projectItems.length}<small>{activeProjectCount} 个生产中</small></dd></div>
        <div><dt>运行任务</dt><dd>{capacity.data?.snapshot.active_attempt_count ?? "—"}<small>{capacity.data?.snapshot.active_worker_count ?? "—"} 个 Worker</small></dd></div>
        <div><dt>排队任务</dt><dd>{capacity.data?.snapshot.queued_count ?? "—"}<small>本机持久化队列</small></dd></div>
        <div><dt>24 小时完成</dt><dd>{capacity.data?.snapshot.completed_last_24h ?? "—"}<small>已结束任务</small></dd></div>
        <div><dt>磁盘可用</dt><dd>{formatBytes(capacity.data?.snapshot.disk?.free_bytes)}<small>{capacity.data?.snapshot.disk?.source || "等待本机观测"}</small></dd></div>
      </dl>
      {capacity.error && <p className="home-metrics__error" role="status">产能数据暂不可用，任务与项目仍可正常访问。</p>}
    </section>

    <div className="home-primary-grid">
      <section className="home-recent" aria-labelledby="home-recent-title">
        <div className="home-section-heading">
          <div><p className="eyebrow">继续制作</p><h3 id="home-recent-title">最近项目</h3></div>
          <Link to={routes.projects()}>管理全部项目</Link>
        </div>
        {projects.isPending ? <div className="home-list-placeholder" role="status">正在读取本地项目…</div> : projects.error ? (
          <div className="home-list-placeholder error" role="alert">项目读取失败，请进入项目页重试。</div>
        ) : recentProjects.length === 0 ? (
          <div className="home-list-placeholder"><strong>还没有项目</strong><span>使用右上角“新建项目”建立第一条制作线。</span></div>
        ) : <div className="home-project-list">{recentProjects.map((project) => <Link key={project.id} to={routes.projectHome(project.id)} className="home-project-row" aria-label={`${project.title}：打开项目首页`}>
          <ProjectPreview project={project as HomeProject} />
          <span className="home-project-row__title"><strong>{project.title}</strong><small>{project.code} · {formatActivity(project.updated_at)}</small></span>
          <span className={`status-pill${project.status === "ACTIVE" ? "" : " neutral"}`}>{PROJECT_STATUS_LABELS[project.status] ?? project.status}</span>
          <span className="home-project-row__action">继续制作</span>
        </Link>)}</div>}
      </section>

      <aside className="home-readiness" aria-labelledby="home-readiness-title">
        <div className="home-section-heading"><div><p className="eyebrow">开机状态</p><h3 id="home-readiness-title">生产准备</h3></div></div>
        <div className={`home-runtime-state ${runtimeTone}`}>
          <span className="home-runtime-state__dot" aria-hidden="true" />
          <div><strong>{runtimeFact}</strong><small>{runtimeHealthy ? "核心依赖、Worker 与生产 Profile 可用" : runtimePending ? "正在确认数据库、FFmpeg、ComfyUI 与 Worker" : "打开诊断查看具体异常与处置方式"}</small></div>
        </div>
        <ul className="home-readiness__facts">
          <li><span>已发布能力</span><strong>{profiles.isPending ? "—" : publishedProfileCount}</strong></li>
          <li><span>已发布工作流</span><strong>{workflows.isPending ? "—" : publishedWorkflowCount}</strong></li>
          <li><span>GPU 并发</span><strong>{capacity.data ? `${capacity.data.snapshot.gpu_active_count}/${capacity.data.snapshot.gpu_concurrency_limit}` : "—"}</strong></li>
        </ul>
        <Link className="secondary home-readiness__action" to={routes.systemDiagnostics()}>查看本机诊断</Link>
      </aside>
    </div>

    <section className="home-system" aria-labelledby="home-system-title">
      <div className="home-section-heading">
        <div><p className="eyebrow">系统中心</p><h3 id="home-system-title">配置与运行维护</h3><p>系统级能力不属于某一个项目，配置一次后由各项目按版本绑定使用。</p></div>
      </div>
      <nav className="home-system-grid" aria-label="系统配置与维护入口">
        <SystemEntry icon="cpu" title="能力与模型" description="配置模型、执行 Profile、连接与兼容性" fact={profiles.isPending ? "读取中" : `${publishedProfileCount} 个已发布`} tone={publishedProfileCount > 0 ? "ready" : "attention"} to={routes.systemCapabilities()} />
        <SystemEntry icon="activity" title="任务与机器" description="查看队列、执行记录、Worker 与 GPU 占用" fact={capacity.isPending ? "读取中" : `${capacity.data?.snapshot.active_attempt_count ?? 0} 运行 · ${capacity.data?.snapshot.queued_count ?? 0} 排队`} tone={(capacity.data?.snapshot.queued_count ?? 0) > 0 ? "attention" : "neutral"} to={routes.systemJobs()} />
        <SystemEntry icon="shield" title="诊断与审计" description="检查本机依赖、审计历史与跨项目检索" fact={runtimeFact} tone={runtimeTone} to={routes.systemDiagnostics()} />
        <SystemEntry icon="workflow" title="工作流与环境" description="管理可发布工作流和本地素材实验环境" fact={workflows.isPending ? "读取中" : `${publishedWorkflowCount} 个已发布`} tone={publishedWorkflowCount > 0 ? "ready" : "neutral"} to={routes.systemWorkflows()} />
      </nav>
    </section>
  </div>;
}

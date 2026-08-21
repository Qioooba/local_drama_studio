import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ProjectCreateWizard } from "../features/projects/ProjectCreateWizard";
import { listProfiles, listProjects, type Project } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import { partitionRecentProjects, type ProjectWithUpdatedAt } from "./projectRecency";

const RECENT_PROJECT_LIMIT = 4;
const statusLabels: Record<string, string> = {
  ACTIVE: "生产中", DRAFT: "待配置", PAUSED: "已暂停", ARCHIVED: "已归档",
};

function nextStep(project: Project) {
  if (project.status === "DRAFT") return "完成项目设定";
  if (project.status === "PAUSED") return "查看暂停原因";
  if (project.status === "ARCHIVED") return "查看归档项目";
  return "继续生产";
}

/** Creator-first project entry. Each project exposes one clear next action. */
export function ProjectsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const projects = useQuery({
    queryKey: queryKeys.projects.list({ limit: 100 }),
    queryFn: () => listProjects({ limit: 100 }),
  });
  const profiles = useQuery({ queryKey: queryKeys.profiles.list(), queryFn: () => listProfiles() });
  const visibleProjects = useMemo<ProjectWithUpdatedAt[]>(() => {
    const term = search.trim().toLocaleLowerCase();
    return ((projects.data?.items ?? []) as ProjectWithUpdatedAt[]).filter((project) => {
      const matchesTerm = !term || `${project.title} ${project.code}`.toLocaleLowerCase().includes(term);
      return matchesTerm && (!status || project.status === status);
    });
  }, [projects.data?.items, search, status]);
  const { recentProjects, otherProjects } = useMemo(
    () => partitionRecentProjects(visibleProjects, RECENT_PROJECT_LIMIT),
    [visibleProjects],
  );

  const projectCards = (items: ProjectWithUpdatedAt[]) => items.map((project) => (
    <article className="project-card" key={project.id}>
      <div className="project-card-heading">
        <span className={`status-pill${project.status === "ACTIVE" ? "" : " neutral"}`}>{statusLabels[project.status] ?? project.status}</span>
        <small>修订 {project.revision}</small>
      </div>
      <div><h3>{project.title}</h3><p className="project-code">{project.code}</p></div>
      <Link className="primary-action project-next-action" to={`/projects/${project.id}`} aria-label={`${project.title}：${nextStep(project)}`}>{nextStep(project)}</Link>
    </article>
  ));

  return (
    <div className="v2-page projects-page">
      <div className="projects-hero">
        <div><p className="eyebrow">项目工作区</p><h2>从一个项目继续创作</h2><p className="muted">选择现有项目继续下一步，或创建一个新的本地项目。</p></div>
        <ProjectCreateWizard
          profiles={profiles.data?.items ?? []}
          onCreated={(created) => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.projects.lists() });
            navigate(`/projects/${created.id}`);
          }}
        />
      </div>

      <div className="project-filters projects-page-filters" role="search" aria-label="筛选项目">
        <label>搜索项目<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="标题或 code" /></label>
        <label>项目状态<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option><option value="ACTIVE">生产中</option><option value="DRAFT">待配置</option><option value="PAUSED">已暂停</option><option value="ARCHIVED">已归档</option></select></label>
      </div>

      {projects.isPending && <p className="empty-state" role="status">正在读取本地项目…</p>}
      {projects.error && <p className="inline-error" role="alert">项目读取失败：{projects.error.message}</p>}
      {!projects.isPending && !projects.error && visibleProjects.length === 0 && (
        <section className="panel projects-empty" aria-labelledby="projects-empty-title">
          <p className="eyebrow">尚未开始</p>
          <h3 id="projects-empty-title">{projects.data?.items.length ? "没有符合筛选条件的项目" : "创建你的第一个项目"}</h3>
          <p className="muted">{projects.data?.items.length ? "调整搜索词或状态筛选即可恢复项目列表。" : "创建向导会先做只读预检，确认后才写入本地项目。"}</p>
        </section>
      )}

      {recentProjects.length > 0 && <section aria-labelledby="recent-projects-title">
        <div className="panel-heading"><h3 id="recent-projects-title">最近项目</h3><span className="muted">按最近更新排序</span></div>
        <div className="projects-grid">{projectCards(recentProjects)}</div>
      </section>}
      {otherProjects.length > 0 && <section aria-labelledby="other-projects-title">
        <div className="panel-heading"><h3 id="other-projects-title">其他项目</h3></div>
        <div className="projects-grid">{projectCards(otherProjects)}</div>
      </section>}
    </div>
  );
}

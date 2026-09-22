import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ProjectCreateWizard } from "../features/projects/ProjectCreateWizard";
import { ErrorState } from "../components/ui";
import { listProjects, type ProjectListPage } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import { partitionRecentProjects, type ProjectWithUpdatedAt } from "./projectRecency";

const RECENT_PROJECT_LIMIT = 4;
const PROJECT_PAGE_SIZE = 50;
const statusLabels: Record<string, string> = {
  ACTIVE: "生产中", DRAFT: "草稿", PAUSED: "已暂停", ARCHIVED: "已归档",
};

const projectEntryLabel = "打开项目概览";

const loadProjectPage = (cursor: number, search: string, status: string) =>
  listProjects({ search: search || undefined, status: status || undefined, cursor, limit: PROJECT_PAGE_SIZE });

/** Creator-first project entry. Each project exposes one clear next action. */
export function ProjectsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get("q") ?? "";
  const status = searchParams.get("status") ?? "";
  const requestedPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const [searchDraft, setSearchDraft] = useState(search);

  // The server owns search and paging: filtering only what page 1 happened to
  // return would make project 101 undiscoverable.
  const projects = useInfiniteQuery({
    queryKey: queryKeys.projects.list({ search, status, limit: PROJECT_PAGE_SIZE }),
    queryFn: ({ pageParam }) => loadProjectPage(pageParam, search, status),
    initialPageParam: 0,
    getNextPageParam: (lastPage: ProjectListPage) => lastPage.page?.next_cursor ?? undefined,
  });

  const pages = projects.data?.pages ?? [];
  const lastPage = pages[pages.length - 1];
  const projectItems = useMemo<ProjectWithUpdatedAt[]>(() => {
    const byId = new Map<string, ProjectWithUpdatedAt>();
    for (const page of pages) for (const project of page.items) byId.set(String(project.id), project as ProjectWithUpdatedAt);
    return [...byId.values()];
  }, [pages]);
  const { recentProjects, otherProjects } = useMemo(
    () => partitionRecentProjects(projectItems, RECENT_PROJECT_LIMIT),
    [projectItems],
  );

  useEffect(() => setSearchDraft(search), [search]);

  // `?page=N` is the refresh restore entry: fetch forward until the requested page exists.
  const restoring = useRef(false);
  const unavailablePages = useRef(0);
  useEffect(() => {
    const target = requestedPage - 1;
    if (projects.isPending || projects.isFetchingNextPage) return;
    if (pages.length > target) { restoring.current = false; unavailablePages.current = 0; return; }
    if (!projects.hasNextPage) {
      // The requested page no longer exists (for example its only row was deleted).
      // Fall back to the last valid page instead of showing an empty list.
      if (requestedPage > pages.length && pages.length > 0) {
        unavailablePages.current = pages.length;
        applyParams({ page: pages.length <= 1 ? null : String(pages.length) });
      }
      return;
    }
    if (unavailablePages.current === requestedPage || restoring.current) return;
    restoring.current = true;
    void projects.fetchNextPage().finally(() => { restoring.current = false; });
  }, [pages.length, projects, requestedPage]);
  useEffect(() => { restoring.current = false; unavailablePages.current = 0; }, [search, status]);

  const applyParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value); else next.delete(key);
    }
    setSearchParams(next, { replace: true });
  };
  const goToPage = (page: number) => applyParams({ page: page <= 1 ? null : String(page), q: search || null, status: status || null });

  const empty = !projects.isPending && !projects.error && projectItems.length === 0;
  const filtered = Boolean(search.trim() || status);

  const projectCards = (items: ProjectWithUpdatedAt[]) => items.map((project, index) => (
    <article className={`project-card project-card--tone-${index % 4}`} key={project.id}>
      <div className="project-card__visual" aria-hidden="true">
        <span className="project-card__frame">{String(index + 1).padStart(2, "0")}</span>
        <span className="project-card__monogram">{project.title.trim().slice(0, 1).toLocaleUpperCase() || "L"}</span>
        <span className="project-card__format">本地制作</span>
      </div>
      <div className="project-card-heading">
        <span className={`status-pill${project.status === "ACTIVE" ? "" : " neutral"}`}>{statusLabels[project.status] ?? project.status}</span>
        <small>修订 {project.revision}</small>
      </div>
      <div className="project-card__body"><h3 title={project.title}>{project.title}</h3><p className="project-code" title={project.code}>{project.code}</p></div>
      <Link className="secondary project-next-action" to={`/projects/${project.id}`} aria-label={`${project.title}：${projectEntryLabel}`}>{projectEntryLabel}</Link>
    </article>
  ));

  return (
    <div className="v2-page projects-page">
      <div className="projects-hero">
        <div><p className="eyebrow">创作项目</p><h2>让每个故事都有自己的片场</h2><p className="muted">从最近项目继续导演，或开启一条从故事、分镜到成片的全新制作线。</p></div>
        <ProjectCreateWizard
          onCreated={(created) => {
            void queryClient.invalidateQueries({ queryKey: queryKeys.projects.lists() });
            navigate(`/projects/${created.id}/story#story-import`);
          }}
        />
      </div>

      <form
        className="project-filters projects-page-filters"
        role="search"
        aria-label="筛选项目"
        onSubmit={(event) => { event.preventDefault(); applyParams({ q: searchDraft.trim() || null, page: null }); }}
      >
        <label>搜索项目<input aria-label="搜索项目" value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="输入标题或项目编号" /></label>
        <label>项目状态<select aria-label="项目状态" value={status} onChange={(event) => applyParams({ status: event.target.value || null, page: null })}><option value="">全部状态</option><option value="ACTIVE">生产中</option><option value="DRAFT">草稿</option><option value="PAUSED">已暂停</option><option value="ARCHIVED">已归档</option></select></label>
        <button type="submit" className="secondary">搜索全部项目</button>
        {filtered && <button type="button" className="secondary" onClick={() => { setSearchDraft(""); applyParams({ q: null, status: null, page: null }); }}>清除筛选</button>}
      </form>

      <p className="muted projects-page-count" role="status" aria-live="polite">
        已加载 {projectItems.length} 个项目{projects.hasNextPage ? "（还有更多）" : "（已到末页）"}
        {search.trim() ? ` · 搜索“${search.trim()}”在服务器全库执行` : ""}
      </p>

      {projects.isPending && <p className="empty-state" role="status">正在读取本地项目…</p>}
      {projects.error && <ErrorState title="项目读取失败" description={projects.error.message} onRetry={() => void projects.refetch()} />}
      {empty && (
        <section className="panel projects-empty" aria-labelledby="projects-empty-title">
          <p className="eyebrow">尚未开始</p>
          <h3 id="projects-empty-title">{filtered ? "没有符合条件的项目" : "创建你的第一个项目"}</h3>
          <p className="muted">{filtered ? "清除筛选或换一个搜索词；搜索与状态筛选都在服务器全库执行，不受已加载页数限制。" : "创建向导会先做只读预检，确认后才写入本地项目。"}</p>
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

      {!empty && <nav className="project-pager" aria-label="项目分页">
        <button type="button" className="secondary" disabled={requestedPage <= 1 || projects.isFetching} onClick={() => goToPage(requestedPage - 1)}>上一页</button>
        <span className="muted" role="status">第 {requestedPage} 页 · 本页 {lastPage?.items.length ?? 0} 项 · 服务端页大小 {PROJECT_PAGE_SIZE}</span>
        <button type="button" className="secondary" disabled={!projects.hasNextPage || projects.isFetchingNextPage} onClick={() => { goToPage(requestedPage + 1); void projects.fetchNextPage(); }}>{projects.isFetchingNextPage ? "读取中…" : "下一页"}</button>
      </nav>}
    </div>
  );
}

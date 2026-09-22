import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, useLocation, useParams } from "react-router-dom";
import { EXPLAINER_PAGES, EXPLAINER_PAGE_LABELS, parseRouteContext, routes, type ExplainerPage } from "../../app/routeRegistry";
import { getExplainerOverview } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { runStatusLabel } from "./viewModels";
import "./explainers.css";

/**
 * Explainer workspace shell.
 *
 * Two deliberate deviations from the drama workspace:
 *
 * * Page switching uses query-free links, and the parent `Outlet` is keyed by
 *   route id rather than by the full search string, so using `?issue=` or
 *   `?locale=` to locate a problem does not remount the editor and lose drafts
 *   (design §5.4 / UI-07).
 * * There is no season, episode, dialogue-completion or whole-drama delivery
 *   surface anywhere in this subtree.
 */
export function ExplainerWorkspaceShell() {
  const { projectId = "" } = useParams();
  const location = useLocation();
  const context = parseRouteContext(location.pathname);
  const activePage: ExplainerPage = (context.explainerPage as ExplainerPage | undefined) ?? "overview";

  const overview = useQuery({
    queryKey: queryKeys.explainers.workspace(projectId),
    queryFn: () => getExplainerOverview(projectId),
    enabled: Boolean(projectId),
    retry: 1,
  });

  const title = overview.data?.video?.title ?? "解说作品";
  const runStatus = overview.data?.latest_run?.projected_status ?? null;
  const openIssues = overview.data?.open_issue_count ?? 0;
  const blockingIssues = overview.data?.blocking_issue_count ?? 0;

  return <div className="explainer-workspace">
    <header className="explainer-header">
      <div>
        <p className="eyebrow">EXPLAINER WORKSPACE</p>
        <h1>{title}</h1>
        <div className="explainer-meta">
          <span className="badge">解说作品</span>
          {overview.data?.video?.content_kind === "ORIGINAL_FICTION"
            ? <span className="badge warn">原创虚构</span>
            : <span className="badge">事实解说</span>}
          {overview.data?.video?.target_seconds
            ? <span className="badge">目标 {Math.round(overview.data.video.target_seconds / 60)} 分钟</span>
            : null}
          {overview.data?.video?.automation_mode
            ? <span className="badge">{automationLabel(overview.data.video.automation_mode)}</span>
            : null}
        </div>
      </div>
      <div className="explainer-header-actions">
        {runStatus ? <span className={`badge ${runStatus === "FAILED" ? "danger" : runStatus === "WAITING_INPUT" ? "warn" : "blue"}`}>{runStatusLabel(runStatus)}</span> : null}
        <NavLink className="explainer-issue-link" to={routes.explainerPage(projectId, "review")}>
          需处理 <strong>{openIssues}</strong>
          {blockingIssues > 0 ? <span className="badge danger">{blockingIssues} 阻塞</span> : null}
        </NavLink>
      </div>
    </header>

    {overview.isPending ? <p className="explainer-state" role="status">正在载入解说作品…</p> : null}
    {overview.isError ? (
      <p className="explainer-state danger" role="alert">
        无法载入该解说作品：{overview.error instanceof Error ? overview.error.message : "未知错误"}
      </p>
    ) : null}

    <nav className="explainer-tabs" aria-label="解说制作工作区">
      {EXPLAINER_PAGES.map((page) => (
        <NavLink
          key={page}
          to={routes.explainerPage(projectId, page)}
          className={({ isActive }) => `explainer-tab${isActive || activePage === page ? " active" : ""}`}
          aria-current={activePage === page ? "page" : undefined}
        >
          {EXPLAINER_PAGE_LABELS[page]}
        </NavLink>
      ))}
    </nav>

    <div className="explainer-page" key={`${context.routeId ?? "explainer"}:${activePage}`}>
      <Outlet />
    </div>
  </div>;
}

function automationLabel(mode: string): string {
  switch (mode) {
    case "AUTO_WITH_EXCEPTIONS":
      return "自动成片 · 异常时暂停";
    case "REVIEW_BEFORE_RENDER":
      return "成片前确认一次";
    case "MANUAL_REVIEW":
      return "人工审查";
    default:
      return mode;
  }
}

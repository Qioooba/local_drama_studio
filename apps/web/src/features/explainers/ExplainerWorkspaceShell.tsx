/**
 * Explainer workspace shell.
 *
 * One navigation, three header rows, one drawer (design §B1.1–§B1.3):
 *
 * * row 1 — 返回作品列表, the work title, one compact status, and the
 *   制作进度 / 一键生成到预览 / 更多 controls;
 * * row 2 — the single numeric step bar (`ExplainerSteps`);
 * * row 3 — the current page title plus at most one line of explanation.
 *
 * The shell also owns the one sticky bottom action bar (pages declare their
 * content through `useExplainerActionBar`) and the 制作进度 drawer, whose
 * open/closed state lives in `?panel=progress` so a refresh keeps it open.
 *
 * `?edition=/?beat=/?panel=` are object locators, not page identities: the
 * inner page container is keyed by route id, so locating an object never
 * remounts the page and never drops a draft (design §B12.2).
 */

import { useMemo, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, Navigate, Outlet, useLocation, useParams, useSearchParams } from "react-router-dom";
import {
  EXPLAINER_LEGACY_PAGE_LABELS,
  EXPLAINER_PAGE_LABELS,
  isExplainerPage,
  parseRouteContext,
  routes,
  type ExplainerPage,
} from "../../app/routeRegistry";
import { getExplainerOverview } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { Popover } from "../../components/ui/primitives";
import { draftRegistry } from "../drafts/draftRegistry";
import { ExplainerActionBarProvider, ExplainerStepActionBar } from "./ExplainerStepActionBar";
import { ExplainerProgressDrawer, useExplainerPreviewStart } from "./ExplainerProgressDrawer";
import {
  EXPLAINER_PROGRESS_PANEL,
  EXPLAINER_STEP_HINTS,
  ExplainerSteps,
  explainerProgressPanelOpen,
  explainerStepStatuses,
  firstStepNeedingAttention,
} from "./ExplainerSteps";
import { runStatusLabel } from "./viewModels";
import { useExplainerReadiness } from "./useExplainerQueries";
import "./explainers.css";

export function ExplainerWorkspaceShell() {
  const { projectId = "" } = useParams();
  const location = useLocation();
  const context = parseRouteContext(location.pathname);
  const routePage = context.explainerPage ?? null;
  const activePage: ExplainerPage | null = isExplainerPage(routePage) ? routePage : null;
  const [, setSearchParams] = useSearchParams();
  const [moreOpen, setMoreOpen] = useState(false);

  const overview = useQuery({
    queryKey: queryKeys.explainers.workspace(projectId),
    queryFn: () => getExplainerOverview(projectId),
    enabled: Boolean(projectId),
    retry: 1,
  });

  // The server readiness projection is the authority for the numeric step bar: it
  // counts the real produced/required objects per step, so the bar reflects what the
  // backend actually has rather than what the overview summary happened to include
  // (design §F2.1).  `explainerStepStatuses` accepts it as an optional second input
  // and falls back to the overview projection when this query has not answered yet.
  const readiness = useExplainerReadiness(projectId);

  const statuses = useMemo(
    () => explainerStepStatuses(overview.data, readiness.data ?? undefined),
    [overview.data, readiness.data],
  );
  const drawerOpen = explainerProgressPanelOpen(location.search);
  const start = useExplainerPreviewStart({ projectId, overview: overview.data });

  // The save-state summary comes from the real draft registry, never a timer.
  const draftSnapshot = useSyncExternalStore(draftRegistry.subscribe, draftRegistry.getSnapshot);
  const hasDirtyDraft = draftSnapshot.some((owner) => owner.dirty);
  const doneSteps = Object.values(statuses).filter((status) => status === "DONE").length;
  const summary = hasDirtyDraft
    ? "有未保存修改"
    : overview.data
      ? `${doneSteps} / 6 步已完成`
      : null;

  const title = overview.data?.video?.title ?? (overview.isError ? "解说作品" : "正在载入…");
  const runStatus = overview.data?.latest_run?.projected_status ?? null;
  const openIssues = Number(overview.data?.open_issue_count ?? 0);
  const blockingIssues = Number(overview.data?.blocking_issue_count ?? 0);

  const setDrawerOpen = (open: boolean) => {
    setSearchParams((params) => {
      const next = new URLSearchParams(params);
      if (open) next.set("panel", EXPLAINER_PROGRESS_PANEL);
      else next.delete("panel");
      return next;
    });
  };

  return <div className="explainer-workspace">
    <header className="explainer-head">
      <div className="explainer-head__row">
        <Link className="explainer-head__back" to={routes.explainers()}>← 返回作品列表</Link>
        <h1 className="explainer-head__title" title={title}>{title}</h1>
        <span className="explainer-head__status" role="status">
          {runStatus ? runStatusLabel(runStatus) : (overview.isPending ? "正在载入状态…" : "尚未开始制作")}
          {openIssues > 0 ? ` · 需处理 ${openIssues} 项` : ""}
          {blockingIssues > 0 ? `（阻塞 ${blockingIssues}）` : ""}
        </span>
        <div className="explainer-head__actions">
          <button
            type="button"
            className="explainer-head__button"
            aria-haspopup="dialog"
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen(true)}
          >
            制作进度
          </button>
          <button
            type="button"
            className="explainer-head__button"
            disabled={start.pending || !overview.data}
            onClick={start.start}
            title={overview.data ? undefined : "载入作品后才能提交生产"}
          >
            {start.label}
          </button>
          <span className="explainer-head__more">
            <button
              type="button"
              className="explainer-head__button"
              aria-haspopup="dialog"
              aria-expanded={moreOpen}
              onClick={() => setMoreOpen((open) => !open)}
            >
              更多
            </button>
            <Popover open={moreOpen} onClose={() => setMoreOpen(false)}>
              <ul className="explainer-head__menu">
                <li><Link to={routes.systemJobs(projectId)} onClick={() => setMoreOpen(false)}>查看本机后台任务</Link></li>
                <li><Link to={routes.systemCapabilities(projectId)} onClick={() => setMoreOpen(false)}>能力与模型</Link></li>
                <li><Link to={routes.systemDiagnostics(projectId)} onClick={() => setMoreOpen(false)}>诊断与审计</Link></li>
                <li>
                  {/* No PATCH endpoint for the work title exists yet, so this is an
                      honest disabled placeholder — it does not pretend to save. */}
                  <button type="button" disabled title="作品改名接口尚未提供，当前不可修改">重命名作品（尚未提供）</button>
                </li>
              </ul>
            </Popover>
          </span>
        </div>
      </div>

      <ExplainerSteps projectId={projectId} activePage={activePage} statuses={statuses} />

      <div className="explainer-head__page">
        <h2>{activePage ? EXPLAINER_PAGE_LABELS[activePage] : EXPLAINER_LEGACY_PAGE_LABELS.overview}</h2>
        <p className="explainer-head__hint">
          {activePage ? EXPLAINER_STEP_HINTS[activePage] : "正在定位第一个需要处理的步骤…"}
        </p>
      </div>
    </header>

    {overview.isPending ? <p className="explainer-state" role="status">正在载入解说作品…</p> : null}
    {overview.isError ? (
      <p className="explainer-state danger" role="alert">
        无法载入该解说作品：{overview.error instanceof Error ? overview.error.message : "未知错误"}
      </p>
    ) : null}

    <ExplainerActionBarProvider>
      <div className="explainer-body">
        <div className="explainer-page" key={context.routeId ?? "explainer"}>
          <Outlet />
        </div>
      </div>
      <ExplainerStepActionBar
        projectId={projectId}
        activePage={activePage}
        statuses={statuses}
        summary={summary}
      />
    </ExplainerActionBarProvider>

    <ExplainerProgressDrawer
      open={drawerOpen}
      onClose={() => setDrawerOpen(false)}
      projectId={projectId}
      activePage={activePage}
      overview={overview.data}
      statuses={statuses}
      start={start}
    />
  </div>;
}

/**
 * `/explainers/:projectId/overview` is not a seventh step.  It resolves the
 * first step that needs attention from the real workspace projection and
 * replaces the URL, keeping `?panel=progress` (and every other locator) so the
 * drawer stays open across the redirect and across a refresh.
 */
export function ExplainerOverviewRedirect() {
  const { projectId = "" } = useParams();
  const location = useLocation();
  const overview = useQuery({
    queryKey: queryKeys.explainers.workspace(projectId),
    queryFn: () => getExplainerOverview(projectId),
    enabled: Boolean(projectId),
    retry: 1,
  });

  if (overview.isPending) {
    return <p className="explainer-state" role="status">正在确认第一个需要处理的步骤…</p>;
  }
  if (overview.isError || !overview.data) {
    return <p className="explainer-state danger" role="alert">
      无法确认制作步骤：{overview.error instanceof Error ? overview.error.message : "作品数据不可用"}。
      请返回作品列表或稍后重试。
    </p>;
  }
  const target = firstStepNeedingAttention(explainerStepStatuses(overview.data));
  return <Navigate to={{ pathname: routes.explainerPage(projectId, target), search: location.search }} replace />;
}

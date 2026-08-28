import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { routes } from "../app/routeRegistry";
import { ErrorState } from "../components/ui";
import { useProjectEventInvalidation } from "../features/events/useProjectEventInvalidation";
import { CapacitySnapshotPanel, JobsPanel } from "../features/jobs/JobsPanel";
import { getCapacitySnapshot, listJobsPage, listProjects } from "../generated/api";
import { queryKeys } from "../query/queryKeys";

/** Durable jobs are operational facts, not a second creative-history system. */
export function JobsPage() {
  const { projectId: routeProjectId } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = routeProjectId || searchParams.get("project") || undefined;
  const focusJobId = searchParams.get("job");
  const projects = useQuery({ queryKey: queryKeys.projects.list({ limit: 100 }), queryFn: () => listProjects({ limit: 100 }) });
  const jobs = useInfiniteQuery({
    queryKey: queryKeys.jobs.list(projectId),
    queryFn: ({ pageParam }) => listJobsPage(projectId, pageParam, 100),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    maxPages: 10,
    refetchInterval: 1500,
  });
  const capacity = useQuery({
    queryKey: queryKeys.capacity.scope(projectId),
    queryFn: () => getCapacitySnapshot(projectId),
    refetchInterval: 5000,
  });
  useProjectEventInvalidation(
    projectId ?? "",
    ["JOB_QUEUED", "JOB_CLAIMED", "JOB_HEARTBEAT", "JOB_FINISHED", "JOB_RECONCILED", "JOB_REQUEUED", "JOB_CANCEL_REQUESTED", "ARTIFACT_REGISTERED"],
    [queryKeys.jobs.scope(projectId), queryKeys.capacity.scope(projectId)],
    (event) => event.type === "JOB_HEARTBEAT"
      ? [queryKeys.jobs.scope(projectId)]
      : [queryKeys.jobs.scope(projectId), queryKeys.capacity.scope(projectId)],
  );
  const refresh = () => { void jobs.refetch(); void capacity.refetch(); };
  const focusJob = (jobId: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (jobId) next.set("job", jobId); else next.delete("job");
    setSearchParams(next, { replace: true });
  };
  const jobItems = jobs.data?.pages.flatMap((page) => page.items) ?? [];
  const projectTitles = Object.fromEntries((projects.data?.items ?? []).map((project) => [project.id, project.title]));
  const selectedProjectTitle = projectId ? projectTitles[projectId] ?? "所选项目" : "全部任务（含独立生成）";
  return (
    <div className="v2-page">
      <div className="panel-heading">
        <div><p className="eyebrow">系统区</p><h2>任务与机器</h2></div>
        <span className="status-pill neutral">本机持久化队列</span>
      </div>
      <p className="muted">查看本机后台任务、每次执行记录、占用状态、进度和已验证产物。故障重试会继续原任务；如果想换一套创作结果，请回到生成工作台创建新候选。</p>
      <section className="panel" aria-label="任务范围">
        <label>任务范围
          <select
            aria-label="后台任务范围"
            value={projectId ?? ""}
            onChange={(event) => {
              if (routeProjectId) {
                navigate(routes.systemJobs(event.target.value || undefined));
                return;
              }
              const next = new URLSearchParams(searchParams);
              next.delete("job");
              if (event.target.value) next.set("project", event.target.value); else next.delete("project");
              setSearchParams(next, { replace: true });
            }}
          >
            <option value="">全部任务（含独立生成）</option>
            {projects.data?.items.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
          </select>
          <small className="muted jobs-scope-summary" role="status">当前范围：{selectedProjectTitle} · 已读取 {jobItems.length} 个任务{jobs.hasNextPage ? "（还有更早任务）" : ""}</small>
        </label>
      </section>
      {jobs.error ? <ErrorState description={`任务读取失败：${String(jobs.error)}`} onRetry={refresh} /> : <>
        <JobsPanel jobs={jobItems} loading={jobs.isPending} onChanged={refresh} focusJobId={focusJobId} onFocusJob={focusJob} scopeKey={projectId ?? "all"} capacity={capacity.data?.snapshot} projectTitles={projectTitles} showProjectScope={!projectId} />
        {jobs.hasNextPage ? <button type="button" className="secondary list-more" onClick={() => void jobs.fetchNextPage()} disabled={jobs.isFetchingNextPage}>{jobs.isFetchingNextPage ? "读取中…" : `加载更早任务（已加载 ${jobItems.length}）`}</button> : null}
      </>}
      {capacity.error ? <ErrorState title="无法读取产能" description={String(capacity.error)} onRetry={() => void capacity.refetch()} /> : <CapacitySnapshotPanel snapshot={capacity.data?.snapshot} />}
    </div>
  );
}

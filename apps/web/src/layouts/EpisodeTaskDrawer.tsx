import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Drawer } from "../components/ui";
import { listEpisodeJobs, retryJob } from "../generated/api";

export const episodeJobsKey = (projectId: string, episodeId: string) => ["jobs", "episode", projectId, episodeId] as const;

export function EpisodeTaskDrawer({ open, onClose, projectId, episodeId }: { open: boolean; onClose: () => void; projectId: string; episodeId: string }) {
  const jobs = useQuery({
    queryKey: episodeJobsKey(projectId, episodeId),
    queryFn: () => listEpisodeJobs(projectId, episodeId),
    enabled: open,
    refetchInterval: open ? 2_000 : false,
  });
  const retry = async (jobId: string) => { await retryJob(jobId); await jobs.refetch(); };
  return <Drawer open={open} onClose={onClose} title="当前集任务" width={460} footer={<Link className="secondary v2-inline-link" to={`/system/jobs?project=${encodeURIComponent(projectId)}`}>打开项目任务中心</Link>}>
    <p className="muted">仅显示当前项目、当前分集的持久化任务。失败重试会复用原任务冻结输入。</p>
    {jobs.isPending ? <p role="status">正在读取当前集任务…</p> : null}
    {jobs.error ? <p className="inline-error" role="alert">任务读取失败：{jobs.error instanceof Error ? jobs.error.message : String(jobs.error)} <button type="button" className="text-action" onClick={() => void jobs.refetch()}>重试读取</button></p> : null}
    {!jobs.isPending && !jobs.error && !jobs.data?.items.length ? <p className="empty-state">当前集还没有后台任务。</p> : null}
    <div className="episode-task-list">
      {jobs.data?.items.map((job) => {
        const progress = Number(job.progress?.percent);
        const failed = ["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(job.state);
        return <article key={job.id}>
          <div><strong>{String(job.stage_code ?? job.type)}</strong><span className={`status-pill ${failed ? "attention" : job.state === "SUCCEEDED" ? "success" : "running"}`}>{job.state}</span></div>
          <small>{Number.isFinite(progress) ? `${Math.round(progress <= 1 ? progress * 100 : progress)}% · ` : ""}{job.last_error_detail_redacted || `任务 ${job.id.slice(0, 8)}`}</small>
          <div>{failed ? <button type="button" className="secondary" onClick={() => void retry(job.id)}>按原输入重试</button> : null}<Link to={`/system/jobs?project=${encodeURIComponent(projectId)}&job=${encodeURIComponent(job.id)}`}>查看详情</Link></div>
        </article>;
      })}
    </div>
  </Drawer>;
}

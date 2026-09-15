import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Drawer } from "../components/ui";
import { ApiRequestError, listEpisodeJobs, retryJob, type Job } from "../generated/api";

export const episodeJobsKey = (projectId: string, episodeId: string) => ["jobs", "episode", projectId, episodeId] as const;

function errorText(error: unknown): string {
  if (error instanceof ApiRequestError) return error.message;
  return error instanceof Error ? error.message : String(error);
}

function isUnknownRetryError(error: unknown): boolean {
  if (error instanceof ApiRequestError) {
    // Explicit business rejections are confirmed unaccepted; everything else
    // (network, timeout, 5xx, acceptance-unknown) stays待确认.
    if (error.code === "PROVIDER_ACCEPTANCE_RECONCILIATION_REQUIRED") return false;
    if (error.code === "JOB_NOT_RETRYABLE" || error.code === "JOB_NOT_FOUND") return false;
    if (error.status >= 500) return true;
    if (error.code === "PROVIDER_ACCEPTANCE_UNKNOWN" || error.code === "COMFY_PROVIDER_ACCEPTANCE_UNKNOWN") return true;
    // TanStack-free heuristic: retryable flag false + 4xx with explicit code
    // means confirmed rejection; otherwise unknown.
    if (error.status >= 400 && error.status < 500) {
      return error.retryable;
    }
    return true;
  }
  return true;
}

type RetryStatus = "idle" | "accepted" | "unknown" | "rejected";

function EpisodeJobRow({
  projectId,
  episodeId,
  job,
}: {
  projectId: string;
  episodeId: string;
  job: Job;
}) {
  const queryClient = useQueryClient();
  const retryLockRef = useRef(false);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  const [retryStatus, setRetryStatus] = useState<RetryStatus>("idle");
  const [retryMessage, setRetryMessage] = useState<string | null>(null);
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null);
  const [acceptedRevision, setAcceptedRevision] = useState<number | null>(null);

  const retryMutation = useMutation({
    mutationKey: ["job-retry", projectId, episodeId, job.id],
    mutationFn: () => retryJob(job.id),
    retry: 0,
  });

  const failed = ["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(job.state);
  // After an accepted retry, keep the button closed until the authoritative
  // attempt/revision progress is observed; a stale FAILED cache must not
  // reopen an unknown request. A genuinely new failed attempt (different
  // revision) re-enables explicit retry via fresh props.
  const awaitingReceipt = retryStatus === "accepted" || retryStatus === "unknown";
  const progress = Number((job as { progress?: { percent?: unknown } }).progress?.percent);
  const canRetry = failed && !retryMutation.isPending && !awaitingReceipt;

  async function refreshOriginalScope(originProjectId: string, originEpisodeId: string) {
    try {
      const result = await queryClient.fetchQuery({
        queryKey: episodeJobsKey(originProjectId, originEpisodeId),
        queryFn: () => listEpisodeJobs(originProjectId, originEpisodeId),
      });
      void result;
      await queryClient.invalidateQueries({ queryKey: episodeJobsKey(originProjectId, originEpisodeId) });
      return { ok: true as const };
    } catch (error) {
      return { ok: false as const, error };
    }
  }

  async function handleRetry() {
    if (retryLockRef.current || retryMutation.isPending || awaitingReceipt) return;
    const originProjectId = projectId;
    const originEpisodeId = episodeId;
    const originJobId = job.id;
    const originRevision = (job as { revision?: unknown }).revision;
    retryLockRef.current = true;
    if (mountedRef.current) {
      setRetryMessage(null);
      setRefreshWarning(null);
    }
    let accepted = false;
    try {
      const receipt = await retryMutation.mutateAsync();
      accepted = true;
      if (!mountedRef.current) return;
      // Record the accepted receipt (job ID + revision), never "task succeeded".
      const nextRevision = (receipt.job as { revision?: unknown }).revision;
      setAcceptedRevision(typeof nextRevision === "number" ? nextRevision : null);
      setRetryStatus("accepted");
      setRetryMessage("重试已受理，正在刷新任务状态。");
      void originJobId;
      void originRevision;
    } catch (error) {
      if (!mountedRef.current) return;
      if (isUnknownRetryError(error)) {
        setRetryStatus("unknown");
        setRetryMessage("重试请求结果待确认，请先查看当前任务状态，不要重复重试。");
      } else {
        setRetryStatus("rejected");
        setRetryMessage(`重试未受理：${errorText(error)}`);
      }
    } finally {
      // Separate the refresh stage: refetch errors never masquerade as retry
      // failures. TanStack refetch resolves with isError instead of throwing,
      // so use fetchQuery/invalidate explicitly and inspect the outcome.
      try {
        const refreshed = await refreshOriginalScope(originProjectId, originEpisodeId);
        if (!mountedRef.current) return;
        if (!refreshed.ok) {
          setRefreshWarning(
            accepted
              ? "重试已受理，但列表刷新失败，请刷新状态，不要重复重试。"
              : `任务状态读取失败：${errorText(refreshed.error)}`,
          );
        }
        // When not accepted, the row stays待确认 via awaitingReceipt until
        // authoritative attempt/revision progress arrives as new props.
      } catch (error) {
        if (!mountedRef.current) return;
        setRefreshWarning(
          accepted
            ? "重试已受理，但列表刷新失败，请刷新状态，不要重复重试。"
            : `任务状态读取失败：${errorText(error)}`,
        );
      } finally {
        retryLockRef.current = false;
      }
    }
  }

  // When a genuinely new failed attempt arrives (different revision), allow
  // explicit retry again. Stale re-renders with the same revision keep等待.
  useEffect(() => {
    if (retryStatus !== "accepted" && retryStatus !== "unknown") return;
    const currentRevision = (job as { revision?: unknown }).revision;
    if (typeof currentRevision === "number" && acceptedRevision !== null && currentRevision !== acceptedRevision) {
      // Authoritative progress observed; reset to allow future explicit retries
      // only when the new attempt actually fails (parent controls visibility).
      if (["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(job.state)) {
        setRetryStatus("idle");
        setRetryMessage(null);
        setRefreshWarning(null);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.id, (job as { revision?: unknown }).revision, job.state]);

  return (
    <article key={job.id}>
      <div>
        <strong>{String((job as { stage_code?: unknown; type?: unknown }).stage_code ?? (job as { type?: unknown }).type)}</strong>
        <span
          className={`status-pill ${failed ? "attention" : job.state === "SUCCEEDED" ? "success" : "running"}`}
        >
          {job.state}
        </span>
      </div>
      <small>
        {Number.isFinite(progress) ? `${Math.round(progress <= 1 ? progress * 100 : progress)}% · ` : ""}
        {(job as { last_error_detail_redacted?: unknown }).last_error_detail_redacted
          ? String((job as { last_error_detail_redacted?: unknown }).last_error_detail_redacted)
          : `任务 ${job.id.slice(0, 8)}`}
      </small>
      <div>
        {failed ? (
          <button
            type="button"
            className="secondary"
            disabled={!canRetry}
            onClick={() => void handleRetry()}
          >
            {retryMutation.isPending ? "正在重试…" : awaitingReceipt ? "已受理，等待确认…" : "按原输入重试"}
          </button>
        ) : null}
        <Link to={`/system/jobs?project=${encodeURIComponent(projectId)}&job=${encodeURIComponent(job.id)}`}>
          查看详情
        </Link>
      </div>
      {retryMessage ? (
        <p className={retryStatus === "rejected" ? "inline-error" : "muted"} role={retryStatus === "rejected" ? "alert" : "status"}>
          {retryMessage}
        </p>
      ) : null}
      {refreshWarning ? (
        <p className="inline-error" role="alert">
          {refreshWarning}
        </p>
      ) : null}
    </article>
  );
}

export function EpisodeTaskDrawer({ open, onClose, projectId, episodeId }: { open: boolean; onClose: () => void; projectId: string; episodeId: string }) {
  const jobs = useQuery({
    queryKey: episodeJobsKey(projectId, episodeId),
    queryFn: () => listEpisodeJobs(projectId, episodeId),
    enabled: open,
    refetchInterval: open ? 2_000 : false,
  });
  return <Drawer open={open} onClose={onClose} title="当前集任务" width={460} footer={<Link className="secondary v2-inline-link" to={`/system/jobs?project=${encodeURIComponent(projectId)}`}>打开项目任务中心</Link>}>
    <p className="muted">仅显示当前项目、当前分集的持久化任务。失败重试会复用原任务冻结输入。</p>
    {jobs.isPending ? <p role="status">正在读取当前集任务…</p> : null}
    {jobs.error ? <p className="inline-error" role="alert">任务读取失败：{jobs.error instanceof Error ? jobs.error.message : String(jobs.error)} <button type="button" className="text-action" onClick={() => void jobs.refetch()}>重试读取</button></p> : null}
    {!jobs.isPending && !jobs.error && !jobs.data?.items.length ? <p className="empty-state">当前集还没有后台任务。</p> : null}
    <div className="episode-task-list">
      {jobs.data?.items.map((job) => (
        <EpisodeJobRow key={`${projectId}:${episodeId}:${job.id}`} projectId={projectId} episodeId={episodeId} job={job} />
      ))}
    </div>
  </Drawer>;
}

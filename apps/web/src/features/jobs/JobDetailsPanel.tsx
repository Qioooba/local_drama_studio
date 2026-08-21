import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getJob, promoteJobArtifactToMedia, type JobArtifact } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";

export function JobDetailsPanel({ jobId, onChanged }: { jobId: string | null; onChanged?: () => void }) {
  const detail = useQuery({ queryKey: queryKeys.jobs.detail(jobId ?? "missing"), queryFn: () => getJob(jobId as string), enabled: Boolean(jobId) });
  const [busy, setBusy] = useState<string | null>(null);
  const [kind, setKind] = useState<"IMAGE" | "VIDEO" | "AUDIO">("VIDEO");
  const [stage, setStage] = useState<"KEYFRAME" | "PROXY" | "FORMAL" | "TIMELINE">("PROXY");
  const [purpose, setPurpose] = useState("GENERATED_OUTPUT");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!jobId) return null;
  const job = detail.data?.job;
  const promote = async (artifact: JobArtifact) => {
    setBusy(artifact.id); setError(null); setMessage(null);
    try {
      const result = await promoteJobArtifactToMedia(artifact.id, { purpose: purpose.trim() || "GENERATED_OUTPUT", media_kind: kind, stage });
      setMessage(`产物已登记为媒体版本：${String(result.media.media_version_id ?? result.media.id ?? "已完成").slice(0, 16)}`);
      await detail.refetch();
      onChanged?.();
    } catch (caught) { setError(String(caught)); }
    finally { setBusy(null); }
  };
  return <section className="panel job-details-panel" aria-labelledby="job-details-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-JOB-005 · 产物谱系</p><h3 id="job-details-title">任务详情与产物登记</h3></div><span className="status-pill neutral">不可变媒体</span></div>
    {detail.isPending ? <p className="empty-state">正在读取任务尝试与产物…</p> : detail.error ? <p className="inline-error" role="alert">任务详情读取失败：{String(detail.error)}</p> : job && <><div className="review-meta"><span>Job：{job.id.slice(0, 16)}…</span><span>状态：{job.state}</span><span>输入快照：{JSON.stringify(job.input_snapshot ?? {}).slice(0, 120)}</span></div><div className="field-grid"><label>登记媒体类型<select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="VIDEO">VIDEO</option><option value="IMAGE">IMAGE</option><option value="AUDIO">AUDIO</option></select></label><label>登记阶段<select value={stage} onChange={(event) => setStage(event.target.value as typeof stage)}><option value="PROXY">PROXY</option><option value="KEYFRAME">KEYFRAME</option><option value="FORMAL">FORMAL</option><option value="TIMELINE">TIMELINE</option></select></label><label>媒体用途<input value={purpose} onChange={(event) => setPurpose(event.target.value)} /></label></div><div className="job-attempt-list">{job.attempts?.length ? job.attempts.map((attempt) => <article className="job-attempt" key={String(attempt.id)}><div><strong>Attempt {String(attempt.attempt_no ?? "?")}</strong><span>{String(attempt.state ?? "UNKNOWN")}</span></div>{(attempt.artifacts ?? []).length ? <div className="artifact-list">{(attempt.artifacts ?? []).map((artifact) => <div className="artifact-row" key={artifact.id}><span>{artifact.kind} · {artifact.status} · {String(artifact.sha256 ?? "").slice(0, 12) || "—"}…</span><button className="secondary" type="button" onClick={() => void promote(artifact)} disabled={busy !== null || artifact.status !== "VERIFIED"}>{busy === artifact.id ? "登记中…" : "登记为媒体版本"}</button></div>)}</div> : <small className="muted">该尝试暂无已验证产物。</small>}</article>) : <p className="empty-state">该任务还没有 Attempt。</p>}</div>{message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">产物登记失败：{error}</p>}</>}
  </section>;
}

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { finalizeTTSJob, getJob, promoteJobArtifactToMedia, transformJobArtifactImage, type JobArtifact } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { MEDIA_PURPOSE_LABELS, MEDIA_PURPOSE_OPTIONS } from "../shared/formOptions";
import { ARTIFACT_KIND_LABELS, MEDIA_KIND_LABELS, MEDIA_STAGE_LABELS, statusLabel, userFacingLabel } from "../shared/optionLabels";

function artifactMediaKind(artifact: JobArtifact): "IMAGE" | "VIDEO" | "AUDIO" | null {
  const suffix = artifact.sandbox_rel_path.toLowerCase().split("?")[0];
  if (/\.(png|jpe?g|webp|gif|bmp|tiff?)$/.test(suffix)) return "IMAGE";
  if (/\.(mp4|mov|mkv|webm|avi|m4v)$/.test(suffix)) return "VIDEO";
  if (/\.(wav|mp3|m4a|aac|flac|ogg|opus)$/.test(suffix)) return "AUDIO";
  return null;
}

function progressText(state: string, progress: Record<string, unknown>) {
  if (state === "SUCCEEDED") return "已完成 · 100%";
  if (["FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"].includes(state)) return statusLabel(state);
  const phase = String(progress.phase ?? state ?? "RUNNING");
  const numeric = Number(progress.percent);
  if (!Number.isFinite(numeric)) return phase;
  const percent = Math.max(0, Math.min(100, Math.round(numeric > 0 && numeric <= 1 ? numeric * 100 : numeric)));
  const stepNumeric = Number(progress.step_percent);
  const normalizedStep = stepNumeric > 0 && stepNumeric <= 1 ? stepNumeric * 100 : stepNumeric;
  const step = Number.isFinite(stepNumeric) ? ` · 当前编码步骤 ${Math.max(0, Math.min(100, Math.round(normalizedStep)))}%` : "";
  return `${phase} · 总体 ${percent}%${step}`;
}

const TERMINAL_JOB_STATES = new Set(["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"]);

export function JobDetailsPanel({ jobId, onChanged }: { jobId: string | null; onChanged?: () => void }) {
  const detail = useQuery({
    queryKey: queryKeys.jobs.detail(jobId ?? "missing"),
    queryFn: () => getJob(jobId as string),
    enabled: Boolean(jobId),
    refetchOnMount: "always",
    refetchInterval: (query) => TERMINAL_JOB_STATES.has(String(query.state.data?.job.state ?? "")) ? false : 1500,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [stage, setStage] = useState<"KEYFRAME" | "PROXY" | "FORMAL" | "TIMELINE">("PROXY");
  const [purpose, setPurpose] = useState<(typeof MEDIA_PURPOSE_OPTIONS)[number]>("GENERATED_OUTPUT");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ttsCandidateId, setTtsCandidateId] = useState<string | null>(null);
  if (!jobId) return null;
  const job = detail.data?.job;
  const promote = async (artifact: JobArtifact) => {
    setBusy(artifact.id); setError(null); setMessage(null);
    try {
      const detectedKind = artifactMediaKind(artifact);
      if (!detectedKind) throw new Error("该产物不是可登记的图片、视频或音频文件");
      const result = await promoteJobArtifactToMedia(artifact.id, { purpose, media_kind: detectedKind, stage });
      setMessage(`产物已登记为媒体版本：${String(result.media.media_version_id ?? result.media.id ?? "已完成").slice(0, 16)}`);
      await detail.refetch();
      onChanged?.();
    } catch (caught) { setError(String(caught)); }
    finally { setBusy(null); }
  };
  const mirror = async (artifact: JobArtifact) => {
    setBusy(`mirror:${artifact.id}`); setError(null); setMessage(null);
    try {
      const result = await transformJobArtifactImage(artifact.id, { transform: "HORIZONTAL_MIRROR", purpose, stage });
      setMessage(`已水平镜像并登记为可追溯新图片：${String(result.media.media_version_id ?? result.media.id ?? "已完成").slice(0, 16)}`);
      onChanged?.();
    } catch (caught) { setError(String(caught)); }
    finally { setBusy(null); }
  };
  const finalizeTts = async () => {
    if (!job || job.type !== "TTS_GENERATION" || job.state !== "SUCCEEDED") return;
    setBusy(`tts-finalize:${job.id}`); setError(null); setMessage(null); setTtsCandidateId(null);
    try {
      const result = await finalizeTTSJob(job.id);
      const candidateId = String(result.result.candidate?.id ?? "");
      setTtsCandidateId(candidateId || null);
      setMessage(`TTS 已登记为对白候选${candidateId ? `：${candidateId.slice(0, 16)}…` : ""}；可回到对应镜头试听并采用。`);
      await detail.refetch();
      onChanged?.();
    } catch (caught) { setError(String(caught)); }
    finally { setBusy(null); }
  };
  const hasAttemptError = Boolean(job?.attempts?.some((attempt) => attempt.error_code));
  return <section className="panel job-details-panel" aria-labelledby="job-details-title">
    <div className="panel-heading"><div><p className="eyebrow">任务产物谱系</p><h3 id="job-details-title">任务详情与产物登记</h3></div><span className="status-pill neutral">不可变媒体</span></div>
    {detail.isPending ? <p className="empty-state">正在读取任务尝试与产物…</p> : detail.error ? <p className="inline-error" role="alert">任务详情读取失败：{String(detail.error)}</p> : job && <>
      <div className="review-meta job-detail-meta">
        <span>任务标识：{job.id.slice(0, 16)}…</span>
        <span className="job-state-meta">状态 <span className={`status-pill state-${job.state.toLowerCase()}`}>{statusLabel(job.state)}</span></span>
      </div>
      <details className="job-input-snapshot">
        <summary>高级：查看任务输入快照</summary>
        <pre><code>{JSON.stringify(job.input_snapshot ?? {}, null, 2)}</code></pre>
      </details>
      {job.progress && Object.keys(job.progress).length > 0 && <p className="muted" role="status">当前进度：{progressText(job.state, job.progress)}</p>}
      {job.last_error_code && !hasAttemptError && <p className="inline-error" role="alert"><strong>{job.last_error_code}</strong>{job.last_error_detail_redacted ? `：${job.last_error_detail_redacted}` : ""}</p>}
      <div className="field-grid">
        <div className="field-fact"><span>登记媒体类型</span><strong>由产物文件自动识别</strong></div>
        <label>登记阶段<select value={stage} onChange={(event) => setStage(event.target.value as typeof stage)}>{(["PROXY", "KEYFRAME", "FORMAL", "TIMELINE"] as const).map((value) => <option key={value} value={value}>{MEDIA_STAGE_LABELS[value]}</option>)}</select></label>
        <label>媒体用途<select value={purpose} onChange={(event) => setPurpose(event.target.value as typeof purpose)}>{MEDIA_PURPOSE_OPTIONS.map((value) => <option key={value} value={value}>{MEDIA_PURPOSE_LABELS[value]}</option>)}</select></label>
      </div>
      <div className="job-attempt-list">
        {job.attempts?.length ? job.attempts.map((attempt) => {
          const attemptState = String(attempt.state ?? "UNKNOWN");
          return <article className="job-attempt" key={String(attempt.id)}>
            <div className="job-attempt-header"><strong>第 {String(attempt.attempt_no ?? "?")} 次执行</strong><span className={`status-pill state-${attemptState.toLowerCase()}`}>{statusLabel(attemptState)}</span></div>
            {attempt.progress && Object.keys(attempt.progress).length > 0 && <small className="muted">进度：{progressText(attemptState, attempt.progress)}</small>}
            {attempt.error_code && <small className="inline-error"><strong>{attempt.error_code}</strong>{attempt.error_detail_redacted ? `：${attempt.error_detail_redacted}` : ""}</small>}
            {(attempt.artifacts ?? []).length ? <div className="artifact-list">{(attempt.artifacts ?? []).map((artifact) => {
              const detectedKind = artifactMediaKind(artifact);
              const promoted = Boolean(artifact.promoted_media_version_id);
              const canFinalizeTts = job.type === "TTS_GENERATION" && job.state === "SUCCEEDED" && artifact.kind === "TTS_AUDIO" && artifact.status === "VERIFIED";
              const mediaLabel = detectedKind ? userFacingLabel(MEDIA_KIND_LABELS, detectedKind, "媒体") : null;
              const downloadable = artifact.status === "VERIFIED" && Boolean(detectedKind);
              const filename = artifact.sandbox_rel_path.replaceAll("\\", "/").split("/").pop() || "未命名产物";
              const artifactUrl = `/api/v1/artifacts/${encodeURIComponent(artifact.id)}/download`;
              return <div className="artifact-row" key={artifact.id}>
                {downloadable && <figure className="artifact-preview">
                  {detectedKind === "IMAGE" && <img loading="eager" decoding="async" src={artifactUrl} alt={`已验证产物预览：${filename}`} onError={(e) => { e.currentTarget.style.display = "none"; }} />}
                  {detectedKind === "VIDEO" && <video src={artifactUrl} controls preload="none" aria-label={`已验证产物预览：${filename}`} />}
                  {detectedKind === "AUDIO" && <audio src={artifactUrl} controls preload="none" aria-label={`已验证产物预览：${filename}`} />}
                  <figcaption>页面直读已验证产物 · {filename}</figcaption>
                </figure>}
                <span><strong>{filename}</strong> · {userFacingLabel(ARTIFACT_KIND_LABELS, artifact.kind, "任务产物")} · {statusLabel(artifact.status)} · 校验指纹 {String(artifact.sha256 ?? "").slice(0, 12) || "—"}…{mediaLabel ? ` · ${mediaLabel}` : " · 不能登记为媒体"}</span>
                <div className="artifact-actions">{downloadable && <a className="secondary" href={artifactUrl} download={filename}>{`下载${mediaLabel ?? "媒体"}到当前电脑`}</a>}{detectedKind === "IMAGE" && <button className="secondary" type="button" onClick={() => void mirror(artifact)} disabled={busy !== null || artifact.status !== "VERIFIED"}>{busy === `mirror:${artifact.id}` ? "镜像并登记中…" : "水平镜像并登记为新图片"}</button>}{canFinalizeTts && <button className="secondary" type="button" onClick={() => void finalizeTts()} disabled={busy !== null}>{busy === `tts-finalize:${job.id}` ? "TTS 登记中…" : "完成 TTS 登记"}</button>}<button className="secondary" type="button" onClick={() => void promote(artifact)} disabled={busy !== null || artifact.status !== "VERIFIED" || !detectedKind || promoted}>{busy === artifact.id ? "登记中…" : promoted && mediaLabel ? `已登记为${mediaLabel}` : mediaLabel ? `登记为${mediaLabel}` : "不可登记"}</button></div>
              </div>;
            })}</div> : <small className="muted">本次执行暂无已验证产物。</small>}
          </article>;
        }) : <p className="empty-state">该任务还没有执行记录。</p>}
      </div>
      {message && <p className="review-success" role="status">{message}</p>}
      {ttsCandidateId && job.scope_episode_id && job.scope_shot_id && <a className="secondary" href={`/projects/${encodeURIComponent(String(job.project_id))}/episodes/${encodeURIComponent(String(job.scope_episode_id))}/studio/${encodeURIComponent(String(job.scope_shot_id))}?focus=sound`}>回到对应镜头试听与采用</a>}
      {error && <p className="inline-error" role="alert">产物登记失败：{error}</p>}
    </>}
  </section>;
}

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  cancelJob,
  commitImportSession,
  importScriptDocument,
  listJobs,
  pickLocalDocumentFile,
  retryJob,
  type DocumentImport,
  type Job,
} from "../../generated/api";
import { getLocalLLMStatus, requestScriptBreakdown, syncLocalLLMProfile, publishLocalLLMProfile } from "../story-workspace-v2/breakdownClient";
import { queryKeys } from "../../query/queryKeys";

export function ScriptImportPanel({ projectId }: { projectId: string }) {
  const [path, setPath] = useState("");
  const [prepared, setPrepared] = useState<DocumentImport | null>(null);
  const [committed, setCommitted] = useState(false);
  const [pending, setPending] = useState<"browse" | "preview" | "commit" | "breakdown" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [breakdownSuccess, setBreakdownSuccess] = useState<string | null>(null);
  const [jobAction, setJobAction] = useState<string | null>(null);

  const llmStatus = useQuery({
    queryKey: ["local-llm-status"],
    queryFn: () => getLocalLLMStatus(),
  });
  const breakdownJobQuery = useQuery({
    queryKey: queryKeys.scriptBreakdown.jobs(projectId),
    queryFn: () => listJobs(projectId),
    refetchInterval: 3_000,
  });
  const breakdownJobs = (breakdownJobQuery.data?.items ?? [])
    .filter((job) => job.type === "SCRIPT_BREAKDOWN_LOCAL_LLM")
    .slice(0, 5);

  const changePath = (value: string) => {
    setPath(value);
    setPrepared(null);
    setCommitted(false);
    setError(null);
    setBreakdownSuccess(null);
  };

  const browse = async () => {
    setPending("browse");
    setError(null);
    try {
      const result = await pickLocalDocumentFile();
      if (result.selection.selected && result.selection.path) changePath(result.selection.path);
    } catch (reason) {
      setError(`选择器失败：${String(reason)}。也可以粘贴绝对路径。`);
    } finally {
      setPending(null);
    }
  };

  const preview = async () => {
    setPending("preview");
    setError(null);
    try {
      const result = await importScriptDocument(projectId, path.trim());
      setPrepared(result.import);
      setCommitted(result.import.status === "COMMITTED");
    } catch (reason) {
      setError(`解析失败：${String(reason)}`);
    } finally {
      setPending(null);
    }
  };

  const commit = async () => {
    if (!prepared) return;
    setPending("commit");
    setError(null);
    try {
      const result = await commitImportSession(prepared.import_session_id, prepared.preview_hash);
      setCommitted(result.commit.status === "COMMITTED");
    } catch (reason) {
      setError(`提交失败：${String(reason)}`);
    } finally {
      setPending(null);
    }
  };

  const triggerBreakdown = async () => {
    if (!prepared || !committed) return;
    setPending("breakdown");
    setError(null);
    setBreakdownSuccess(null);
    try {
      // 1. Sync candidate profile for current model if needed
      const syncResult = await syncLocalLLMProfile();
      const profileVersionId = syncResult.profile.profile_version_id;

      // 2. Publish profile if status is CANDIDATE or probe passed
      await publishLocalLLMProfile(profileVersionId);

      // 3. Persist a durable Job. Ollama is contacted only by the local worker.
      const submission = await requestScriptBreakdown(
        prepared.import_session_id,
        profileVersionId,
        crypto.randomUUID(),
      );
      setBreakdownSuccess(
        `AI 拆解任务已持久化排队（Job ${submission.job.id.slice(0, 12)}…）。可以刷新或关闭页面；草稿完成后仍需进入第 3 阶段人工审核并应用。`
      );
      await breakdownJobQuery.refetch();
    } catch (reason) {
      setError(`发起 AI 拆解失败：${String(reason)}`);
    } finally {
      setPending(null);
    }
  };

  const isLLMPass = llmStatus.data?.status.status === "PASS";

  const mutateJob = async (job: Job, action: "cancel" | "retry") => {
    setJobAction(`${action}:${job.id}`);
    setError(null);
    try {
      if (action === "cancel") await cancelJob(job.id);
      else await retryJob(job.id);
      await breakdownJobQuery.refetch();
      if (action === "retry") setBreakdownSuccess(`Job ${job.id.slice(0, 12)}… 已重新排队，将创建新的 Attempt。`);
    } catch (reason) {
      setError(`${action === "cancel" ? "取消" : "重试"} AI 拆解任务失败：${String(reason)}`);
    } finally {
      setJobAction(null);
    }
  };

  return (
    <section className="subpanel script-import-panel" aria-labelledby="script-import-title">
      <div className="section-title">
        <span id="script-import-title">剧本文档导入</span>
        <small>FR-ING-001 · TXT / Markdown / DOCX</small>
      </div>
      <label>
        电脑中的文档绝对路径
        <span className="inline-control">
          <input
            value={path}
            onChange={(event) => changePath(event.target.value)}
            placeholder="D:\\Scripts\\episode-01.docx"
          />
          <button
            type="button"
            className="secondary"
            onClick={() => {
              void browse();
            }}
            disabled={pending !== null}
          >
            {pending === "browse" ? "选择中…" : "浏览…"}
          </button>
        </span>
      </label>
      <p className="muted">
        平台会复制并注册不可变源版本；不会修改原文档。解析预览和显式确认分为两步。
      </p>
      <div className="post-process-actions">
        <button
          type="button"
          className="secondary"
          onClick={() => {
            void preview();
          }}
          disabled={!path.trim() || pending !== null}
        >
          {pending === "preview" ? "解析中…" : "建立源版本并解析预览"}
        </button>
        <button
          type="button"
          className="primary-action"
          onClick={() => {
            void commit();
          }}
          disabled={!prepared || committed || pending !== null}
        >
          {pending === "commit" ? "提交中…" : committed ? "已确认导入" : "确认 commit（不覆盖母本）"}
        </button>
      </div>

      {prepared && (
        <div className="import-preview" aria-label="剧本文档解析预览">
          <p>
            <strong>
              {prepared.preview.paragraph_count} 段 · {prepared.preview.character_count} 字符
            </strong>{" "}
            · preview <code>{String(prepared.preview_hash ?? "").slice(0, 16) || "—"}</code>
          </p>
          <ol>
            {prepared.preview.paragraphs.map((paragraph, index) => (
              <li key={`${index}-${paragraph.slice(0, 16)}`}>{paragraph}</li>
            ))}
          </ol>
          <p className={committed ? "frame-feedback success" : "frame-feedback"}>
            {committed
              ? "COMMITTED：源文档版本已保留，重复提交幂等。"
              : "PREVIEW_READY：尚未 commit，不会自动创建生产镜头。"}
          </p>

          {committed && (
            <div className="breakdown-trigger-area">
              <div className="section-title breakdown-trigger-title">
                <span>提交 AI 剧本拆解任务（FR-WRT-007）</span>
                <small>SQLite durable Job · 本地 worker · 人工应用</small>
              </div>
              <p className="muted breakdown-trigger-guidance">
                提交后立即返回 Job；Ollama 由独立 worker 调用。拆解只生成结构化草稿与逐字原文引用，<strong>绝不自动批准、应用或覆盖生产事实</strong>。
              </p>
              <div className="breakdown-trigger-actions">
                <button
                  type="button"
                  className="primary-action"
                  onClick={() => {
                    void triggerBreakdown();
                  }}
                  disabled={pending !== null || !isLLMPass}
                >
                  {pending === "breakdown" ? "正在验证 Profile 并排队…" : "提交 AI 拆解任务"}
                </button>
                {!isLLMPass && !llmStatus.isPending && (
                  <span className="breakdown-runtime-warning" role="status">
                    本地 LLM 未就绪（状态: {llmStatus.data?.status.status ?? "检查中"}）。请确认 Ollama 已启动。
                  </span>
                )}
              </div>
              {breakdownSuccess && (
                <div className="frame-feedback success breakdown-submit-success" role="status">
                  <p>{breakdownSuccess}</p>
                  <a href="#story-review">前往第 3 阶段：审核 AI 拆解草稿 →</a>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      <section className="breakdown-job-monitor" aria-labelledby="breakdown-job-monitor-title">
        <div className="section-title">
          <span id="breakdown-job-monitor-title">AI 拆解任务进度</span>
          <small>刷新恢复 · 取消 · 失败后显式重试</small>
        </div>
        <p className="muted">
          页面关闭不会删除任务；执行依赖独立本地 worker。若长期停在 QUEUED，请到任务与机器页检查 WorkerSession。
        </p>
        {breakdownJobQuery.isPending ? <p className="empty-state">正在读取已持久化任务…</p> : null}
        {breakdownJobQuery.error ? (
          <div className="query-error-actions">
            <p className="inline-error" role="alert">任务读取失败：{String(breakdownJobQuery.error)}</p>
            <button type="button" className="secondary" onClick={() => void breakdownJobQuery.refetch()}>重新读取任务</button>
          </div>
        ) : null}
        {breakdownJobs.length ? (
          <div className="breakdown-job-list">
            {breakdownJobs.map((job) => {
              const state = String(job.state ?? "UNKNOWN");
              const progress = Number(job.progress?.percent ?? (state === "SUCCEEDED" ? 100 : 0));
              const boundedProgress = Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : 0;
              const canCancel = ["QUEUED", "CLAIMED", "RUNNING"].includes(state);
              const canRetry = ["FAILED", "NEEDS_ATTENTION", "ORPHANED"].includes(state);
              const subjectSession = String(job.subject_id ?? "");
              return (
                <article className="breakdown-job-card" key={job.id} aria-label={`AI 拆解任务 ${job.id}`}>
                  <div className="breakdown-job-heading">
                    <div>
                      <strong>{subjectSession ? `导入会话 ${subjectSession.slice(0, 12)}…` : "剧本拆解"}</strong>
                      <small>Job {job.id.slice(0, 12)}… · Attempt 失败重试保持同一 Job</small>
                    </div>
                    <span className={`status-pill state-${state.toLowerCase()}`}>{state}</span>
                  </div>
                  <label className="breakdown-progress-label">
                    <span>{String(job.progress?.phase ?? (state === "QUEUED" ? "WAITING_FOR_WORKER" : state))}</span>
                    <span>{Math.round(boundedProgress)}%</span>
                    <progress value={boundedProgress} max={100} aria-label={`AI 拆解进度 ${Math.round(boundedProgress)}%`} />
                  </label>
                  {job.last_error_code ? (
                    <p className="inline-error" role="alert">
                      {job.last_error_code}：{String(job.last_error_detail_redacted ?? "请检查本地模型与 worker 后重试。")}
                    </p>
                  ) : null}
                  {state === "SUCCEEDED" ? (
                    <p className="frame-feedback success">草稿已生成，但尚未应用。请进入审核阶段选择目标分集并人工确认。</p>
                  ) : null}
                  {state === "CANCEL_REQUESTED" ? <p className="muted">正在等待本地 Ollama 调用返回；取消会在草稿持久化前再次检查。</p> : null}
                  <div className="breakdown-job-actions">
                    <Link className="secondary v2-inline-link" to={`/projects/${projectId}/jobs?job=${encodeURIComponent(job.id)}`}>查看 Job 详情</Link>
                    {canCancel ? (
                      <button type="button" className="secondary" disabled={jobAction !== null} onClick={() => void mutateJob(job, "cancel")}>
                        {jobAction === `cancel:${job.id}` ? "取消中…" : "取消任务"}
                      </button>
                    ) : null}
                    {canRetry ? (
                      <button type="button" className="secondary" disabled={jobAction !== null} onClick={() => void mutateJob(job, "retry")}>
                        {jobAction === `retry:${job.id}` ? "重新排队中…" : "失败重试"}
                      </button>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
        ) : !breakdownJobQuery.isPending && !breakdownJobQuery.error ? (
          <p className="empty-state">当前项目还没有 AI 拆解 Job。</p>
        ) : null}
      </section>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

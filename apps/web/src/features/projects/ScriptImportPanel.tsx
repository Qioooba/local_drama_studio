import { useEffect, useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import {
  cancelJob,
  commitImportSession,
  getProjectConfiguration,
  getClientCapabilities,
  importScriptDocument,
  uploadScriptDocument,
  listEpisodes,
  listJobs,
  listSeasons,
  pickLocalDocumentFile,
  retryJob,
  type DocumentImport,
  type Job,
} from "../../generated/api";
import { requestScriptBreakdown } from "../story-workspace-v2/breakdownClient";
import { queryKeys } from "../../query/queryKeys";

export function ScriptImportPanel({ projectId, onDraftReady }: { projectId: string; onDraftReady?: (job: Job) => void }) {
  const queryClient = useQueryClient();
  const fileInputId = useId();
  const [path, setPath] = useState("");
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [prepared, setPrepared] = useState<DocumentImport | null>(null);
  const [committed, setCommitted] = useState(false);
  const [pending, setPending] = useState<"browse" | "preview" | "commit" | "breakdown" | "upload" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [breakdownSuccess, setBreakdownSuccess] = useState<string | null>(null);
  const [jobAction, setJobAction] = useState<string | null>(null);
  const [targetSeasonId, setTargetSeasonId] = useState("");
  const [targetEpisodeId, setTargetEpisodeId] = useState("");
  const [sourceParagraphStart, setSourceParagraphStart] = useState(1);
  const [sourceParagraphEnd, setSourceParagraphEnd] = useState(1);
  const [paragraphSelectionAnchor, setParagraphSelectionAnchor] = useState<number | null>(null);
  const observedJobStates = useRef<Map<string, string> | null>(null);
  const submittedJobIds = useRef(new Set<string>());
  const announcedReadyJobIds = useRef(new Set<string>());

  const projectConfiguration = useQuery({
    queryKey: queryKeys.productionSettings.section(projectId, "configuration"),
    queryFn: () => getProjectConfiguration(projectId),
  });
  const clientCapabilities = useQuery({ queryKey: ["client-capabilities"], queryFn: () => getClientCapabilities(), staleTime: Infinity });
  const seasons = useQuery({
    queryKey: queryKeys.seasons.list(projectId),
    queryFn: () => listSeasons(projectId),
  });
  const effectiveSeasonId = targetSeasonId || seasons.data?.items?.[0]?.id || "";
  const episodes = useQuery({
    queryKey: queryKeys.episodes.list(effectiveSeasonId),
    queryFn: () => listEpisodes(effectiveSeasonId),
    enabled: Boolean(effectiveSeasonId),
  });
  const effectiveEpisodeId = targetEpisodeId || episodes.data?.items?.[0]?.id || "";
  const targetEpisode = episodes.data?.items?.find((episode) => episode.id === effectiveEpisodeId);
  const projectLLMBinding = projectConfiguration.data?.configuration.profile_bindings.find(
    (binding) => binding.capability === "LLM_STORY_PARSE" && binding.binding_status === "ACTIVE" && binding.profile_status === "PUBLISHED",
  );
  const breakdownJobQuery = useQuery({
    queryKey: queryKeys.scriptBreakdown.jobs(projectId),
    queryFn: () => listJobs(projectId),
    refetchInterval: 3_000,
  });
  const breakdownJobs = (breakdownJobQuery.data?.items ?? [])
    .filter((job) => job.type === "SCRIPT_BREAKDOWN_LOCAL_LLM")
    .slice(0, 5);
  const breakdownJobStateSignature = breakdownJobs.map((job) => `${job.id}:${job.state}`).join("|");

  useEffect(() => {
    const currentStates = new Map(breakdownJobs.map((job) => [job.id, String(job.state ?? "UNKNOWN")]));
    const previousStates = observedJobStates.current;
    if (previousStates) {
      const newlyReady = breakdownJobs.find((job) => {
        if (String(job.state) !== "SUCCEEDED" || announcedReadyJobIds.current.has(job.id)) return false;
        const previousState = previousStates.get(job.id);
        return (previousState !== undefined && previousState !== "SUCCEEDED") || submittedJobIds.current.has(job.id);
      });
      if (newlyReady) {
        announcedReadyJobIds.current.add(newlyReady.id);
        submittedJobIds.current.delete(newlyReady.id);
        setBreakdownSuccess(`AI 拆解已完成，正在打开下一步进行人工审核。`);
        void queryClient.invalidateQueries({ queryKey: queryKeys.scriptBreakdown.all(projectId) });
        onDraftReady?.(newlyReady);
      }
    }
    observedJobStates.current = currentStates;
  }, [breakdownJobStateSignature, onDraftReady, projectId, queryClient]);

  const changePath = (value: string) => {
    setPath(value);
    setSelectedFileName(null);
    setPrepared(null);
    setCommitted(false);
    setError(null);
    setBreakdownSuccess(null);
  };

  const handleFileUpload = async (file: File | undefined) => {
    if (!file) return;
    const name = file.name.toLowerCase();
    if (!name.endsWith(".txt") && !name.endsWith(".md") && !name.endsWith(".markdown") && !name.endsWith(".docx")) {
      setError("剧本文档仅支持 TXT、Markdown、DOCX 格式。");
      return;
    }
    setPending("upload");
    setError(null);
    setSelectedFileName(file.name);
    setPrepared(null);
    setCommitted(false);
    setBreakdownSuccess(null);
    try {
      const result = await uploadScriptDocument(projectId, file);
      setPrepared(result.import);
      setCommitted(result.import.status === "COMMITTED");
      setSourceParagraphStart(1);
      setSourceParagraphEnd(Math.max(1, Number(result.import.preview.paragraph_count ?? 1)));
      setParagraphSelectionAnchor(null);
    } catch (reason) {
      setError(`文档上传与解析失败：${String(reason)}`);
    } finally {
      setPending(null);
    }
  };

  const browse = async () => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 65_000);
    setPending("browse");
    setError(null);
    try {
      const result = await pickLocalDocumentFile("", controller.signal);
      if (result.selection.selected && result.selection.path) changePath(result.selection.path);
    } catch (reason) {
      setError(controller.signal.aborted
        ? "Windows 服务端文件选择器长时间没有返回，页面已恢复可操作；请重试，或直接点击上方“选择本地文档”上传。"
        : `Windows 服务端文件选择器失败：${String(reason)}。建议直接点击上方“选择本地文档”上传。`);
    } finally {
      window.clearTimeout(timeout);
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
      setSourceParagraphStart(1);
      setSourceParagraphEnd(Math.max(1, Number(result.import.preview.paragraph_count ?? 1)));
      setParagraphSelectionAnchor(null);
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
    if (!effectiveEpisodeId) {
      setError("请选择 AI 拆解目标集；目标时长会冻结进 Job 快照并在服务端强制校验。");
      return;
    }
    const paragraphCount = Number(prepared.preview.paragraph_count ?? 0);
    if (sourceParagraphStart < 1 || sourceParagraphEnd < sourceParagraphStart || sourceParagraphEnd > paragraphCount) {
      setError(`请选择有效的本集原文范围：1–${paragraphCount} 段。`);
      return;
    }
    if (!projectLLMBinding) {
      setError("本项目尚未绑定已发布的 LLM_STORY_PARSE Profile；请先在模型与能力中完成测试、发布与项目绑定。");
      return;
    }
    setPending("breakdown");
    setError(null);
    setBreakdownSuccess(null);
    try {
      // Persist a durable Job. Worker calls the model independently.
      const submission = await requestScriptBreakdown(
        prepared.import_session_id,
        projectLLMBinding.profile_version_id,
        effectiveEpisodeId,
        crypto.randomUUID(),
        { sourceParagraphStart, sourceParagraphEnd },
      );
      submittedJobIds.current.add(submission.job.id);
      setBreakdownSuccess(
        "AI 拆解已在后台排队。可以刷新或关闭页面；草稿完成后仍需进入下一步人工审核并应用。"
      );
      await breakdownJobQuery.refetch();
    } catch (reason) {
      setError(`发起 AI 拆解失败：${String(reason)}`);
    } finally {
      setPending(null);
    }
  };

  const isLLMPass = Boolean(projectLLMBinding);
  const sourceRangeValid = Boolean(prepared) && sourceParagraphStart >= 1 && sourceParagraphEnd >= sourceParagraphStart && sourceParagraphEnd <= Number(prepared?.preview.paragraph_count ?? 0);

  const selectParagraph = (paragraphNumber: number, extend: boolean) => {
    if (extend && paragraphSelectionAnchor !== null) {
      setSourceParagraphStart(Math.min(paragraphSelectionAnchor, paragraphNumber));
      setSourceParagraphEnd(Math.max(paragraphSelectionAnchor, paragraphNumber));
      return;
    }
    setParagraphSelectionAnchor(paragraphNumber);
    setSourceParagraphStart(paragraphNumber);
    setSourceParagraphEnd(paragraphNumber);
  };

  const selectParagraphRange = (start: number, end: number) => {
    setParagraphSelectionAnchor(start);
    setSourceParagraphStart(start);
    setSourceParagraphEnd(end);
  };

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
        <small>支持 TXT、Markdown、DOCX</small>
      </div>

      {/* File Upload Zone for LAN / Browser native picker (e.g. Mac/Windows client) */}
      <div
        className={`script-upload-dropzone${dragOver ? " drag-over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void handleFileUpload(e.dataTransfer.files?.[0]);
        }}
        style={{
          border: dragOver ? "2px dashed #3b82f6" : "2px dashed #4b5563",
          borderRadius: "8px",
          padding: "16px",
          textAlign: "center",
          marginBottom: "12px",
          backgroundColor: dragOver ? "rgba(59, 130, 246, 0.08)" : "rgba(255, 255, 255, 0.02)",
        }}
      >
        <input
          id={fileInputId}
          type="file"
          accept=".txt,.md,.markdown,.docx"
          style={{ display: "none" }}
          disabled={pending !== null}
          onChange={(event) => void handleFileUpload(event.target.files?.[0])}
        />
        <p style={{ margin: "0 0 8px 0", fontSize: "14px", fontWeight: 500 }}>
          {selectedFileName ? `已选择：${selectedFileName}` : "从本地电脑选择文档（TXT / Markdown / DOCX）"}
        </p>
        <div style={{ display: "flex", gap: "10px", justifyContent: "center", alignItems: "center", flexWrap: "wrap" }}>
          <label
            htmlFor={fileInputId}
            className="primary-action"
            style={{ display: "inline-block", cursor: pending !== null ? "not-allowed" : "pointer", margin: 0 }}
          >
            {pending === "upload" ? "正在上传解析…" : "选择本地文档"}
          </label>
          <small className="muted" style={{ margin: 0 }}>支持直接拖拽文件到此处</small>
        </div>
      </div>

      {clientCapabilities.data?.capabilities.server_file_dialogs && <details className="script-server-path-details" style={{ marginBottom: "12px" }}>
        <summary style={{ cursor: "pointer", color: "#9ca3af", fontSize: "13px" }}>
          高级：从 Windows 服务端目录导入
        </summary>
        <div style={{ marginTop: "8px" }}>
          <label>
            Windows 服务端中的文档绝对路径
            <span className="inline-control">
              <strong className="selected-path" aria-label="已选择文档路径" title={path}>{path || "尚未选择文档"}</strong>
              <button
                type="button"
                className="secondary"
                aria-label="浏览…"
                onClick={() => {
                  void browse();
                }}
                disabled={pending !== null}
              >
                {pending === "browse" ? "选择中…" : "浏览…"}
              </button>
            </span>
            <small>通过系统文件选择器取得路径，不需要手动输入。</small>
          </label>
          <div className="post-process-actions" style={{ marginTop: "8px" }}>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                void preview();
              }}
              disabled={!path.trim() || pending !== null}
            >
              {pending === "preview" ? "解析中…" : "读取文档并预览"}
            </button>
          </div>
        </div>
      </details>}

      <p className="muted">
        系统不会修改原文档。请先预览并选择正文范围，确认后再建立可追溯的项目副本。
      </p>
      <div className="post-process-actions">
        <button
          type="button"
          className="primary-action"
          onClick={() => {
            void commit();
          }}
          disabled={!prepared || committed || pending !== null}
        >
          {pending === "commit" ? "正在导入…" : committed ? "已导入项目" : "确认导入所选原稿"}
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
          <div className="import-selection-toolbar" aria-label="章节与原文范围快捷选择">
            <div>
              <strong>选择本集原文</strong>
              <small>单击选择一段；按住 Shift 再点另一段可连续选择。</small>
            </div>
            <button type="button" className="secondary" onClick={() => selectParagraphRange(1, prepared.preview.paragraph_count)}>选择全文</button>
            {(prepared.preview.chapters ?? []).map((chapter) => (
              <button
                type="button"
                className="secondary"
                key={`${chapter.start_paragraph}-${chapter.title}`}
                onClick={() => selectParagraphRange(chapter.start_paragraph, chapter.end_paragraph)}
              >
                {chapter.title} · {chapter.start_paragraph}–{chapter.end_paragraph} 段
              </button>
            ))}
          </div>
          <ol className="import-paragraph-picker" aria-label="可选择的原文段落">
            {prepared.preview.paragraphs.map((paragraph, index) => (
              <li key={`${index}-${paragraph.slice(0, 16)}`}>
                <button
                  type="button"
                  className={index + 1 >= sourceParagraphStart && index + 1 <= sourceParagraphEnd ? "selected" : ""}
                  aria-pressed={index + 1 >= sourceParagraphStart && index + 1 <= sourceParagraphEnd}
                  onClick={(event) => selectParagraph(index + 1, event.shiftKey)}
                >
                  <span>第 {index + 1} 段</span>
                  <span>{paragraph}</span>
                </button>
              </li>
            ))}
          </ol>
          {prepared.preview.preview_truncated && <p className="muted">预览仅展示前 {prepared.preview.paragraphs.length} 段；可用章节按钮选择完整章节，或在下方“精确段号”中输入未展示范围。</p>}
          <p className={committed ? "frame-feedback success" : "frame-feedback"}>
            {committed
              ? "原稿已安全导入，项目副本可随时追溯。"
              : "预览已准备好。确认前不会写入项目，也不会创建镜头。"}
          </p>

          {committed && (
            <div className="breakdown-trigger-area">
              <div className="section-title breakdown-trigger-title">
                <span>让 AI 整理场次、镜头和对白</span>
                <small>后台运行 · 可关闭页面 · 应用前由你审核</small>
              </div>
              <p className="muted breakdown-trigger-guidance">
                系统会在后台生成可编辑草稿并保留原文引用，<strong>不会自动批准、应用或覆盖你的生产内容</strong>。
              </p>
              {projectLLMBinding ? (
                <p className="frame-feedback success" role="status">
                  已准备：{projectLLMBinding.profile_title || "本地故事拆解模型"}
                </p>
              ) : null}

              <div className="breakdown-target-fields" aria-label="AI 拆解目标集与时长">
                <label>
                  目标季度
                  <select
                    aria-label="AI 拆解目标季度"
                    value={effectiveSeasonId}
                    onChange={(event) => {
                      setTargetSeasonId(event.target.value);
                      setTargetEpisodeId("");
                    }}
                  >
                    {(seasons.data?.items ?? []).map((season) => (
                      <option key={season.id} value={season.id}>{season.title || season.code}</option>
                    ))}
                  </select>
                </label>
                <label>
                  AI 拆解目标集
                  <select
                    aria-label="AI 拆解目标集"
                    value={effectiveEpisodeId}
                    onChange={(event) => setTargetEpisodeId(event.target.value)}
                    disabled={!effectiveSeasonId || episodes.isPending}
                  >
                    {(episodes.data?.items ?? []).map((episode) => (
                      <option key={episode.id} value={episode.id}>{episode.title || episode.code}</option>
                    ))}
                  </select>
                </label>
                <p className="muted" role="status">
                  {targetEpisode
                    ? `目标成片时长 ${Math.round(Number(targetEpisode.target_duration_ms ?? 0) / 1000)} 秒；草稿镜头合计必须落在目标 ±20% 内。`
                    : "项目暂无可用分集；请先创建季度与分集。"}
                </p>
                <p className="frame-feedback" role="status">已选择第 {sourceParagraphStart}–{sourceParagraphEnd} 段，共 {sourceRangeValid ? sourceParagraphEnd - sourceParagraphStart + 1 : 0} 段。章节标题会从模型输入中排除，源文件与偏移保持不变。</p>
                <details className="breakdown-paragraph-range-details">
                  <summary>高级：精确段号</summary>
                  <div className="breakdown-paragraph-range-inputs">
                    <label>
                      本集原文起始段
                      <input aria-label="本集原文起始段" type="number" min={1} max={prepared.preview.paragraph_count} value={sourceParagraphStart} onChange={(event) => { setParagraphSelectionAnchor(null); setSourceParagraphStart(Number(event.target.value)); }} />
                    </label>
                    <label>
                      本集原文结束段
                      <input aria-label="本集原文结束段" type="number" min={1} max={prepared.preview.paragraph_count} value={sourceParagraphEnd} onChange={(event) => { setParagraphSelectionAnchor(null); setSourceParagraphEnd(Number(event.target.value)); }} />
                    </label>
                  </div>
                </details>
              </div>

              <div className="breakdown-trigger-actions">
                <button
                  type="button"
                  className="primary-action"
                  onClick={() => {
                    void triggerBreakdown();
                  }}
                  disabled={pending !== null || !isLLMPass || !effectiveEpisodeId || !sourceRangeValid}
                >
                  {pending === "breakdown" ? "正在准备后台拆解…" : "开始 AI 拆解"}
                </button>
                {!isLLMPass && !projectConfiguration.isPending && (
                  <span className="breakdown-runtime-warning" role="status">
                    尚未准备故事拆解模型。<Link to={routes.settings(projectId, "capabilities")}>前往项目能力完成绑定</Link>。
                  </span>
                )}
              </div>
              {breakdownSuccess && (
                <div className="frame-feedback success breakdown-submit-success" role="status">
                  <p>{breakdownSuccess}</p>
                  <a href="#story-review">前往下一步：审核 AI 拆解草稿 →</a>
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
          拆解会在 Windows 服务端后台持续运行，关闭客户端页面也不会丢失。若长时间没有进展，请到“任务与机器”查看并恢复运行环境。
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
                    <div className="frame-feedback success"><p>草稿已生成，但尚未应用。请进入审核阶段选择目标分集并人工确认。</p>{onDraftReady ? <button type="button" className="secondary" onClick={() => { void queryClient.invalidateQueries({ queryKey: queryKeys.scriptBreakdown.all(projectId) }); onDraftReady(job); }}>打开审核阶段</button> : <a href="#story-review">打开审核阶段</a>}</div>
                  ) : null}
                  {state === "CANCEL_REQUESTED" ? <p className="muted">正在等待本地 Ollama 调用返回；取消会在草稿持久化前再次检查。</p> : null}
                  <div className="breakdown-job-actions">
                    <Link className="secondary v2-inline-link" to={`${routes.systemJobs(projectId)}&job=${encodeURIComponent(job.id)}`}>查看任务详情</Link>
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
          <p className="empty-state">当前项目还没有 AI 拆解任务。</p>
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

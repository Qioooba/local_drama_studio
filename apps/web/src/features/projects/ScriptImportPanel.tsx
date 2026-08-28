import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import {
  cancelJob,
  commitImportSession,
  getImportSessionParagraphs,
  uploadScriptDocument,
  listEpisodes,
  listJobs,
  listProfiles,
  listSeasons,
  retryJob,
  type DocumentImport,
  type Job,
} from "../../generated/api";
import { requestScriptBreakdown } from "../story-workspace-v2/breakdownClient";
import { queryKeys } from "../../query/queryKeys";
import { BreakdownJobMonitor } from "./BreakdownJobMonitor";

function preferredInitialSourceRange(preview: DocumentImport["preview"]) {
  const paragraphCount = Math.max(1, Number(preview.paragraph_count ?? 1));
  const firstChapter = preview.chapters?.[0];
  if (firstChapter && firstChapter.start_paragraph >= 1 && firstChapter.end_paragraph >= firstChapter.start_paragraph) {
    return { start: firstChapter.start_paragraph, end: Math.min(paragraphCount, firstChapter.end_paragraph) };
  }
  return { start: 1, end: paragraphCount };
}

const PARAGRAPH_PAGE_SIZE = 40;

export function ScriptImportPanel({ projectId, onDraftReady }: { projectId: string; onDraftReady?: (job: Job) => void }) {
  const queryClient = useQueryClient();
  const fileInputId = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [storedPathCopyState, setStoredPathCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [dragOver, setDragOver] = useState(false);
  const [prepared, setPrepared] = useState<DocumentImport | null>(null);
  const [committed, setCommitted] = useState(false);
  const [pending, setPending] = useState<"commit" | "breakdown" | "upload" | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [breakdownSuccess, setBreakdownSuccess] = useState<string | null>(null);
  const [jobAction, setJobAction] = useState<string | null>(null);
  const [targetSeasonId, setTargetSeasonId] = useState("");
  const [targetEpisodeId, setTargetEpisodeId] = useState("");
  const [selectedBreakdownProfileId, setSelectedBreakdownProfileId] = useState("");
  const [sourceParagraphStart, setSourceParagraphStart] = useState(1);
  const [sourceParagraphEnd, setSourceParagraphEnd] = useState(1);
  const [paragraphSelectionAnchor, setParagraphSelectionAnchor] = useState<number | null>(null);
  const [paragraphPageStart, setParagraphPageStart] = useState(1);
  const observedJobStates = useRef<Map<string, string> | null>(null);
  const submittedJobIds = useRef(new Set<string>());
  const announcedReadyJobIds = useRef(new Set<string>());

  const profiles = useQuery({
    queryKey: queryKeys.profiles.list(),
    queryFn: () => listProfiles(),
  });
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
  const breakdownModels = useMemo(() => {
    const modelOptions = (profiles.data?.models ?? []).flatMap((model) => {
      const route = model.routes.find(
        (candidate) => candidate.capability === "LLM_STORY_PARSE" && candidate.status === "PUBLISHED",
      );
      return route ? [{
        id: model.id,
        name: model.name,
        profileVersionId: route.profile_version_id,
        profileTitle: route.profile_title,
        versionNo: route.version_no,
      }] : [];
    });
    if (modelOptions.length) return modelOptions;
    return (profiles.data?.items ?? [])
      .filter((profile) => profile.capability === "LLM_STORY_PARSE" && profile.status === "PUBLISHED")
      .sort((left, right) => (right.version_no ?? 0) - (left.version_no ?? 0))
      .map((profile) => ({
        id: profile.id,
        name: String(profile.model_bundle && typeof profile.model_bundle === "object" && "model" in profile.model_bundle
          ? profile.model_bundle.model
          : profile.title),
        profileVersionId: profile.version_id,
        profileTitle: profile.title,
        versionNo: profile.version_no ?? 1,
      }));
  }, [profiles.data]);
  const selectedBreakdownModel = breakdownModels.find((model) => model.profileVersionId === selectedBreakdownProfileId) ?? null;
  const breakdownJobQuery = useQuery({
    queryKey: queryKeys.scriptBreakdown.jobs(projectId),
    queryFn: () => listJobs(projectId),
    refetchInterval: 3_000,
  });
  const paragraphPage = useQuery({
    queryKey: ["import-session-paragraphs", prepared?.import_session_id ?? "", paragraphPageStart, PARAGRAPH_PAGE_SIZE],
    queryFn: () => getImportSessionParagraphs(prepared!.import_session_id, paragraphPageStart, PARAGRAPH_PAGE_SIZE),
    enabled: Boolean(prepared?.import_session_id),
    placeholderData: (previous) => previous,
  });
  const breakdownJobs = (breakdownJobQuery.data?.items ?? [])
    .filter((job) => job.type === "SCRIPT_BREAKDOWN_LOCAL_LLM")
    .slice(0, 5);
  const breakdownJobStateSignature = breakdownJobs.map((job) => `${job.id}:${job.state}`).join("|");
  const storedSourcePath = prepared?.stored_source_path ?? "";

  useEffect(() => setStoredPathCopyState("idle"), [storedSourcePath]);

  useEffect(() => {
    if (!breakdownModels.length) {
      setSelectedBreakdownProfileId("");
      return;
    }
    setSelectedBreakdownProfileId((current) => (
      breakdownModels.some((model) => model.profileVersionId === current)
        ? current
        : breakdownModels[0].profileVersionId
    ));
  }, [breakdownModels]);

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

  const copyStoredSourcePath = async () => {
    if (!storedSourcePath) return;
    try {
      await navigator.clipboard.writeText(storedSourcePath);
      setStoredPathCopyState("copied");
    } catch {
      setStoredPathCopyState("failed");
    }
  };

  const handleFileUpload = async (file: File | undefined) => {
    if (!file) return;
    const name = file.name.toLowerCase();
    if (!name.endsWith(".txt") && !name.endsWith(".md") && !name.endsWith(".markdown") && !name.endsWith(".docx")) {
      setUploadError("该文件无法上传。请选择 TXT、Markdown 或 DOCX 文档。");
      return;
    }
    setPending("upload");
    setUploadError(null);
    setError(null);
    setSelectedFileName(file.name);
    setPrepared(null);
    setCommitted(false);
    setBreakdownSuccess(null);
    try {
      const result = await uploadScriptDocument(projectId, file);
      setPrepared(result.import);
      setCommitted(result.import.status === "COMMITTED");
      const initialRange = preferredInitialSourceRange(result.import.preview);
      setSourceParagraphStart(initialRange.start);
      setSourceParagraphEnd(initialRange.end);
      setParagraphPageStart(initialRange.start);
      setParagraphSelectionAnchor(null);
    } catch (reason) {
      setUploadError(`上传或解析失败：${String(reason)}。请检查文件后重新选择上传。`);
    } finally {
      setPending(null);
    }
  };

  const commit = async () => {
    if (!prepared) return;
    setPending("commit");
    setError(null);
    try {
      const result = await commitImportSession(prepared.import_session_id, {
        expected_preview_hash: prepared.preview_hash,
        source_paragraph_start: sourceParagraphStart,
        source_paragraph_end: sourceParagraphEnd,
      });
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
      setError("请选择这份拆解草稿对应的分集。系统会读取该集目标时长，用于控制镜头数量和总时长。");
      return;
    }
    const paragraphCount = Number(prepared.preview.paragraph_count ?? 0);
    if (sourceParagraphStart < 1 || sourceParagraphEnd < sourceParagraphStart || sourceParagraphEnd > paragraphCount) {
      setError(`请选择有效的本集原文范围：1–${paragraphCount} 段。`);
      return;
    }
    if (!selectedBreakdownModel) {
      setError("请选择一个已发布的故事拆解模型。");
      return;
    }
    setPending("breakdown");
    setError(null);
    setBreakdownSuccess(null);
    try {
      // Persist a durable Job. Worker calls the model independently.
      const submission = await requestScriptBreakdown(
        prepared.import_session_id,
        selectedBreakdownModel.profileVersionId,
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

  const isLLMPass = Boolean(selectedBreakdownModel);
  const sourceRangeValid = Boolean(prepared) && sourceParagraphStart >= 1 && sourceParagraphEnd >= sourceParagraphStart && sourceParagraphEnd <= Number(prepared?.preview.paragraph_count ?? 0);
  const selectedParagraphCount = sourceRangeValid ? sourceParagraphEnd - sourceParagraphStart + 1 : 0;
  const importWorkflowStep = committed ? 4 : prepared ? 2 : 1;
  const targetEpisodeLabel = targetEpisode?.title || targetEpisode?.code || "所选分集";
  const targetDurationSeconds = targetEpisode ? Math.round(Number(targetEpisode.target_duration_ms ?? 0) / 1000) : 0;
  const targetDurationMinimum = Math.round(targetDurationSeconds * 0.8);
  const targetDurationMaximum = Math.round(targetDurationSeconds * 1.2);
  const visibleParagraphs = paragraphPage.data?.items ?? (paragraphPageStart === 1
    ? (prepared?.preview.paragraphs ?? []).map((text, index) => ({
        number: index + 1,
        text,
        source_start: 0,
        source_end: 0,
        is_heading: false,
      }))
    : []);
  const visibleParagraphEnd = paragraphPage.data?.end_paragraph
    ?? visibleParagraphs.at(-1)?.number
    ?? paragraphPageStart;

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
    setParagraphPageStart(start);
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
        <span id="script-import-title">小说与剧本文档导入</span>
        <small>支持 TXT、Markdown、DOCX</small>
      </div>

      <ol className="import-workflow-steps" aria-label="剧本文档导入进度">
        {[
          ["上传与解析", "服务端读取并识别结构"],
          ["核对正文", "浏览章节并选择连续范围"],
          ["确认入库", "冻结范围、来源和版本记录"],
        ].map(([title, description], index) => {
          const step = index + 1;
          const state = step < importWorkflowStep ? "done" : step === importWorkflowStep ? "current" : "upcoming";
          return (
            <li key={title} className={state} aria-current={state === "current" ? "step" : undefined}>
              <span className="import-workflow-step-marker" aria-hidden="true">{state === "done" ? "✓" : step}</span>
              <span>
                <strong>{title}</strong>
                <small>{description}</small>
              </span>
            </li>
          );
        })}
      </ol>

      <aside className="import-safety-note" aria-label="原文保护说明">
        <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M12 3 5.5 5.5v5.8c0 4.1 2.6 7.8 6.5 9.7 3.9-1.9 6.5-5.6 6.5-9.7V5.5L12 3Z" />
          <path d="m9.3 12 1.8 1.8 3.8-4" />
        </svg>
        <div>
          <strong>原文受保护</strong>
          <p>系统不会修改原文档。请先预览并选择正文范围，确认后再建立可追溯的项目副本。</p>
        </div>
      </aside>

      <div
        className={`script-upload-dropzone${dragOver ? " drag-over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void handleFileUpload(e.dataTransfer.files?.[0]);
        }}
      >
        <input
          id={fileInputId}
          ref={fileInputRef}
          type="file"
          accept=".txt,.md,.markdown,.docx"
          aria-label="选择本地文档"
          className="script-upload-input"
          disabled={pending !== null}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            void handleFileUpload(file);
          }}
        />
        <p className="script-upload-file-name">
          {selectedFileName
            ? pending === "upload" ? `正在上传：${selectedFileName}` : `已上传：${selectedFileName}`
            : "从本地电脑上传原稿"}
        </p>
        <p className="script-upload-guidance">选择或拖入 TXT、Markdown、DOCX 文件，上传完成后自动解析预览。</p>
        <div className="script-upload-actions">
          <button
            type="button"
            className="primary-action"
            aria-controls={fileInputId}
            disabled={pending !== null}
            onClick={() => fileInputRef.current?.click()}
          >
            {pending === "upload" ? "正在上传并解析…" : "选择并上传文档"}
          </button>
          <small className="muted">仅接受实际文件，不接受路径文字</small>
        </div>
      </div>
      {uploadError && <p className="inline-error script-upload-error" role="alert">{uploadError}</p>}

      {prepared && (
        <div className="import-preview" aria-label="剧本文档解析预览">
          <header className="import-preview-header">
            <div>
              <small>步骤 2 · 核对正文</small>
              <h3>正文预览与范围</h3>
              <p>{prepared.preview.paragraph_count} 段 · {prepared.preview.character_count} 字符</p>
            </div>
            <span className="status-pill state-ready">
              {committed ? "项目副本已建立" : "预览已就绪"}
            </span>
          </header>

          {storedSourcePath && <section className="script-upload-location" aria-labelledby="script-upload-location-label">
            <div className="script-upload-location__value">
              <span id="script-upload-location-label">上传后服务器保存位置</span>
              <code aria-label="上传文档服务器绝对路径" title={storedSourcePath}>{storedSourcePath}</code>
              <small>{committed ? "已纳入项目版本记录，可通过来源信息追溯。" : "文件已复制到项目受控目录；确认前不会生成生产内容。"}</small>
            </div>
            <button type="button" className="secondary" onClick={() => void copyStoredSourcePath()}>
              {storedPathCopyState === "copied" ? "已复制" : storedPathCopyState === "failed" ? "复制失败，请手动选择" : "复制路径"}
            </button>
          </section>}

          <section className="manuscript-range-workspace" aria-labelledby="manuscript-range-title">
            <header className="manuscript-range-header">
              <div>
                <small>服务端解析结果</small>
                <h4 id="manuscript-range-title">确定要纳入项目的正文范围</h4>
                <p>章节、段号和字符偏移均来自不可变解析副本。先核对结构，再选择连续正文范围。</p>
              </div>
              <div className="manuscript-range-status" aria-live="polite">
                <span>{prepared.preview.paragraph_count} 段正文</span>
                <strong>{sourceRangeValid ? `已选 ${selectedParagraphCount} 段` : "范围待修正"}</strong>
              </div>
            </header>

            <div className="manuscript-browser">
              <nav className="manuscript-chapter-nav" aria-label="识别到的章节">
                <div className="manuscript-pane-heading">
                  <strong>章节导航</strong>
                  <small>规则识别，可人工改选</small>
                </div>
                <button
                  type="button"
                  className={sourceParagraphStart === 1 && sourceParagraphEnd === prepared.preview.paragraph_count ? "selected" : ""}
                  onClick={() => selectParagraphRange(1, prepared.preview.paragraph_count)}
                >
                  <span>全文</span>
                  <small>第 1–{prepared.preview.paragraph_count} 段</small>
                </button>
                {(prepared.preview.chapters ?? []).length ? (prepared.preview.chapters ?? []).map((chapter) => (
                  <button
                    type="button"
                    className={sourceParagraphStart === chapter.start_paragraph && sourceParagraphEnd === chapter.end_paragraph ? "selected" : ""}
                    key={`${chapter.start_paragraph}-${chapter.title}`}
                    onClick={() => selectParagraphRange(chapter.start_paragraph, chapter.end_paragraph)}
                  >
                    <span>{chapter.title}</span>
                    <small>第 {chapter.start_paragraph}–{chapter.end_paragraph} 段</small>
                  </button>
                )) : <p className="empty-state">未识别到章节标题。仍可在正文中连续选择，或输入精确段号。</p>}
              </nav>

              <div className="manuscript-paragraph-pane">
                <div className="manuscript-paragraph-toolbar">
                  <div className="manuscript-pane-heading">
                    <strong>正文段落</strong>
                    <small>当前显示第 {paragraphPageStart}–{visibleParagraphEnd} 段 · 单击起点，Shift + 单击终点</small>
                  </div>
                  <div className="manuscript-page-actions" aria-label="正文分页">
                    <button
                      type="button"
                      className="secondary"
                      disabled={paragraphPageStart <= 1 || paragraphPage.isFetching}
                      onClick={() => setParagraphPageStart(Math.max(1, paragraphPageStart - PARAGRAPH_PAGE_SIZE))}
                    >
                      上一页
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={!paragraphPage.data?.has_more || paragraphPage.isFetching}
                      onClick={() => setParagraphPageStart(visibleParagraphEnd + 1)}
                    >
                      下一页
                    </button>
                  </div>
                </div>

                {paragraphPage.isPending && !visibleParagraphs.length ? <p className="empty-state" role="status">正在从服务端读取正文段落…</p> : null}
                {paragraphPage.error ? (
                  <div className="query-error-actions">
                    <p className="inline-error" role="alert">正文读取失败：{String(paragraphPage.error)}</p>
                    <button type="button" className="secondary" onClick={() => void paragraphPage.refetch()}>重新读取本页</button>
                  </div>
                ) : null}
                <ol className="import-paragraph-picker" aria-label="可选择的原文段落" start={paragraphPageStart}>
                  {visibleParagraphs.map((paragraph) => (
                    <li key={`${paragraph.number}-${paragraph.source_start}`}>
                      <button
                        type="button"
                        className={`${paragraph.number >= sourceParagraphStart && paragraph.number <= sourceParagraphEnd ? "selected" : ""}${paragraph.is_heading ? " is-heading" : ""}`}
                        aria-pressed={paragraph.number >= sourceParagraphStart && paragraph.number <= sourceParagraphEnd}
                        onClick={(event) => selectParagraph(paragraph.number, event.shiftKey)}
                      >
                        <span>{paragraph.is_heading ? `章节 · 第 ${paragraph.number} 段` : `第 ${paragraph.number} 段`}</span>
                        <span>{paragraph.text}</span>
                      </button>
                    </li>
                  ))}
                </ol>
              </div>
            </div>

            <div className="manuscript-selection-inspector">
              <div className="import-selection-summary" aria-live="polite">
                <span>当前正文范围</span>
                <strong>{sourceRangeValid ? `第 ${sourceParagraphStart}–${sourceParagraphEnd} 段，共 ${selectedParagraphCount} 段` : "段落范围无效，请重新选择"}</strong>
                <small>确认后，范围和段落偏移会与导入会话一起保存，可用于后续分集拆解和来源追溯。</small>
              </div>
              <details className="breakdown-paragraph-range-details import-exact-range">
                <summary>精确输入段号</summary>
                <div className="breakdown-paragraph-range-inputs">
                  <label>
                    正文起始段
                    <input aria-label="本集原文起始段" type="number" min={1} max={prepared.preview.paragraph_count} value={sourceParagraphStart} onChange={(event) => { const value = Number(event.target.value); setParagraphSelectionAnchor(null); setSourceParagraphStart(value); if (value >= 1 && value <= prepared.preview.paragraph_count) setParagraphPageStart(value); }} />
                  </label>
                  <label>
                    正文结束段
                    <input aria-label="本集原文结束段" type="number" min={1} max={prepared.preview.paragraph_count} value={sourceParagraphEnd} onChange={(event) => { setParagraphSelectionAnchor(null); setSourceParagraphEnd(Number(event.target.value)); }} />
                  </label>
                </div>
              </details>
            </div>
          </section>

          <div className={`import-confirmation-area${committed ? " is-complete" : ""}`}>
            {committed ? (
              <div className="import-commit-success" role="status">
                <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M20 11.1V12a8 8 0 1 1-4.7-7.3" />
                  <path d="m9 11 2 2 9-9" />
                </svg>
                <div>
                  <strong>已建立可追溯的项目副本</strong>
                  <small>原文未被修改；当前选择为第 {sourceParagraphStart}–{sourceParagraphEnd} 段，共 {selectedParagraphCount} 段。</small>
                </div>
              </div>
            ) : (
              <>
                <div className="import-selection-summary" aria-live="polite">
                  <span>准备建立项目副本</span>
                  <strong>{sourceRangeValid ? `已选择第 ${sourceParagraphStart}–${sourceParagraphEnd} 段，共 ${selectedParagraphCount} 段` : "段落范围无效，请重新选择"}</strong>
                  <small>确认后将冻结当前预览版本和正文范围；不会创建镜头，也不会启动 AI。</small>
                </div>
                <button
                  type="button"
                  className="primary-action"
                  onClick={() => {
                    void commit();
                  }}
                  disabled={!sourceRangeValid || pending !== null}
                >
                  {pending === "commit" ? "正在建立副本…" : "确认并建立项目副本"}
                </button>
              </>
            )}
          </div>

          {committed && (
            <div className="breakdown-trigger-area">
              <div className="section-title breakdown-trigger-title">
                <span>让 AI 整理场次、镜头和对白</span>
                <small>后台运行 · 可关闭页面 · 应用前由你审核</small>
              </div>
              <p className="muted breakdown-trigger-guidance">
                系统会在后台生成可编辑草稿并保留原文引用，<strong>不会自动批准、应用或覆盖你的生产内容</strong>。
              </p>
              <div className="breakdown-target-fields" aria-label="拆解模型与草稿对应分集">
                <label className="breakdown-model-field">
                  拆解模型
                  <select
                    aria-label="AI 拆解模型"
                    value={selectedBreakdownProfileId}
                    onChange={(event) => setSelectedBreakdownProfileId(event.target.value)}
                    disabled={profiles.isPending || breakdownModels.length === 0 || pending !== null}
                  >
                    {profiles.isPending ? <option value="">正在读取全局模型清单…</option> : null}
                    {!profiles.isPending && breakdownModels.length === 0 ? <option value="">没有可用模型</option> : null}
                    {breakdownModels.map((model) => (
                      <option key={model.profileVersionId} value={model.profileVersionId}>
                        {model.name}
                      </option>
                    ))}
                  </select>
                  <small>
                    {selectedBreakdownModel
                      ? `本次使用“${selectedBreakdownModel.name}”；对应已发布执行配置 ${selectedBreakdownModel.profileTitle} v${selectedBreakdownModel.versionNo}。提交后会冻结到任务中。`
                      : "这里只列出全局能力清单中已发布的故事拆解模型。"}
                  </small>
                </label>
                <section className="breakdown-destination" aria-labelledby="breakdown-destination-title">
                  <header className="breakdown-destination__header">
                    <small>本次草稿去向</small>
                    <h4 id="breakdown-destination-title">这段正文准备做成哪一集？</h4>
                    <p>选择分集后，系统会读取它的目标成片时长，帮助 AI 控制镜头数量和总时长。这里只生成待审核草稿，不会覆盖该集现有内容。</p>
                  </header>

                  <div className="breakdown-destination__fields">
                    <label>
                      所属季度
                      <select
                        aria-label="草稿所属季度"
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
                      草稿对应分集
                      <select
                        aria-label="草稿对应分集"
                        value={effectiveEpisodeId}
                        onChange={(event) => setTargetEpisodeId(event.target.value)}
                        disabled={!effectiveSeasonId || episodes.isPending}
                      >
                        {(episodes.data?.items ?? []).map((episode) => (
                          <option key={episode.id} value={episode.id}>{episode.title || episode.code}</option>
                        ))}
                      </select>
                    </label>
                  </div>

                  {targetEpisode ? (
                    <div className="breakdown-destination__summary" role="status">
                      <div className="breakdown-destination__result">
                        <span>本次将生成</span>
                        <strong>{targetEpisodeLabel}的待审核拆解草稿</strong>
                      </div>
                      <dl>
                        <div><dt>目标成片</dt><dd>{targetDurationSeconds} 秒</dd></div>
                        <div><dt>草稿时长范围</dt><dd>{targetDurationMinimum}–{targetDurationMaximum} 秒</dd></div>
                        <div><dt>使用正文</dt><dd>第 {sourceParagraphStart}–{sourceParagraphEnd} 段</dd></div>
                      </dl>
                      <small>章节标题会从模型输入中排除；原文件、段落偏移和版本记录保持不变。</small>
                    </div>
                  ) : (
                    <div className="breakdown-destination__empty" role="status">
                      <strong>项目暂无可用分集</strong>
                      <span>请先在项目首页创建季度与分集，再回来生成拆解草稿。</span>
                      <Link to={routes.projectHome(projectId)}>前往项目首页</Link>
                    </div>
                  )}
                </section>
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
                  {pending === "breakdown" ? `正在为${targetEpisodeLabel}准备草稿…` : targetEpisode ? `为${targetEpisodeLabel}生成拆解草稿` : "生成拆解草稿"}
                </button>
                {!isLLMPass && !profiles.isPending && (
                  <span className="breakdown-runtime-warning" role="status">
                    尚无已发布的故事拆解模型。<Link to={routes.systemCapabilities()}>前往能力与模型完成接入和发布</Link>。
                  </span>
                )}
                {profiles.error ? (
                  <span className="breakdown-runtime-warning" role="alert">
                    模型清单读取失败。<button type="button" className="text-action" onClick={() => void profiles.refetch()}>重新读取</button>
                  </span>
                ) : null}
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
      <BreakdownJobMonitor
        projectId={projectId}
        jobs={breakdownJobs}
        models={breakdownModels}
        isPending={breakdownJobQuery.isPending}
        error={breakdownJobQuery.error}
        jobAction={jobAction}
        onRefetch={() => {
          void breakdownJobQuery.refetch();
        }}
        onMutate={mutateJob}
        onDraftReady={onDraftReady ? (job) => {
          void queryClient.invalidateQueries({ queryKey: queryKeys.scriptBreakdown.all(projectId) });
          onDraftReady(job);
        } : undefined}
      />
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

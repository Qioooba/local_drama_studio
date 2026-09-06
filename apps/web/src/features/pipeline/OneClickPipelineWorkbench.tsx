import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getProjectOverviewV2, uploadScriptDocument } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { CapabilityPicker, effectiveCapabilityProfile, useCapabilityOptions } from "../model-config/CapabilityPicker";
import { listAdaptationSources, type SourceVersionSummary } from "../story-adaptation/adaptationPlanClient";
import {
  applyPipelineRun,
  cancelPipelineRun,
  getLatestPipeline,
  getPipelineRun,
  getWholeDramaStatus,
  listPipelineRuns,
  preflightStoryPipeline,
  retryPipelineRun,
  runWholeDrama,
  startOneClickPipeline,
  type PipelineAssetCandidate,
  type PipelineRun,
  type StartPipelinePayload,
} from "./pipelineClient";
import "./one-click-pipeline.css";

const VISUAL_STYLES = [
  "国风仙侠 电影级写实 (Cinematic Realistic)",
  "现代都市 悬疑写实 (Urban Suspense)",
  "玄幻奇幻 动漫风格 (Anime Fantasy)",
  "复古港风 胶片质感 (Vintage Film)",
  "科幻赛博 霓虹写实 (Cyberpunk Sci-Fi)",
];
const MAX_DOCUMENT_BYTES = 25 * 1024 * 1024;
const PIPELINE_SECTIONS = ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"];
const DURATION_OPTIONS_SECONDS = [60, 90, 120, 180];

type SourceMode = "upload" | "existing" | "paste";
type ActiveSource = { versionId: string; name: string; charCount?: number; paragraphCount?: number };

function errorText(reason: unknown): string {
  const message = String((reason as Error)?.message ?? reason ?? "未知错误");
  if (message.includes("PIPELINE_ALREADY_RUNNING")) return "当前项目已有 AI 制作任务正在运行。";
  if (message.includes("PIPELINE_REVISION_CONFLICT")) return "任务状态刚刚发生变化，请刷新后重试。";
  return message;
}

function kindLabel(kind: string): string {
  if (kind === "CHARACTER") return "人物";
  if (kind === "SCENE") return "场景";
  return "道具";
}

function AssetChip({ asset }: { asset: PipelineAssetCandidate }) {
  return (
    <span className="pipeline-asset-chip" title={asset.description || asset.introduction || asset.visual_prompt || asset.name}>
      <small>{kindLabel(asset.kind)}</small>
      <strong>{asset.name}</strong>
    </span>
  );
}

function durationFromProjectOverview(overview: Awaited<ReturnType<typeof getProjectOverviewV2>> | undefined): number | null {
  // The project default is the only project-level source.  An episode's
  // target_duration_ms is a resolved value and may be an intentional
  // per-episode override, so the first episode must never stand in for the
  // project setting.
  const seconds = Math.round(Number(overview?.project?.target_duration_ms ?? 0) / 1_000);
  return Number.isFinite(seconds) && seconds >= 30 && seconds <= 600 ? seconds : null;
}

export function OneClickPipelineWorkbench({
  projectId,
  sourceDocumentVersionId,
}: {
  projectId: string;
  sourceDocumentVersionId?: string;
}) {
  const queryClient = useQueryClient();
  const fileInputId = useId();
  const autoApplyRun = useRef<string | null>(null);
  const [sourceMode, setSourceMode] = useState<SourceMode>(sourceDocumentVersionId ? "existing" : "upload");
  const [activeSource, setActiveSource] = useState<ActiveSource | null>(null);
  const [rawText, setRawText] = useState("");
  const [visualStyle, setVisualStyle] = useState(VISUAL_STYLES[0]);
  const [targetDurationOverride, setTargetDurationOverride] = useState<number | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [configuring, setConfiguring] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const capabilityOptions = useCapabilityOptions("LLM_STORY_PARSE", { projectId });
  const resolvedCapability = effectiveCapabilityProfile(capabilityOptions, selectedProfileId);
  const projectOverviewQuery = useQuery({
    queryKey: queryKeys.projects.overview(projectId),
    queryFn: () => getProjectOverviewV2(projectId),
    enabled: Boolean(projectId),
  });
  const projectTargetDuration = useMemo(() => durationFromProjectOverview(projectOverviewQuery.data), [projectOverviewQuery.data]);
  const targetDuration = targetDurationOverride ?? projectTargetDuration ?? 120;
  const targetDurationOptions = useMemo(
    () => Array.from(new Set([...DURATION_OPTIONS_SECONDS, targetDuration])).sort((left, right) => left - right),
    [targetDuration],
  );
  const latestKey = ["one-click-pipeline", projectId, "latest"] as const;
  const historyKey = ["one-click-pipeline", projectId, "runs"] as const;
  const latestQuery = useQuery({
    queryKey: latestKey,
    queryFn: () => getLatestPipeline(projectId),
    refetchInterval: (query) => (query.state.data?.run?.state === "RUNNING" ? 1200 : false),
  });
  const historyQuery = useQuery({ queryKey: historyKey, queryFn: () => listPipelineRuns(projectId) });
  const selectedRunQuery = useQuery({
    queryKey: ["one-click-pipeline", projectId, "run", selectedRunId],
    queryFn: () => getPipelineRun(projectId, selectedRunId!),
    enabled: Boolean(selectedRunId),
  });
  const sourcesQuery = useQuery({
    queryKey: queryKeys.adaptationPlanning.sources(projectId),
    queryFn: () => listAdaptationSources(projectId),
    enabled: sourceMode === "existing",
  });

  const latestRun = latestQuery.data?.run ?? null;
  const run = selectedRunId ? selectedRunQuery.data?.run ?? null : latestRun;
  const loadingSelectedRun = Boolean(selectedRunId) && selectedRunQuery.isPending;
  const showConfig = configuring || (!latestQuery.isPending && !loadingSelectedRun && !run);

  useEffect(() => {
    if (!latestRun || latestRun.state === "RUNNING") return;
    void queryClient.invalidateQueries({ queryKey: historyKey });
  }, [latestRun?.revision, latestRun?.state, projectId, queryClient]);

  const sourcePayload = useMemo(() => {
    const sourceId = activeSource?.versionId || sourceDocumentVersionId;
    if (sourceMode === "paste") return { raw_text: rawText.trim() || undefined };
    return { source_document_version_id: sourceId || undefined };
  }, [activeSource?.versionId, rawText, sourceDocumentVersionId, sourceMode]);
  const canStart = Boolean(sourcePayload.source_document_version_id || (sourcePayload.raw_text?.length ?? 0) >= 20);

  const startPayload = (): StartPipelinePayload => ({
    ...sourcePayload,
    visual_style: visualStyle,
    target_episode_duration_seconds: targetDuration,
    capability_profile_version_id: selectedProfileId || resolvedCapability?.profileVersionId || undefined,
  });

  const launchMutation = useMutation({
    mutationFn: async () => {
      const payload = startPayload();
      const preflight = await preflightStoryPipeline(projectId, payload);
      if (!preflight.ai.ready) throw new Error(preflight.ai.message || "故事解析模型尚未就绪");
      return startOneClickPipeline(projectId, payload);
    },
    onSuccess: ({ run: nextRun }) => {
      queryClient.setQueryData(latestKey, { run: nextRun });
      setSelectedRunId(null);
      setConfiguring(false);
      void queryClient.invalidateQueries({ queryKey: historyKey });
    },
  });
  const cancelMutation = useMutation({
    mutationFn: () => {
      if (!run) throw new Error("没有可取消的任务");
      return cancelPipelineRun(projectId, run.run_id);
    },
    onSuccess: ({ run: nextRun }) => {
      queryClient.setQueryData(latestKey, { run: nextRun });
      setSelectedRunId(null);
      void queryClient.invalidateQueries({ queryKey: historyKey });
    },
  });
  const retryMutation = useMutation({
    mutationFn: () => {
      if (!run) throw new Error("没有可重试的任务");
      return retryPipelineRun(projectId, run.run_id, run.revision);
    },
    onSuccess: ({ run: nextRun }) => {
      queryClient.setQueryData(latestKey, { run: nextRun });
      setSelectedRunId(null);
      void queryClient.invalidateQueries({ queryKey: historyKey });
    },
  });
  const applyMutation = useMutation({
    mutationFn: () => {
      if (!run) throw new Error("没有可写入的 AI 分析结果");
      return applyPipelineRun(projectId, run.run_id, run.revision, PIPELINE_SECTIONS);
    },
    onSuccess: ({ run: nextRun }) => {
      queryClient.setQueryData(latestKey, { run: nextRun });
      setSelectedRunId(null);
      autoApplyRun.current = null;
      void queryClient.invalidateQueries({ queryKey: historyKey });
      void queryClient.invalidateQueries({ queryKey: ["story-assets", projectId] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects.overview(projectId) });
      void queryClient.invalidateQueries({ queryKey: ["creative-entries", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["asset-proposals", projectId] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.adaptationPlanning.sources(projectId) });
      // Applying a story plan changes existing episode titles and creates the
      // catalog used by both navigation and the project overview.
      void queryClient.invalidateQueries({ queryKey: queryKeys.seasons.catalog(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.seasons.list(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.episodes.lists() });
    },
    onError: () => {
      autoApplyRun.current = null;
    },
  });

  const wholeDramaStatusQuery = useQuery({
    queryKey: ["whole-drama-status", projectId],
    queryFn: () => getWholeDramaStatus(projectId),
    enabled: Boolean(projectId && run?.apply_state === "APPLIED"),
    refetchInterval: (query) => (query.state.data?.overall_status === "RUNNING" ? 3000 : false),
  });

  const wholeDramaMutation = useMutation({
    mutationFn: () => runWholeDrama(projectId, { production_mode: "BALANCED" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["whole-drama-status", projectId] });
    },
  });

  useEffect(() => {
    if (
      selectedRunId
      || !run
      || run.state !== "SUCCEEDED"
      || run.apply_state === "APPLIED"
      || run.quality_report?.status !== "READY"
      || autoApplyRun.current === run.run_id
    ) return;
    autoApplyRun.current = run.run_id;
    applyMutation.mutate();
  }, [run?.run_id, run?.state, run?.apply_state, run?.quality_report?.status, selectedRunId]);

  const selectMode = (mode: SourceMode) => {
    setSourceMode(mode);
    setUploadError(null);
    launchMutation.reset();
  };
  const uploadFile = async (file?: File) => {
    if (!file) return;
    if (!/\.(txt|md|markdown|docx|pdf|epub)$/i.test(file.name)) {
      setUploadError("请选择 TXT、Markdown、DOCX、PDF 或 EPUB 文档。");
      return;
    }
    if (file.size > MAX_DOCUMENT_BYTES) {
      setUploadError("文件超过 25 MB，请拆分后上传。");
      return;
    }
    setIsUploading(true);
    setUploadError(null);
    try {
      const result = await uploadScriptDocument(projectId, file);
      setActiveSource({
        versionId: result.import.source_document_version_id,
        name: file.name,
        charCount: Number(result.import.preview?.character_count ?? 0),
        paragraphCount: Number(result.import.preview?.paragraph_count ?? 0),
      });
      setRawText("");
      launchMutation.reset();
    } catch (reason) {
      setUploadError(`上传或解析失败：${errorText(reason)}`);
    } finally {
      setIsUploading(false);
    }
  };

  const stageSteps = [
    ["SOURCE_ANALYSIS", "理解原稿"],
    ["STORY_PLANNING", "规划分集"],
    ["ASSET_EXTRACTION", "识别核心资产"],
    ["SCRIPT_BREAKDOWN", "准备分集制作"],
    ["REVIEW_READY", "写入项目"],
  ] as const;
  const currentStageIndex = Math.max(0, stageSteps.findIndex(([code]) => code === run?.stage));
  const assetCandidates = [
    ...(run?.assets?.characters ?? run?.draft?.assets?.characters ?? []),
    ...(run?.assets?.scenes ?? run?.draft?.assets?.scenes ?? []),
    ...(run?.assets?.props ?? run?.draft?.assets?.props ?? []),
  ];
  const needsAttention = run?.state === "SUCCEEDED"
    && run.apply_state !== "APPLIED"
    && run.quality_report?.status !== "READY";

  return (
    <section className="story-draft-workbench" aria-labelledby="story-draft-heading">
      <header className="story-draft-header">
        <div>
          <span className="story-draft-eyebrow">一键制作</span>
          <h3 id="story-draft-heading">从完整原稿开始制作</h3>
          <p>AI 先建立轻量分集计划和可复用核心资产，详细剧本、分镜与生成提示词在制作每一集时按需完成。</p>
        </div>
        {run && !showConfig && run.state !== "RUNNING" && (
          <button type="button" className="pipeline-button secondary" onClick={() => setConfiguring(true)}>更换原稿</button>
        )}
      </header>

      {(historyQuery.data?.runs.length ?? 0) > 0 && (
        <details className="pipeline-history">
          <summary>历史分析（{historyQuery.data!.runs.length}）</summary>
          <div className="pipeline-history-list">
            {historyQuery.data!.runs.map((item, index) => (
              <button
                key={item.run_id}
                type="button"
                className={(selectedRunId ? item.run_id === selectedRunId : item.run_id === latestRun?.run_id) ? "active" : ""}
                onClick={() => { setSelectedRunId(item.run_id === latestRun?.run_id ? null : item.run_id); setConfiguring(false); }}
              >
                <span><strong>{index === 0 ? "最新版" : `版本 ${historyQuery.data!.runs.length - index}`}</strong><small>{new Date(item.created_at).toLocaleString()}</small></span>
                <span><b>{item.state === "SUCCEEDED" ? (item.apply_state === "APPLIED" ? "已进入制作" : "待处理") : item.state === "FAILED" ? "失败" : item.state === "CANCELLED" ? "已取消" : "生成中"}</b><small>{item.episodes_count} 集</small></span>
              </button>
            ))}
          </div>
        </details>
      )}

      {loadingSelectedRun ? (
        <div className="pipeline-state-card" aria-live="polite"><span>正在读取</span><h4>加载历史分析…</h4></div>
      ) : showConfig ? (
        <div className="story-config-main pipeline-simple-config">
          <div className="pipeline-section-heading"><span>1</span><div><strong>提供完整原稿</strong><small>AI 自动识别章节与正文范围</small></div></div>
          <div className="source-mode-tabs" role="tablist" aria-label="原稿来源">
            {(["upload", "existing", "paste"] as SourceMode[]).map((mode) => (
              <button key={mode} type="button" role="tab" aria-selected={sourceMode === mode} className={sourceMode === mode ? "active" : ""} onClick={() => selectMode(mode)}>
                {{ upload: "上传文档", existing: "已有原稿", paste: "粘贴正文" }[mode]}
              </button>
            ))}
          </div>

          {sourceMode === "upload" && (activeSource ? (
            <div className="selected-source-card">
              <div><strong>{activeSource.name}</strong><small>{activeSource.charCount || 0} 字符 · {activeSource.paragraphCount || 0} 段</small></div>
              <button type="button" className="pipeline-button quiet" onClick={() => { setActiveSource(null); launchMutation.reset(); }}>更换</button>
            </div>
          ) : (
            <label className="source-dropzone" htmlFor={fileInputId}>
              <input id={fileInputId} type="file" accept=".txt,.md,.markdown,.docx,.pdf,.epub" disabled={isUploading} onChange={(event) => { void uploadFile(event.target.files?.[0]); event.target.value = ""; }} />
              <strong>{isUploading ? "正在读取原稿…" : "选择小说或剧本文档"}</strong>
              <span>TXT、Markdown、DOCX、PDF、EPUB，最大 25 MB</span>
            </label>
          ))}
          {sourceMode === "existing" && (
            <label className="pipeline-field">
              <span>项目原稿</span>
              <select
                value={activeSource?.versionId || sourceDocumentVersionId || ""}
                onChange={(event) => {
                  const item = sourcesQuery.data?.items.find((source: SourceVersionSummary) => source.source_document_version_id === event.target.value);
                  setActiveSource(item ? {
                    versionId: item.source_document_version_id,
                    name: item.source_name || item.title,
                    charCount: item.character_count,
                    paragraphCount: item.paragraph_count,
                  } : null);
                  launchMutation.reset();
                }}
              >
                <option value="">请选择原稿</option>
                {(sourcesQuery.data?.items ?? []).map((source: SourceVersionSummary) => (
                  <option key={source.source_document_version_id} value={source.source_document_version_id}>{source.source_name || source.title}（{source.character_count} 字）</option>
                ))}
              </select>
            </label>
          )}
          {sourceMode === "paste" && (
            <label className="pipeline-field">
              <span>正文内容</span>
              <textarea rows={7} value={rawText} placeholder="粘贴小说正文或剧本内容" onChange={(event) => { setRawText(event.target.value); setActiveSource(null); launchMutation.reset(); }} />
              <small className={rawText.trim().length > 0 && rawText.trim().length < 20 ? "field-warning" : ""}>{rawText.trim().length.toLocaleString()} 字符</small>
            </label>
          )}
          {uploadError && <p className="pipeline-alert error" role="alert">{uploadError}</p>}

          <div className="pipeline-section-heading"><span>2</span><div><strong>选择成片方向</strong><small>其余参数由项目默认值和模型能力自动决定</small></div></div>
          <div className="pipeline-setting-grid pipeline-setting-grid--compact">
            <label className="pipeline-field"><span>视觉风格</span><select value={visualStyle} onChange={(event) => { setVisualStyle(event.target.value); launchMutation.reset(); }}>{VISUAL_STYLES.map((style) => <option key={style}>{style}</option>)}</select></label>
            <label className="pipeline-field"><span>单集时长</span><select value={targetDuration} onChange={(event) => { setTargetDurationOverride(Number(event.target.value)); launchMutation.reset(); }}>{targetDurationOptions.map((seconds) => <option key={seconds} value={seconds}>约 {seconds} 秒</option>)}</select></label>
          </div>
          <details className="pipeline-advanced">
            <summary>模型设置（通常无需修改）</summary>
            <CapabilityPicker capability="LLM_STORY_PARSE" value={selectedProfileId} onChange={(value) => { setSelectedProfileId(value); launchMutation.reset(); }} query={capabilityOptions} label="故事解析模型" description="未指定时自动使用当前可用方案。" />
          </details>
          <div className="pipeline-launch">
            <button type="button" className="pipeline-button primary" disabled={!canStart || isUploading || launchMutation.isPending} onClick={() => launchMutation.mutate()}>
              {launchMutation.isPending ? "AI 正在检查并启动…" : "开始 AI 制作"}
            </button>
            <small>无需逐项审核文字档案；识别可靠时会自动进入分集制作。</small>
          </div>
          {launchMutation.isError && <p className="pipeline-alert error" role="alert">{errorText(launchMutation.error)}</p>}
          {launchMutation.isError && <Link className="pipeline-text-link" to="/system/capabilities?view=resources">检查 AI 模型配置</Link>}
        </div>
      ) : run?.state === "RUNNING" ? (
        <div className="pipeline-running-card" aria-live="polite">
          <div className="running-summary"><div><span>AI 制作中</span><h4>{run.stage_label}</h4></div><strong>{run.progress_pct}%</strong></div>
          <div className="pipeline-progress" aria-label={`生成进度 ${run.progress_pct}%`}><span style={{ width: `${run.progress_pct}%` }} /></div>
          <ol className="pipeline-stage-list">
            {stageSteps.map(([code, label], index) => <li key={code} className={index < currentStageIndex ? "done" : index === currentStageIndex ? "active" : ""}><span>{index + 1}</span><small>{label}</small></li>)}
          </ol>
          <div className="pipeline-card-actions"><p>可以离开此页，后台会继续运行。</p><button type="button" className="pipeline-button danger" disabled={cancelMutation.isPending} onClick={() => cancelMutation.mutate()}>{cancelMutation.isPending ? "正在取消…" : "取消任务"}</button></div>
          {cancelMutation.isError && <p className="pipeline-alert error">{errorText(cancelMutation.error)}</p>}
        </div>
      ) : run?.state === "FAILED" ? (
        <div className="pipeline-state-card error-state"><span>自动处理失败</span><h4>项目数据没有被覆盖</h4><p>{run.error_message || "可直接重试，已完成内容会尽量复用。"}</p><div className="pipeline-card-actions"><button type="button" className="pipeline-button primary" disabled={retryMutation.isPending} onClick={() => retryMutation.mutate()}>自动重试</button><button type="button" className="pipeline-button secondary" onClick={() => setConfiguring(true)}>更换原稿</button></div></div>
      ) : run?.state === "CANCELLED" ? (
        <div className="pipeline-state-card"><span>任务已取消</span><h4>没有修改正式项目</h4><button type="button" className="pipeline-button primary" onClick={() => setConfiguring(true)}>重新开始</button></div>
      ) : run?.state === "SUCCEEDED" ? (
        <div className="pipeline-review-main pipeline-result-summary">
          <div className="review-heading">
            <div>
              <span className={`quality-pill ${needsAttention ? "review_required" : ""}`}>{run.apply_state === "APPLIED" ? "已准备完成" : needsAttention ? "需要你确认" : "正在写入项目"}</span>
              <h4>AI 分析摘要</h4>
              <p>{needsAttention ? "AI 发现了少量不确定项，请看完提示后决定是否继续。" : "详细创作记忆已在后台保存，后续会按集按需生成。"}</p>
            </div>
            <div className="review-counts"><strong>{run.episodes_count}<small>集</small></strong><strong>{run.characters_count}<small>核心人物</small></strong><strong>{run.scenes_count}<small>核心场景</small></strong></div>
          </div>
          {(run.quality_report.warnings ?? []).map((warning) => <p key={warning} className="pipeline-alert warning">{warning}</p>)}
          {(run.quality_report.blockers ?? []).map((blocker) => <p key={blocker} className="pipeline-alert error">{blocker}</p>)}
          <section className="draft-preview-section">
            <h5>分集结果</h5>
            <div className="episode-draft-list">
              {(run.draft.story_plan?.episodes ?? run.episodes ?? []).slice(0, 8).map((episode) => <article key={episode.code}><span>{episode.code}</span><div><strong>{episode.title}</strong><p>{episode.summary}</p></div></article>)}
            </div>
            {run.episodes_count > 8 && <small>另有 {run.episodes_count - 8} 集，可在项目中继续制作。</small>}
          </section>
          <section className="draft-preview-section">
            <h5>可复用核心资产</h5>
            <div className="pipeline-asset-chips">{assetCandidates.slice(0, 18).map((asset) => <AssetChip key={`${asset.kind}-${asset.name}`} asset={asset} />)}</div>
            {assetCandidates.length > 18 && <small>另有 {assetCandidates.length - 18} 项由 AI 后台管理。</small>}
          </section>
          {run.apply_state === "APPLIED" ? (
            <div className="pipeline-applied-flow">
              <div className="pipeline-next-actions">
                <button
                  type="button"
                  className="pipeline-button primary"
                  disabled={wholeDramaMutation.isPending}
                  onClick={() => wholeDramaMutation.mutate()}
                >
                  {wholeDramaMutation.isPending ? "正在自愈并启动全剧生产…" : "🚀 一键启动全剧自动成片"}
                </button>
                <Link className="pipeline-button secondary" to={`/projects/${projectId}`}>进入分集制作</Link>
                <Link className="pipeline-text-link" to={`/projects/${projectId}/assets`}>查看核心资产</Link>
              </div>
              {wholeDramaMutation.isSuccess && (
                <p className="pipeline-alert success" role="status">
                  已成功调度全剧自动生产，共调度 {wholeDramaMutation.data.dispatched_count} / {wholeDramaMutation.data.total_episodes} 集！
                </p>
              )}
              {wholeDramaMutation.isError && (
                <p className="pipeline-alert error" role="alert">
                  启动全剧自动化失败：{errorText(wholeDramaMutation.error)}
                </p>
              )}
              {wholeDramaStatusQuery.data && (
                <div className="whole-drama-status-summary">
                  <small>全剧状态：{wholeDramaStatusQuery.data.overall_status}（共 {wholeDramaStatusQuery.data.total_episodes} 集）</small>
                </div>
              )}
            </div>
          ) : needsAttention ? (
            <div className="pipeline-next-actions">
              <button type="button" className="pipeline-button primary" disabled={applyMutation.isPending || (run.quality_report.blockers?.length ?? 0) > 0} onClick={() => applyMutation.mutate()}>{applyMutation.isPending ? "正在继续…" : "确认并进入分集制作"}</button>
              <button type="button" className="pipeline-button secondary" onClick={() => setConfiguring(true)}>重新分析</button>
            </div>
          ) : (
            <div className="pipeline-state-card"><span>自动继续</span><h4>{applyMutation.isPending ? "正在把结果写入项目…" : "正在准备分集制作…"}</h4>{applyMutation.isError && <><p className="pipeline-alert error">{errorText(applyMutation.error)}</p><button type="button" className="pipeline-button primary" onClick={() => { autoApplyRun.current = run.run_id; applyMutation.mutate(); }}>重试写入</button></>}</div>
          )}
        </div>
      ) : run ? (
        <div className="pipeline-state-card error-state"><span>旧版记录</span><h4>请使用当前流程重新分析</h4><button type="button" className="pipeline-button primary" onClick={() => setConfiguring(true)}>开始新制作</button></div>
      ) : null}
    </section>
  );
}

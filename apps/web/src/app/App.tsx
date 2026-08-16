import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getAdapterContracts,
  getCapacitySnapshot,
  getProjectConfiguration,
  getModelCompatibility,
  getEpisodeProduction,
  getShotContinuityContext,
  getEpisodeTimelineStatus,
  getG6Readiness,
  getG8Readiness,
  getG9Readiness,
  planG6I2VProbe,
  getReviewContext,
  healthLive,
  h3CandidateRuntime,
  listReviewTemplates,
  listFormalSelectionCandidates,
  latestDiagnostics,
  listEpisodes,
  listDialogueLines,
  listEpisodeAudioBindings,
  listJobs,
  listProjectAssetGrantCandidates,
  listProjectAssetGrants,
  listWorkspaceAssetAuthorizations,
  listProfiles,
  listProjects,
  listVoiceProfileVersions,
  listSeasons,
  listWorkflowVersions,
  runDiagnostics,
  reviewInbox,
  runMachineCheck,
  selectMediaVersion,
  systemContract,
  submitReview,
  type HealthCheck,
  type SystemContract,
} from "../generated/api";
import { GenerationWorkbench } from "../features/generation/GenerationWorkbench";
import { PostProcessPanel } from "../features/generation/PostProcessPanel";
import { CapacitySnapshotPanel, JobsPanel } from "../features/jobs/JobsPanel";
import { ReviewInboxPanel } from "../features/reviews/ReviewInboxPanel";
import { ImageCandidateGrid } from "../features/reviews/ImageCandidateGrid";
import { FormalSelectionPanel } from "../features/reviews/FormalSelectionPanel";
import { ProfileConfigurationPanel } from "../features/profiles/ProfileConfigurationPanel";
import { ProductionCanvasPanel } from "../features/canvas/ProductionCanvasPanel";
import { EpisodeContactSheetAction } from "../features/production/EpisodeContactSheetAction";
import { EpisodeReviewPanel } from "../features/production/EpisodeReviewPanel";
import { TimelineExportAction } from "../features/production/TimelineExportAction";
import { SubtitleRevisionPanel } from "../features/production/SubtitleRevisionPanel";
import { TimelineRevisionPanel } from "../features/production/TimelineRevisionPanel";
import { DeliveryWorkflowPanel } from "../features/production/DeliveryWorkflowPanel";
import { GlobalSearchPanel } from "../features/shared/GlobalSearchPanel";
import { ProjectHealthPanel } from "../features/shared/ProjectHealthPanel";
import { WorkspaceAssetAuthorizationPanel } from "../features/shared/WorkspaceAssetAuthorizationPanel";
import { OutboxDeliveryPanel } from "../features/shared/OutboxDeliveryPanel";
import { AutomationPanel } from "../features/shared/AutomationPanel";
import { AutomationWorkflowPanel } from "../features/shared/AutomationWorkflowPanel";
import { ComfyLabPanel } from "../features/shared/ComfyLabPanel";
import { AuditHistoryPanel } from "../features/shared/AuditHistoryPanel";
import { BrandKitPanel } from "../features/shared/BrandKitPanel";
import { ContinuityPanel } from "../features/production/ContinuityPanel";
import { DirectorShotEditor } from "../features/production/DirectorShotEditor";
import { PromptTemplatePanel } from "../features/production/PromptTemplatePanel";
import { ProjectTemplateCopyAction } from "../features/projects/ProjectTemplateCopyAction";
import { ProjectCreateWizard } from "../features/projects/ProjectCreateWizard";
import { EpisodeSceneRanges } from "../features/projects/EpisodeSceneRanges";
import { ProjectPackageAction } from "../features/projects/ProjectPackageAction";
import { ProjectAssetGrantPanel } from "../features/projects/ProjectAssetGrantPanel";
import { CreativeLibrary } from "../features/projects/CreativeLibrary";
import { AIDraftReviewPanel } from "../features/projects/AIDraftReviewPanel";
import { ScriptImportPanel } from "../features/projects/ScriptImportPanel";
import { StoryboardBatchWorkbench } from "../features/projects/StoryboardBatchWorkbench";
import { DialogueTTSPanel } from "../features/status/DialogueTTSPanel";
import { AudioTrackPanel } from "../features/status/AudioTrackPanel";
import { AdapterContractsPanel, DiagnosticPanel, G8ReadinessPanel, G9ReadinessPanel, ModelCompatibilityPanel, ProjectConfigurationSnapshot, ProjectList, TimelineStatusPanel } from "../features/status/ReadinessPanels";
import { selectedItemOrFirst } from "../features/shared/selection";
import { BreadcrumbSeparatorIcon, ChevronRightIcon, StatusDotIcon, StudioMarkIcon } from "../components/icons";

type View = "overview" | "projects" | "canvas" | "reviews" | "jobs" | "profiles" | "generation" | "diagnostics";
const views: View[] = ["overview", "projects", "canvas", "reviews", "jobs", "profiles", "generation", "diagnostics"];

function readLocationState() {
  const params = new URLSearchParams(window.location.search);
  const requestedView = params.get("view");
  return {
    view: views.includes(requestedView as View) ? requestedView as View : "overview",
    projectId: params.get("project"),
    episodeId: params.get("episode"),
    shotId: params.get("shot"),
    reviewId: params.get("review"),
  };
}
function writeLocationState(state: { view: View; projectId: string | null; episodeId: string | null; shotId: string | null; reviewId?: string | null }, replace = false) {
  const url = new URL(window.location.href);
  url.searchParams.set("view", state.view);
  const selections: Array<[string, string | null]> = [["project", state.projectId], ["episode", state.episodeId], ["shot", state.shotId]];
  selections.forEach(([key, value]) => {
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  });
  if (state.reviewId) url.searchParams.set("review", state.reviewId);
  else if (state.reviewId === null || state.view !== "reviews") url.searchParams.delete("review");
  window.history[replace ? "replaceState" : "pushState"]({}, "", url);
}

function StatusCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <section className="status-card">
      <span className="status-label">{label}</span>
      <strong>{value}</strong>
      <span className="status-detail">{detail}</span>
    </section>
  );
}

type RecoverableQuery = { error: Error | null; isFetching: boolean; refetch: () => Promise<unknown> };

function WorkspaceErrorPanel({ failures }: { failures: Array<{ label: string; query: RecoverableQuery }> }) {
  if (failures.length === 0) return null;
  const retry = () => { void Promise.all(failures.map(({ query }) => query.refetch())); };
  return <section className="workspace-error" role="alert" aria-labelledby="workspace-error-title">
    <div><strong id="workspace-error-title">当前区域有 {failures.length} 项数据读取失败</strong>{failures.map(({ label, query }) => <p key={label}><span>{label}</span>：{query.error?.message ?? String(query.error)}</p>)}</div>
    <button className="secondary" onClick={retry} disabled={failures.some(({ query }) => query.isFetching)}>{failures.some(({ query }) => query.isFetching) ? "重试中…" : "重试当前区域"}</button>
  </section>;
}

function ViewButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return <button type="button" className={`nav-item${active ? " active" : ""}`} aria-current={active ? "page" : undefined} onClick={onClick}><span className="nav-label">{label}</span><ChevronRightIcon /></button>;
}

export function App() {
  const initialLocation = useMemo(readLocationState, []);
  const [view, setView] = useState<View>(initialLocation.view);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(initialLocation.projectId);
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(initialLocation.episodeId);
  const [selectedShotId, setSelectedShotId] = useState<string | null>(initialLocation.shotId);
  const [projectSearch, setProjectSearch] = useState("");
  const [projectStatus, setProjectStatus] = useState("");
  const queryClient = useQueryClient();
  const live = useQuery<HealthCheck>({ queryKey: ["health", "live"], queryFn: () => healthLive() });
  const contract = useQuery<SystemContract>({ queryKey: ["system", "contract"], queryFn: () => systemContract() });
  const adapterContracts = useQuery({ queryKey: ["adapters", "contracts"], queryFn: () => getAdapterContracts(), enabled: view === "overview" || view === "diagnostics" });
  const projects = useQuery({ queryKey: ["projects", projectSearch, projectStatus], queryFn: () => listProjects({ search: projectSearch || undefined, status: projectStatus || undefined }) });
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: () => listProfiles(), enabled: view === "profiles" || view === "generation" || view === "overview" || view === "projects" });
  const workflows = useQuery({ queryKey: ["workflow-versions"], queryFn: () => listWorkflowVersions(), enabled: view === "profiles" });
  const diagnostics = useQuery({ queryKey: ["diagnostics", "latest"], queryFn: () => latestDiagnostics(), enabled: view === "diagnostics" || view === "overview" });
  const h3Runtime = useQuery({ queryKey: ["h3", "candidate-runtime"], queryFn: () => h3CandidateRuntime(), enabled: view === "generation" || view === "overview" });
  const reviewTemplates = useQuery({ queryKey: ["reviews", "templates"], queryFn: () => listReviewTemplates(), enabled: view === "reviews" || view === "projects" });
  const selectedProjectRecord = selectedItemOrFirst(projects.data?.items, selectedProjectId);
  const selectedProject = selectedProjectRecord?.id ?? null;
  const formalCandidates = useQuery({ queryKey: ["reviews", "formal-selection", selectedProject], queryFn: () => listFormalSelectionCandidates(selectedProject as string), enabled: Boolean(selectedProject) && view === "reviews" });
  const capacitySnapshot = useQuery({ queryKey: ["capacity", selectedProject], queryFn: () => getCapacitySnapshot(selectedProject ?? undefined), enabled: view === "overview" || view === "jobs" });
  const projectConfiguration = useQuery({ queryKey: ["project-configuration", selectedProject], queryFn: () => getProjectConfiguration(selectedProject as string), enabled: Boolean(selectedProject) && (view === "profiles" || view === "projects") });
  const assetGrantCandidates = useQuery({ queryKey: ["asset-grant-candidates", selectedProject], queryFn: () => listProjectAssetGrantCandidates(selectedProject as string), enabled: Boolean(selectedProject) && view === "projects" });
  const assetGrants = useQuery({ queryKey: ["asset-grants", selectedProject], queryFn: () => listProjectAssetGrants(selectedProject as string), enabled: Boolean(selectedProject) && view === "projects" });
  const workspaceAuthorizations = useQuery({ queryKey: ["workspace-asset-authorizations", selectedProject], queryFn: () => listWorkspaceAssetAuthorizations(selectedProject as string), enabled: Boolean(selectedProject) && view === "generation" });
  const modelCompatibility = useQuery({ queryKey: ["model-compatibility", selectedProject], queryFn: () => getModelCompatibility(selectedProject as string), enabled: Boolean(selectedProject) && (view === "profiles" || view === "diagnostics" || view === "overview") });
  const seasons = useQuery({ queryKey: ["project", selectedProject, "seasons"], queryFn: () => listSeasons(selectedProject as string), enabled: Boolean(selectedProject) });
  const selectedSeason = seasons.data?.items[0]?.id ?? null;
  const episodes = useQuery({ queryKey: ["season", selectedSeason, "episodes"], queryFn: () => listEpisodes(selectedSeason as string), enabled: Boolean(selectedSeason) });
  const selectedEpisodeRecord = selectedItemOrFirst(episodes.data?.items, selectedEpisodeId);
  const selectedEpisode = selectedEpisodeRecord?.id ?? null;
  const production = useQuery({ queryKey: ["episode", selectedEpisode, "production"], queryFn: () => getEpisodeProduction(selectedEpisode as string), enabled: Boolean(selectedEpisode) });
  const timelineStatus = useQuery({ queryKey: ["episode", selectedEpisode, "timeline-status"], queryFn: () => getEpisodeTimelineStatus(selectedEpisode as string), enabled: Boolean(selectedEpisode) && view === "projects" });
  const dialogueLines = useQuery({ queryKey: ["episode", selectedEpisode, "dialogue-lines"], queryFn: () => listDialogueLines(selectedEpisode as string), enabled: Boolean(selectedEpisode) && view === "projects" });
  const voiceProfiles = useQuery({ queryKey: ["project", selectedProject, "voice-profiles"], queryFn: () => listVoiceProfileVersions(selectedProject as string), enabled: Boolean(selectedProject) && view === "projects" });
  const audioBindings = useQuery({ queryKey: ["episode", selectedEpisode, "audio-bindings"], queryFn: () => listEpisodeAudioBindings(selectedEpisode as string), enabled: Boolean(selectedEpisode) && view === "projects" });
  const g8Readiness = useQuery({ queryKey: ["gates", "g8", selectedProject, selectedEpisode], queryFn: () => getG8Readiness(selectedProject as string, selectedEpisode as string), enabled: Boolean(selectedProject && selectedEpisode) && view === "projects" });
  const g9Readiness = useQuery({ queryKey: ["gates", "g9", selectedProject, selectedEpisode], queryFn: () => getG9Readiness(selectedProject as string, selectedEpisode as string), enabled: Boolean(selectedProject && selectedEpisode) && view === "canvas" });
  const selectedShot = production.data?.items.some((item) => String(item.id) === selectedShotId) ? selectedShotId : production.data?.items[0] ? String(production.data.items[0].id) : null;
  const continuity = useQuery({ queryKey: ["shot", selectedShot, "continuity"], queryFn: () => getShotContinuityContext(selectedShot as string), enabled: Boolean(selectedShot) && view === "generation" });
  const reviewItems = useQuery({ queryKey: ["reviews", "inbox", selectedProject], queryFn: () => reviewInbox(selectedProject as string), enabled: Boolean(selectedProject) && (view === "reviews" || view === "generation") });
  const g6Readiness = useQuery({ queryKey: ["gates", "g6", selectedProject], queryFn: () => getG6Readiness(selectedProject as string), enabled: Boolean(selectedProject) && view === "generation" });
  const i2vProbePlan = useQuery({ queryKey: ["gates", "g6", "i2v-probe-plan", selectedProject], queryFn: () => planG6I2VProbe(selectedProject as string), enabled: Boolean(selectedProject) && view === "generation" });
  const [selectedReviewVersionId, setSelectedReviewVersionId] = useState<string | null>(initialLocation.reviewId);
  const selectedReviewVersion = selectedReviewVersionId ?? reviewItems.data?.items[0]?.media_version_id ?? null;
  const reviewContext = useQuery({ queryKey: ["reviews", "context", selectedReviewVersion], queryFn: () => getReviewContext(selectedReviewVersion as string), enabled: Boolean(selectedReviewVersion) && view === "reviews" });
  const jobs = useQuery({ queryKey: ["jobs", selectedProject], queryFn: () => listJobs(selectedProject ?? undefined), enabled: view === "jobs" });
  const activeQueries: Array<{ label: string; query: RecoverableQuery }> = [
    { label: "API 状态", query: live },
    { label: "本地系统契约", query: contract },
    { label: "项目列表", query: projects },
  ];
  if (view === "overview") activeQueries.push({ label: "本地能力", query: profiles }, { label: "诊断摘要", query: diagnostics }, { label: "适配器契约", query: adapterContracts }, { label: "容量摘要", query: capacitySnapshot }, { label: "模型证据", query: modelCompatibility });
  if (view === "projects") activeQueries.push({ label: "季数据", query: seasons }, { label: "分集数据", query: episodes }, { label: "生产状态", query: production }, { label: "时间线状态", query: timelineStatus }, { label: "G8 门禁", query: g8Readiness }, { label: "审核模板", query: reviewTemplates }, { label: "资产授权候选", query: assetGrantCandidates }, { label: "资产 Grant", query: assetGrants }, { label: "项目配置", query: projectConfiguration });
  if (view === "canvas") activeQueries.push({ label: "季数据", query: seasons }, { label: "分集数据", query: episodes }, { label: "生产状态", query: production }, { label: "G9 门禁", query: g9Readiness });
  if (view === "reviews") activeQueries.push({ label: "审核收件箱", query: reviewItems }, { label: "正式交付候选", query: formalCandidates }, { label: "审核模板", query: reviewTemplates }, { label: "审核上下文", query: reviewContext });
  if (view === "jobs") activeQueries.push({ label: "任务列表", query: jobs }, { label: "容量摘要", query: capacitySnapshot });
  if (view === "profiles") activeQueries.push({ label: "能力版本", query: profiles }, { label: "工作流版本", query: workflows }, { label: "项目配置", query: projectConfiguration }, { label: "模型证据", query: modelCompatibility });
  if (view === "generation") activeQueries.push({ label: "能力版本", query: profiles }, { label: "H3 本机状态", query: h3Runtime }, { label: "媒体候选", query: reviewItems }, { label: "工作区授权", query: workspaceAuthorizations }, { label: "生产上下文", query: production }, { label: "连续性上下文", query: continuity }, { label: "G6 门禁", query: g6Readiness }, { label: "I2V 探针计划", query: i2vProbePlan });
  if (view === "diagnostics") activeQueries.push({ label: "诊断详情", query: diagnostics }, { label: "适配器契约", query: adapterContracts }, { label: "模型证据", query: modelCompatibility });
  const queryFailures = activeQueries.filter(({ query }) => Boolean(query.error));
  const diagnosticMutation = useMutation({
    mutationFn: () => runDiagnostics(),
    onSuccess: (data) => queryClient.setQueryData(["diagnostics", "latest"], data),
  });
  const selectMutation = useMutation({
    mutationFn: ({ mediaVersionId, selectionType }: { mediaVersionId: string; selectionType: string }) => selectMediaVersion(mediaVersionId, selectionType),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reviews", "inbox"] });
      void queryClient.invalidateQueries({ queryKey: ["reviews", "context"] });
    },
  });
  const reviewMutation = useMutation({
    mutationFn: ({ mediaVersionId, payload }: { mediaVersionId: string; payload: Parameters<typeof submitReview>[1] }) => submitReview(mediaVersionId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reviews", "inbox"] });
      void queryClient.invalidateQueries({ queryKey: ["reviews", "context"] });
    },
  });
  const machineCheckMutation = useMutation({ mutationFn: (mediaVersionId: string) => runMachineCheck(mediaVersionId), onSuccess: () => { void reviewContext.refetch(); } });
  const navigate = useCallback((nextView: View) => {
    setView(nextView);
    writeLocationState({ view: nextView, projectId: selectedProject, episodeId: selectedEpisode, shotId: selectedShot });
  }, [selectedEpisode, selectedProject, selectedShot]);
  const selectProject = useCallback((projectId: string, nextView = view) => {
    setSelectedProjectId(projectId);
    setSelectedEpisodeId(null);
    setSelectedShotId(null);
    if (nextView !== view) setView(nextView);
    writeLocationState({ view: nextView, projectId, episodeId: null, shotId: null });
  }, [view]);
  const selectEpisode = useCallback((episodeId: string) => {
    setSelectedEpisodeId(episodeId);
    setSelectedShotId(null);
    writeLocationState({ view, projectId: selectedProject, episodeId, shotId: null });
  }, [selectedProject, view]);
  const selectShot = useCallback((shotId: string) => {
    setSelectedShotId(shotId);
    writeLocationState({ view, projectId: selectedProject, episodeId: selectedEpisode, shotId });
  }, [selectedEpisode, selectedProject, view]);
  useEffect(() => {
    const onPopState = () => {
      const location = readLocationState();
      setView(location.view);
      setSelectedProjectId(location.projectId);
      setSelectedEpisodeId(location.episodeId);
      setSelectedShotId(location.shotId);
      setSelectedReviewVersionId(location.reviewId);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  useEffect(() => {
    if (!projects.data || !selectedProject) return;
    const location = readLocationState();
    if (location.projectId === selectedProject && (!selectedEpisode || location.episodeId === selectedEpisode) && (!selectedShot || location.shotId === selectedShot)) return;
    setSelectedProjectId(selectedProject);
    if (selectedEpisode) setSelectedEpisodeId(selectedEpisode);
    if (selectedShot) setSelectedShotId(selectedShot);
    writeLocationState({ view, projectId: selectedProject, episodeId: selectedEpisode, shotId: selectedShot }, true);
  }, [projects.data, selectedEpisode, selectedProject, selectedShot, view]);
  useEffect(() => {
    if (view !== "jobs") return undefined;
    const source = new EventSource(`/api/v1/events?after_event_id=0&follow=true${selectedProject ? `&project_id=${encodeURIComponent(selectedProject)}` : ""}`);
    const refresh = () => { void queryClient.invalidateQueries({ queryKey: ["jobs", selectedProject] }); };
    ["JOB_QUEUED", "JOB_CLAIMED", "JOB_HEARTBEAT", "JOB_FINISHED", "JOB_RECONCILED", "JOB_REQUEUED", "JOB_CANCEL_REQUESTED", "ARTIFACT_REGISTERED"].forEach((eventName) => source.addEventListener(eventName, refresh));
    return () => { source.close(); };
  }, [queryClient, selectedProject, view]);

  const reload = () => {
    void live.refetch();
    void contract.refetch();
    void projects.refetch();
    void profiles.refetch();
    void diagnostics.refetch();
  };

  return (
    <main className="shell">
      <a className="skip-link" href="#workspace-content">跳到工作区内容</a>
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true"><StudioMarkIcon /></span>
          <div><p className="eyebrow">LOCAL PRODUCTION OS</p><h1>LocalDramaStudio</h1></div>
        </div>
        <div className="topbar-context">
          <div className="context-selectors">
            <label>项目<select aria-label="当前项目" value={selectedProject ?? ""} onChange={(event) => selectProject(event.target.value)} disabled={!projects.data?.items.length}><option value="">未选择项目</option>{projects.data?.items.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
            <label>分集<select aria-label="当前分集" value={selectedEpisode ?? ""} onChange={(event) => selectEpisode(event.target.value)} disabled={!episodes.data?.items.length}><option value="">未选择分集</option>{episodes.data?.items.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
          </div>
          <div className="mode-badge" aria-label="执行模式：本地-only"><StatusDotIcon /> LOCAL_ONLY</div>
        </div>
      </header>

      <div className="layout">
        <nav className="sidebar" aria-label="全局导航">
          <span className="nav-title">制片中心</span>
          <ViewButton label="概览" active={view === "overview"} onClick={() => navigate("overview")} />
          <ViewButton label="分集生产" active={view === "projects"} onClick={() => navigate("projects")} />
          <ViewButton label="AI 生成工作台" active={view === "generation"} onClick={() => navigate("generation")} />
          <ViewButton label="审核收件箱" active={view === "reviews"} onClick={() => navigate("reviews")} />
          <ViewButton label="业务画布" active={view === "canvas"} onClick={() => navigate("canvas")} />
          <span className="nav-title nav-section">资源与系统</span>
          <ViewButton label="模型与能力" active={view === "profiles"} onClick={() => navigate("profiles")} />
          <ViewButton label="任务与机器" active={view === "jobs"} onClick={() => navigate("jobs")} />
          <ViewButton label="诊断中心" active={view === "diagnostics"} onClick={() => navigate("diagnostics")} />
          <div className="sidebar-foot"><span>本地任务持续运行</span><small>关闭浏览器不会中断 Worker</small></div>
        </nav>

        <section className="content" id="workspace-content" aria-live="polite" tabIndex={-1}>
          <div className="hero">
            <div>
              <p className="eyebrow">{view === "generation" ? "CREATE / COMPARE / PROMOTE" : "PRODUCTION OVERVIEW"}</p>
              <h2>{view === "generation" ? "从镜头意图到可审核候选，一条清晰的生成路径。" : "回到最需要你决策的地方。"}</h2>
              <p className="muted">{view === "generation" ? "先选择生成方式和本地能力，再锁定输入、预检资源、生成候选；任何结果都不会覆盖历史。" : "项目进度、待审内容与本机任务使用真实数据；不可运行的能力会说明原因和下一步。"}</p>
            </div>
            <button className="secondary" onClick={reload} disabled={live.isFetching || projects.isFetching}>
              {live.isFetching || projects.isFetching ? "刷新中…" : "刷新数据"}
            </button>
          </div>

          {view === "overview" ? <div className="card-grid">
            <StatusCard label="API live" value={live.data?.status ?? (live.isPending ? "加载中…" : "不可用")} detail={live.error ? String(live.error) : "FastAPI /health/live"} />
            <StatusCard label="网络策略" value={contract.data?.mode ?? "读取中…"} detail={contract.data?.remote_provider ?? "仅允许本地 loopback"} />
            <StatusCard label="数据库" value={contract.data?.database_authority ?? "SQLite"} detail="SQLite WAL 是业务状态权威" />
            <StatusCard label="本机 Profile" value={profiles.data?.items.length === undefined ? "读取中…" : String(profiles.data.items.length)} detail="候选版本需显式发布" />
          </div> : <div className="system-strip" aria-label="本机系统状态"><span><i className={live.data?.status === "HEALTHY" ? "ok" : "warn"} /> API {live.data?.status ?? "读取中"}</span><span>网络 {contract.data?.mode ?? "读取中"}</span><span>SQLite WAL</span><button onClick={() => navigate("diagnostics")}>查看诊断</button></div>}

          <WorkspaceErrorPanel failures={queryFailures} />

          {(view === "overview" || view === "projects") && <div className="project-filters" role="search" aria-label="筛选项目"><label>搜索项目<input value={projectSearch} onChange={(event) => setProjectSearch(event.target.value)} placeholder="标题或 code" /></label><label>项目状态<select value={projectStatus} onChange={(event) => setProjectStatus(event.target.value)}><option value="">全部状态</option><option value="DRAFT">DRAFT</option><option value="ACTIVE">ACTIVE</option><option value="PAUSED">PAUSED</option><option value="ARCHIVED">ARCHIVED</option></select></label></div>}

          {(view === "overview" || view === "projects") && <GlobalSearchPanel projectId={selectedProject} />}
          {view === "projects" && selectedProject && <ProjectHealthPanel projectId={selectedProject} />}
          {view === "projects" && selectedProject && <ProjectAssetGrantPanel projectId={selectedProject} />}

          {(view === "overview" || view === "projects") && <ProjectCreateWizard profiles={profiles.data?.items ?? []} onCreated={(created) => { void queryClient.invalidateQueries({ queryKey: ["projects"] }); selectProject(created.id, "projects"); }} />}

          {view === "overview" && (
            <section className="panel">
              <div className="panel-heading"><div><p className="eyebrow">项目概览</p><h3>从真实项目继续工作</h3></div><span className="status-pill">{projects.data?.items.length ?? 0} 个项目</span></div>
              <ProjectList projects={projects.data?.items ?? []} selectedProjectId={selectedProject} onSelect={(id) => selectProject(id, "projects")} />
            </section>
          )}

          {view === "projects" && (
            <section className="panel">
              <div className="panel-heading"><div><p className="eyebrow">项目与生产台</p><h3 className="production-path"><span>项目</span><BreadcrumbSeparatorIcon /><span>季</span><BreadcrumbSeparatorIcon /><span>集</span><BreadcrumbSeparatorIcon /><span>镜头 read model</span></h3></div><span className="status-pill">单次生产查询</span></div>
              <ProjectList projects={projects.data?.items ?? []} selectedProjectId={selectedProject} onSelect={selectProject} />
              {selectedProjectRecord && <ProjectTemplateCopyAction project={selectedProjectRecord} onCopied={(copied) => { void queryClient.invalidateQueries({ queryKey: ["projects"] }); selectProject(copied.id, "projects"); }} />}
              {selectedProject && <ProjectPackageAction projectId={selectedProject} onImported={(projectId) => { void queryClient.invalidateQueries({ queryKey: ["projects"] }); selectProject(projectId, "projects"); }} />}
              {selectedProject && <div className="production-summary">
                <p className="eyebrow">当前集</p>
                <p className="muted">{seasons.data?.items[0]?.title ?? "季数据加载中…"} · {selectedEpisodeRecord?.title ?? "集数据加载中…"}</p>
                {production.isPending && <p className="empty-state">正在读取生产行…</p>}
                {production.data?.items.map((shot) => <div className="shot-row" key={String(shot.id)}><strong>{String(shot.code)}</strong><span>{String(shot.status)}</span><span className="blocker-text">{Array.isArray(shot.blockers) ? `${shot.blockers.length} 个阻塞` : "读取中"}</span><span>{String(shot.next_action)}</span></div>)}
                {production.data?.items.length === 0 && <p className="empty-state">当前集还没有镜头；请从真实 API 创建镜头。</p>}
                {selectedProject && selectedEpisode && <EpisodeSceneRanges projectId={selectedProject} episodeId={selectedEpisode} />}
                {selectedEpisode && <StoryboardBatchWorkbench episodeId={selectedEpisode} />}
                {selectedProject && <CreativeLibrary projectId={selectedProject} />}
                {selectedProject && <ScriptImportPanel projectId={selectedProject} />}
                {selectedProject && <AIDraftReviewPanel projectId={selectedProject} />}
                {selectedProject && selectedEpisode && <DialogueTTSPanel lines={dialogueLines.data?.items ?? []} voices={voiceProfiles.data?.items ?? []} profiles={profiles.data?.items ?? []} projectId={selectedProject} episodeId={selectedEpisode} onChanged={() => { void dialogueLines.refetch(); void voiceProfiles.refetch(); void profiles.refetch(); void queryClient.invalidateQueries({ queryKey: ["jobs"] }); }} />}
                {selectedProject && selectedEpisode && <AudioTrackPanel bindings={audioBindings.data?.items ?? []} projectId={selectedProject} episodeId={selectedEpisode} onBound={() => { void audioBindings.refetch(); void timelineStatus.refetch(); void g8Readiness.refetch(); }} />}
                {selectedEpisode && <SubtitleRevisionPanel episodeId={selectedEpisode} defaultSourceDocumentVersionId={String(timelineStatus.data?.status.subtitles.latest?.source_document_version_id ?? "")} onCreated={() => { void timelineStatus.refetch(); void g8Readiness.refetch(); }} />}
                {selectedEpisode && <TimelineRevisionPanel episodeId={selectedEpisode} onCreated={() => { void timelineStatus.refetch(); void g8Readiness.refetch(); }} />}
                <EpisodeContactSheetAction episodeId={selectedEpisode} />
                {timelineStatus.data?.status && <TimelineStatusPanel status={timelineStatus.data.status} />}
                {selectedEpisode && <EpisodeReviewPanel render={timelineStatus.data?.status.renders.latest ?? null} templates={reviewTemplates.data?.items ?? []} onChanged={() => { void timelineStatus.refetch(); void g8Readiness.refetch(); }} />}
                <TimelineExportAction timelineRevisionId={timelineStatus.data?.status.timeline.latest?.id ? String(timelineStatus.data.status.timeline.latest.id) : null} />
                {selectedEpisode && <DeliveryWorkflowPanel episodeId={selectedEpisode} timelineRevisionId={timelineStatus.data?.status.timeline.latest?.id ? String(timelineStatus.data.status.timeline.latest.id) : null} renderId={timelineStatus.data?.status.renders.latest?.id ? String(timelineStatus.data.status.renders.latest.id) : null} targetVersionId={projectConfiguration.data?.configuration.selected_delivery_target_version_id ? String(projectConfiguration.data.configuration.selected_delivery_target_version_id) : null} deliveryId={timelineStatus.data?.status.delivery.latest?.id ? String(timelineStatus.data.status.delivery.latest.id) : null} onChanged={() => { void timelineStatus.refetch(); void g8Readiness.refetch(); }} />}
                {g8Readiness.data?.readiness && <G8ReadinessPanel readiness={g8Readiness.data.readiness} />}
              </div>}
              {projectConfiguration.data?.configuration && <ProjectConfigurationSnapshot configuration={projectConfiguration.data.configuration} projectId={selectedProject ?? undefined} onChanged={() => { void projectConfiguration.refetch(); void timelineStatus.refetch(); }} />}
              {selectedProject && <BrandKitPanel projectId={selectedProject} />}
            </section>
          )}

          {view === "canvas" && <><ProductionCanvasPanel episodeId={selectedEpisode} selectedShotId={selectedShot} onSelectShot={selectShot} />{g9Readiness.data?.readiness && <G9ReadinessPanel readiness={g9Readiness.data.readiness} />}</>}

          {view === "reviews" && <>{selectedProject && <FormalSelectionPanel projectId={selectedProject} candidates={formalCandidates.data?.items ?? []} onChanged={() => { void formalCandidates.refetch(); void reviewItems.refetch(); }} />}<ImageCandidateGrid items={reviewItems.data?.items ?? []} selectedVersionId={selectedReviewVersion} onSelect={(id) => { setSelectedReviewVersionId(id); writeLocationState({ view: "reviews", projectId: selectedProject, episodeId: selectedEpisode, shotId: selectedShot, reviewId: id }, true); }} /><ReviewInboxPanel items={reviewItems.data?.items ?? []} templates={reviewTemplates.data?.items ?? []} selectedVersionId={selectedReviewVersion} context={reviewContext.data} onSelect={(id) => { setSelectedReviewVersionId(id); writeLocationState({ view: "reviews", projectId: selectedProject, episodeId: selectedEpisode, shotId: selectedShot, reviewId: id }, true); }} onPromote={(mediaVersionId, selectionType) => selectMutation.mutate({ mediaVersionId, selectionType })} selecting={selectMutation.isPending} onMachineCheck={(mediaVersionId) => machineCheckMutation.mutate(mediaVersionId)} machineChecking={machineCheckMutation.isPending} machineCheckError={machineCheckMutation.error ? String(machineCheckMutation.error) : null} onSubmit={(mediaVersionId, payload) => reviewMutation.mutate({ mediaVersionId, payload })} submitting={reviewMutation.isPending} submitError={reviewMutation.error ? String(reviewMutation.error) : null} submitSucceeded={reviewMutation.isSuccess} /></>}

          {view === "jobs" && <><JobsPanel jobs={jobs.data?.items ?? []} loading={jobs.isPending} onChanged={() => { void jobs.refetch(); void capacitySnapshot.refetch(); }} /><CapacitySnapshotPanel snapshot={capacitySnapshot.data?.snapshot} /></>}

          {view === "profiles" && <><ProfileConfigurationPanel profiles={profiles.data?.items ?? []} workflows={workflows.data?.items ?? []} workflowsLoading={workflows.isPending} onChanged={() => { void profiles.refetch(); void workflows.refetch(); }} />{projectConfiguration.data?.configuration && <ProjectConfigurationSnapshot configuration={projectConfiguration.data.configuration} projectId={selectedProject ?? undefined} onChanged={() => { void projectConfiguration.refetch(); }} />}{selectedProject && <BrandKitPanel projectId={selectedProject} />}{modelCompatibility.data?.compatibility && <ModelCompatibilityPanel snapshot={modelCompatibility.data.compatibility} projectId={selectedProject ?? undefined} onEvidenceImported={() => { void modelCompatibility.refetch(); }} />}</>}

          {view === "generation" && <><GenerationWorkbench projectId={selectedProject} profiles={profiles.data?.items ?? []} candidates={reviewItems.data?.items ?? []} h3={h3Runtime.data?.runtime} g6Readiness={g6Readiness.data?.readiness} i2vProbePlan={i2vProbePlan.data?.plan} shots={production.data?.items ?? []} selectedShotId={selectedShot} onSelectShot={selectShot} onOpenProfiles={() => navigate("profiles")} onSubmitted={() => { void jobs.refetch(); void production.refetch(); }} onOpenReviews={(mediaVersionId) => { void queryClient.invalidateQueries({ queryKey: ["reviews", "inbox"] }); void queryClient.invalidateQueries({ queryKey: ["gates", "g6"] }); void queryClient.invalidateQueries({ queryKey: ["gates", "g6", "i2v-probe-plan"] }); if (mediaVersionId) setSelectedReviewVersionId(mediaVersionId); setView("reviews"); writeLocationState({ view: "reviews", projectId: selectedProject, episodeId: selectedEpisode, shotId: selectedShot, reviewId: mediaVersionId ?? null }); }} /><WorkspaceAssetAuthorizationPanel projectId={selectedProject ?? ""} items={reviewItems.data?.items ?? []} onChanged={() => { void reviewItems.refetch(); void g6Readiness.refetch(); }} /><PostProcessPanel videos={(reviewItems.data?.items ?? []).filter((item) => item.media_kind === "VIDEO")} /><DirectorShotEditor shot={production.data?.items.find((item) => String(item.id) === selectedShot)} profiles={profiles.data?.items ?? []} onChanged={() => { void production.refetch(); void continuity.refetch(); }} /><PromptTemplatePanel projectId={selectedProject} shot={production.data?.items.find((item) => String(item.id) === selectedShot)} profiles={profiles.data?.items ?? []} /><ContinuityPanel context={continuity.data?.continuity} /></>}

          {view === "diagnostics" && <><section className="panel"><div className="panel-heading"><div><p className="eyebrow">诊断中心</p><h3>本机环境检查</h3></div><button className="secondary" onClick={() => diagnosticMutation.mutate()} disabled={diagnosticMutation.isPending}>{diagnosticMutation.isPending ? "检查中…" : "运行诊断"}</button></div><DiagnosticPanel run={diagnostics.data?.run ?? null} /></section><ComfyLabPanel /><AdapterContractsPanel registry={adapterContracts.data?.registry} /><OutboxDeliveryPanel projectId={selectedProject} /><AutomationPanel projectId={selectedProject} />{selectedProject && <AutomationWorkflowPanel projectId={selectedProject} />}<AuditHistoryPanel projectId={selectedProject} />{modelCompatibility.data?.compatibility && <ModelCompatibilityPanel snapshot={modelCompatibility.data.compatibility} projectId={selectedProject ?? undefined} onEvidenceImported={() => { void modelCompatibility.refetch(); }} />}</>}

          <p className="footer-note">{contract.data?.legacy_migration ?? "G11 legacy migration deferred"} · 业务状态来自真实本地后端；ComfyUI/本地 LLM 不可用时保持可解释阻塞。</p>
        </section>
      </div>
    </main>
  );
}

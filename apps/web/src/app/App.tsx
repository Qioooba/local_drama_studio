import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getAdapterContracts,
  getCapacitySnapshot,
  getProjectConfiguration,
  getModelCompatibility,
  getEpisodeProduction,
  getEpisodeTimelineStatus,
  getG6Readiness,
  getG8Readiness,
  getG9Readiness,
  planG6I2VProbe,
  getReviewContext,
  healthLive,
  h3CandidateRuntime,
  listReviewTemplates,
  latestDiagnostics,
  listEpisodes,
  listJobs,
  listProfiles,
  listProjects,
  listSeasons,
  listWorkflowVersions,
  runDiagnostics,
  reviewInbox,
  selectMediaVersion,
  systemContract,
  submitReview,
  type HealthCheck,
  type SystemContract,
  type TimelineStatus,
  type G8Readiness,
  type G9Readiness,
} from "../generated/api";
import { GenerationWorkbench } from "../features/generation/GenerationWorkbench";
import { CapacitySnapshotPanel, JobsPanel } from "../features/jobs/JobsPanel";
import { ReviewInboxPanel } from "../features/reviews/ReviewInboxPanel";
import { ProfileConfigurationPanel } from "../features/profiles/ProfileConfigurationPanel";
import { ProductionCanvasPanel } from "../features/canvas/ProductionCanvasPanel";

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
  return <button className={`nav-item${active ? " active" : ""}`} aria-current={active ? "page" : undefined} onClick={onClick}><span className="nav-label">{label}</span><svg aria-hidden="true" viewBox="0 0 16 16"><path d="m6 3 5 5-5 5" /></svg></button>;
}

export function App() {
  const initialLocation = useMemo(readLocationState, []);
  const [view, setView] = useState<View>(initialLocation.view);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(initialLocation.projectId);
  const [selectedEpisodeId, setSelectedEpisodeId] = useState<string | null>(initialLocation.episodeId);
  const [selectedShotId, setSelectedShotId] = useState<string | null>(initialLocation.shotId);
  const queryClient = useQueryClient();
  const live = useQuery<HealthCheck>({ queryKey: ["health", "live"], queryFn: () => healthLive() });
  const contract = useQuery<SystemContract>({ queryKey: ["system", "contract"], queryFn: () => systemContract() });
  const adapterContracts = useQuery({ queryKey: ["adapters", "contracts"], queryFn: () => getAdapterContracts(), enabled: view === "overview" || view === "diagnostics" });
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => listProjects() });
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: () => listProfiles(), enabled: view === "profiles" || view === "generation" || view === "overview" });
  const workflows = useQuery({ queryKey: ["workflow-versions"], queryFn: () => listWorkflowVersions(), enabled: view === "profiles" });
  const diagnostics = useQuery({ queryKey: ["diagnostics", "latest"], queryFn: () => latestDiagnostics(), enabled: view === "diagnostics" || view === "overview" });
  const h3Runtime = useQuery({ queryKey: ["h3", "candidate-runtime"], queryFn: () => h3CandidateRuntime(), enabled: view === "generation" || view === "overview" });
  const reviewTemplates = useQuery({ queryKey: ["reviews", "templates"], queryFn: () => listReviewTemplates(), enabled: view === "reviews" });
  const selectedProject = projects.data?.items.some((item) => item.id === selectedProjectId) ? selectedProjectId : projects.data?.items[0]?.id ?? null;
  const capacitySnapshot = useQuery({ queryKey: ["capacity", selectedProject], queryFn: () => getCapacitySnapshot(selectedProject ?? undefined), enabled: view === "overview" || view === "jobs" });
  const projectConfiguration = useQuery({ queryKey: ["project-configuration", selectedProject], queryFn: () => getProjectConfiguration(selectedProject as string), enabled: Boolean(selectedProject) && (view === "profiles" || view === "projects") });
  const modelCompatibility = useQuery({ queryKey: ["model-compatibility", selectedProject], queryFn: () => getModelCompatibility(selectedProject as string), enabled: Boolean(selectedProject) && (view === "profiles" || view === "diagnostics" || view === "overview") });
  const seasons = useQuery({ queryKey: ["project", selectedProject, "seasons"], queryFn: () => listSeasons(selectedProject as string), enabled: Boolean(selectedProject) });
  const selectedSeason = seasons.data?.items[0]?.id ?? null;
  const episodes = useQuery({ queryKey: ["season", selectedSeason, "episodes"], queryFn: () => listEpisodes(selectedSeason as string), enabled: Boolean(selectedSeason) });
  const selectedEpisode = episodes.data?.items.some((item) => item.id === selectedEpisodeId) ? selectedEpisodeId : episodes.data?.items[0]?.id ?? null;
  const production = useQuery({ queryKey: ["episode", selectedEpisode, "production"], queryFn: () => getEpisodeProduction(selectedEpisode as string), enabled: Boolean(selectedEpisode) });
  const timelineStatus = useQuery({ queryKey: ["episode", selectedEpisode, "timeline-status"], queryFn: () => getEpisodeTimelineStatus(selectedEpisode as string), enabled: Boolean(selectedEpisode) && view === "projects" });
  const g8Readiness = useQuery({ queryKey: ["gates", "g8", selectedProject, selectedEpisode], queryFn: () => getG8Readiness(selectedProject as string, selectedEpisode as string), enabled: Boolean(selectedProject && selectedEpisode) && view === "projects" });
  const g9Readiness = useQuery({ queryKey: ["gates", "g9", selectedProject, selectedEpisode], queryFn: () => getG9Readiness(selectedProject as string, selectedEpisode as string), enabled: Boolean(selectedProject && selectedEpisode) && view === "canvas" });
  const selectedShot = production.data?.items.some((item) => String(item.id) === selectedShotId) ? selectedShotId : production.data?.items[0] ? String(production.data.items[0].id) : null;
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
  if (view === "projects") activeQueries.push({ label: "季数据", query: seasons }, { label: "分集数据", query: episodes }, { label: "生产状态", query: production }, { label: "时间线状态", query: timelineStatus }, { label: "G8 门禁", query: g8Readiness }, { label: "项目配置", query: projectConfiguration });
  if (view === "canvas") activeQueries.push({ label: "季数据", query: seasons }, { label: "分集数据", query: episodes }, { label: "生产状态", query: production }, { label: "G9 门禁", query: g9Readiness });
  if (view === "reviews") activeQueries.push({ label: "审核收件箱", query: reviewItems }, { label: "审核模板", query: reviewTemplates }, { label: "审核上下文", query: reviewContext });
  if (view === "jobs") activeQueries.push({ label: "任务列表", query: jobs }, { label: "容量摘要", query: capacitySnapshot });
  if (view === "profiles") activeQueries.push({ label: "能力版本", query: profiles }, { label: "工作流版本", query: workflows }, { label: "项目配置", query: projectConfiguration }, { label: "模型证据", query: modelCompatibility });
  if (view === "generation") activeQueries.push({ label: "能力版本", query: profiles }, { label: "H3 本机状态", query: h3Runtime }, { label: "媒体候选", query: reviewItems }, { label: "生产上下文", query: production }, { label: "G6 门禁", query: g6Readiness }, { label: "I2V 探针计划", query: i2vProbePlan });
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
          <span className="brand-mark" aria-hidden="true">剧</span>
          <div><p className="eyebrow">LOCAL PRODUCTION OS</p><h1>LocalDramaStudio</h1></div>
        </div>
        <div className="topbar-context">
          <div className="context-selectors">
            <label>项目<select aria-label="当前项目" value={selectedProject ?? ""} onChange={(event) => selectProject(event.target.value)} disabled={!projects.data?.items.length}><option value="">未选择项目</option>{projects.data?.items.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
            <label>分集<select aria-label="当前分集" value={selectedEpisode ?? ""} onChange={(event) => selectEpisode(event.target.value)} disabled={!episodes.data?.items.length}><option value="">未选择分集</option>{episodes.data?.items.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
          </div>
          <div className="mode-badge" aria-label="执行模式：本地-only"><span aria-hidden="true">●</span> LOCAL_ONLY</div>
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

          {view === "overview" && (
            <section className="panel">
              <div className="panel-heading"><div><p className="eyebrow">项目概览</p><h3>从真实项目继续工作</h3></div><span className="status-pill">{projects.data?.items.length ?? 0} 个项目</span></div>
              <ProjectList projects={projects.data?.items ?? []} selectedProjectId={selectedProject} onSelect={(id) => selectProject(id, "projects")} />
            </section>
          )}

          {view === "projects" && (
            <section className="panel">
              <div className="panel-heading"><div><p className="eyebrow">项目与生产台</p><h3>项目 → 季 → 集 → 镜头 read model</h3></div><span className="status-pill">单次生产查询</span></div>
              <ProjectList projects={projects.data?.items ?? []} selectedProjectId={selectedProject} onSelect={selectProject} />
              {selectedProject && <div className="production-summary">
                <p className="eyebrow">当前集</p>
                <p className="muted">{seasons.data?.items[0]?.title ?? "季数据加载中…"} · {episodes.data?.items[0]?.title ?? "集数据加载中…"}</p>
                {production.isPending && <p className="empty-state">正在读取生产行…</p>}
                {production.data?.items.map((shot) => <div className="shot-row" key={String(shot.id)}><strong>{String(shot.code)}</strong><span>{String(shot.status)}</span><span className="blocker-text">{Array.isArray(shot.blockers) ? `${shot.blockers.length} 个阻塞` : "读取中"}</span><span>{String(shot.next_action)}</span></div>)}
                {production.data?.items.length === 0 && <p className="empty-state">当前集还没有镜头；请从真实 API 创建镜头。</p>}
                {timelineStatus.data?.status && <TimelineStatusPanel status={timelineStatus.data.status} loading={timelineStatus.isPending} />}
                {g8Readiness.data?.readiness && <G8ReadinessPanel readiness={g8Readiness.data.readiness} />}
              </div>}
              {projectConfiguration.data?.configuration && <ProjectConfigurationSnapshot configuration={projectConfiguration.data.configuration} />}
            </section>
          )}

          {view === "canvas" && <><ProductionCanvasPanel episodeId={selectedEpisode} selectedShotId={selectedShot} onSelectShot={selectShot} />{g9Readiness.data?.readiness && <G9ReadinessPanel readiness={g9Readiness.data.readiness} />}</>}

          {view === "reviews" && <ReviewInboxPanel items={reviewItems.data?.items ?? []} templates={reviewTemplates.data?.items ?? []} selectedVersionId={selectedReviewVersion} context={reviewContext.data} onSelect={(id) => { setSelectedReviewVersionId(id); writeLocationState({ view: "reviews", projectId: selectedProject, episodeId: selectedEpisode, shotId: selectedShot, reviewId: id }, true); }} onPromote={(mediaVersionId, selectionType) => selectMutation.mutate({ mediaVersionId, selectionType })} selecting={selectMutation.isPending} onSubmit={(mediaVersionId, payload) => reviewMutation.mutate({ mediaVersionId, payload })} submitting={reviewMutation.isPending} submitError={reviewMutation.error ? String(reviewMutation.error) : null} submitSucceeded={reviewMutation.isSuccess} />}

          {view === "jobs" && <><JobsPanel jobs={jobs.data?.items ?? []} loading={jobs.isPending} /><CapacitySnapshotPanel snapshot={capacitySnapshot.data?.snapshot} /></>}

          {view === "profiles" && <><ProfileConfigurationPanel profiles={profiles.data?.items ?? []} workflows={workflows.data?.items ?? []} workflowsLoading={workflows.isPending} onChanged={() => { void profiles.refetch(); }} />{projectConfiguration.data?.configuration && <ProjectConfigurationSnapshot configuration={projectConfiguration.data.configuration} />}{modelCompatibility.data?.compatibility && <ModelCompatibilityPanel snapshot={modelCompatibility.data.compatibility} />}</>}

          {view === "generation" && <GenerationWorkbench profiles={profiles.data?.items ?? []} videos={(reviewItems.data?.items ?? []).filter((item) => item.media_kind === "VIDEO")} h3={h3Runtime.data?.runtime} g6Readiness={g6Readiness.data?.readiness} i2vProbePlan={i2vProbePlan.data?.plan} shots={production.data?.items ?? []} selectedShotId={selectedShot} onSelectShot={selectShot} onOpenProfiles={() => navigate("profiles")} onOpenReviews={(mediaVersionId) => { void queryClient.invalidateQueries({ queryKey: ["reviews", "inbox"] }); void queryClient.invalidateQueries({ queryKey: ["gates", "g6"] }); void queryClient.invalidateQueries({ queryKey: ["gates", "g6", "i2v-probe-plan"] }); if (mediaVersionId) setSelectedReviewVersionId(mediaVersionId); setView("reviews"); writeLocationState({ view: "reviews", projectId: selectedProject, episodeId: selectedEpisode, shotId: selectedShot, reviewId: mediaVersionId ?? null }); }} />}

          {view === "diagnostics" && <><section className="panel"><div className="panel-heading"><div><p className="eyebrow">诊断中心</p><h3>本机环境检查</h3></div><button className="secondary" onClick={() => diagnosticMutation.mutate()} disabled={diagnosticMutation.isPending}>{diagnosticMutation.isPending ? "检查中…" : "运行诊断"}</button></div><DiagnosticPanel run={diagnostics.data?.run ?? null} /></section><AdapterContractsPanel registry={adapterContracts.data?.registry} />{modelCompatibility.data?.compatibility && <ModelCompatibilityPanel snapshot={modelCompatibility.data.compatibility} />}</>}

          <p className="footer-note">{contract.data?.legacy_migration ?? "G11 legacy migration deferred"} · 业务状态来自真实本地后端；ComfyUI/本地 LLM 不可用时保持可解释阻塞。</p>
        </section>
      </div>
    </main>
  );
}


function ProjectConfigurationSnapshot({ configuration }: { configuration: import("../generated/api").ProjectConfiguration }) {
  return <section className="panel configuration-snapshot" aria-labelledby="configuration-snapshot-title">
    <div className="panel-heading"><div><p className="eyebrow">G7 PROJECT CONFIGURATION</p><h3 id="configuration-snapshot-title">项目配置快照与切换影响</h3></div><span className="status-pill">只读 · LOCAL_ONLY</span></div>
    <p className="muted">当前绑定、版本和已冻结任务来自持久化状态。切换 Profile 不会改写历史 Job；交付目标必须显式选择，远程 transport 永不启用。</p>
    <div className="configuration-grid">
      <div className="configuration-card"><small>ProductionPlan</small><strong>{configuration.production_plan ? `${configuration.production_plan.code} · v${configuration.production_plan.version_no}` : "未绑定"}</strong><span>{configuration.production_plan?.status ?? "BLOCKED"}</span></div>
      <div className="configuration-card"><small>DeliveryTargetVersion</small><strong>{configuration.selected_delivery_target_version_id ? (configuration.delivery_targets.find((item) => item.version_id === configuration.selected_delivery_target_version_id)?.code ?? "已选择") : "需明确选择"}</strong><span>{configuration.impact.remote_transport_allowed ? "REMOTE 可用" : "REMOTE 已禁用"}</span></div>
      <div className="configuration-card"><small>Profile 矩阵</small><strong>{configuration.profile_bindings.length} 个绑定</strong><span>{configuration.profile_bindings.reduce((total, item) => total + item.frozen_job_count, 0)} 个历史 Job 快照</span></div>
    </div>
    <div className="configuration-table" role="table" aria-label="Profile 绑定矩阵">
      <div className="configuration-row configuration-header" role="row"><strong>能力</strong><strong>Profile 版本</strong><strong>状态</strong><strong>冻结 Job</strong></div>
      {configuration.profile_bindings.map((item) => <div className="configuration-row" role="row" key={`${item.capability}-${item.profile_version_id}`}><span>{item.capability}</span><span>{item.profile_code} · v{item.version_no}</span><span className="status-pill">{item.profile_status}</span><span>{item.frozen_job_count}</span></div>)}
      {configuration.profile_bindings.length === 0 && <p className="empty-state">尚未绑定 Profile。</p>}
    </div>
  </section>;
}

function AdapterContractsPanel({ registry }: { registry?: import("../generated/api").AdapterRegistry }) {
  return <section className="panel adapter-contracts" aria-labelledby="adapter-contracts-title">
    <div className="panel-heading"><div><p className="eyebrow">G7 ADAPTER SDK</p><h3 id="adapter-contracts-title">本地适配器契约</h3></div><span className="status-pill">静态检查 · 无运行时接触</span></div>
    <p className="muted">这里仅展示 transport 边界与能力声明；不会启动 ComfyUI、Ollama、CLI 或 FFmpeg，也不会打开网络连接。</p>
    <div className="configuration-table" role="table" aria-label="本地适配器契约">
      <div className="configuration-row configuration-header" role="row"><strong>适配器</strong><strong>Transport</strong><strong>状态</strong><strong>能力</strong></div>
      {(registry?.contracts ?? []).map((item) => <div className="configuration-row" role="row" key={item.code}><span>{item.title}</span><span>{item.transport}</span><span className="status-pill">{item.status}</span><span>{item.capabilities.slice(0, 3).join(" · ")}</span></div>)}
      {!registry && <p className="empty-state">正在读取本地契约…</p>}
    </div>
  </section>;
}

function ModelCompatibilityPanel({ snapshot }: { snapshot: import("../generated/api").ModelCompatibilitySnapshot }) {
  return <section className="panel configuration-snapshot" aria-labelledby="model-compatibility-title">
    <div className="panel-heading"><div><p className="eyebrow">G7 MODEL EVIDENCE</p><h3 id="model-compatibility-title">离线模型兼容性与许可证证据</h3></div><span className={`status-pill${snapshot.summary.pass_count === snapshot.summary.reported_count && snapshot.summary.missing_license_evidence_count === 0 ? "" : " neutral"}`}>{snapshot.summary.pass_count}/{snapshot.summary.reported_count} PASS</span></div>
    <div className="configuration-grid">
      <div className="configuration-card"><small>模型 Artifact</small><strong>{snapshot.summary.artifact_count}</strong><span>只读磁盘证据索引</span></div>
      <div className="configuration-card"><small>许可证证据</small><strong>{snapshot.summary.missing_license_evidence_count} 缺失</strong><span>仅接受项目 00_admin/licenses 内真实记录</span></div>
      <div className="configuration-card"><small>报告状态</small><strong>{snapshot.summary.blocked_count} BLOCKED</strong><span>不自动改变 G7 门禁</span></div>
    </div>
    <div className="configuration-table" role="table" aria-label="模型兼容性证据">
      <div className="configuration-row configuration-header" role="row"><strong>模型</strong><strong>Hash / 量化</strong><strong>许可证</strong><strong>状态</strong></div>
      {snapshot.reports.slice(0, 8).map((item) => <div className="configuration-row" role="row" key={item.artifact_id}><span>{item.code}<small>{item.kind}</small></span><span>{item.report_sha256 ? `${item.report_sha256.slice(0, 12)}…` : "未报告"} · {String(item.quantization.status ?? "UNKNOWN")}</span><span>{item.has_license_evidence ? item.license_path_rel : "缺失真实证据"}</span><span className={`status-pill${item.report_status === "PASS" ? "" : " neutral"}`}>{item.report_status ?? "未报告"}</span></div>)}
    </div>
    <p className="muted">只读 projection：runtime_contacted=false · network_contacted=false · mutated=false。模型许可证不可由插件 LICENSE、下载 URL 或推测替代。</p>
  </section>;
}


function TimelineStatusPanel({ status }: { status: TimelineStatus; loading: boolean }) {
  const latestTimeline = status.timeline.latest;
  const latestSubtitle = status.subtitles.latest;
  const latestRender = status.renders.latest;
  const latestDelivery = status.delivery.latest;
  return <section className="panel timeline-status-panel" aria-labelledby="timeline-status-title">
    <div className="panel-heading"><div><p className="eyebrow">G8 TIMELINE / DELIVERY</p><h3 id="timeline-status-title">时间线与交付状态</h3></div><span className="status-pill neutral">只读观测</span></div>
    <p className="muted">仅汇总当前集已持久化的 timeline、字幕、音频、渲染和交付记录；没有真实记录就明确显示为空，不会自动生成样片或交付包。</p>
    <div className="configuration-grid capacity-grid">
      <div className="configuration-card"><small>Timeline revision</small><strong>{status.timeline.revision_count}</strong><span>{latestTimeline ? `最新 v${String(latestTimeline.revision_no)}` : "暂无真实 revision"}</span></div>
      <div className="configuration-card"><small>字幕 / 音频</small><strong>{status.subtitles.revision_count} / {status.audio.binding_count}</strong><span>{latestSubtitle ? `${String(latestSubtitle.format)} · ${String(latestSubtitle.cue_count)} cues` : "暂无字幕；音频授权记录按实际汇总"}</span></div>
      <div className="configuration-card"><small>整集渲染</small><strong>{status.renders.count}</strong><span>{latestRender ? String(latestRender.status) : "暂无真实 render"}</span></div>
      <div className="configuration-card"><small>交付包</small><strong>{status.delivery.count}</strong><span>{latestDelivery ? String(latestDelivery.status) : "暂无真实 delivery"}</span></div>
    </div>
    <div className="canvas-status"><span>本地授权音频：{status.audio.verified_local_count}</span><span>已验证渲染：{status.renders.verified_count}</span><span>已验证交付：{status.delivery.verified_count}</span><span>runtime_contacted=false</span><span>network_contacted=false</span><span>mutated=false</span></div>
  </section>;
}

const g8CheckLabels: Record<string, string> = {
  THREE_REAL_SHOTS: "3+ 个真实镜头",
  DIALOGUE_ENVIRONMENT_SFX_MUSIC: "对白 / 环境 / SFX / 音乐",
  SUBTITLES: "字幕 revision",
  TIMELINE_INPUT_LOCKED: "时间线输入锁定",
  APPROVED_EPISODE_RENDER: "整集批准渲染",
  VERIFIED_DELIVERY: "交付包校验",
  TAMPER_DETECTION: "篡改检测",
};

function G8ReadinessPanel({ readiness }: { readiness: G8Readiness }) {
  return <section className="panel gate-readiness" aria-labelledby="g8-readiness-title">
    <div className="panel-heading"><div><p className="eyebrow">G8 FORMAL EXIT READINESS</p><h3 id="g8-readiness-title">整集音频、字幕、时间线与交付门禁</h3></div><span className={`status-pill${readiness.status === "PASS" ? "" : " neutral"}`}>{readiness.status}</span></div>
    <p className="muted">只读检查蓝图 09 的正式退出条件；不会创建素材、启动 ComfyUI 或自动替代整集人工批准。当前集：{readiness.episode.code} · {readiness.episode.title}</p>
    <ol className="gate-checks">{readiness.checks.map((check) => <li className={check.passed ? "passed" : "blocked"} key={check.code}><span aria-hidden="true">{check.passed ? "✓" : "○"}</span><strong>{g8CheckLabels[check.code] ?? check.code}</strong>{check.count !== undefined && <small>{check.count} 项真实证据</small>}<small>{check.detail}</small></li>)}</ol>
    {readiness.next_required_action && <p className="gate-next"><strong>下一项真实动作：</strong>{g8CheckLabels[readiness.next_required_action] ?? readiness.next_required_action}。系统保持阻塞，不以空记录或机器推测冒充 PASS。</p>}
    <div className="canvas-status"><span>timeline {readiness.evidence.timeline_revision_id ? "已锁定" : "缺失"}</span><span>render {readiness.evidence.render_id ? "已记录" : "缺失"}</span><span>delivery {readiness.evidence.delivery_id ? "已记录" : "缺失"}</span><span>runtime_contacted=false</span><span>network_contacted=false</span><span>mutated=false</span></div>
  </section>;
}

const g9CheckLabels: Record<string, string> = {
  LAZY_GRAPH_READ_MODEL: "懒加载画布 read model",
  LAYOUT_DEPENDENCY_ISOLATION: "布局与业务依赖隔离",
  PREFLIGHT_PERSISTENCE: "执行先行 preflight",
  VISIBLE_NODE_PERFORMANCE_UAT: "100—300 节点性能 UAT",
  ACCESSIBILITY_ROUTE_UAT: "三视图键盘 / 可访问性 UAT",
};

function G9ReadinessPanel({ readiness }: { readiness: G9Readiness }) {
  return <section className="panel gate-readiness" aria-labelledby="g9-readiness-title">
    <div className="panel-heading"><div><p className="eyebrow">G9 FORMAL EXIT READINESS</p><h3 id="g9-readiness-title">业务画布与生产效率门禁</h3></div><span className={`status-pill${readiness.status === "PASS" ? "" : " neutral"}`}>{readiness.status}</span></div>
    <p className="muted">只读区分生产图事实与规模 fixture 证据；不会创建镜头、布局、执行计划或 Job。当前集：{readiness.episode.code} · {readiness.episode.title}</p>
    <ol className="gate-checks">{readiness.checks.map((check) => <li className={check.passed ? "passed" : "blocked"} key={check.code}><span aria-hidden="true">{check.passed ? "✓" : "○"}</span><strong>{g9CheckLabels[check.code] ?? check.code}</strong>{check.count !== undefined && <small>{check.count} 项真实观测</small>}<small>{check.detail}</small></li>)}</ol>
    {readiness.next_required_action && <p className="gate-next"><strong>下一项真实动作：</strong>{g9CheckLabels[readiness.next_required_action] ?? readiness.next_required_action}。fixture 不会被当作生产退出证据。</p>}
    <div className="canvas-status"><span>生产镜头：{readiness.evidence.production_total_shots}</span><span>可见节点：{readiness.evidence.production_visible_nodes}</span><span>layout：{readiness.evidence.persisted_layout_count}</span><span>preflight：{readiness.evidence.persisted_preflight_count}</span><span>runtime_contacted=false</span><span>network_contacted=false</span><span>mutated=false</span></div>
  </section>;
}

function ProjectList({ projects, selectedProjectId, onSelect }: { projects: Array<{ id: string; code: string; title: string; status: string }>; selectedProjectId: string | null; onSelect: (id: string) => void }) {
  if (!projects.length) return <p className="empty-state">暂无项目。通过真实项目 API 创建后，项目会出现在这里。</p>;
  return <div className="project-list">{projects.map((project) => <button className={`project-row${selectedProjectId === project.id ? " selected" : ""}`} key={project.id} onClick={() => onSelect(project.id)}><span><strong>{project.title}</strong><small>{project.code}</small></span><span className="status-pill">{project.status}</span></button>)}</div>;
}

function DiagnosticPanel({ run }: { run: { status: string; checks: Array<{ code: string; status: string; observed: Record<string, unknown> }> } | null }) {
  if (!run) return <p className="empty-state">还没有诊断记录；点击“运行诊断”执行本机只读检查。</p>;
  return <div className="diagnostic-grid"><div className="diagnostic-status"><span>整体状态</span><strong>{run.status}</strong></div>{run.checks.map((check, index) => <div className="diagnostic-row" key={`${check.code}-${index}`}><span>{check.code}</span><strong>{check.status}</strong></div>)}</div>;
}

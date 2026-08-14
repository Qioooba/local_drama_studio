import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Background, Controls, MiniMap, ReactFlow, type Node, type NodeChange, applyNodeChanges } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  getAdapterContracts,
  getCapacitySnapshot,
  getProductionCanvas,
  getProjectConfiguration,
  getModelCompatibility,
  getProfileVersion,
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
  deriveProfileContractVersion,
  validateProfileContractVersion,
  publishProfileContractVersion,
  preflightProductionCanvasRun,
  runDiagnostics,
  reviewInbox,
  selectMediaVersion,
  saveProductionCanvasLayout,
  systemContract,
  submitReview,
  type HealthCheck,
  type CanvasGraph,
  type SystemContract,
  type Profile,
  type ProfileVersionDetail,
  type WorkflowVersionSummary,
  type TimelineStatus,
  type G8Readiness,
  type G9Readiness,
} from "../generated/api";
import { GenerationWorkbench } from "../features/generation/GenerationWorkbench";
import { CapacitySnapshotPanel, JobsPanel } from "../features/jobs/JobsPanel";
import { ReviewInboxPanel } from "../features/reviews/ReviewInboxPanel";

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

function parseObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error(`${label} 必须是有效 JSON。`); }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(`${label} 必须是 JSON 对象。`);
  return parsed as Record<string, unknown>;
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

function ProfileConfigurationPanel({ profiles, workflows, workflowsLoading, onChanged }: { profiles: Profile[]; workflows: WorkflowVersionSummary[]; workflowsLoading: boolean; onChanged: () => void }) {
  const [selectedId, setSelectedId] = useState<string | null>(profiles.find((item) => item.status === "PUBLISHED")?.version_id ?? profiles[0]?.version_id ?? null);
  const selected = profiles.find((item) => item.version_id === selectedId) ?? profiles[0] ?? null;
  const detail = useQuery({ queryKey: ["profile-version", selected?.version_id], queryFn: () => getProfileVersion(selected!.version_id), enabled: Boolean(selected) });
  const [draftId, setDraftId] = useState<string | null>(null);
  const activeDetail = useQuery({ queryKey: ["profile-version", draftId], queryFn: () => getProfileVersion(draftId as string), enabled: Boolean(draftId) });
  const current: ProfileVersionDetail | undefined = draftId ? activeDetail.data?.profile_version : detail.data?.profile_version;
  const [inputJson, setInputJson] = useState("{}");
  const [parameterJson, setParameterJson] = useState("{}");
  const [outputJson, setOutputJson] = useState("{}");
  const [resourceJson, setResourceJson] = useState("{}");
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  useEffect(() => {
    const item = current;
    if (!item) return;
    setInputJson(JSON.stringify(item.input_contract, null, 2));
    setParameterJson(JSON.stringify(item.parameter_schema, null, 2));
    setOutputJson(JSON.stringify(item.output_contract, null, 2));
    setResourceJson(JSON.stringify(item.resource_policy, null, 2));
  }, [current?.id]);
  const derive = useMutation({
    mutationFn: async () => {
      if (!current) throw new Error("Profile 版本尚未加载。");
      return deriveProfileContractVersion(current.id, { expected_source_revision: current.revision, input_contract: parseObject(inputJson, "输入契约"), parameter_schema: parseObject(parameterJson, "参数 Schema"), output_contract: parseObject(outputJson, "输出契约"), resource_policy: parseObject(resourceJson, "资源策略") });
    },
    onSuccess: (data) => { setDraftId(data.profile_version.id); setFeedback({ kind: "success", message: `已创建不可变 DRAFT v${data.profile_version.version_no}；原版本未覆盖。` }); onChanged(); },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });
  const validate = useMutation({
    mutationFn: () => validateProfileContractVersion(current!.id),
    onSuccess: (data) => { void activeDetail.refetch(); setFeedback({ kind: data.validation.status === "PASS" ? "success" : "error", message: data.validation.status === "PASS" ? "本地契约验证 PASS；未连接 runtime 或网络。" : "契约验证未通过，请查看字段和验证项。" }); },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });
  const publish = useMutation({
    mutationFn: () => publishProfileContractVersion(current!.id),
    onSuccess: (data) => { setFeedback({ kind: "success", message: `Profile v${data.profile_version.version_no} 已发布。` }); setDraftId(null); onChanged(); },
    onError: (error) => setFeedback({ kind: "error", message: `${String(error)} 执行指纹变化时必须转入真实媒体证据发布。` }),
  });
  const isDraft = current?.status === "DRAFT";
  return <section className="panel">
    <div className="panel-heading"><div><p className="eyebrow">G7 PROFILE CONFIGURATION</p><h3>本地能力契约与不可变版本</h3></div><span className="status-pill">LOCAL_ONLY</span></div>
    <p className="muted">编辑只会派生新 DRAFT；本地验证不会连接 ComfyUI。执行指纹有变化时，发布必须提供真实成功媒体证据。</p>
    <div className="profile-editor-layout">
      <aside className="profile-version-list" aria-label="Profile 版本">
        {profiles.map((profile) => <button key={profile.version_id} className={`profile-version-choice${selected?.version_id === profile.version_id ? " selected" : ""}`} onClick={() => { setSelectedId(profile.version_id); setDraftId(null); setFeedback(null); }}><span><strong>{profile.code}</strong><small>{profile.capability} · v{String(profile.version_no ?? "—")}</small></span><span className={`status-pill${profile.status === "PUBLISHED" ? "" : " neutral"}`}>{profile.status}</span></button>)}
      </aside>
      <div className="profile-contract-editor">
        {!current ? <p className="empty-state">正在读取 Profile 契约…</p> : <>
          <div className="profile-contract-meta"><span><small>版本</small><strong>v{current.version_no}</strong></span><span><small>能力</small><strong>{current.capability}</strong></span><span><small>状态</small><strong>{current.status}</strong></span><span><small>契约 hash</small><code>{current.contract_hash.slice(0, 12)}</code></span></div>
          <div className="profile-contract-fields">
            <label>输入契约<textarea value={inputJson} onChange={(event) => setInputJson(event.target.value)} spellCheck={false} /><small>声明 transport 与语义输入槽；只允许本地 transport。</small></label>
            <label>参数 Schema<textarea value={parameterJson} onChange={(event) => setParameterJson(event.target.value)} spellCheck={false} /><small>必须明确 seed 与 determinism。</small></label>
            <label>输出契约<textarea value={outputJson} onChange={(event) => setOutputJson(event.target.value)} spellCheck={false} /><small>必须声明 media_kind；容器、编码按能力补充。</small></label>
            <label>资源策略<textarea value={resourceJson} onChange={(event) => setResourceJson(event.target.value)} spellCheck={false} /><small>GPU heavy 并发必须为 1。</small></label>
          </div>
          {current.validation && <div className={`profile-validation ${current.validation.status === "PASS" ? "passed" : "failed"}`}><strong>最新验证：{current.validation.status}</strong><small>{current.validation.checks.filter((item) => item.passed).length}/{current.validation.checks.length} 项 · hash {current.validation.contract_hash.slice(0, 12)}</small></div>}
          {feedback && <p className={feedback.kind === "error" ? "inline-error" : "review-success"} role="status">{feedback.message}</p>}
          <div className="profile-editor-actions">
            {!isDraft ? <button className="primary-action" onClick={() => derive.mutate()} disabled={derive.isPending}>{derive.isPending ? "创建中…" : "保存为新 DRAFT"}</button> : <>
              <button className="secondary" onClick={() => validate.mutate()} disabled={validate.isPending}>{validate.isPending ? "验证中…" : "运行本地契约验证"}</button>
              <button className="primary-action" onClick={() => publish.mutate()} disabled={publish.isPending || current.validation?.status !== "PASS"}>{publish.isPending ? "发布中…" : "发布已验证版本"}</button>
            </>}
          </div>
          {isDraft && current.validation?.status !== "PASS" && <small className="action-help">发布保持禁用，直到当前 contract hash 获得 PASS 验证证明。</small>}
        </>}
      </div>
    </div>
    <div className="workflow-history-heading"><div><p className="eyebrow">WORKFLOW HISTORY</p><h3>工作流发布证据</h3></div><span className="status-pill neutral">只读 · 未连接 ComfyUI</span></div>
    {workflowsLoading ? <p className="empty-state">正在读取本地工作流版本…</p> : <div className="workflow-history">{workflows.map((workflow) => <article className="workflow-version" key={workflow.id}><div><strong>{workflow.code}</strong><small>v{workflow.version_no} · {String(workflow.contract.capability ?? "未声明 capability")}</small></div><span className={`status-pill${workflow.status === "PUBLISHED" ? "" : " neutral"}`}>{workflow.status}</span><code>{workflow.content_hash.slice(0, 12)}</code><small>{workflow.published_at ? `发布于 ${new Date(workflow.published_at).toLocaleString()}` : "尚未发布；验证、发布与回滚均需显式操作。"}</small></article>)}</div>}
  </section>;
}

type CanvasFocus = "ALL" | "UPSTREAM" | "DOWNSTREAM";

export function focusCanvasNodeIds(selectedNodeId: string | null, edges: Array<{ source: string; target: string }>, focus: CanvasFocus): Set<string> {
  if (!selectedNodeId || focus === "ALL") return new Set();
  const adjacency = new Map<string, string[]>();
  edges.forEach((edge) => {
    const key = focus === "UPSTREAM" ? edge.target : edge.source;
    const value = focus === "UPSTREAM" ? edge.source : edge.target;
    adjacency.set(key, [...(adjacency.get(key) ?? []), value]);
  });
  const included = new Set<string>([selectedNodeId]);
  const queue = [selectedNodeId];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const neighbor of adjacency.get(current) ?? []) {
      if (included.has(neighbor)) continue;
      included.add(neighbor);
      queue.push(neighbor);
    }
  }
  return included;
}

function ProductionCanvasPanel({ episodeId, selectedShotId, onSelectShot }: { episodeId: string | null; selectedShotId: string | null; onSelectShot: (shotId: string) => void }) {
  const graphQuery = useQuery({ queryKey: ["canvas", episodeId], queryFn: () => getProductionCanvas("EPISODE", episodeId as string, 0, 60), enabled: Boolean(episodeId) });
  const [nodes, setNodes] = useState<Node[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [focus, setFocus] = useState<CanvasFocus>("ALL");
  const [plan, setPlan] = useState<{ status: string; node_ids: string[]; blockers: Array<Record<string, unknown>>; estimate: Record<string, unknown> } | null>(null);
  const graph = graphQuery.data?.graph;
  useEffect(() => {
    if (!graph) return;
    setNodes(graph.nodes.map((item, index) => ({
      id: item.id,
      position: item.position ?? { x: (index % 5) * 230, y: Math.floor(index / 5) * 145 },
      data: { label: item.label, state: item.state, blockers: item.blockers, takeCount: item.take_count, variantCount: item.variant_count, variantLineage: item.variant_lineage, experimentProgress: item.experiment_progress, adjacentConstraints: item.adjacent_constraints },
      className: `canvas-node state-${item.state.toLowerCase()}`,
    })));
  }, [graph]);
  useEffect(() => {
    if (!selectedShotId || !graph) return;
    const directId = `shot:${selectedShotId}:direct`;
    if (graph.nodes.some((item) => item.id === directId)) setSelectedNodeId(directId);
  }, [graph, selectedShotId]);
  const allEdges = useMemo(() => graph?.edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, type: "smoothstep" as const, animated: edge.kind === "TRANSITION_CONSTRAINT", className: `canvas-edge kind-${edge.kind.toLowerCase()}` })) ?? [], [graph]);
  const visibleNodeIds = useMemo(() => {
    const query = search.trim().toLowerCase();
    const focused = focusCanvasNodeIds(selectedNodeId, allEdges, focus);
    return new Set(nodes.filter((node) => {
      const data = node.data as { label?: string; state?: string; blockers?: string[] };
      const matchesSearch = !query || [node.id, data.label, data.state, ...(data.blockers ?? [])].filter(Boolean).some((value) => String(value).toLowerCase().includes(query));
      return matchesSearch && (focus === "ALL" || focused.has(node.id));
    }).map((node) => node.id));
  }, [allEdges, focus, nodes, search, selectedNodeId]);
  const visibleNodes = useMemo(() => nodes.filter((node) => visibleNodeIds.has(node.id)), [nodes, visibleNodeIds]);
  const edges = useMemo(() => allEdges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)), [allEdges, visibleNodeIds]);
  const selectedGraphNode = graph?.nodes.find((item) => item.id === selectedNodeId) ?? null;
  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes((items) => applyNodeChanges(changes, items)), []);
  const saveMutation = useMutation({
    mutationFn: () => saveProductionCanvasLayout("EPISODE", episodeId as string, { expected_revision: graph?.layout.revision || undefined, positions: Object.fromEntries(nodes.map((node) => [node.id, node.position])), groups: graph?.layout.groups, viewport: graph?.layout.viewport }),
    onSuccess: () => { void graphQuery.refetch(); },
  });
  const planMutation = useMutation({
    mutationFn: () => preflightProductionCanvasRun("EPISODE", episodeId as string, { mode: "NODE", from_node_id: selectedNodeId as string, max_nodes: 20 }),
    onSuccess: (data) => setPlan(data.plan),
  });
  if (!episodeId) return <section className="panel"><p className="empty-state">请先选择一个包含集的项目。</p></section>;
  if (graphQuery.isPending) return <section className="panel"><p className="empty-state">正在懒加载业务 DAG…</p></section>;
  if (!graph) return <section className="panel"><div className="workspace-error" role="alert"><div><strong>业务画布读取失败</strong><p>{graphQuery.error?.message ?? String(graphQuery.error)}</p></div><button className="secondary" onClick={() => { void graphQuery.refetch(); }} disabled={graphQuery.isFetching}>{graphQuery.isFetching ? "重试中…" : "重试业务画布"}</button></div></section>;
  return <section className="panel canvas-panel">
    <div className="panel-heading"><div><p className="eyebrow">G9 PRODUCTION CANVAS</p><h3>业务依赖 DAG · 拖动只保存布局</h3></div><div className="canvas-actions"><button className="secondary" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>{saveMutation.isPending ? "保存中…" : "保存布局"}</button><button className="secondary" onClick={() => planMutation.mutate()} disabled={!selectedNodeId || planMutation.isPending}>{planMutation.isPending ? "预检中…" : "运行节点预检"}</button></div></div>
    <p className="muted">节点显示状态、take、variant 和阻塞；edges 来自后端业务依赖，布局接口无法修改它们。当前页 {graph.page.returned_shots}/{graph.page.total_shots} 个镜头，最多显示 {graph.invariants.max_visible_nodes} 个节点。</p>
    <div className="canvas-filterbar" aria-label="画布搜索与聚焦"><label>搜索节点<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="镜头、状态或阻塞" /></label><div className="canvas-focus-actions"><button className={focus === "ALL" ? "active" : ""} onClick={() => setFocus("ALL")}>全部</button><button className={focus === "UPSTREAM" ? "active" : ""} onClick={() => setFocus("UPSTREAM")} disabled={!selectedNodeId}>聚焦上游</button><button className={focus === "DOWNSTREAM" ? "active" : ""} onClick={() => setFocus("DOWNSTREAM")} disabled={!selectedNodeId}>聚焦下游</button></div></div>
    <div className="canvas-node-list" aria-label="键盘节点列表">{visibleNodes.map((node) => <button key={`keyboard-${node.id}`} className={selectedNodeId === node.id ? "selected" : ""} onClick={() => { setSelectedNodeId(node.id); const match = node.id.match(/^shot:([^:]+):/); if (match) onSelectShot(match[1]); }}>{String((node.data as { label?: string }).label ?? node.id)}</button>)}</div>
    <div className="canvas-workspace" aria-label="业务画布"><ReactFlow nodes={visibleNodes} edges={edges} onNodesChange={onNodesChange} onNodeClick={(_, node) => { setSelectedNodeId(node.id); const match = node.id.match(/^shot:([^:]+):/); if (match) onSelectShot(match[1]); }} fitView minZoom={0.15} maxZoom={1.8} nodesConnectable={false} deleteKeyCode={null}><Background /><Controls /><MiniMap pannable zoomable nodeColor={(node) => String(node.className).includes("blocked") ? "#df9d4b" : String(node.className).includes("running") ? "#7c91ff" : "#45d3b3"} /></ReactFlow></div>
    <div className="canvas-status"><span>选中：{selectedNodeId ?? "无"}</span><span>显示：{visibleNodes.length}/{nodes.length} 节点</span><span>布局 revision：{graph.layout.revision}</span><span>业务依赖可编辑：否</span>{plan && <strong>计划 {plan.status} · {plan.node_ids.length} 节点 · {plan.blockers.length} 阻塞</strong>}</div>
    {selectedGraphNode && <div className="canvas-node-detail" aria-label="节点谱系与边界约束"><span><strong>变体谱系</strong> {selectedGraphNode.variant_lineage.length} 个 · {selectedGraphNode.variant_lineage.map((item) => `v${item.variant_no} ${item.status}${item.is_stale ? " · stale" : ""}`).join(" / ") || "暂无真实变体"}</span><span><strong>实验进度</strong> {selectedGraphNode.experiment_progress.map((item) => `${item.title}: ${item.succeeded_count}/${item.expanded_count || item.cell_count} succeeded${item.failed_count ? ` · ${item.failed_count} failed` : ""}`).join(" / ") || "暂无真实实验"}</span><span><strong>相邻边界约束</strong> {selectedGraphNode.adjacent_constraints.map((item) => `${item.constraint_type} · ${item.compatibility_status} · ${item.enforcement}`).join(" / ") || "暂无真实边界约束"}</span></div>}
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

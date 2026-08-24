import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Skeleton, TabPanel, Tabs } from "../components/ui";
import { GenerationPreferencePanel } from "../features/preferences-v2/GenerationPreferencePanel";
import { ProviderConnectionsPanel } from "../features/model-config/ProviderConnectionsPanel";
import { LocalLLMConfigurationPanel } from "../features/profiles/LocalLLMConfigurationPanel";
import { ProfileConfigurationPanel } from "../features/profiles/ProfileConfigurationPanel";
import { AdapterContractsPanel, ModelCompatibilityPanel } from "../features/status/ReadinessPanels";
import { getAdapterContracts, getModelCompatibility, listProfiles, listProjects, listWorkflowVersions } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import "./system-workspaces.css";
import "./models-workspace.css";

type ModelView = "profile-contracts" | "workflows" | "preferences" | "compatibility" | "local-llm" | "connections";

const CREATOR_VIEWS: Array<{ id: ModelView; label: string }> = [
  { id: "preferences", label: "项目生成能力" },
  { id: "compatibility", label: "添加与检查模型" },
  { id: "local-llm", label: "故事拆解模型" },
  { id: "connections", label: "远端服务与密钥" },
];
const EXPERT_VIEWS: Array<{ id: ModelView; label: string; description: string }> = [
  { id: "profile-contracts", label: "Profile 契约", description: "编辑不可变输入、参数、输出与资源合同" },
  { id: "workflows", label: "Workflow 版本", description: "注册、验证、发布或撤销本机执行图" },
];
const MODEL_VIEWS = [...CREATOR_VIEWS, ...EXPERT_VIEWS];

function isModelView(value: string | null): value is ModelView {
  return MODEL_VIEWS.some((item) => item.id === value);
}

/** Models & capabilities: explicit engineering controls kept out of creator pages. */
export function ModelsPage() {
  const { projectId: routeProjectId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = routeProjectId || searchParams.get("project") || undefined;
  const requestedView = searchParams.get("view");
  const activeView: ModelView = isModelView(requestedView) ? requestedView : "preferences";
  const expertActive = EXPERT_VIEWS.some((item) => item.id === activeView);
  const [expertOpen, setExpertOpen] = useState(expertActive);

  const projects = useQuery({
    queryKey: queryKeys.projects.list({ limit: 200 }),
    queryFn: () => listProjects({ limit: 200 }),
  });

  useEffect(() => { if (expertActive) setExpertOpen(true); }, [expertActive]);

  const setActiveView = (view: string) => {
    if (!isModelView(view)) return;
    const next = new URLSearchParams(searchParams);
    next.set("view", view);
    setSearchParams(next, { replace: true });
  };
  const selectProject = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("project", value); else next.delete("project");
    setSearchParams(next, { replace: true });
  };

  const profiles = useQuery({
    queryKey: queryKeys.profiles.list(),
    queryFn: () => listProfiles(),
    enabled: activeView === "profile-contracts",
  });
  const workflows = useQuery({
    queryKey: queryKeys.workflows.versions(),
    queryFn: () => listWorkflowVersions(),
    enabled: activeView === "profile-contracts" || activeView === "workflows",
  });
  const adapterContracts = useQuery({
    queryKey: ["operations", "adapter-contracts"],
    queryFn: () => getAdapterContracts(),
    enabled: activeView === "compatibility",
  });
  const compatibility = useQuery({
    queryKey: ["operations", projectId, "model-compatibility"],
    queryFn: () => getModelCompatibility(projectId!),
    enabled: activeView === "compatibility" && Boolean(projectId),
  });

  return (
    <div className="v2-page models-page">
      <div className="panel-heading models-page-heading">
        <div>
          <p className="eyebrow">创作配置</p>
          <h2>模型与创作能力</h2>
        </div>
        <span className="status-pill neutral">按用途选择 · 系统固定版本</span>
      </div>
      <p className="muted models-page-summary">
        先告诉系统项目需要图像、视频、声音或故事拆解能力。系统会保存确切的已发布版本，工程契约只在专家工具中维护。
      </p>

      <section className="models-quickstart" aria-labelledby="models-quickstart-title">
        <div className="models-quickstart-heading">
          <div><p className="eyebrow">推荐路径</p><h3 id="models-quickstart-title">从项目要完成的创作任务开始</h3></div>
          <span className="status-pill neutral">无需先理解执行契约或工作流版本</span>
        </div>
        <ol>
          <li><span>1</span><div><strong>{projectId ? "当前项目已确定" : "先选择一个项目"}</strong><small>{projectId ? "所有选择只影响当前项目，不会修改其他作品。" : "请使用顶栏项目选择器；未选择时不会跨项目读取或保存。"}</small></div></li>
          <li><span>2</span><div><strong>选择项目需要的能力</strong><small>按文生图、图生视频、声音等创作用途选择，不要求填写技术标识。</small></div></li>
          <li><span>3</span><div><strong>需要时再添加模型</strong><small>从电脑选文件或配置故事拆解服务，兼容性与版本固定由系统处理。</small></div></li>
        </ol>
        {!routeProjectId && <label className="models-project-picker">当前项目<select value={projectId ?? ""} onChange={(event) => selectProject(event.target.value)} disabled={projects.isPending}><option value="">请选择项目</option>{(projects.data?.items ?? []).map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}</select><small>选择后，模型存证与能力偏好只作用于这个项目。</small></label>}
        {projects.error && <p className="inline-error" role="alert">项目列表读取失败：{String(projects.error)}</p>}
      </section>

      {!expertActive ? <div className="system-workspace-tabs models-task-tabs">
        <Tabs items={CREATOR_VIEWS} selectedId={activeView} onChange={setActiveView} ariaLabel="创作能力配置" />
      </div> : <div className="models-back-to-creator"><button type="button" className="secondary" onClick={() => setActiveView(projectId ? "preferences" : "compatibility")}>返回创作能力配置</button></div>}

      <details className="models-expert-tools" open={expertOpen} onToggle={(event) => setExpertOpen(event.currentTarget.open)}>
        <summary><span>专家工具：执行契约与工作流版本</span><small>仅供需要接入新运行时、维护合同或发布工作流的技术人员</small></summary>
        <nav className="models-expert-options" aria-label="专家模型工具">
          {EXPERT_VIEWS.map((item) => <button key={item.id} type="button" className={activeView === item.id ? "selected" : ""} aria-current={activeView === item.id ? "page" : undefined} onClick={() => setActiveView(item.id)}><strong>{item.label}</strong><span>{item.description}</span></button>)}
        </nav>
      </details>

      <TabPanel id="profile-contracts" selectedId={activeView}>
        {profiles.isPending ? (
          <Skeleton label="正在读取 Profile 契约" lines={5} />
        ) : profiles.error ? (
          <ErrorState description={`Profile 契约读取失败：${String(profiles.error)}`} onRetry={() => void profiles.refetch()} />
        ) : (
          <ProfileConfigurationPanel
            mode="profile-contracts"
            profiles={profiles.data?.items ?? []}
            workflows={workflows.data?.items ?? []}
            projectId={projectId}
            onChanged={() => void profiles.refetch()}
          />
        )}
      </TabPanel>

      <TabPanel id="workflows" selectedId={activeView}>
        {workflows.isPending ? (
          <Skeleton label="正在读取 Workflow 版本" lines={5} />
        ) : workflows.error ? (
          <ErrorState description={`Workflow 版本读取失败：${String(workflows.error)}`} onRetry={() => void workflows.refetch()} />
        ) : (
          <ProfileConfigurationPanel
            mode="workflows"
            workflows={workflows.data?.items ?? []}
            workflowsLoading={workflows.isFetching}
            onChanged={() => void workflows.refetch()}
          />
        )}
      </TabPanel>

      <TabPanel id="local-llm" selectedId={activeView}>
        <LocalLLMConfigurationPanel projectId={projectId} onChanged={() => void profiles.refetch()} />
      </TabPanel>

      <TabPanel id="connections" selectedId={activeView}>
        <ProviderConnectionsPanel />
      </TabPanel>

      <TabPanel id="preferences" selectedId={activeView}>
        <GenerationPreferencePanel initialProjectId={projectId} />
      </TabPanel>

      <TabPanel id="compatibility" selectedId={activeView}>
        <section className="v2-section-grid models-compatibility-task" aria-label="兼容性与适配器契约">
          {adapterContracts.isPending ? (
            <Skeleton label="正在读取本地适配器契约" lines={4} />
          ) : adapterContracts.error ? (
            <ErrorState
              description={`适配器契约读取失败：${String(adapterContracts.error)}`}
              onRetry={() => void adapterContracts.refetch()}
            />
          ) : (
            <AdapterContractsPanel registry={adapterContracts.data?.registry} />
          )}

          {!projectId ? (
            <section className="panel models-project-required" aria-labelledby="models-project-required-title">
              <h3 id="models-project-required-title">选择项目以检查模型存证</h3>
              <p className="muted">请使用上方“当前项目”下拉框选择作品；未选择时不会猜测或跨项目读取。</p>
            </section>
          ) : compatibility.isPending ? (
            <Skeleton label="正在读取项目模型兼容性" lines={5} />
          ) : compatibility.error ? (
            <ErrorState
              description={`项目模型兼容性读取失败：${String(compatibility.error)}`}
              onRetry={() => void compatibility.refetch()}
            />
          ) : compatibility.data?.compatibility ? (
            <ModelCompatibilityPanel
              snapshot={compatibility.data.compatibility}
              projectId={projectId}
              onEvidenceImported={() => void compatibility.refetch()}
            />
          ) : (
            <section className="panel models-project-required" aria-label="模型兼容性为空">
              <p className="muted">该项目尚无兼容性快照；系统不会以空记录冒充已验证。</p>
            </section>
          )}
        </section>
      </TabPanel>
    </div>
  );
}

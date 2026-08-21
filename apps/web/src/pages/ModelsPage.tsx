import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Skeleton, TabPanel, Tabs } from "../components/ui";
import { GenerationPreferencePanel } from "../features/preferences-v2/GenerationPreferencePanel";
import { ProfileConfigurationPanel } from "../features/profiles/ProfileConfigurationPanel";
import { AdapterContractsPanel, ModelCompatibilityPanel } from "../features/status/ReadinessPanels";
import { getAdapterContracts, getModelCompatibility, listProfiles, listWorkflowVersions } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import "./system-workspaces.css";
import "./models-workspace.css";

type ModelView = "profile-contracts" | "workflows" | "preferences" | "compatibility";

const MODEL_VIEWS: Array<{ id: ModelView; label: string }> = [
  { id: "profile-contracts", label: "Profile 契约" },
  { id: "workflows", label: "Workflow 版本" },
  { id: "preferences", label: "生成偏好" },
  { id: "compatibility", label: "兼容性与存证" },
];

function isModelView(value: string | null): value is ModelView {
  return MODEL_VIEWS.some((item) => item.id === value);
}

/** Models & capabilities: explicit engineering controls kept out of creator pages. */
export function ModelsPage() {
  const { projectId: routeProjectId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = routeProjectId || searchParams.get("project") || undefined;
  const requestedView = searchParams.get("view");
  const activeView: ModelView = isModelView(requestedView) ? requestedView : "profile-contracts";

  const setActiveView = (view: string) => {
    if (!isModelView(view)) return;
    const next = new URLSearchParams(searchParams);
    next.set("view", view);
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
    enabled: activeView === "workflows",
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
          <p className="eyebrow">系统区</p>
          <h2>模型与能力管理</h2>
        </div>
        <span className="status-pill neutral">显式版本 · 禁止静默回退</span>
      </div>
      <p className="muted models-page-summary">
        Profile 定义能力与输入输出合同，Workflow Version 定义本机可执行图；项目、分集和镜头只保存显式偏好。已冻结的 Variant 永远保留当时解析到的确切 Profile/Workflow 版本。
      </p>

      <div className="system-workspace-tabs models-task-tabs">
        <Tabs items={MODEL_VIEWS} selectedId={activeView} onChange={setActiveView} ariaLabel="模型管理任务" />
      </div>

      <TabPanel id="profile-contracts" selectedId={activeView}>
        {profiles.isPending ? (
          <Skeleton label="正在读取 Profile 契约" lines={5} />
        ) : profiles.error ? (
          <ErrorState description={`Profile 契约读取失败：${String(profiles.error)}`} onRetry={() => void profiles.refetch()} />
        ) : (
          <ProfileConfigurationPanel
            mode="profile-contracts"
            profiles={profiles.data?.items ?? []}
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

      <TabPanel id="preferences" selectedId={activeView}>
        <GenerationPreferencePanel />
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
              <p className="muted">在 URL 中指定项目后，才会读取该项目范围内的模型许可证存证与离线兼容性；未指定时不会猜测或跨项目读取。</p>
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

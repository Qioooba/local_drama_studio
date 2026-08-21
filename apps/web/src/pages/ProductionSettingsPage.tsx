import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ProductionSettingsOverview } from "../features/production-settings-v2/ProductionSettingsOverview";
import { FreshnessPanel } from "../features/freshness/FreshnessPanel";
import { TabPanel, Tabs } from "../components/ui";
import { ProjectAssetGrantPanel } from "../features/projects/ProjectAssetGrantPanel";
import { ProjectPackageAction } from "../features/projects/ProjectPackageAction";
import { ProjectTemplateCopyAction } from "../features/projects/ProjectTemplateCopyAction";
import { AutomationPanel } from "../features/shared/AutomationPanel";
import { AutomationWorkflowPanel } from "../features/shared/AutomationWorkflowPanel";
import { BrandKitPanel } from "../features/shared/BrandKitPanel";
import { OutboxDeliveryPanel } from "../features/shared/OutboxDeliveryPanel";
import { ProjectHealthPanel } from "../features/shared/ProjectHealthPanel";
import { WorkspaceAssetAuthorizationPanel } from "../features/shared/WorkspaceAssetAuthorizationPanel";
import { G9ReadinessPanel, ProjectConfigurationSnapshot } from "../features/status/ReadinessPanels";
import { getG9Readiness, getProjectConfiguration, listProjects, listWorkspaceAssetAuthorizations, reviewInbox } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import "./system-workspaces.css";

const SETTINGS_TABS = [
  { id: "overview", label: "生效概览" },
  { id: "freshness", label: "时效与失效" },
  { id: "delivery", label: "交付目标与品牌" },
  { id: "automation", label: "自动化与外发" },
  { id: "assets", label: "授权与项目包" },
];

export function ProductionSettingsPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedView = searchParams.get("view");
  const activeTab = SETTINGS_TABS.some((item) => item.id === requestedView) ? requestedView! : "overview";
  const setActiveTab = (view: string) => {
    const next = new URLSearchParams(searchParams);
    if (view === "overview") next.delete("view"); else next.set("view", view);
    setSearchParams(next, { replace: true });
  };

  const project = useQuery({
    queryKey: queryKeys.projects.detail(projectId),
    queryFn: async () => (await listProjects({ limit: 100 })).items.find((item) => item.id === projectId) ?? null,
    enabled: Boolean(projectId),
  });
  const inbox = useQuery({
    queryKey: ["operations", projectId, "review-inbox"],
    queryFn: () => reviewInbox(projectId),
    enabled: Boolean(projectId),
  });
  const authorizations = useQuery({
    queryKey: ["operations", projectId, "authorizations"],
    queryFn: () => listWorkspaceAssetAuthorizations(projectId),
    enabled: Boolean(projectId),
  });
  const configuration = useQuery({
    queryKey: ["operations", projectId, "configuration"],
    queryFn: () => getProjectConfiguration(projectId),
    enabled: Boolean(projectId),
  });
  const g9Readiness = useQuery({
    queryKey: ["operations", projectId, "g9-readiness"],
    queryFn: () => getG9Readiness(projectId),
    enabled: Boolean(projectId),
  });

  return (
    <div className="v2-page production-settings-page">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Project Settings</p>
          <h2>生产设置与项目配置</h2>
        </div>
        <span className="status-pill">项目级事实真值</span>
      </div>
      <p className="muted">
        集中管理项目生效默认、覆盖来源、交付规格、自动化与资产授权；具体配置由各自版本化模块管理，不生成第二套业务事实。
      </p>

      <div className="system-workspace-tabs">
        <Tabs items={SETTINGS_TABS} selectedId={activeTab} onChange={setActiveTab} ariaLabel="生产设置任务" />
      </div>

      <TabPanel id="overview" selectedId={activeTab}>
        <ProductionSettingsOverview projectId={projectId} />
      </TabPanel>

      <TabPanel id="freshness" selectedId={activeTab}>
        <FreshnessPanel projectId={projectId} scopeType="PROJECT" scopeId={projectId} />
      </TabPanel>

      <TabPanel id="delivery" selectedId={activeTab}>
        <section className="v2-section-grid" aria-label="交付目标与品牌">
          {configuration.data?.configuration && (
            <ProjectConfigurationSnapshot
              configuration={configuration.data.configuration}
              projectId={projectId}
              onChanged={() => void configuration.refetch()}
            />
          )}
          <BrandKitPanel projectId={projectId} />
          <ProjectHealthPanel projectId={projectId} />
          {g9Readiness.data?.readiness && <G9ReadinessPanel readiness={g9Readiness.data.readiness} />}
        </section>
      </TabPanel>

      <TabPanel id="automation" selectedId={activeTab}>
        <section className="v2-section-grid" aria-label="自动化与外发">
          <AutomationPanel projectId={projectId} />
          <AutomationWorkflowPanel projectId={projectId} />
          <OutboxDeliveryPanel projectId={projectId} />
        </section>
      </TabPanel>

      <TabPanel id="assets" selectedId={activeTab}>
        <section className="v2-section-grid" aria-label="授权与项目包">
          <WorkspaceAssetAuthorizationPanel
            projectId={projectId}
            items={inbox.data?.items ?? []}
            authorizations={authorizations.data?.items ?? []}
            onChanged={() => {
              void authorizations.refetch();
              void inbox.refetch();
            }}
          />
          <ProjectAssetGrantPanel projectId={projectId} />
          <ProjectPackageAction projectId={projectId} onImported={(importedId) => navigate(`/projects/${importedId}`)} />
          {project.data && (
            <ProjectTemplateCopyAction
              project={project.data}
              onCopied={(copied) => {
                void queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
                navigate(`/projects/${copied.id}`);
              }}
            />
          )}
        </section>
      </TabPanel>
    </div>
  );
}

import { useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ProductionSettingsOverview } from "../features/production-settings-v2/ProductionSettingsOverview";
import { MediaDerivativeMaintenancePanel } from "../features/production-settings-v2/MediaDerivativeMaintenancePanel";
import { ProjectAssetGrantPanel } from "../features/projects/ProjectAssetGrantPanel";
import { ProjectPackageAction } from "../features/projects/ProjectPackageAction";
import { ProjectTemplateCopyAction } from "../features/projects/ProjectTemplateCopyAction";
import { AutomationPanel } from "../features/shared/AutomationPanel";
import { AutomationWorkflowPanel } from "../features/shared/AutomationWorkflowPanel";
import { BrandKitPanel } from "../features/shared/BrandKitPanel";
import { OutboxDeliveryPanel } from "../features/shared/OutboxDeliveryPanel";
import { WorkspaceAssetAuthorizationPanel } from "../features/shared/WorkspaceAssetAuthorizationPanel";
import { ProjectConfigurationSnapshot } from "../features/status/ReadinessPanels";
import { getProjectConfiguration, listProjects, listWorkspaceAssetAuthorizations, reviewInbox } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import "./system-workspaces.css";

type SettingsSection = "production" | "delivery" | "automation" | "rights" | "data";

const SECTION_COPY: Record<SettingsSection, { eyebrow: string; title: string; description: string }> = {
  production: { eyebrow: "制作默认值", title: "生产规格", description: "设置当前项目采用的制作规格和默认生产策略。运行状态与故障处理不在这里出现。" },
  delivery: { eyebrow: "输出约束", title: "交付与品牌", description: "定义品牌、母版规格和项目交付约束；实际渲染、验收和打包在分集交付页完成。" },
  automation: { eyebrow: "项目策略", title: "自动化", description: "配置自动推进规则。脚本、Webhook 和事件投递仅在专家区域按需展开。" },
  rights: { eyebrow: "许可边界", title: "权利与授权", description: "管理工作区素材对当前项目的使用授权，以及项目资产的许可证明。" },
  data: { eyebrow: "可移植性", title: "数据与维护", description: "导入、导出、复制和修复项目数据。此处不包含创作流程入口。" },
};

function currentSection(pathname: string): SettingsSection {
  const value = pathname.split("/").filter(Boolean).at(-1);
  return value === "delivery" || value === "automation" || value === "rights" || value === "data" ? value : "production";
}

export function ProductionSettingsPage() {
  const { projectId = "" } = useParams();
  const section = currentSection(useLocation().pathname);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [integrationToolsOpen, setIntegrationToolsOpen] = useState(false);
  const needsRights = section === "rights";
  const needsDelivery = section === "delivery";
  const needsData = section === "data";

  const project = useQuery({ queryKey: queryKeys.projects.detail(projectId), queryFn: async () => (await listProjects({ limit: 100 })).items.find((item) => item.id === projectId) ?? null, enabled: Boolean(projectId) && needsData });
  const inbox = useQuery({ queryKey: ["operations", projectId, "review-inbox"], queryFn: () => reviewInbox(projectId), enabled: Boolean(projectId) && needsRights });
  const authorizations = useQuery({ queryKey: ["operations", projectId, "authorizations"], queryFn: () => listWorkspaceAssetAuthorizations(projectId), enabled: Boolean(projectId) && needsRights });
  const configuration = useQuery({ queryKey: ["operations", projectId, "configuration"], queryFn: () => getProjectConfiguration(projectId), enabled: Boolean(projectId) && needsDelivery });

  const copy = SECTION_COPY[section];
  return <div className="v2-page production-settings-page settings-section-page">
    <div className="panel-heading"><div><p className="eyebrow">{copy.eyebrow}</p><h3>{copy.title}</h3></div><span className="status-pill neutral">仅影响当前项目</span></div>
    <p className="muted">{copy.description}</p>

    {section === "production" && <ProductionSettingsOverview projectId={projectId} />}

    {section === "delivery" && <section className="v2-section-grid" aria-label="交付与品牌">
      {configuration.data?.configuration && <ProjectConfigurationSnapshot configuration={configuration.data.configuration} projectId={projectId} onChanged={() => void configuration.refetch()} />}
      <BrandKitPanel projectId={projectId} />
    </section>}

    {section === "automation" && <section className="v2-section-grid" aria-label="自动化">
      <AutomationWorkflowPanel projectId={projectId} />
      <details className="automation-integration-tools" open={integrationToolsOpen} onToggle={(event) => setIntegrationToolsOpen(event.currentTarget.open)}>
        <summary><span>专家：本机脚本接口与回调</span><small>只有接入外部脚本、Webhook 或调试事件投递时才需要</small></summary>
        {integrationToolsOpen && <div className="automation-integration-body"><AutomationPanel projectId={projectId} /><OutboxDeliveryPanel projectId={projectId} /></div>}
      </details>
    </section>}

    {section === "rights" && <section className="v2-section-grid" aria-label="权利与授权">
      <WorkspaceAssetAuthorizationPanel projectId={projectId} items={inbox.data?.items ?? []} authorizations={authorizations.data?.items ?? []} onChanged={() => { void authorizations.refetch(); void inbox.refetch(); }} />
      <ProjectAssetGrantPanel projectId={projectId} />
    </section>}

    {section === "data" && <section className="v2-section-grid" aria-label="数据与维护">
      <MediaDerivativeMaintenancePanel projectId={projectId} />
      <ProjectPackageAction projectId={projectId} onImported={(importedId) => navigate(`/projects/${importedId}`)} />
      {project.data && <ProjectTemplateCopyAction project={project.data} onCopied={(copied) => { void queryClient.invalidateQueries({ queryKey: queryKeys.projects.all }); navigate(`/projects/${copied.id}`); }} />}
    </section>}
  </div>;
}

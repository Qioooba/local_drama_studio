import { useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ConceptGuide, Dialog, ErrorState, Skeleton, TabPanel, Tabs } from "../components/ui";
import { ProviderConnectionsPanel } from "../features/model-config/ProviderConnectionsPanel";
import { canonicalCapabilityLabel, creatorProfileTitle } from "../features/preferences-v2/canonicalCapabilities";
import { LocalLLMConfigurationPanel } from "../features/profiles/LocalLLMConfigurationPanel";
import { ProfileConfigurationPanel } from "../features/profiles/ProfileConfigurationPanel";
import { ModelCompatibilityPanel } from "../features/status/ReadinessPanels";
import { getGlobalModelRegistry, listProfiles, listWorkflowVersions, type Profile } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import "./system-workspaces.css";
import "./models-workspace.css";

type ModelView = "catalog" | "resources";

const CREATOR_VIEWS: Array<{ id: ModelView; label: string }> = [
  { id: "catalog", label: "能力目录" },
  { id: "resources", label: "模型与服务" },
];
function resolveView(value: string | null): ModelView {
  if (value === "resources" || value === "local-llm" || value === "connections") return "resources";
  return "catalog";
}

function capabilityFamily(capability: string) {
  if (capability.startsWith("LLM_")) return "故事与文本";
  if (capability.startsWith("IMAGE_")) return "图像";
  if (capability.startsWith("VIDEO_")) return "视频";
  if (["TTS", "VOICE_CLONE", "LIPSYNC", "AUDIO_SFX", "AUDIO_MUSIC"].includes(capability)) return "声音";
  return "处理与质检";
}

function CapabilityCatalog({ profiles }: { profiles: Profile[] }) {
  const published = profiles.filter((item) => item.status === "PUBLISHED");
  const groups = useMemo(() => {
    const result = new Map<string, Profile[]>();
    for (const profile of published) {
      const family = capabilityFamily(profile.capability);
      result.set(family, [...(result.get(family) ?? []), profile]);
    }
    return result;
  }, [published]);

  return <section className="panel models-capability-catalog" aria-labelledby="capability-catalog-title">
    <div className="panel-heading">
      <div><p className="eyebrow">全局发布目录</p><h3 id="capability-catalog-title">可供所有项目使用的能力</h3></div>
      <span className="status-pill">{published.length} 个已发布版本</span>
    </div>
    <p className="muted">能力按创作用途组织。同一个模型服务可以发布为多个能力，故事拆解只是“故事与文本”中的一种用途。</p>
    {published.length === 0 ? <div className="models-catalog-empty"><strong>还没有已发布能力</strong><span>前往“模型与服务”添加资源，再由专家工具发布执行版本。</span></div> : <div className="models-capability-groups">
      {[...groups.entries()].map(([family, items]) => <section key={family} className="models-capability-group" aria-label={family}>
        <div className="models-capability-group-heading"><h4>{family}</h4><span>{items.length}</span></div>
        <div className="models-capability-list">
          {items.map((item) => <article key={item.version_id} className="models-capability-item">
            <div><strong>{canonicalCapabilityLabel(item.capability)}</strong><small>{creatorProfileTitle(item.title)}</small></div>
            <span className="status-pill">v{item.version_no ?? "?"} · 已发布</span>
          </article>)}
        </div>
      </section>)}
    </div>}
  </section>;
}

/** Global capability and model center. Project pages only choose preferences from this catalog. */
export function ModelsPage() {
  const { projectId: legacyEvidenceProjectId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeView = resolveView(searchParams.get("view"));
  const expertOpen = searchParams.get("view") === "profile-contracts";
  const [expertReturnView, setExpertReturnView] = useState<ModelView>(activeView);
  const [expertDirty, setExpertDirty] = useState(false);

  const setActiveView = (view: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("view", resolveView(view));
    next.delete("project");
    setSearchParams(next, { replace: true });
  };

  const openExpert = () => {
    setExpertReturnView(activeView);
    const next = new URLSearchParams(searchParams);
    next.set("view", "profile-contracts");
    next.delete("project");
    setSearchParams(next, { replace: true });
  };

  const closeExpert = () => {
    const next = new URLSearchParams(searchParams);
    next.set("view", expertReturnView);
    next.delete("project");
    setSearchParams(next, { replace: true });
  };

  const profiles = useQuery({
    queryKey: queryKeys.profiles.list(),
    queryFn: () => listProfiles(),
    enabled: activeView === "catalog" || expertOpen,
  });
  const workflows = useQuery({
    queryKey: queryKeys.workflows.versions(),
    queryFn: () => listWorkflowVersions(),
    enabled: expertOpen,
  });
  const modelRegistry = useQuery({
    queryKey: ["operations", "global-model-registry"],
    queryFn: () => getGlobalModelRegistry(),
    enabled: activeView === "resources",
  });

  return <div className="v2-page models-page">
    <div className="panel-heading models-page-heading">
      <div><p className="eyebrow">系统资源</p><h3>能力与模型中心</h3></div>
      <span className="status-pill neutral">全局共享 · 项目按需选择</span>
    </div>
    <p className="muted models-page-summary">在这里统一接入、验证和发布模型能力。资源登记一次即可供所有项目使用；项目设置只决定默认选用哪个已发布能力。</p>
    <ConceptGuide title="资源层级说明" items={[
      { term: "模型与服务", description: "本机模型文件、Ollama、OpenAI 兼容服务和安全凭据，属于整个系统。" },
      { term: "创作能力", description: "模型可以完成的用途，例如剧本拆解、文生图、图生视频或语音合成。" },
      { term: "项目生成偏好", description: "项目只选择全局目录中的默认能力，不复制模型，也不创建项目专属模型。" },
    ]} />

    <section className="models-quickstart" aria-labelledby="models-quickstart-title">
      <div className="models-quickstart-heading"><div><p className="eyebrow">统一资源流</p><h3 id="models-quickstart-title">一次接入，所有项目复用</h3></div><span className="status-pill neutral">无需为每个项目重复配置</span></div>
      <ol>
        <li><span>1</span><div><strong>添加模型或服务</strong><small>登记本机模型、Ollama 或远端服务连接，凭据只在系统安全存储。</small></div></li>
        <li><span>2</span><div><strong>验证并发布能力</strong><small>一个模型可承载故事拆解、视觉质检等多个用途，不按项目复制。</small></div></li>
        <li><span>3</span><div><strong>项目按需选择</strong><small>每个项目可使用自动推荐，或把某个已发布版本设为生成偏好。</small></div></li>
      </ol>
    </section>

    <div className="models-view-bar">
      <div className="system-workspace-tabs models-task-tabs"><Tabs items={CREATOR_VIEWS} selectedId={activeView} onChange={setActiveView} ariaLabel="全局能力与模型" /></div>
      <button type="button" className="secondary models-expert-trigger" aria-haspopup="dialog" aria-expanded={expertOpen} onClick={openExpert}>打开专家配置</button>
    </div>

    <TabPanel id="catalog" selectedId={activeView}>
      {profiles.isPending ? <Skeleton label="正在读取全局能力目录" lines={5} /> : profiles.error ? <ErrorState description={`全局能力目录读取失败：${String(profiles.error)}`} onRetry={() => void profiles.refetch()} /> : <CapabilityCatalog profiles={profiles.data?.items ?? []} />}
    </TabPanel>

    <TabPanel id="resources" selectedId={activeView}>
      <section className="models-resource-section" aria-labelledby="provider-resource-title"><div className="models-resource-intro"><p className="eyebrow">01 · 服务入口</p><h3 id="provider-resource-title">远端服务与安全凭据</h3><p>连接只配置一次，发布能力时引用该连接；不会写入任何项目。</p></div><ProviderConnectionsPanel /></section>
      <section className="models-resource-section" aria-labelledby="intelligence-resource-title"><div className="models-resource-intro"><p className="eyebrow">02 · 智能理解模型</p><h3 id="intelligence-resource-title">语言与视觉理解模型</h3><p>统一配置 LLM，可用于故事拆解、策划、提示词和视觉质检，不再为故事拆解单设入口。</p></div><LocalLLMConfigurationPanel onChanged={() => void profiles.refetch()} /></section>
      <section className="models-resource-section" aria-labelledby="local-resource-title"><div className="models-resource-intro"><p className="eyebrow">03 · 本机权重</p><h3 id="local-resource-title">本机模型库</h3><p>登记服务端文件路径并完成离线兼容性检查，结果对所有项目可见。</p></div>
        {modelRegistry.isPending ? <Skeleton label="正在读取全局模型库" lines={5} /> : modelRegistry.error ? <ErrorState description={`全局模型库读取失败：${String(modelRegistry.error)}`} onRetry={() => void modelRegistry.refetch()} /> : modelRegistry.data?.compatibility ? <ModelCompatibilityPanel snapshot={modelRegistry.data.compatibility} onEvidenceImported={() => void modelRegistry.refetch()} /> : <p className="empty-state">全局模型库尚无记录。</p>}
      </section>
    </TabPanel>

    <Dialog open={expertOpen} title="执行配置契约" size="fullscreen" dirtyGuard={expertDirty} onClose={closeExpert}>
      <div className="models-expert-dialog-intro">
        <div><p className="eyebrow">专家工具</p><h3>执行契约与工作流版本</h3></div>
        <p>仅供接入新运行时、维护执行合同或发布工作流的技术人员。编辑不可变输入、参数、输出与资源约束。</p>
      </div>
      {profiles.isPending ? <Skeleton label="正在读取执行配置契约" lines={5} /> : profiles.error ? <ErrorState description={`执行配置契约读取失败：${String(profiles.error)}`} onRetry={() => void profiles.refetch()} /> : <ProfileConfigurationPanel mode="profile-contracts" profiles={profiles.data?.items ?? []} workflows={workflows.data?.items ?? []} projectId={legacyEvidenceProjectId} onChanged={() => void profiles.refetch()} onDirtyChange={setExpertDirty} />}
    </Dialog>
  </div>;
}

import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Dialog, ErrorState, Skeleton, TabPanel, Tabs } from "../components/ui";
import { ProviderConnectionsPanel } from "../features/model-config/ProviderConnectionsPanel";
import { canonicalCapabilityLabel, creatorProfileTitle } from "../features/preferences-v2/canonicalCapabilities";
import { LocalLLMConfigurationPanel } from "../features/profiles/LocalLLMConfigurationPanel";
import type { ProfilePublicationReceiptData } from "../features/profiles/ProfilePublicationReceipt";
import { ProfileConfigurationPanel } from "../features/profiles/ProfileConfigurationPanel";
import { ModelCompatibilityPanel } from "../features/status/ReadinessPanels";
import { ModelPlatformCenter } from "../features/model-platform-v2/ModelPlatformCenter";
import { getGlobalModelRegistry, listProfiles, listWorkflowVersions, putGenerationPreference, type Profile } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import "./system-workspaces.css";
import "./models-workspace.css";

type ModelView = "platform" | "catalog" | "resources";

const CREATOR_VIEWS: Array<{ id: ModelView; label: string }> = [
  { id: "platform", label: "模型平台" },
  { id: "catalog", label: "已发布目录" },
  { id: "resources", label: "接入与验证" },
];
function resolveView(value: string | null): ModelView {
  if (value === "platform") return "platform";
  if (value === "resources" || value === "local-llm" || value === "connections") return "resources";
  if (value === "catalog") return "catalog";
  return "platform";
}

function capabilityFamily(capability: string) {
  if (capability.startsWith("LLM_")) return "故事与文本";
  if (capability.startsWith("IMAGE_")) return "图像";
  if (capability.startsWith("VIDEO_")) return "视频";
  if (["TTS", "VOICE_CLONE", "LIPSYNC", "AUDIO_SFX", "AUDIO_MUSIC"].includes(capability)) return "声音";
  return "处理与质检";
}

function publishedProfileElementId(profileVersionId: string) {
  return `published-profile-${profileVersionId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function CapabilityCatalog({ profiles, focusedProfileVersionId, contextCapability, onSelect, selectingId }: { profiles: Profile[]; focusedProfileVersionId?: string | null; contextCapability?: string | null; onSelect?: (profile: Profile) => void; selectingId?: string | null }) {
  const published = profiles.filter((item) => item.status === "PUBLISHED" && (!contextCapability || item.capability === contextCapability));
  const groups = useMemo(() => {
    const result = new Map<string, Profile[]>();
    for (const profile of published) {
      const family = capabilityFamily(profile.capability);
      result.set(family, [...(result.get(family) ?? []), profile]);
    }
    return result;
  }, [published]);
  const focusedProfile = published.find((item) => item.version_id === focusedProfileVersionId) ?? null;

  useEffect(() => {
    if (!focusedProfile) return;
    const element = document.getElementById(publishedProfileElementId(focusedProfile.version_id));
    element?.focus({ preventScroll: true });
    element?.scrollIntoView?.({ block: "center" });
  }, [focusedProfile]);

  return <section className="panel models-capability-catalog" aria-labelledby="capability-catalog-title">
    <div className="panel-heading">
      <div><p className="eyebrow">已发布</p><h3 id="capability-catalog-title">可用的创作能力</h3></div>
      <span className="status-pill">{published.length} 个已发布版本</span>
    </div>
    <p className="muted">{contextCapability ? `正在为当前创作上下文选择 ${canonicalCapabilityLabel(contextCapability)}；只列出已发布版本。` : "能力按创作用途组织；同一个模型可以承担多个用途。"}</p>
    {focusedProfile ? <p className="models-catalog-location" role="status"><strong>已定位到刚发布的版本</strong><span>{canonicalCapabilityLabel(focusedProfile.capability)} · {creatorProfileTitle(focusedProfile.title)} · v{focusedProfile.version_no ?? "?"}</span></p> : null}
    {published.length === 0 ? <div className="models-catalog-empty"><strong>还没有已发布能力</strong><span>前往“模型与服务”添加资源，再由专家工具发布执行版本。</span></div> : <div className="models-capability-groups">
      {[...groups.entries()].map(([family, items]) => <section key={family} className="models-capability-group" aria-label={family}>
        <div className="models-capability-group-heading"><h4>{family}</h4><span>{items.length}</span></div>
        <div className="models-capability-list">
          {items.map((item) => {
            const focused = item.version_id === focusedProfile?.version_id;
            return <article key={item.version_id} id={publishedProfileElementId(item.version_id)} tabIndex={-1} aria-current={focused ? "true" : undefined} className={`models-capability-item${focused ? " is-focused" : ""}`}>
              <div><strong>{canonicalCapabilityLabel(item.capability)}</strong><small>{creatorProfileTitle(item.title)}</small></div>
              <span className="status-pill">v{item.version_no ?? "?"} · 已发布</span>{onSelect ? <button type="button" className="secondary" disabled={Boolean(selectingId)} onClick={() => onSelect(item)}>{selectingId === item.version_id ? "正在应用…" : "用于当前集"}</button> : null}
            </article>;
          })}
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
  const queryClient = useQueryClient();
  const contextProjectId = searchParams.get("project");
  const contextEpisodeId = searchParams.get("episode");
  const contextCapability = searchParams.get("capability");
  const rawReturnTo = searchParams.get("returnTo");
  const returnTo = rawReturnTo?.startsWith("/projects/") ? rawReturnTo : null;
  const [contextMessage, setContextMessage] = useState("");
  const selectForEpisode = useMutation({
    mutationFn: (profile: Profile) => putGenerationPreference(contextProjectId!, {
      owner_type: "EPISODE",
      owner_id: contextEpisodeId!,
      capability: profile.capability,
      resolution_mode: "EXPLICIT",
      execution_profile_version_id: profile.version_id,
      reason: "从当前分集能力摘要选择已发布版本",
    }),
    onSuccess: async (_result, profile) => {
      setContextMessage(`${canonicalCapabilityLabel(profile.capability)}已切换为 ${creatorProfileTitle(profile.title)} v${profile.version_no ?? "?"}；只影响后续新任务。`);
      await queryClient.invalidateQueries({ queryKey: ["capability-options", profile.capability, contextProjectId ?? "", contextEpisodeId ?? ""] });
    },
  });

  const locatePublishedProfile = (receipt: ProfilePublicationReceiptData) => {
    const next = new URLSearchParams(searchParams);
    next.set("view", "catalog");
    next.set("published", receipt.profileVersionId);
    next.delete("project");
    setExpertDirty(false);
    setSearchParams(next, { replace: true });
  };

  const setActiveView = (view: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("view", resolveView(view));
    next.delete("project");
    next.delete("published");
    setSearchParams(next, { replace: true });
  };

  const openConnections = () => setActiveView("resources");

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
      <div><p className="eyebrow">系统资源</p><h3>能力与模型</h3></div>
    </div>
    <p className="muted models-page-summary">接入本机模型或模型服务，验证后发布为创作能力。</p>
    {contextProjectId && contextEpisodeId && contextCapability ? <section className="panel models-context-return" aria-label="创作上下文">
      <div><strong>为当前分集选择能力</strong><span>{canonicalCapabilityLabel(contextCapability)} · 变更仅影响后续新任务，已冻结运行保持原配置。</span></div>
      {returnTo ? <Link className="secondary v2-inline-link" to={returnTo}>返回当前分集</Link> : null}
      {contextMessage ? <p role="status">{contextMessage}</p> : null}
      {selectForEpisode.error ? <p className="inline-error" role="alert">应用失败：{selectForEpisode.error instanceof Error ? selectForEpisode.error.message : String(selectForEpisode.error)}</p> : null}
    </section> : null}

    <div className="models-view-bar">
      <div className="system-workspace-tabs models-task-tabs"><Tabs items={CREATOR_VIEWS} selectedId={activeView} onChange={setActiveView} ariaLabel="能力与模型" /></div>
      <button type="button" className="secondary models-expert-trigger" aria-haspopup="dialog" aria-expanded={expertOpen} onClick={openExpert}>打开专家配置</button>
    </div>

    <TabPanel id="platform" selectedId={activeView}>
      <ModelPlatformCenter onOpenConnections={openConnections} />
    </TabPanel>

    <TabPanel id="catalog" selectedId={activeView}>
      {profiles.isPending ? <Skeleton label="正在读取全局能力目录" lines={5} /> : profiles.error ? <ErrorState description={`全局能力目录读取失败：${String(profiles.error)}`} onRetry={() => void profiles.refetch()} /> : <CapabilityCatalog profiles={profiles.data?.items ?? []} focusedProfileVersionId={searchParams.get("published")} contextCapability={contextCapability} onSelect={contextProjectId && contextEpisodeId && contextCapability ? (profile) => selectForEpisode.mutate(profile) : undefined} selectingId={selectForEpisode.isPending ? selectForEpisode.variables?.version_id : null} />}
    </TabPanel>

    <TabPanel id="resources" selectedId={activeView}>
      <section className="models-resource-section" aria-labelledby="provider-resource-title"><div className="models-resource-intro"><p className="eyebrow">01 · 服务入口</p><h3 id="provider-resource-title">远端服务与安全凭据</h3><p>连接模型服务并安全保存凭据。</p></div><ProviderConnectionsPanel /></section>
      <section className="models-resource-section" aria-labelledby="intelligence-resource-title"><div className="models-resource-intro"><p className="eyebrow">02 · 智能理解模型</p><h3 id="intelligence-resource-title">语言与视觉理解模型</h3><p>统一配置 LLM，可用于故事拆解、策划、提示词和视觉质检，不再为故事拆解单设入口。</p></div><LocalLLMConfigurationPanel onChanged={() => void profiles.refetch()} onPublished={locatePublishedProfile} /></section>
      <section className="models-resource-section" aria-labelledby="local-resource-title"><div className="models-resource-intro"><p className="eyebrow">03 · 本机权重</p><h3 id="local-resource-title">本机模型库</h3><p>添加电脑里的模型文件并完成离线兼容性检查。</p></div>
        {modelRegistry.isPending ? <Skeleton label="正在读取全局模型库" lines={5} /> : modelRegistry.error ? <ErrorState description={`全局模型库读取失败：${String(modelRegistry.error)}`} onRetry={() => void modelRegistry.refetch()} /> : modelRegistry.data?.compatibility ? <ModelCompatibilityPanel snapshot={modelRegistry.data.compatibility} onEvidenceImported={() => void modelRegistry.refetch()} /> : <p className="empty-state">全局模型库尚无记录。</p>}
      </section>
    </TabPanel>

    <Dialog open={expertOpen} title="执行配置契约" size="fullscreen" dirtyGuard={expertDirty} onClose={closeExpert}>
      <div className="models-expert-dialog-intro">
        <div><p className="eyebrow">专家工具</p><h3>执行契约与工作流版本</h3></div>
        <p>仅供接入新运行时、维护执行合同或发布工作流的技术人员。编辑不可变输入、参数、输出与资源约束。</p>
      </div>
      {profiles.isPending ? <Skeleton label="正在读取执行配置契约" lines={5} /> : profiles.error ? <ErrorState description={`执行配置契约读取失败：${String(profiles.error)}`} onRetry={() => void profiles.refetch()} /> : <ProfileConfigurationPanel mode="profile-contracts" profiles={profiles.data?.items ?? []} workflows={workflows.data?.items ?? []} projectId={legacyEvidenceProjectId} onChanged={() => void profiles.refetch()} onDirtyChange={setExpertDirty} onPublished={locatePublishedProfile} />}
    </Dialog>
  </div>;
}

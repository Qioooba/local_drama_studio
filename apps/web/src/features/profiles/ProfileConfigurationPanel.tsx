import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { routes } from "../../app/routeRegistry";
import {
  deriveProfileContractVersion,
  getProfileVersion,
  publishProfileContractVersion,
  publishWorkflowVersion,
  revokeWorkflowVersion,
  rollbackWorkflowVersion,
  submitMediaDerivative,
  syncProfiles,
  validateProfileContractVersion,
  validateWorkflowLocal,
  getJob,
  listJobs,
  type Profile,
  type ProfileVersionDetail,
  type WorkflowValidation,
  type WorkflowVersionSummary,
} from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { Dialog } from "../../components/ui/primitives";
import {
  finalizeI2VEvidenceProbe,
  finalizeT2IEvidenceProbe,
  planI2VEvidenceProbe,
  planT2IEvidenceProbe,
  prepareI2VEvidenceKeyframe,
  submitI2VEvidenceProbe,
  submitT2IEvidenceProbe,
  validateProfileEvidenceCompatibility,
  type I2VEvidenceProbePlan,
  type T2IEvidenceProbePlan,
} from "./profileEvidenceClient";
import "./profile-configuration.css";
import { WorkflowDefinitionForm } from "./WorkflowDefinitionForm";
import { ProfileContractEditors } from "./ProfileContractEditors";
import { ModelInspectorDrawer } from "../model-config/ModelInspectorDrawer";
import { PRODUCTION_TIER_LABELS, STATUS_LABELS, optionLabel } from "../shared/optionLabels";
import { ProjectMediaVersionSelect } from "../media-picker/ProjectMediaVersionSelect";
import { canonicalCapabilityLabel, creatorProfileTitle } from "../preferences-v2/canonicalCapabilities";
import {
  PROFILE_FAMILIES,
  profileFamilyId,
  profileStageDescription,
  profileStatusDescription,
  profileStatusLabel,
} from "./profilePresentation";
import {
  ProfilePublicationReceipt,
  ProfilePublicationTarget,
  type ProfilePublicationReceiptData,
} from "./ProfilePublicationReceipt";

type ProfileConfigurationPanelProps =
  | {
      mode: "profile-contracts";
      profiles: Profile[];
      workflows: WorkflowVersionSummary[];
      projectId?: string;
      onChanged: () => void;
      onDirtyChange?: (dirty: boolean) => void;
      onPublished?: (receipt: ProfilePublicationReceiptData) => void;
    }
  | {
      mode: "workflows";
      workflows: WorkflowVersionSummary[];
      workflowsLoading: boolean;
      onChanged: () => void;
    };

type Feedback = { kind: "success" | "error"; message: string } | null;
type ProfileCollection = { id: string; code: string; title: string; capability: string; versions: Profile[] };

function parseObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label} 必须是有效 JSON。`);
  }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error(`${label} 必须是 JSON 对象。`);
  }
  return parsed as Record<string, unknown>;
}

function jsonDiffers(value: string, persisted: Record<string, unknown>): boolean {
  try {
    return JSON.stringify(JSON.parse(value)) !== JSON.stringify(persisted);
  } catch {
    return true;
  }
}

function parameterEffectLabel(effect: unknown): string {
  const code = String(effect ?? "");
  if (code === "GRAPH") return "写入候选图";
  if (code === "SEMANTIC_DEFAULT") return "运行时语义槽可覆盖";
  if (code === "INACTIVE_TIER_CONTROLS_FRAMES") return "当前未生效：生产档位控制帧数";
  if (code === "INACTIVE_TIER_CONTROLS_STEPS") return "当前未生效：生产档位控制采样步数";
  if (code === "INACTIVE_FREEFORM_MODE") return "当前未生效：已使用自由参数模式";
  return code;
}

function profileExecutionModel(profile: ProfileVersionDetail): string | undefined {
  const value = profile.execution?.model ?? profile.execution?.model_bundle?.model;
  return typeof value === "string" && value.trim() ? value : undefined;
}

function profileExecutionProvider(profile: ProfileVersionDetail): string | undefined {
  const value = profile.execution?.provider ?? profile.execution?.model_bundle?.provider;
  return typeof value === "string" && value.trim() ? value : undefined;
}

export function ProfileConfigurationPanel(props: ProfileConfigurationPanelProps) {
  if (props.mode === "profile-contracts") {
    return <ProfileContractsTask profiles={props.profiles} workflows={props.workflows} projectId={props.projectId} onChanged={props.onChanged} onDirtyChange={props.onDirtyChange} onPublished={props.onPublished} />;
  }
  return (
    <WorkflowVersionsTask
      workflows={props.workflows}
      workflowsLoading={props.workflowsLoading}
      onChanged={props.onChanged}
    />
  );
}

function ProfileContractsTask({ profiles, workflows, projectId, onChanged, onDirtyChange, onPublished }: { profiles: Profile[]; workflows: WorkflowVersionSummary[]; projectId?: string; onChanged: () => void; onDirtyChange?: (dirty: boolean) => void; onPublished?: (receipt: ProfilePublicationReceiptData) => void }) {
  const preferredId = profiles.find((item) => item.status === "PUBLISHED")?.version_id ?? profiles[0]?.version_id ?? null;
  const [selectedId, setSelectedId] = useState<string | null>(preferredId);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [inputJson, setInputJson] = useState("{}");
  const [parameterJson, setParameterJson] = useState("{}");
  const [outputJson, setOutputJson] = useState("{}");
  const [resourceJson, setResourceJson] = useState("{}");
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [publicationReceipt, setPublicationReceipt] = useState<ProfilePublicationReceiptData | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [probePlan, setProbePlan] = useState<I2VEvidenceProbePlan | T2IEvidenceProbePlan | null>(null);
  const [probeJobId, setProbeJobId] = useState<string | null>(null);
  const [probeConfirmOpen, setProbeConfirmOpen] = useState(false);
  const [keyframeSourceId, setKeyframeSourceId] = useState("");
  const [keyframeConfirmed, setKeyframeConfirmed] = useState(false);
  const [keyframePreviewJobId, setKeyframePreviewJobId] = useState<string | null>(null);
  const publishedI2VWorkflows = workflows.filter(
    (item) => item.status === "PUBLISHED" && item.contract.capability === "H3_FL2VA_I2V_CANDIDATE",
  );
  const publishedT2IWorkflows = workflows.filter(
    (item) => item.status === "PUBLISHED" && item.contract.capability === "SDXL_T2I_CANDIDATE",
  );
  const preferredEvidenceWorkflowId = publishedI2VWorkflows[0]?.id ?? "";
  const [evidenceWorkflowId, setEvidenceWorkflowId] = useState(preferredEvidenceWorkflowId);

  const profileCollections = useMemo(() => {
    const collections = new Map<string, ProfileCollection>();
    for (const profile of profiles) {
      const key = profile.id || `${profile.code}:${profile.capability}`;
      const currentCollection = collections.get(key);
      if (currentCollection) currentCollection.versions.push(profile);
      else collections.set(key, {
        id: key,
        code: profile.code,
        title: profile.title,
        capability: profile.capability,
        versions: [profile],
      });
    }
    for (const collection of collections.values()) {
      collection.versions.sort((left, right) => Number(right.version_no ?? 0) - Number(left.version_no ?? 0));
    }
    return [...collections.values()];
  }, [profiles]);

  const groupedCollections = useMemo(() => PROFILE_FAMILIES.map((family) => ({
    ...family,
    items: profileCollections.filter((item) => profileFamilyId(item.capability) === family.id),
  })).filter((family) => family.items.length), [profileCollections]);

  const selected = profiles.find((item) => item.version_id === selectedId) ?? profiles[0] ?? null;
  const selectedCollection = profileCollections.find((item) => item.id === selected?.id) ?? null;
  const selectedVersions = selectedCollection?.versions ?? [];
  const detail = useQuery({
    queryKey: queryKeys.profiles.version(selected?.version_id),
    queryFn: () => getProfileVersion(selected!.version_id),
    enabled: Boolean(selected),
  });
  const activeDetail = useQuery({
    queryKey: queryKeys.profiles.version(draftId),
    queryFn: () => getProfileVersion(draftId!),
    enabled: Boolean(draftId),
  });
  const current: ProfileVersionDetail | undefined = draftId
    ? activeDetail.data?.profile_version
    : detail.data?.profile_version;

  useEffect(() => {
    if (selectedId && profiles.some((item) => item.version_id === selectedId)) return;
    setSelectedId(preferredId);
    setDraftId(null);
    setFeedback(null);
    setPublicationReceipt(null);
  }, [preferredId, profiles, selectedId]);

  useEffect(() => {
    if (!current) return;
    setInputJson(JSON.stringify(current.input_contract, null, 2));
    setParameterJson(JSON.stringify(current.parameter_schema, null, 2));
    setOutputJson(JSON.stringify(current.output_contract, null, 2));
    setResourceJson(JSON.stringify(current.resource_policy, null, 2));
  }, [current]);

  useEffect(() => {
    setProbePlan(null);
    setProbeJobId(null);
    setProbeConfirmOpen(false);
    setKeyframeSourceId("");
    setKeyframeConfirmed(false);
    setKeyframePreviewJobId(null);
  }, [current?.id]);

  useEffect(() => {
    const isImageDraft = Boolean(current && current.status === "DRAFT" && String(current.capability).startsWith("IMAGE_"));
    if (isImageDraft) {
      if (evidenceWorkflowId && publishedT2IWorkflows.some((item) => item.id === evidenceWorkflowId)) return;
      setEvidenceWorkflowId(publishedT2IWorkflows[0]?.id ?? "");
      setProbePlan(null);
      return;
    }
    if (evidenceWorkflowId && publishedI2VWorkflows.some((item) => item.id === evidenceWorkflowId)) return;
    setEvidenceWorkflowId(preferredEvidenceWorkflowId);
    setProbePlan(null);
  }, [evidenceWorkflowId, preferredEvidenceWorkflowId, publishedI2VWorkflows, publishedT2IWorkflows, current]);

  const derive = useMutation({
    mutationFn: async () => {
      if (!current) throw new Error("Profile 版本尚未加载。");
      return deriveProfileContractVersion(current.id, {
        expected_source_revision: current.revision,
        input_contract: parseObject(inputJson, "输入契约"),
        parameter_schema: parseObject(parameterJson, "参数 Schema"),
        output_contract: parseObject(outputJson, "输出契约"),
        resource_policy: parseObject(resourceJson, "资源策略"),
      });
    },
    onSuccess: (data) => {
      setDraftId(data.profile_version.id);
      setPublicationReceipt(null);
      setFeedback({
        kind: "success",
        message: `已创建不可变 DRAFT v${data.profile_version.version_no}；原版本未覆盖。`,
      });
      onChanged();
    },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });

  const validate = useMutation({
    mutationFn: () => {
      if (!current || current.status !== "DRAFT") throw new Error("只能验证当前 DRAFT 版本。");
      return validateProfileContractVersion(current.id);
    },
    onSuccess: (data) => {
      void (draftId ? activeDetail.refetch() : detail.refetch());
      setFeedback({
        kind: data.validation.status === "PASS" ? "success" : "error",
        message:
          data.validation.status === "PASS"
            ? "本地契约验证 PASS；未连接 runtime 或网络。"
            : "契约验证未通过，请查看字段和验证项。",
      });
    },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });

  const publish = useMutation({
    mutationFn: () => {
      if (!current || current.status !== "DRAFT" || current.validation?.status !== "PASS") {
        throw new Error("Profile 发布要求当前 DRAFT 的 contract hash 已获得 PASS 验证证明。");
      }
      return publishProfileContractVersion(current.id);
    },
    onSuccess: (data) => {
      const receipt: ProfilePublicationReceiptData = {
        profileVersionId: data.profile_version.id,
        profileCode: current?.code,
        versionNo: data.profile_version.version_no,
        capability: current?.capability ?? selected?.capability ?? "UNKNOWN",
        model: current ? profileExecutionModel(current) : undefined,
        provider: current ? profileExecutionProvider(current) : undefined,
      };
      setFeedback(null);
      setPublicationReceipt(receipt);
      setSelectedId(data.profile_version.id);
      setDraftId(null);
      onChanged();
    },
    onError: (error) => {
      setFeedback({
        kind: "error",
        message: `${String(error)} 执行指纹变化时必须转入真实媒体证据发布。`,
      });
    },
  });

  const isImageEvidence = Boolean(current && String(current.capability).startsWith("IMAGE_"));

  const evidencePlan = useMutation({
    mutationFn: () => {
      if (!projectId || !current || !evidenceWorkflowId) throw new Error("真实证据探测需要当前项目、DRAFT Profile 与显式 Workflow 版本。");
      return isImageEvidence
        ? planT2IEvidenceProbe(projectId, current.id, evidenceWorkflowId)
        : planI2VEvidenceProbe(projectId, current.id, evidenceWorkflowId);
    },
    onSuccess: ({ plan }) => {
      setProbePlan(plan);
      setFeedback({
        kind: plan.status === "READY" ? "success" : "error",
        message: plan.status === "READY"
          ? "证据探测预检 READY；确认后只创建一个本机 GPU_H3 Job。"
          : `证据探测 BLOCKED：${plan.blockers.join("、")}`,
      });
    },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });

  const evidenceCompatibility = useMutation({
    mutationFn: () => {
      if (!current) throw new Error("Profile 尚未加载。");
      return validateProfileEvidenceCompatibility(current.id);
    },
    onSuccess: ({ compatibility }) => {
      setProbePlan(null);
      setFeedback({
        kind: compatibility.status === "PASS" ? "success" : "error",
        message: `Capability 兼容性验证 ${compatibility.status} · ${compatibility.checks.filter((item) => item.passed).length}/${compatibility.checks.length}。请重新预检证据探测。`,
      });
    },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });

  const prepareKeyframe = useMutation({
    mutationFn: () => {
      if (!projectId || !keyframeSourceId || !keyframeConfirmed) {
        throw new Error("请选择真实图片并完成画面检查确认。");
      }
      return prepareI2VEvidenceKeyframe(projectId, keyframeSourceId);
    },
    onSuccess: ({ approved_keyframe: keyframe }) => {
      setProbePlan(null);
      setFeedback({
        kind: "success",
        message: keyframe.reused
          ? `已复用相同内容的专用验证首帧 ${keyframe.media_version_id.slice(0, 12)}…。`
          : `已创建并批准专用验证首帧 ${keyframe.media_version_id.slice(0, 12)}…；来源图片保持不变。`,
      });
    },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });

  const prepareKeyframePreview = useMutation({
    mutationFn: (mediaVersionId: string) => submitMediaDerivative(
      mediaVersionId,
      "THUMBNAIL",
      { size: "medium", frame: "poster" },
    ),
    onSuccess: ({ job }) => setKeyframePreviewJobId(job.id),
    onError: (error) => setFeedback({
      kind: "error",
      message: `验证首帧预览准备失败：${String(error)}`,
    }),
  });

  const keyframePreviewJob = useQuery({
    queryKey: ["profile-evidence-keyframe-preview", keyframePreviewJobId],
    queryFn: () => getJob(keyframePreviewJobId!),
    enabled: Boolean(keyframePreviewJobId),
    refetchInterval: (query) => {
      const state = query.state.data?.job.state;
      return state && ["SUCCEEDED", "FAILED", "CANCELLED"].includes(state) ? false : 1_000;
    },
  });
  const keyframePreviewReady = keyframePreviewJob.data?.job.state === "SUCCEEDED";

  const evidenceSubmit = useMutation({
    mutationFn: () => {
      if (!projectId || !current || !probePlan || probePlan.status !== "READY") {
        throw new Error("请先获得 READY 的证据探测计划。");
      }
      return isImageEvidence
        ? submitT2IEvidenceProbe(projectId, current.id, evidenceWorkflowId, probePlan.plan_hash)
        : submitI2VEvidenceProbe(projectId, current.id, evidenceWorkflowId, probePlan.plan_hash);
    },
    onSuccess: ({ job }) => {
      setProbeConfirmOpen(false);
      setProbeJobId(job.id);
      setFeedback({ kind: "success", message: `证据 Job ${job.id.slice(0, 12)}… 已排队；由本机 Supervisor 执行。` });
    },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });

  const evidenceJob = useQuery({
    queryKey: ["profile-evidence-job", probeJobId],
    queryFn: () => getJob(probeJobId!),
    enabled: Boolean(probeJobId),
  });
  const evidenceJobs = useQuery({
    queryKey: ["profile-evidence-jobs", projectId, current?.id],
    queryFn: () => listJobs(projectId),
    enabled: Boolean(projectId && current),
  });
  const recoverableEvidenceJobs = (evidenceJobs.data?.items ?? []).filter((job) => (
    job.type === "PROFILE_EVIDENCE_PROBE"
    && (!job.subject_id || job.subject_id === current?.id)
  ));

  const evidenceFinalize = useMutation({
    mutationFn: () => {
      if (!projectId || !probeJobId) throw new Error("没有可结束登记的证据 Job。");
      return isImageEvidence
        ? finalizeT2IEvidenceProbe(projectId, probeJobId)
        : finalizeI2VEvidenceProbe(projectId, probeJobId);
    },
    onSuccess: (data) => {
      const receipt: ProfilePublicationReceiptData = {
        profileVersionId: data.profile_version.id,
        profileCode: data.profile_version.code,
        versionNo: data.profile_version.version_no,
        capability: data.profile_version.capability,
        model: profileExecutionModel(data.profile_version),
        provider: profileExecutionProvider(data.profile_version),
      };
      setFeedback(null);
      setPublicationReceipt(receipt);
      setSelectedId(data.profile_version.id);
      setDraftId(null);
      onChanged();
    },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });

  const isDraft = current?.status === "DRAFT";
  const hasContractChanges = Boolean(current) && (
    jsonDiffers(inputJson, current!.input_contract)
    || jsonDiffers(parameterJson, current!.parameter_schema)
    || jsonDiffers(outputJson, current!.output_contract)
    || jsonDiffers(resourceJson, current!.resource_policy)
  );

  useEffect(() => {
    onDirtyChange?.(hasContractChanges);
    return () => onDirtyChange?.(false);
  }, [hasContractChanges, onDirtyChange]);

  const [syncing, setSyncing] = useState(false);
  const runManifestSync = async () => {
    setSyncing(true);
    setFeedback(null);
    try {
      await syncProfiles();
      setFeedback({ kind: "success", message: "模型清单已重新扫描；新增能力会以“待验证候选”进入对应分类，现有版本未修改。" });
      onChanged();
    } catch (error) {
      setFeedback({ kind: "error", message: `同步模型清单失败：${String(error)}` });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <section className="panel profile-configuration-panel" aria-labelledby="profile-contracts-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">能力配置</p>
          <h3 id="profile-contracts-title">按创作阶段管理能力与版本</h3>
        </div>
        <span className="status-pill">仅本地</span>
      </div>
      <p className="muted">
        先按故事、图像、视频或声音选择能力，再管理该能力的版本和默认参数。编辑只会派生新草稿，不会覆盖已发布版本。
      </p>

      <details className="profile-capability-import">
        <summary><span>发现本机新增能力</span><small>仅在修改模型清单或接入新运行时后使用</small></summary>
        <div>
          <p>Studio 启动时会自动读取本机模型清单。只有在运行中手动更新了 <code>model_manifest.json</code>，才需要重新扫描；扫描只登记“待验证候选”，不会修改现有版本或自动发布。</p>
          <button type="button" className="secondary" onClick={() => void runManifestSync()} disabled={syncing}>
            {syncing ? "正在重新扫描…" : "重新扫描模型清单"}
          </button>
        </div>
      </details>

      <div className="profile-editor-layout">
        <aside className="profile-capability-browser" aria-label="按创作阶段选择能力">
          <div className="profile-capability-browser__heading"><strong>选择能力</strong><small>{profileCollections.length} 项能力</small></div>
          {groupedCollections.map((family) => <section key={family.id} className="profile-capability-family" aria-labelledby={`profile-family-${family.id}`}>
            <div className="profile-capability-family__heading"><div><h4 id={`profile-family-${family.id}`}>{family.label}</h4><small>{family.description}</small></div><span>{family.items.length}</span></div>
            <div className="profile-capability-family__items">
              {family.items.map((item) => {
                const representative = item.versions.find((version) => version.status === "PUBLISHED") ?? item.versions[0];
                const isSelected = selectedCollection?.id === item.id;
                return <button key={item.id} type="button" className={`profile-capability-choice${isSelected ? " selected" : ""}`} aria-pressed={isSelected} onClick={() => {
                  setSelectedId(representative.version_id);
                  setDraftId(null);
                  setFeedback(null);
                  setPublicationReceipt(null);
                }}>
                  <span><strong>{canonicalCapabilityLabel(item.capability)}</strong><small>{creatorProfileTitle(item.title)}</small></span>
                  <span><small>{item.versions.length} 个版本</small><span className={`status-pill state-${String(representative.status).toLowerCase()}`}>{profileStatusLabel(representative.status)}</span></span>
                </button>;
              })}
            </div>
          </section>)}
          {profiles.length === 0 ? <p className="empty-state">尚未发现可管理的能力。接入模型或服务后再重新扫描模型清单。</p> : null}
        </aside>

        <div className="profile-contract-editor">
          {!selected ? (
            <p className="empty-state">选择或创建 Profile 版本后才能编辑契约。</p>
          ) : detail.isPending || (draftId && activeDetail.isPending) ? (
            <p className="empty-state" role="status">正在读取 Profile 契约…</p>
          ) : detail.error || (draftId ? activeDetail.error : null) ? (
            <div className="inline-error" role="alert">
              Profile 契约读取失败：{String(detail.error ?? (draftId ? activeDetail.error : null))}
            </div>
          ) : !current ? (
            <p className="empty-state">当前版本没有可编辑的契约事实。</p>
          ) : (
            <>
              <header className="profile-contract-context">
                <div><p className="eyebrow">{profileStageDescription(current.capability)}</p><h4>{canonicalCapabilityLabel(current.capability)}</h4><p>{creatorProfileTitle(current.title)} · <code>{current.code}</code></p></div>
              </header>

              <section className="profile-version-switcher" aria-labelledby="profile-version-switcher-title">
                <div className="profile-version-switcher__heading"><div><strong id="profile-version-switcher-title">版本历史</strong><small>选择要查看或派生的不可变版本</small></div><small>{selectedVersions.length} 个版本</small></div>
                <div className="profile-version-switcher__items" role="group" aria-label="能力版本历史">
                  {selectedVersions.map((profile) => {
                    const isSelected = (draftId ?? selected?.version_id) === profile.version_id;
                    return <button key={profile.version_id} type="button" className={`profile-version-chip${isSelected ? " selected" : ""}`} aria-pressed={isSelected} aria-label={`${profile.code} ${profile.capability} · v${String(profile.version_no ?? "—")} ${profile.status}`} onClick={() => {
                      setSelectedId(profile.version_id);
                      setDraftId(null);
                      setFeedback(null);
                      setPublicationReceipt(null);
                    }}><strong>v{String(profile.version_no ?? "—")}</strong><span className={`status-pill state-${String(profile.status).toLowerCase()}`}>{profileStatusLabel(profile.status)}</span></button>;
                  })}
                </div>
                <p className="profile-version-state-help"><strong>{profileStatusLabel(current.status)}：</strong>{profileStatusDescription(current.status)}</p>
              </section>

              <div className="profile-contract-meta">
                <span><small>当前版本</small><strong>第 {current.version_no} 版</strong></span>
                <span><small>版本阶段</small><strong>{profileStatusLabel(current.status)}</strong></span>
                <span><small>本地验证</small><strong>{current.validation ? profileStatusLabel(current.validation.status) : "尚未验证"}</strong></span>
                <button type="button" className="secondary profile-execution-details-button" onClick={() => setInspectorOpen(true)}>查看模型、工作流与运行时</button>
              </div>

              <ProfileContractEditors capability={current.capability} overrideSchema={current.execution?.override_schema} inputJson={inputJson} parameterJson={parameterJson} outputJson={outputJson} resourceJson={resourceJson} onInputChange={setInputJson} onParameterChange={setParameterJson} onOutputChange={setOutputJson} onResourceChange={setResourceJson} />

              {current.validation ? (
                <div className={`profile-validation ${current.validation.status === "PASS" ? "passed" : "failed"}`}>
                  <strong>最新验证：{current.validation.status}</strong>
                  <small>
                    {Array.isArray(current.validation.checks)
                      ? current.validation.checks.filter((item) => item.passed).length
                      : "—"}
                    /{Array.isArray(current.validation.checks) ? current.validation.checks.length : "—"} 项 · hash {String(current.validation.contract_hash ?? "").slice(0, 12) || "—"}
                  </small>
                </div>
              ) : null}

              {feedback ? (
                <p className={feedback.kind === "error" ? "inline-error" : "review-success"} role={feedback.kind === "error" ? "alert" : "status"}>
                  {feedback.message}
                </p>
              ) : null}
              {publicationReceipt ? <ProfilePublicationReceipt receipt={publicationReceipt} onLocate={onPublished} /> : null}

              {isDraft && (current.capability === "VIDEO_I2V" || String(current.capability).startsWith("IMAGE_")) ? (
                <section className="profile-evidence-probe" aria-label={isImageEvidence ? "T2I 真实媒体证据发布" : "I2V 真实媒体证据发布"}>
                  <div>
                    <strong>{isImageEvidence ? "T2I 真实媒体证据发布" : "I2V 真实媒体证据发布"}</strong>
                    <small>
                      {isImageEvidence
                        ? "执行指纹变化时：预检 → 明确确认 → 单个本机 GPU Job → VERIFIED 图片 → 发布新版本。"
                        : "执行指纹变化时：预检 → 明确确认 → 单个本机 GPU Job → VERIFIED 视频 → 发布新版本。"}
                    </small>
                  </div>
                  {!projectId ? <p className="inline-error">请从具体项目的 Models 页面进入，证据不能跨项目猜测。</p> : null}
                  {!isImageEvidence ? (
                    <fieldset className="profile-evidence-keyframe">
                      <legend>首次启用：准备验证首帧</legend>
                      <p>从当前项目选择一张真实图片，检查后登记为独立的镜头关键帧。系统会制作不可变副本，来源图片的归属与用途不会改变。</p>
                      <ProjectMediaVersionSelect
                        projectId={projectId}
                        value={keyframeSourceId}
                        onChange={(value) => {
                          setKeyframeSourceId(value);
                          setKeyframeConfirmed(false);
                          setKeyframePreviewJobId(null);
                          setProbePlan(null);
                          if (value) prepareKeyframePreview.mutate(value);
                        }}
                        label="真实图片来源"
                        mediaKinds={["IMAGE"]}
                        disabled={prepareKeyframe.isPending}
                        required
                      />
                      {projectId && keyframeSourceId && keyframePreviewReady ? (
                        <figure className="profile-evidence-keyframe__preview">
                          <img
                            src={`/api/v1/media-versions/${encodeURIComponent(keyframeSourceId)}/thumbnail?size=medium&frame=poster`}
                            alt="待登记的 I2V 验证首帧预览"
                            width="320"
                            height="568"
                          />
                          <figcaption>请按实际画面检查，而不是仅凭文件名确认。</figcaption>
                        </figure>
                      ) : null}
                      {projectId && keyframeSourceId && !keyframePreviewReady ? (
                        <p role="status">
                          {prepareKeyframePreview.isPending || keyframePreviewJob.isPending
                            ? "正在准备安全预览…"
                            : keyframePreviewJob.data?.job.state === "FAILED"
                              ? "预览生成失败，请重新选择图片或查看任务详情。"
                              : "安全预览正在后台生成…"}
                        </p>
                      ) : null}
                      <label className="profile-evidence-keyframe__confirmation">
                        <input
                          type="checkbox"
                          checked={keyframeConfirmed}
                          onChange={(event) => setKeyframeConfirmed(event.target.checked)}
                          disabled={!keyframeSourceId || !keyframePreviewReady || prepareKeyframe.isPending}
                        />
                        <span>我已检查人物身份、服装、人体/手部、场景道具、构图、光线、连续性和可视频化，确认全部通过。</span>
                      </label>
                      <button
                        type="button"
                        className="secondary"
                        disabled={!projectId || !keyframeSourceId || !keyframeConfirmed || prepareKeyframe.isPending}
                        onClick={() => prepareKeyframe.mutate()}
                      >
                        {prepareKeyframe.isPending ? "登记中…" : "登记为专用验证首帧"}
                      </button>
                    </fieldset>
                  ) : null}
                  <label>
                    验证工作流版本
                    <select
                      aria-label="验证工作流版本"
                      value={evidenceWorkflowId}
                      onChange={(event) => {
                        setEvidenceWorkflowId(event.target.value);
                        setProbePlan(null);
                      }}
                    >
                      <option value="">{isImageEvidence ? "请选择已发布的图像生成工作流" : "请选择已发布的首帧生成工作流"}</option>
                      {(isImageEvidence ? publishedT2IWorkflows : publishedI2VWorkflows).map((workflow) => (
                        <option key={workflow.id} value={workflow.id}>
                          {workflow.title} · 第 {workflow.version_no} 版{workflow.contract.production_tier ? ` · ${optionLabel(PRODUCTION_TIER_LABELS, String(workflow.contract.production_tier))}` : " · 未冻结档位"}
                        </option>
                      ))}
                    </select>
                    <small>证据与发布会冻结此精确 Workflow；不会沿用 Profile 中的旧版本或静默选择最新项。</small>
                  </label>
                  {probePlan ? (
                    <dl>
                      <div><dt>预检</dt><dd>{probePlan.status}</dd></div>
                      {isImageEvidence ? null : (
                        <div><dt>首帧</dt><dd>{(probePlan as I2VEvidenceProbePlan).snapshot.approved_keyframe?.media_version_id.slice(0, 12) ?? "缺失"}</dd></div>
                      )}
                      <div><dt>Workflow</dt><dd>{probePlan.snapshot.workflow?.id.slice(0, 12) ?? "缺失"}</dd></div>
                    </dl>
                  ) : null}
                  {probeJobId ? (
                    <p role="status">Job {probeJobId.slice(0, 12)}… · {evidenceJob.data?.job.state ?? (evidenceJob.isPending ? "读取中" : "待刷新")}</p>
                  ) : null}
                  <label>
                    验证任务（恢复）
                    <select value={probeJobId ?? ""} onChange={(event) => setProbeJobId(event.target.value || null)} disabled={!projectId || evidenceJobs.isLoading}>
                      <option value="">{evidenceJobs.isLoading ? "正在读取项目验证任务…" : "选择此模型配置的验证任务"}</option>
                      {probeJobId && !recoverableEvidenceJobs.some((job) => job.id === probeJobId) ? <option value={probeJobId}>当前会话任务 · {probeJobId.slice(0, 12)}</option> : null}
                      {recoverableEvidenceJobs.map((job) => <option key={job.id} value={job.id}>{optionLabel(STATUS_LABELS, job.state)} · 任务 {job.id.slice(0, 12)}{job.finished_at ? ` · ${new Date(job.finished_at).toLocaleString()}` : ""}</option>)}
                    </select>
                    <small>页面刷新后可恢复；最终发布仍由服务端核对 Profile、执行指纹、首帧与 VERIFIED 产物。</small>
                  </label>
                  {evidenceJob.error ? <p className="inline-error" role="alert">证据 Job 读取失败：{String(evidenceJob.error)}</p> : null}
                  <div className="profile-editor-actions">
                    <button type="button" className="secondary" disabled={evidenceCompatibility.isPending} onClick={() => evidenceCompatibility.mutate()}>
                      {evidenceCompatibility.isPending ? "验证中…" : "验证 capability 兼容性"}
                    </button>
                    <button type="button" className="secondary" disabled={!projectId || evidencePlan.isPending || evidenceSubmit.isPending} onClick={() => evidencePlan.mutate()}>
                      {evidencePlan.isPending ? "预检中…" : "预检真实证据探测"}
                    </button>
                    <button type="button" className="secondary" disabled={probePlan?.status !== "READY" || evidenceSubmit.isPending || Boolean(probeJobId)} onClick={() => setProbeConfirmOpen(true)}>
                      {evidenceSubmit.isPending ? "提交中…" : "确认并排队单次 Job"}
                    </button>
                    <button type="button" className="secondary" disabled={!probeJobId || evidenceJob.isFetching} onClick={() => void evidenceJob.refetch()}>
                      {evidenceJob.isFetching ? "刷新中…" : "刷新证据 Job"}
                    </button>
                    <button type="button" className="primary-action" disabled={evidenceJob.data?.job.state !== "SUCCEEDED" || evidenceFinalize.isPending} onClick={() => evidenceFinalize.mutate()}>
                      {evidenceFinalize.isPending ? "登记发布中…" : "登记证据并发布"}
                    </button>
                  </div>
                  {projectId && probeJobId ? <a href={`${routes.systemJobs(projectId)}&job=${encodeURIComponent(probeJobId)}`}>打开 Job 详情</a> : null}
                  <Dialog
                    open={probeConfirmOpen}
                    title="确认创建真实媒体证据 Job"
                    onClose={() => setProbeConfirmOpen(false)}
                    footer={(
                      <>
                        <button type="button" className="secondary" onClick={() => setProbeConfirmOpen(false)}>取消</button>
                        <button type="button" className="primary-action" disabled={evidenceSubmit.isPending} onClick={() => evidenceSubmit.mutate()}>
                          {evidenceSubmit.isPending ? "排队中…" : "确认并排队"}
                        </button>
                      </>
                    )}
                  >
                    <p>将以当前 DRAFT Profile、{isImageEvidence ? "已发布的 SDXL T2I Workflow" : "已批准首帧和 Published Workflow"}创建一个本机 GPU_H3 证据 Job。</p>
                    <p className="muted">只创建一次；关闭或取消不会提交任务。</p>
                  </Dialog>
                </section>
              ) : null}

              {isDraft ? <ProfilePublicationTarget capability={current.capability} model={profileExecutionModel(current)} /> : null}

              <div className="profile-editor-actions">
                {!isDraft ? (
                  <button type="button" className="primary-action" onClick={() => derive.mutate()} disabled={derive.isPending}>
                    {derive.isPending ? "创建中…" : "保存为新 DRAFT"}
                  </button>
                ) : (
                  <>
                    <button type="button" className="primary-action" onClick={() => derive.mutate()} disabled={!hasContractChanges || derive.isPending || validate.isPending || publish.isPending}>
                      {derive.isPending ? "创建中…" : "保存修改为新 DRAFT"}
                    </button>
                    <button type="button" className="secondary" onClick={() => validate.mutate()} disabled={hasContractChanges || derive.isPending || validate.isPending || publish.isPending}>
                      {validate.isPending ? "验证中…" : "运行本地契约验证"}
                    </button>
                    <button
                      type="button"
                      className="primary-action"
                      onClick={() => publish.mutate()}
                      disabled={hasContractChanges || derive.isPending || publish.isPending || validate.isPending || current.validation?.status !== "PASS"}
                    >
                      {publish.isPending ? "正在发布到全局目录…" : "发布到全局能力目录"}
                    </button>
                  </>
                )}
              </div>
              {isDraft && hasContractChanges ? (
                <small className="action-help">当前表单有未保存修改。先派生新的不可变 DRAFT，再验证并发布，避免误发布旧契约。</small>
              ) : isDraft && current.validation?.status !== "PASS" ? (
                <small className="action-help">发布保持禁用，直到当前 contract hash 获得 PASS 验证证明。</small>
              ) : null}
            </>
          )}
        </div>
      </div>
      <ModelInspectorDrawer
        open={inspectorOpen}
        profile={current ?? null}
        onClose={() => setInspectorOpen(false)}
        onEditDefaults={() => {
          setInspectorOpen(false);
          window.requestAnimationFrame(() => document.getElementById("profile-generation-defaults")?.scrollIntoView({ behavior: "smooth", block: "start" }));
        }}
      />
    </section>
  );
}

function WorkflowVersionsTask({
  workflows,
  workflowsLoading,
  onChanged,
}: {
  workflows: WorkflowVersionSummary[];
  workflowsLoading: boolean;
  onChanged: () => void;
}) {
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [validations, setValidations] = useState<Record<string, WorkflowValidation>>({});
  const [revokeReasons, setRevokeReasons] = useState<Record<string, string>>({});
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const validateWorkflow = async (workflow: WorkflowVersionSummary) => {
    setBusyAction(`validate:${workflow.id}`);
    setFeedback(null);
    try {
      const result = await validateWorkflowLocal(workflow.id);
      const validation = {
        ...result.validation,
        id: String(result.validation.id ?? result.validation.validation_id ?? ""),
      };
      setValidations((current) => ({ ...current, [workflow.id]: validation }));
      setFeedback({
        kind: validation.status === "PASS" ? "success" : "error",
        message: `${workflow.code} v${workflow.version_no} 本地工作流验证：${validation.status}`,
      });
    } catch (error) {
      setFeedback({ kind: "error", message: `工作流验证失败：${String(error)}` });
    } finally {
      setBusyAction(null);
    }
  };

  const publishWorkflow = async (workflow: WorkflowVersionSummary) => {
    const validation = validations[workflow.id];
    if (!validation || validation.status !== "PASS") {
      setFeedback({ kind: "error", message: "发布前必须对该 Workflow 版本执行 PASS 本地验证。" });
      return;
    }
    setBusyAction(`publish:${workflow.id}`);
    setFeedback(null);
    try {
      await publishWorkflowVersion(workflow.id, validation.id);
      setFeedback({ kind: "success", message: `${workflow.code} v${workflow.version_no} 已发布` });
      onChanged();
    } catch (error) {
      setFeedback({ kind: "error", message: `工作流发布失败：${String(error)}` });
    } finally {
      setBusyAction(null);
    }
  };

  const revokeWorkflow = async (workflow: WorkflowVersionSummary) => {
    const reason = revokeReasons[workflow.id]?.trim() ?? "";
    if (!reason) {
      setFeedback({ kind: "error", message: "撤销 Workflow 必须填写原因。" });
      return;
    }
    setBusyAction(`revoke:${workflow.id}`);
    setFeedback(null);
    try {
      await revokeWorkflowVersion(workflow.id, reason);
      setFeedback({ kind: "success", message: `${workflow.code} v${workflow.version_no} 已撤销` });
      onChanged();
    } catch (error) {
      setFeedback({ kind: "error", message: `工作流撤销失败：${String(error)}` });
    } finally {
      setBusyAction(null);
    }
  };

  const rollbackWorkflow = async (workflow: WorkflowVersionSummary) => {
    const validation = validations[workflow.id];
    if (!validation || validation.status !== "PASS") {
      setFeedback({ kind: "error", message: "回滚前必须对目标历史版本重新执行本地验证。" });
      return;
    }
    setBusyAction(`rollback:${workflow.id}`);
    setFeedback(null);
    try {
      await rollbackWorkflowVersion(workflow.id, validation.id);
      setFeedback({ kind: "success", message: `${workflow.code} v${workflow.version_no} 已回滚并重新发布` });
      onChanged();
    } catch (error) {
      setFeedback({ kind: "error", message: `工作流回滚失败：${String(error)}` });
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="panel workflow-configuration-panel" aria-labelledby="workflow-versions-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">工作流历史</p>
          <h3 id="workflow-versions-title">工作流版本、验证与发布证据</h3>
        </div>
        <span className="status-pill neutral">本地验证 · 显式变更</span>
      </div>
      <p className="muted">
        这里操作同一套权威 Workflow Version API。兼容性验证会连接配置的本机 ComfyUI，检查节点与输入 schema；发布、撤销和回滚都必须由用户显式触发并保留证据。
      </p>

      <WorkflowDefinitionForm disabled={busyAction !== null} onCreated={onChanged} />

      {feedback ? (
        <p className={feedback.kind === "error" ? "inline-error" : "review-success"} role={feedback.kind === "error" ? "alert" : "status"}>
          {feedback.message}
        </p>
      ) : null}

      {workflowsLoading ? (
        <p className="empty-state" role="status">正在读取本地工作流版本…</p>
      ) : workflows.length === 0 ? (
        <p className="empty-state">尚无 Workflow 版本；系统不会创建或发布隐式默认工作流。</p>
      ) : (
        <div className="workflow-history" aria-label="Workflow 版本列表">
          {workflows.map((workflow) => {
            const validation = validations[workflow.id];
            const revokeReason = revokeReasons[workflow.id] ?? "";
            const anyBusy = busyAction !== null;
            return (
              <article className="workflow-version" key={workflow.id}>
                <header className="workflow-version-summary">
                  <span>
                    <strong>{workflow.code}</strong>
                    <small>v{workflow.version_no} · {String(workflow.contract.capability ?? "未声明 capability")}</small>
                  </span>
                  <span className={`status-pill state-${String(workflow.status).toLowerCase()}`}>{workflow.status}</span>
                </header>

                <dl className="workflow-version-facts">
                  <div>
                    <dt>内容 hash</dt>
                    <dd><code title={String(workflow.content_hash ?? "")}>{String(workflow.content_hash ?? "").slice(0, 12) || "—"}</code></dd>
                  </div>
                  <div>
                    <dt>发布记录</dt>
                    <dd>{workflow.published_at ? new Date(workflow.published_at).toLocaleString() : "尚未发布"}</dd>
                  </div>
                </dl>
                {workflow.contract.parameter_effects && typeof workflow.contract.parameter_effects === "object" ? (
                  <details className="workflow-effect-report">
                    <summary>参数生效范围</summary>
                    <ul>
                      {Object.entries(workflow.contract.parameter_effects as Record<string, unknown>).map(([name, effect]) => (
                        <li key={name}><code>{name}</code><span>{parameterEffectLabel(effect)}</span></li>
                      ))}
                    </ul>
                  </details>
                ) : null}

                <div className="workflow-actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => void validateWorkflow(workflow)}
                    disabled={anyBusy}
                  >
                    {busyAction === `validate:${workflow.id}` ? "验证中…" : "本地验证"}
                  </button>

                  {validation?.status === "PASS" && workflow.status !== "PUBLISHED" && workflow.status !== "RETIRED" ? (
                    <button
                      type="button"
                      className="primary-action"
                      onClick={() => void publishWorkflow(workflow)}
                      disabled={anyBusy}
                    >
                      {busyAction === `publish:${workflow.id}` ? "发布中…" : "发布"}
                    </button>
                  ) : null}

                  {workflow.status === "RETIRED" && validation?.status === "PASS" ? (
                    <button
                      type="button"
                      className="primary-action"
                      onClick={() => void rollbackWorkflow(workflow)}
                      disabled={anyBusy}
                    >
                      {busyAction === `rollback:${workflow.id}` ? "回滚中…" : "验证后回滚"}
                    </button>
                  ) : null}

                  {workflow.status === "PUBLISHED" ? (
                    <>
                      <label className="revoke-reason-field">
                        <span>撤销原因（必填）</span>
                        <input
                          aria-label={`撤销原因 ${workflow.code} v${workflow.version_no}`}
                          value={revokeReason}
                          onChange={(event) => setRevokeReasons((current) => ({ ...current, [workflow.id]: event.target.value }))}
                        />
                      </label>
                      <button
                        type="button"
                        className="secondary"
                        onClick={() => void revokeWorkflow(workflow)}
                        disabled={anyBusy || !revokeReason.trim()}
                      >
                        {busyAction === `revoke:${workflow.id}` ? "撤销中…" : "撤销"}
                      </button>
                    </>
                  ) : null}
                </div>

                {validation ? (
                  <small className={validation.status === "PASS" ? "ok-text" : "blocker-text"}>
                    最近验证：{String(validation.status)} · {String(validation.id ?? "").slice(0, 12) || "—"}
                  </small>
                ) : (
                  <small className="workflow-validation-required">发布或回滚前必须重新执行本地验证。</small>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  deriveProfileContractVersion,
  getProfileVersion,
  publishProfileContractVersion,
  publishWorkflowVersion,
  registerH3I2VCandidateWorkflow,
  registerWorkflowPackage,
  revokeWorkflowVersion,
  rollbackWorkflowVersion,
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
  submitI2VEvidenceProbe,
  submitT2IEvidenceProbe,
  validateProfileEvidenceCompatibility,
  type I2VEvidenceProbePlan,
  type T2IEvidenceProbePlan,
} from "./profileEvidenceClient";
import "./profile-configuration.css";
import { ProfileContractEditors } from "./ProfileContractEditors";
import { ModelInspectorDrawer } from "../model-config/ModelInspectorDrawer";
import { PRODUCTION_TIER_LABELS, STATUS_LABELS, optionLabel } from "../shared/optionLabels";

type ProfileConfigurationPanelProps =
  | {
      mode: "profile-contracts";
      profiles: Profile[];
      workflows: WorkflowVersionSummary[];
      projectId?: string;
      onChanged: () => void;
    }
  | {
      mode: "workflows";
      workflows: WorkflowVersionSummary[];
      workflowsLoading: boolean;
      onChanged: () => void;
    };

type Feedback = { kind: "success" | "error"; message: string } | null;

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

export function ProfileConfigurationPanel(props: ProfileConfigurationPanelProps) {
  if (props.mode === "profile-contracts") {
    return <ProfileContractsTask profiles={props.profiles} workflows={props.workflows} projectId={props.projectId} onChanged={props.onChanged} />;
  }
  return (
    <WorkflowVersionsTask
      workflows={props.workflows}
      workflowsLoading={props.workflowsLoading}
      onChanged={props.onChanged}
    />
  );
}

function ProfileContractsTask({ profiles, workflows, projectId, onChanged }: { profiles: Profile[]; workflows: WorkflowVersionSummary[]; projectId?: string; onChanged: () => void }) {
  const preferredId = profiles.find((item) => item.status === "PUBLISHED")?.version_id ?? profiles[0]?.version_id ?? null;
  const [selectedId, setSelectedId] = useState<string | null>(preferredId);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [inputJson, setInputJson] = useState("{}");
  const [parameterJson, setParameterJson] = useState("{}");
  const [outputJson, setOutputJson] = useState("{}");
  const [resourceJson, setResourceJson] = useState("{}");
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [probePlan, setProbePlan] = useState<I2VEvidenceProbePlan | T2IEvidenceProbePlan | null>(null);
  const [probeJobId, setProbeJobId] = useState<string | null>(null);
  const [probeConfirmOpen, setProbeConfirmOpen] = useState(false);
  const publishedI2VWorkflows = workflows.filter(
    (item) => item.status === "PUBLISHED" && item.contract.capability === "H3_FL2VA_I2V_CANDIDATE",
  );
  const publishedT2IWorkflows = workflows.filter(
    (item) => item.status === "PUBLISHED" && item.contract.capability === "SDXL_T2I_CANDIDATE",
  );
  const preferredEvidenceWorkflowId = publishedI2VWorkflows[0]?.id ?? "";
  const [evidenceWorkflowId, setEvidenceWorkflowId] = useState(preferredEvidenceWorkflowId);

  const selected = profiles.find((item) => item.version_id === selectedId) ?? profiles[0] ?? null;
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
      setFeedback({ kind: "success", message: `Profile v${data.profile_version.version_no} 已发布。` });
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
      setFeedback({
        kind: "success",
        message: `真实媒体证据已登记；Profile v${data.profile_version.version_no} ${data.profile_version.status}。`,
      });
      setSelectedId(data.profile_version.id);
      setDraftId(null);
      onChanged();
    },
    onError: (error) => setFeedback({ kind: "error", message: String(error) }),
  });

  const isDraft = current?.status === "DRAFT";

  const [syncing, setSyncing] = useState(false);
  const runManifestSync = async () => {
    setSyncing(true);
    setFeedback(null);
    try {
      await syncProfiles();
      setFeedback({ kind: "success", message: "模型清单候选已同步；新能力以 CANDIDATE 版本进入列表。" });
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
          <h3 id="profile-contracts-title">本地能力契约与不可变版本</h3>
        </div>
        <span className="status-pill">仅本地</span>
      </div>
      <p className="muted">
        编辑只会派生新 DRAFT；本地验证不会连接 ComfyUI。执行指纹有变化时，发布必须提供真实成功媒体证据。
      </p>

      <div className="profile-editor-actions">
        <button type="button" className="secondary" onClick={() => void runManifestSync()} disabled={syncing}>
          {syncing ? "同步中…" : "同步模型清单候选"}
        </button>
        <small>读取本机 model_manifest.json，把新能力登记为 CANDIDATE 版本；不会自动发布。</small>
      </div>

      <div className="profile-editor-layout">
        <aside className="profile-version-list" aria-label="Profile 版本">
          {profiles.map((profile) => (
            <button
              key={profile.version_id}
              type="button"
              className={`profile-version-choice${selected?.version_id === profile.version_id ? " selected" : ""}`}
              aria-pressed={selected?.version_id === profile.version_id}
              onClick={() => {
                setSelectedId(profile.version_id);
                setDraftId(null);
                setFeedback(null);
              }}
            >
              <span>
                <strong>{profile.code}</strong>
                <small>{profile.capability} · v{String(profile.version_no ?? "—")}</small>
              </span>
              <span className={`status-pill state-${String(profile.status).toLowerCase()}`}>{profile.status}</span>
            </button>
          ))}
          {profiles.length === 0 ? <p className="empty-state">尚无 Profile 版本；系统不会创建隐式默认项。</p> : null}
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
              <div className="profile-contract-meta">
                <span><small>版本</small><strong>v{current.version_no}</strong></span>
                <span><small>能力</small><strong>{current.capability}</strong></span>
                <span><small>状态</small><strong>{current.status}</strong></span>
                <span><small>契约 hash</small><code title={String(current.contract_hash ?? "")}>{String(current.contract_hash ?? "").slice(0, 12) || "—"}</code></span>
                <button type="button" className="secondary profile-execution-details-button" onClick={() => setInspectorOpen(true)}>查看执行详情</button>
              </div>

              <ProfileContractEditors capability={current.capability} inputJson={inputJson} parameterJson={parameterJson} outputJson={outputJson} resourceJson={resourceJson} onInputChange={setInputJson} onParameterChange={setParameterJson} onOutputChange={setOutputJson} onResourceChange={setResourceJson} />

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
                  {projectId && probeJobId ? <a href={`/projects/${projectId}/jobs?job=${encodeURIComponent(probeJobId)}`}>打开 Job 详情</a> : null}
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

              <div className="profile-editor-actions">
                {!isDraft ? (
                  <button type="button" className="primary-action" onClick={() => derive.mutate()} disabled={derive.isPending}>
                    {derive.isPending ? "创建中…" : "保存为新 DRAFT"}
                  </button>
                ) : (
                  <>
                    <button type="button" className="secondary" onClick={() => validate.mutate()} disabled={validate.isPending || publish.isPending}>
                      {validate.isPending ? "验证中…" : "运行本地契约验证"}
                    </button>
                    <button
                      type="button"
                      className="primary-action"
                      onClick={() => publish.mutate()}
                      disabled={publish.isPending || validate.isPending || current.validation?.status !== "PASS"}
                    >
                      {publish.isPending ? "发布中…" : "发布已验证版本"}
                    </button>
                  </>
                )}
              </div>
              {isDraft && current.validation?.status !== "PASS" ? (
                <small className="action-help">发布保持禁用，直到当前 contract hash 获得 PASS 验证证明。</small>
              ) : null}
            </>
          )}
        </div>
      </div>
      <ModelInspectorDrawer open={inspectorOpen} profile={current ?? null} onClose={() => setInspectorOpen(false)} />
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
  const [showH3Create, setShowH3Create] = useState(false);
  const h3Code = "h3_fl2va_i2v_fast";
  const [h3Title, setH3Title] = useState("H3 FL2VA I2V · FAST");
  const h3Prompt = "由生成任务在运行时注入提示词";
  const h3Seed = "107";
  const [h3Tier, setH3Tier] = useState("FAST");
  const [showT2ICreate, setShowT2ICreate] = useState(false);
  const t2iCode = "sdxl-turbo-t2i-keyframe";
  const [t2iTitle, setT2ITitle] = useState("SDXL Turbo T2I · KEYFRAME");
  const t2iPrompt = "由生成任务在运行时注入提示词";
  const t2iSeed = "260826";

  const createSdxlT2IWorkflow = async () => {
    const code = t2iCode.trim();
    const title = t2iTitle.trim();
    const prompt = t2iPrompt.trim();
    const seed = Number(t2iSeed);
    if (!code || !title || !prompt || !Number.isSafeInteger(seed) || seed < 0) {
      setFeedback({ kind: "error", message: "创建 SDXL T2I 工作流需要 code、标题、占位提示词和非负整数 seed。" });
      return;
    }
    setBusyAction("create:sdxl-t2i");
    setFeedback(null);
    try {
      const workflow: Record<string, unknown> = {
        "1": { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "sdxl_turbo_fp16.safetensors" } },
        "2": { class_type: "CLIPTextEncode", inputs: { text: prompt, clip: ["1", 1] } },
        "3": { class_type: "EmptyLatentImage", inputs: { width: 480, height: 852, batch_size: 1 } },
        "4": {
          class_type: "KSampler",
          inputs: { seed, steps: 4, cfg: 1.0, sampler_name: "euler", scheduler: "simple", denoise: 1.0, model: ["1", 0], positive: ["2", 0], negative: ["2", 0], latent_image: ["3", 0] },
        },
        "5": { class_type: "VAEDecode", inputs: { samples: ["4", 0], vae: ["1", 2] } },
        "6": { class_type: "SaveImage", inputs: { filename_prefix: "local_drama/t2i_keyframe", images: ["5", 0] } },
      };
      const contract = {
        capability: "SDXL_T2I_CANDIDATE",
        input_slots: {},
        local_only: true,
        production_tier: "KEYFRAME",
        requires_explicit_validation: true,
      };
      const node_bindings = {
        PROMPT: { node_id: "2", input: "text" },
        SEED: { node_id: "4", input: "seed" },
        OUTPUT_PREFIX: { node_id: "6", input: "filename_prefix" },
      };
      const runtime_contract = { transport: "LOOPBACK_HTTP", worker_policy: "ONE_H3_WORKER_ONE_GPU_TASK" };
      const result = await registerWorkflowPackage({ code, title, workflow, contract, node_bindings, runtime_contract });
      setFeedback({
        kind: "success",
        message: `${result.workflow_version.code} v${result.workflow_version.version_no} 已创建为候选；PROMPT、SEED 与 OUTPUT_PREFIX 为运行时语义绑定。`,
      });
      setShowT2ICreate(false);
      onChanged();
    } catch (error) {
      setFeedback({ kind: "error", message: `SDXL T2I 工作流创建失败：${String(error)}` });
    } finally {
      setBusyAction(null);
    }
  };

  const createH3I2VWorkflow = async () => {
    const code = h3Code.trim();
    const title = h3Title.trim();
    const prompt = h3Prompt.trim();
    const seed = Number(h3Seed);
    if (!code || !title || !prompt || !Number.isSafeInteger(seed) || seed < 0) {
      setFeedback({ kind: "error", message: "创建 H3 I2V 工作流需要 code、标题、占位提示词和非负整数 seed。" });
      return;
    }
    setBusyAction("create:h3-i2v");
    setFeedback(null);
    try {
      const result = await registerH3I2VCandidateWorkflow({
        code,
        title,
        prompt,
        seed,
        first_frame: "runtime/first-frame.png",
        aspect_ratio: "9:16",
        filename_prefix: "local_drama/h3_i2v",
        sigma_points: 20,
        acceleration: "off",
        tier: h3Tier,
      });
      setFeedback({
        kind: "success",
        message: `${result.workflow_version.code} v${result.workflow_version.version_no} 已创建为候选；PROMPT、SEED、FRAME_COUNT、OUTPUT_PREFIX 与 FIRST_FRAME 均为运行时语义绑定。`,
      });
      setShowH3Create(false);
      onChanged();
    } catch (error) {
      setFeedback({ kind: "error", message: `H3 I2V 工作流创建失败：${String(error)}` });
    } finally {
      setBusyAction(null);
    }
  };

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
        这里操作同一套权威 Workflow Version API。本地验证不会连接 ComfyUI；发布、撤销和回滚都必须由用户显式触发，并保留对应验证或原因。
      </p>

      <div className="workflow-create-toolbar">
        <button type="button" className="primary-action" onClick={() => setShowH3Create((current) => !current)} disabled={busyAction !== null}>
          {showH3Create ? "收起创建表单" : "创建 H3 首帧工作流"}
        </button>
        <button type="button" className="secondary" onClick={() => setShowT2ICreate((current) => !current)} disabled={busyAction !== null}>
          {showT2ICreate ? "收起 T2I 表单" : "创建 SDXL T2I 工作流"}
        </button>
        <small>创建后仍需本地验证和显式发布；不会在此步骤运行 ComfyUI。</small>
      </div>

      {showH3Create ? (
        <form
          className="workflow-create-form"
          aria-label="创建 H3 首帧工作流"
          onSubmit={(event) => {
            event.preventDefault();
            void createH3I2VWorkflow();
          }}
        >
          <label>显示标题<input value={h3Title} onChange={(event) => setH3Title(event.target.value)} /></label>
          <label>生产档位<select value={h3Tier} onChange={(event) => setH3Tier(event.target.value)}><option value="FAST">极速粗筛（速度优先）</option><option value="DRAFT">日常生成（主力候选）</option><option value="SCREEN">候选精筛（质量优先）</option><option value="PRODUCTION">正式成片</option><option value="MASTER">关键镜头精制</option></select></label>
          <div className="workflow-create-form__wide workflow-auto-facts"><strong>系统自动配置验证参数</strong><span>技术标识：{h3Code}</span><span>验证 Seed：{h3Seed}</span><small>占位提示词、帧数、输出前缀和首帧语义槽由系统模板注入，真实生成时会被镜头内容替换。</small></div>
          <div className="workflow-create-form__wide profile-editor-actions">
            <button type="submit" className="primary-action" disabled={busyAction !== null}>{busyAction === "create:h3-i2v" ? "创建中…" : "创建候选版本"}</button>
            <button type="button" className="secondary" onClick={() => setShowH3Create(false)} disabled={busyAction !== null}>取消</button>
          </div>
        </form>
      ) : null}

      {showT2ICreate ? (
        <form
          className="workflow-create-form"
          aria-label="创建 SDXL T2I 工作流"
          onSubmit={(event) => {
            event.preventDefault();
            void createSdxlT2IWorkflow();
          }}
        >
          <label>显示标题<input value={t2iTitle} onChange={(event) => setT2ITitle(event.target.value)} /></label>
          <div className="workflow-create-form__wide workflow-auto-facts"><strong>系统自动配置验证参数</strong><span>技术标识：{t2iCode}</span><span>验证 Seed：{t2iSeed}</span><small>系统按 SDXL Turbo 模板注入提示词、输出前缀、竖屏尺寸与采样步数；真实生成时会替换占位内容。</small></div>
          <div className="workflow-create-form__wide profile-editor-actions">
            <button type="submit" className="primary-action" disabled={busyAction !== null}>{busyAction === "create:sdxl-t2i" ? "创建中…" : "创建候选版本"}</button>
            <button type="button" className="secondary" onClick={() => setShowT2ICreate(false)} disabled={busyAction !== null}>取消</button>
          </div>
        </form>
      ) : null}

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

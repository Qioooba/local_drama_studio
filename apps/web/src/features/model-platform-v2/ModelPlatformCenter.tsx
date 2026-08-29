import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ErrorState, Skeleton } from "../../components/ui";
import { listWorkflowVersions, type WorkflowVersionSummary } from "../../generated/api";
import {
  bindModelPlatformComfyWorkflow,
  createModelPlatformOfflineInstallationPlan,
  createModelPlatformTrustedDownloadPlan,
  getModelPlatformOverview,
  getModelPlatformStoragePolicy,
  listModelPlatformBusinessSelectionRollouts,
  listModelPlatformInstallationPlans,
  listModelPlatformInstallationTargets,
  listModelPlatformSystemCapabilityAssignments,
  listModelPlatformValidationHistory,
  listModelPlatformCapabilities,
  listModelPlatformComfyWorkflowBindings,
  listModelPlatformDiscoveryObservations,
  listModelPlatformProfileVersions,
  listModelPlatformRegisteredCandidates,
  provisionModelPlatformProfile,
  publishModelPlatformProfile,
  putModelPlatformCapabilityAssignment,
  runModelPlatformModelLockDiscovery,
  runModelPlatformOllamaDiscovery,
  registerModelPlatformDiscoveryObservation,
  smokeModelPlatformCapabilityOffering,
  smokeModelPlatformProfile,
  submitModelPlatformComfyCapabilitySmoke,
  verifyModelPlatformInstallationIntegrity,
  type ModelPlatformCapability,
  type ModelPlatformBusinessSelectionRollout,
  type ModelPlatformCapabilityAssignmentPut,
  type ModelPlatformComfyWorkflowBinding,
  type ModelPlatformRegisteredCandidate,
  type ModelPlatformProfileLifecycle,
  type ModelPlatformDiscoveryObservation,
  type ModelPlatformStoragePolicy,
  type ModelPlatformSystemCapabilityAssignment,
  type ModelPlatformSystemOverrideField,
  type ModelPlatformValidationHistoryItem,
  type ModelPlatformInstallationPlan,
  type ModelPlatformOfflineInstallationPlanInput,
  type ModelPlatformInstallationTarget,
  type ModelPlatformTrustedDownloadPlanInput,
} from "./api";

const FAMILY_TITLES: Record<string, string> = {
  TEXT: "故事与文本",
  RETRIEVAL: "检索与知识库",
  IMAGE: "图像",
  VIDEO: "视频",
  AUDIO: "声音",
  POST: "后处理",
  QUALITY: "质检",
};

const LIFECYCLE_STEPS = ["发现", "完整性", "运行时", "冒烟验证", "发布", "可执行"];

function groupCapabilities(capabilities: ModelPlatformCapability[]) {
  const groups = new Map<string, ModelPlatformCapability[]>();
  for (const capability of capabilities) {
    groups.set(capability.family, [...(groups.get(capability.family) ?? []), capability]);
  }
  return [...groups.entries()];
}

function OverviewCard({ label, value, description }: { label: string; value: number; description: string }) {
  return <article className="model-platform-overview-card">
    <span>{label}</span>
    <strong>{value}</strong>
    <small>{description}</small>
  </article>;
}

function ModelStoragePolicyPanel({ policy, error }: { policy?: ModelPlatformStoragePolicy; error?: Error | null }) {
  const rootLabel = policy?.root_kind === "INSTANCE_DEFAULT"
    ? "实例默认存储"
    : policy?.root_kind === "DEDICATED_LOCAL_VOLUME"
      ? "专用本地数据盘"
      : "尚未配置";
  return <section className="model-platform-storage-policy" aria-labelledby="model-platform-storage-policy-title">
    <div>
      <p className="eyebrow">机器存储策略</p>
      <h4 id="model-platform-storage-policy-title">模型目录有边界，扫描也有边界</h4>
      <p>浏览器只看到存储角色和就绪状态，不显示服务器绝对路径。模型根路径只能由 Windows Host 在停机状态下修改。</p>
    </div>
    {error ? <p className="inline-error" role="alert">无法读取模型存储策略：{String(error)}</p> : policy ? <>
      <dl>
        <div><dt>模型根目录</dt><dd>{rootLabel}</dd></div>
        <div><dt>可扫描模型库</dt><dd>{policy.discovery_library_count} 个</dd></div>
        <div><dt>配置权限</dt><dd>Windows Host 命令</dd></div>
        <div><dt>在线下载</dt><dd>{policy.online_download_default_enabled ? `${policy.trusted_download_source_count} 个可信来源` : "默认关闭"}</dd></div>
      </dl>
      <div className="model-platform-storage-policy__zones">
        <div><strong>可发现</strong><span>{policy.discovery_libraries.length ? policy.discovery_libraries.join("、") : "尚未配置受管模型库"}</span></div>
        <div><strong>不参与扫描</strong><span>{policy.operational_areas.join("、")}</span></div>
      </div>
      <details>
        <summary>更换到专用模型盘的安全方式</summary>
        <p>先停止 Host，再运行 <code>configure-model-root --path "E:\模型根目录"</code>。该命令会备份配置、创建目录，但不会移动、删除或自动登记任何模型；完成迁移后请重新扫描和验证。</p>
      </details>
      <p className="muted">在线下载只有在 Host 配置中明确登记可信 HTTPS 来源后才可计划；页面不显示来源主机或下载链接。</p>
    </> : <p className="muted" role="status">正在读取模型存储策略…</p>}
  </section>;
}

type OfflineArtifactDraft = { relative_path: string; sha256: string; size_bytes: string };
const EMPTY_OFFLINE_ARTIFACT: OfflineArtifactDraft = { relative_path: "", sha256: "", size_bytes: "" };

function OfflineImportPlanPanel({ targets, plans, targetsError, plansError, saving, onCreate }: {
  targets?: ModelPlatformInstallationTarget[];
  plans?: ModelPlatformInstallationPlan[];
  targetsError?: Error | null;
  plansError?: Error | null;
  saving: boolean;
  onCreate: (input: ModelPlatformOfflineInstallationPlanInput) => void;
}) {
  const [targetLibraryId, setTargetLibraryId] = useState("");
  const [releaseCode, setReleaseCode] = useState("");
  const [bundleReference, setBundleReference] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [artifacts, setArtifacts] = useState<OfflineArtifactDraft[]>([{ ...EMPTY_OFFLINE_ARTIFACT }]);
  useEffect(() => {
    if (!targetLibraryId && targets?.[0]) setTargetLibraryId(targets[0].id);
  }, [targetLibraryId, targets]);
  const updateArtifact = (index: number, patch: Partial<OfflineArtifactDraft>) => setArtifacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  const validArtifacts = artifacts.every((item) => item.relative_path.trim() && /^[a-fA-F0-9]{64}$/.test(item.sha256.trim()) && Number.isSafeInteger(Number(item.size_bytes)) && Number(item.size_bytes) > 0);
  const canCreate = Boolean(targetLibraryId && releaseCode.trim() && /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(bundleReference.trim()) && licenseId.trim() && validArtifacts);
  const offlinePlans = plans?.filter((plan) => plan.source_kind === "OFFLINE_BUNDLE") ?? [];
  return <section className="model-platform-offline-import" aria-labelledby="model-platform-offline-import-title">
    <div><p className="eyebrow">离线模型导入</p><h4 id="model-platform-offline-import-title">先建立校验计划，再由 Host 停机执行</h4><p>页面只创建不可变导入合同，不读取电脑文件，也不会开始下载。离线包标识和服务端路径不会回显；导入必须由 Windows Host 从 staging 区执行。</p></div>
    {targetsError || plansError ? <p className="inline-error" role="alert">无法读取离线导入状态：{String(targetsError ?? plansError)}</p> : <>
      {!targets ? <p className="muted">正在读取可写入的 V2 模型库…</p> : targets.length === 0 ? <div className="model-platform-migration__empty"><strong>尚无可写入的 V2 模型库</strong><span>请先通过受控扫描登记模型库；页面不会要求或显示服务器目录。</span></div> : <form onSubmit={(event) => {
        event.preventDefault();
        if (!canCreate) return;
        onCreate({ target_library_id: targetLibraryId, release_code: releaseCode.trim(), bundle_reference: bundleReference.trim(), license_id: licenseId.trim(), expected_artifacts: artifacts.map((item) => ({ relative_path: item.relative_path.trim(), sha256: item.sha256.trim(), size_bytes: Number(item.size_bytes) })) });
      }}>
        <label>目标模型库<select value={targetLibraryId} disabled={saving} onChange={(event) => setTargetLibraryId(event.target.value)}>{targets.map((target) => <option key={target.id} value={target.id}>{target.label}</option>)}</select></label>
        <label>模型发布标识<input value={releaseCode} disabled={saving} maxLength={140} onChange={(event) => setReleaseCode(event.target.value)} placeholder="例如 qwen3-embedding-8b" /></label>
        <label>离线包标识<input value={bundleReference} disabled={saving} maxLength={120} onChange={(event) => setBundleReference(event.target.value)} placeholder="只允许字母、数字、点、短横线和下划线" /></label>
        <label>许可证标识<input value={licenseId} disabled={saving} maxLength={200} onChange={(event) => setLicenseId(event.target.value)} placeholder="例如 apache-2.0" /></label>
        <div className="model-platform-offline-import__artifacts"><strong>预期组件</strong><span>导入时必须与这些库内相对路径、SHA-256 和字节数逐项一致。</span>{artifacts.map((artifact, index) => <div key={index} className="model-platform-offline-import__artifact"><input aria-label={`组件 ${index + 1} 路径`} value={artifact.relative_path} disabled={saving} onChange={(event) => updateArtifact(index, { relative_path: event.target.value })} placeholder="Embedding/model.safetensors" /><input aria-label={`组件 ${index + 1} SHA-256`} value={artifact.sha256} disabled={saving} onChange={(event) => updateArtifact(index, { sha256: event.target.value })} placeholder="64 位 SHA-256" /><input aria-label={`组件 ${index + 1} 大小`} type="number" min="1" value={artifact.size_bytes} disabled={saving} onChange={(event) => updateArtifact(index, { size_bytes: event.target.value })} placeholder="字节数" />{artifacts.length > 1 ? <button type="button" className="text-action" disabled={saving} onClick={() => setArtifacts((current) => current.filter((_, itemIndex) => itemIndex !== index))}>移除</button> : null}</div>)}</div>
        <div className="model-platform-offline-import__actions"><button type="button" className="secondary" disabled={saving || artifacts.length >= 100} onClick={() => setArtifacts((current) => [...current, { ...EMPTY_OFFLINE_ARTIFACT }])}>添加组件</button><button type="submit" className="secondary" disabled={saving || !canCreate}>{saving ? "正在创建计划…" : "创建离线导入计划"}</button></div>
      </form>}
      <ul className="model-platform-offline-import__plans">{offlinePlans.length ? offlinePlans.map((plan) => <li key={plan.id}><div><strong>{plan.release_code}</strong><small>{plan.target_library_label} · {plan.expected_artifact_count} 个组件 · {formatBytes(plan.expected_total_bytes)}</small></div><span className="status-pill">{plan.status === "AWAITING_OFFLINE_IMPORT" ? "等待 Host 导入" : plan.status}</span></li>) : <li className="muted">尚无离线导入计划。</li>}</ul>
    </>}
  </section>;
}

type TrustedArtifactDraft = OfflineArtifactDraft & { source_url: string };
const EMPTY_TRUSTED_ARTIFACT: TrustedArtifactDraft = { ...EMPTY_OFFLINE_ARTIFACT, source_url: "" };

function TrustedDownloadPlanPanel({ enabled, targets, plans, targetsError, plansError, saving, onCreate }: {
  enabled: boolean;
  targets?: ModelPlatformInstallationTarget[];
  plans?: ModelPlatformInstallationPlan[];
  targetsError?: Error | null;
  plansError?: Error | null;
  saving: boolean;
  onCreate: (input: ModelPlatformTrustedDownloadPlanInput) => void;
}) {
  const [targetLibraryId, setTargetLibraryId] = useState("");
  const [releaseCode, setReleaseCode] = useState("");
  const [bundleReference, setBundleReference] = useState("");
  const [licenseId, setLicenseId] = useState("");
  const [artifacts, setArtifacts] = useState<TrustedArtifactDraft[]>([{ ...EMPTY_TRUSTED_ARTIFACT }]);
  useEffect(() => {
    if (!targetLibraryId && targets?.[0]) setTargetLibraryId(targets[0].id);
  }, [targetLibraryId, targets]);
  const updateArtifact = (index: number, patch: Partial<TrustedArtifactDraft>) => setArtifacts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  const validArtifacts = artifacts.every((item) => item.relative_path.trim() && /^https:\/\/.+/.test(item.source_url.trim()) && /^[a-fA-F0-9]{64}$/.test(item.sha256.trim()) && Number.isSafeInteger(Number(item.size_bytes)) && Number(item.size_bytes) > 0);
  const canCreate = enabled && Boolean(targetLibraryId && releaseCode.trim() && /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(bundleReference.trim()) && licenseId.trim() && validArtifacts);
  const trustedPlans = plans?.filter((plan) => plan.source_kind === "TRUSTED_HTTPS") ?? [];
  return <section className="model-platform-offline-import model-platform-trusted-download" aria-labelledby="model-platform-trusted-download-title">
    <div><p className="eyebrow">受信在线下载</p><h4 id="model-platform-trusted-download-title">先冻结来源与校验，再由 Host 停机下载</h4><p>页面只保存一次性 HTTPS 下载合同；服务端会核对机器允许的主机。提交不会联网，成功后不回显来源 URL，Windows Host 负责下载到隔离区并推进到 staging。</p></div>
    {!enabled ? <div className="model-platform-migration__empty"><strong>在线下载默认关闭</strong><span>请由 Windows Host 管理员在机器配置中登记可信 HTTPS 主机；页面不会显示或修改该列表。</span></div> : targetsError || plansError ? <p className="inline-error" role="alert">无法读取在线下载计划状态：{String(targetsError ?? plansError)}</p> : <>
      {!targets ? <p className="muted">正在读取可写入的 V2 模型库…</p> : targets.length === 0 ? <div className="model-platform-migration__empty"><strong>尚无可写入的 V2 模型库</strong><span>请先通过受控扫描登记模型库；页面不会要求或显示服务器目录。</span></div> : <form onSubmit={(event) => {
        event.preventDefault();
        if (!canCreate) return;
        onCreate({ target_library_id: targetLibraryId, release_code: releaseCode.trim(), bundle_reference: bundleReference.trim(), license_id: licenseId.trim(), artifacts: artifacts.map((item) => ({ relative_path: item.relative_path.trim(), source_url: item.source_url.trim(), sha256: item.sha256.trim(), size_bytes: Number(item.size_bytes) })) });
      }}>
        <label>目标模型库<select value={targetLibraryId} disabled={saving} onChange={(event) => setTargetLibraryId(event.target.value)}>{targets.map((target) => <option key={target.id} value={target.id}>{target.label}</option>)}</select></label>
        <label>模型发布标识<input value={releaseCode} disabled={saving} maxLength={140} onChange={(event) => setReleaseCode(event.target.value)} placeholder="例如 qwen3-embedding-8b" /></label>
        <label>下载包标识<input value={bundleReference} disabled={saving} maxLength={120} onChange={(event) => setBundleReference(event.target.value)} placeholder="只允许字母、数字、点、短横线和下划线" /></label>
        <label>许可证标识<input value={licenseId} disabled={saving} maxLength={200} onChange={(event) => setLicenseId(event.target.value)} placeholder="例如 apache-2.0" /></label>
        <div className="model-platform-offline-import__artifacts"><strong>预期下载组件</strong><span>每个组件均需填写可信 HTTPS 来源、库内相对路径、SHA-256 和字节数。浏览器永远不会执行下载。</span>{artifacts.map((artifact, index) => <div key={index} className="model-platform-offline-import__artifact model-platform-trusted-download__artifact"><input aria-label={`在线组件 ${index + 1} 路径`} value={artifact.relative_path} disabled={saving} onChange={(event) => updateArtifact(index, { relative_path: event.target.value })} placeholder="Embedding/model.safetensors" /><input aria-label={`在线组件 ${index + 1} 来源`} type="url" value={artifact.source_url} disabled={saving} onChange={(event) => updateArtifact(index, { source_url: event.target.value })} placeholder="https://trusted-host/releases/model.safetensors" /><input aria-label={`在线组件 ${index + 1} SHA-256`} value={artifact.sha256} disabled={saving} onChange={(event) => updateArtifact(index, { sha256: event.target.value })} placeholder="64 位 SHA-256" /><input aria-label={`在线组件 ${index + 1} 大小`} type="number" min="1" value={artifact.size_bytes} disabled={saving} onChange={(event) => updateArtifact(index, { size_bytes: event.target.value })} placeholder="字节数" />{artifacts.length > 1 ? <button type="button" className="text-action" disabled={saving} onClick={() => setArtifacts((current) => current.filter((_, itemIndex) => itemIndex !== index))}>移除</button> : null}</div>)}</div>
        <div className="model-platform-offline-import__actions"><button type="button" className="secondary" disabled={saving || artifacts.length >= 100} onClick={() => setArtifacts((current) => [...current, { ...EMPTY_TRUSTED_ARTIFACT }])}>添加组件</button><button type="submit" className="secondary" disabled={saving || !canCreate}>{saving ? "正在创建计划…" : "创建可信下载计划"}</button></div>
      </form>}
      <ul className="model-platform-offline-import__plans">{trustedPlans.length ? trustedPlans.map((plan) => <li key={plan.id}><div><strong>{plan.release_code}</strong><small>{plan.target_library_label} · {plan.expected_artifact_count} 个组件 · {formatBytes(plan.expected_total_bytes)}</small></div><span className="status-pill">{plan.status === "AWAITING_TRUSTED_DOWNLOAD" ? "等待 Host 下载" : plan.status === "AWAITING_OFFLINE_IMPORT" ? "等待 Host 导入" : plan.status}</span></li>) : <li className="muted">尚无可信在线下载计划。</li>}</ul>
    </>}
  </section>;
}

function BusinessMigrationPanel({ rollouts, error }: { rollouts?: ModelPlatformBusinessSelectionRollout[]; error?: Error | null }) {
  return <section className="model-platform-migration" aria-labelledby="model-platform-migration-title">
    <div>
      <p className="eyebrow">业务迁移门禁</p>
      <h4 id="model-platform-migration-title">Profile 等价不等于已经切换</h4>
      <p>这里记录的是进入 V2 集中 Facade 验收的审批资格。它不会自行改变创作页面、旧偏好或 Worker；每次实际解析仍须复核 crosswalk、参数合同、运行时就绪度和精确 Handler。</p>
    </div>
    {error ? <p className="inline-error" role="alert">无法读取业务迁移门禁：{String(error)}</p> : rollouts?.length ? <ul>
      {rollouts.map((item) => <li key={item.id ?? `${item.business_surface}:${item.capability_code}:${item.scope_type}`}>
        <div><strong>{item.business_surface} · {item.capability_code}</strong><small>{item.scope_type} 继承层级 · {item.approved_by ?? "未记录操作人"}</small></div>
        <span className="status-pill">{item.state === "CUTOVER_APPROVED" ? "已批准进入切换验收" : "仅影子对账"}</span>
      </li>)}
    </ul> : <div className="model-platform-migration__empty"><strong>尚未批准任何业务切换</strong><span>所有创作业务继续使用旧解析链；V2 只用于扫描、验证、Profile 生命周期和只读对账。</span></div>}
  </section>;
}

function SystemOverrideField({ field, value, onChange, disabled }: { field: ModelPlatformSystemOverrideField; value: string | number | boolean | undefined; onChange: (value: string | number | boolean | undefined) => void; disabled: boolean }) {
  const schema = field.schema;
  const current = value ?? schema.default;
  const common = { disabled, "aria-label": field.label };
  const setText = (next: string) => onChange(next === "" ? undefined : next);
  return <label className="model-platform-system-assignment__field">
    <span>{field.label}</span>
    {schema.enum?.length ? <select {...common} value={String(current ?? "")} onChange={(event) => {
      const option = schema.enum!.find((item) => String(item) === event.target.value);
      onChange(option);
    }}><option value="">使用 Profile 默认值</option>{schema.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select>
      : schema.type === "boolean" ? <input {...common} type="checkbox" checked={current === true} onChange={(event) => onChange(event.target.checked)} />
      : schema.type === "integer" || schema.type === "number" ? <input {...common} type="number" value={typeof current === "number" ? current : ""} min={schema.minimum} max={schema.maximum} step={schema.multipleOf ?? (schema.type === "integer" ? 1 : "any")} onChange={(event) => {
        const next = event.target.value;
        onChange(next === "" ? undefined : schema.type === "integer" ? Number.parseInt(next, 10) : Number(next));
      }} />
      : <input {...common} type="text" value={typeof current === "string" ? current : ""} maxLength={schema.maxLength} pattern={schema.pattern} onChange={(event) => setText(event.target.value)} />}
    {value !== undefined ? <button type="button" className="text-action" disabled={disabled} onClick={() => onChange(undefined)}>使用 Profile 默认值</button> : null}
    {field.help ? <small>{field.help}</small> : null}
  </label>;
}

function SystemCapabilityAssignmentEditor({ item, saving, onSave }: { item: ModelPlatformSystemCapabilityAssignment; saving: boolean; onSave: (payload: ModelPlatformCapabilityAssignmentPut) => void }) {
  const [mode, setMode] = useState<"AUTO" | "EXPLICIT">(item.assignment.resolution_mode);
  const [profileVersionId, setProfileVersionId] = useState(item.assignment.execution_profile_version_id ?? "");
  const [overrides, setOverrides] = useState<Record<string, string | number | boolean>>(item.assignment.overrides);
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState("local-user");
  useEffect(() => {
    setMode(item.assignment.resolution_mode);
    setProfileVersionId(item.assignment.execution_profile_version_id ?? "");
    setOverrides(item.assignment.overrides);
  }, [item.assignment.execution_profile_version_id, item.assignment.overrides, item.assignment.resolution_mode, item.assignment.revision]);
  const selected = item.profiles.find((profile) => profile.profile_version_id === profileVersionId) ?? null;
  const editableFields = selected?.system_override_fields ?? [];
  const setOverride = (name: string, value: string | number | boolean | undefined) => setOverrides((current) => {
    const next = { ...current };
    if (value === undefined) delete next[name]; else next[name] = value;
    return next;
  });
  const canSave = mode === "AUTO" || Boolean(profileVersionId && (Object.keys(overrides).length === 0 || (reason.trim() && actor.trim())));
  return <article className="model-platform-system-assignment">
    <header>
      <div><strong>{item.title}</strong><small>{item.capability_code} · {item.background_only ? "后台能力" : "创作能力"}</small></div>
      <span className="status-pill">{item.assignment.revision ? `当前第 ${item.assignment.revision} 版` : "继承系统自动值"}</span>
    </header>
    {item.assignment.has_unrenderable_override ? <p className="inline-error" role="alert">当前 Assignment 含不能安全显示的历史参数，已禁止在此覆盖；请先由管理员审计该 Profile 合同。</p> : null}
    <fieldset className="model-platform-system-assignment__mode">
      <legend>系统选择方式</legend>
      <label><input type="radio" checked={mode === "AUTO"} disabled={saving} onChange={() => { setMode("AUTO"); setOverrides({}); }} /> 自动选择已发布 Profile</label>
      <label><input type="radio" checked={mode === "EXPLICIT"} disabled={saving || item.profiles.length === 0} onChange={() => setMode("EXPLICIT")} /> 固定已发布 Profile</label>
    </fieldset>
    {mode === "EXPLICIT" ? <>
      <label className="model-platform-system-assignment__profile">已发布 Profile<select value={profileVersionId} disabled={saving} onChange={(event) => { setProfileVersionId(event.target.value); setOverrides({}); }}><option value="">选择 Profile</option>{item.profiles.map((profile) => <option key={profile.profile_version_id} value={profile.profile_version_id}>{profile.profile_title} · v{profile.version_no}</option>)}</select></label>
      {editableFields.length ? <div className="model-platform-system-assignment__fields"><strong>允许的系统范围参数</strong><span>未填写的字段继续使用 Profile 默认值。</span>{editableFields.map((field) => <SystemOverrideField key={field.name} field={field} value={overrides[field.name]} disabled={saving} onChange={(value) => setOverride(field.name, value)} />)}</div> : <p className="muted">此 Profile 没有允许在 SYSTEM 范围覆盖的参数。</p>}
      {Object.keys(overrides).length ? <div className="model-platform-system-assignment__audit"><label>变更理由<textarea value={reason} onChange={(event) => setReason(event.target.value)} disabled={saving} maxLength={1000} placeholder="说明为何修改系统范围参数" /></label><label>操作人<input value={actor} onChange={(event) => setActor(event.target.value)} disabled={saving} maxLength={120} /></label></div> : null}
    </> : <p className="muted">AUTO 不保存 Profile 私有参数。解析时由 V2 从当前已发布 Profile 中选择；旧创作任务不会因此切换。</p>}
    <button type="button" className="secondary" disabled={saving || !canSave || item.assignment.has_unrenderable_override} onClick={() => onSave({ scope_type: "SYSTEM", scope_id: "", capability_code: item.capability_code, resolution_mode: mode, execution_profile_version_id: mode === "EXPLICIT" ? profileVersionId : null, overrides: mode === "EXPLICIT" ? overrides : {}, reason, actor })}>{saving ? "正在保存…" : "保存系统能力设置"}</button>
  </article>;
}

function SystemCapabilityAssignmentsPanel({ items, error, saving, onSave }: { items?: ModelPlatformSystemCapabilityAssignment[]; error?: Error | null; saving: boolean; onSave: (payload: ModelPlatformCapabilityAssignmentPut) => void }) {
  const configurable = items?.filter((item) => item.profiles.length > 0) ?? [];
  return <section className="model-platform-system-assignments" aria-labelledby="model-platform-system-assignments-title">
    <div><p className="eyebrow">系统能力配置</p><h4 id="model-platform-system-assignments-title">为 V2 设置默认 Profile 与受控参数</h4><p>这里仅写入 V2 SYSTEM Assignment。参数必须是 Profile 明确允许、非敏感且适用于 SYSTEM 的字段；保存参数时会创建不可变参数版本并记录审计。它不会改写旧生成偏好。</p></div>
    {error ? <p className="inline-error" role="alert">无法读取系统能力配置：{String(error)}</p> : !items ? <p className="muted">正在读取可配置的已发布 Profile…</p> : configurable.length ? <div>{configurable.map((item) => <SystemCapabilityAssignmentEditor key={item.capability_code} item={item} saving={saving} onSave={onSave} />)}</div> : <div className="model-platform-migration__empty"><strong>尚无可配置的已发布 V2 Profile</strong><span>先完成候选登记、能力 smoke、Profile smoke 与发布。旧页面配置不会自动成为 V2 Assignment。</span></div>}
  </section>;
}

function runtimeLabel(kind: string) {
  return ({ OLLAMA: "Ollama", COMFYUI: "ComfyUI", PYTORCH_PROCESS: "PyTorch", OS_NATIVE: "Windows 原生" })[kind] ?? kind;
}

function presenceLabel(presence: string) {
  return ({ PRESENT: "文件/标签已发现", MISSING: "资源缺失", INCOMPLETE: "资源不完整" })[presence] ?? presence;
}

function readinessLabel(status: string) {
  return ({
    ASSIGNABLE: "可分配",
    PARTIALLY_ASSIGNABLE: "部分可分配",
    VALIDATION_REQUIRED: "需要验证",
    PROFILE_PUBLICATION_REQUIRED: "需要发布 Profile",
    INSTALLATION_VERIFICATION_REQUIRED: "需要安装验证",
    CAPABILITY_MAPPING_REQUIRED: "需要能力映射",
    PROFILE_REQUIRED: "需要 Profile",
  })[status] ?? status;
}

function blockerLabel(blocker: string) {
  return ({
    CAPABILITY_SMOKE_NOT_PASSED: "能力冒烟未通过",
    PROFILE_REQUIRED: "尚未创建 Profile",
    PROFILE_SMOKE_AND_PUBLICATION_REQUIRED: "Profile 冒烟与发布待完成",
    INSTALLATION_NOT_READY: "安装尚未验证为就绪",
    CAPABILITY_MAPPING_REQUIRED: "尚未映射业务能力",
  })[blocker] ?? blocker;
}

function integrityLabel(status: string) {
  return ({ PASSED: "完整性已确认", FAILED: "完整性验证失败", NOT_RUN: "完整性未验证" })[status] ?? status;
}

function validationKindLabel(kind: string) {
  return ({ INSTALLATION_INTEGRITY: "安装完整性", CAPABILITY_SMOKE: "能力冒烟", PROFILE_SMOKE: "Profile 冒烟" })[kind] ?? kind;
}

function validationStatusLabel(status: string) {
  return ({ INTEGRITY_PASSED: "通过", SMOKE_PASSED: "通过", FAILED: "失败" })[status] ?? status;
}

function formatBytes(value: number | null) {
  if (value === null || value < 0) return "大小未报告";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index < 2 ? 0 : 1)} ${units[index]}`;
}

function CandidateValidationHistory({ runtimeModelInstallationId }: { runtimeModelInstallationId: string }) {
  const [open, setOpen] = useState(false);
  const history = useQuery({
    queryKey: ["model-platform-v2", "validation-history", runtimeModelInstallationId],
    queryFn: () => listModelPlatformValidationHistory(runtimeModelInstallationId),
    enabled: open,
  });
  return <details className="model-platform-validation-history" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>查看验证记录</summary>
    <p className="muted">只显示验证类型、结果和时间；模型路径、运行时端点、密钥及原始证据不会发送到浏览器。</p>
    {history.isPending ? <p className="muted">正在读取验证记录…</p>
      : history.error ? <p className="inline-error" role="alert">无法读取验证记录：{String(history.error)}</p>
        : history.data?.items.length ? <ul>
          {history.data.items.map((item: ModelPlatformValidationHistoryItem) => <li key={item.validation_run_id}>
            <strong>{validationKindLabel(item.validation_kind)}</strong>
            <span>{item.capability_code ? ` · ${item.capability_code}` : " · 安装范围"}</span>
            <span> · {validationStatusLabel(item.status)}</span>
            <small>{item.occurred_at}</small>
          </li>)}
        </ul> : <p className="muted">尚无已完成的验证记录。</p>}
  </details>;
}

function DiscoveryObservationList({ items, onRegister, registeringId }: { items: ModelPlatformDiscoveryObservation[]; onRegister: (observationId: string) => void; registeringId: string | null }) {
  return <section className="model-platform-discovery" aria-labelledby="model-platform-discovery-title">
    <div className="panel-heading">
      <div><p className="eyebrow">扫描证据</p><h4 id="model-platform-discovery-title">最近发现的本机模型</h4></div>
      <span className="status-pill">{items.length} 条观察</span>
    </div>
    <p className="muted">这是各运行时的只读扫描结果。发现资源后仍需完成完整性、冒烟验证和发布，才可能在业务页被选择。</p>
    {items.length === 0 ? <div className="model-platform-discovery-empty"><strong>尚未记录扫描结果</strong><span>接入并运行 Ollama、ComfyUI 或 PyTorch 的 V2 扫描后，模型会按其原生运行时显示在这里。</span></div> : <ul className="model-platform-discovery-list">
      {items.map((item) => <li key={item.id}>
        <div className="model-platform-discovery-list__identity"><strong>{item.native_id}</strong><small>{runtimeLabel(item.runtime_kind)} · {formatBytes(item.size_bytes)}</small></div>
        <div className="model-platform-discovery-list__facts"><span>{presenceLabel(item.presence)}</span>{item.candidate_capabilities.length > 0 ? <small>候选能力：{item.candidate_capabilities.join("、")}</small> : <small>尚未声明候选能力</small>}<button type="button" className="secondary" disabled={item.presence !== "PRESENT" || registeringId !== null} onClick={() => onRegister(item.id)}>{registeringId === item.id ? "正在登记…" : "登记候选"}</button></div>
      </li>)}
    </ul>}
  </section>;
}

function canSmokeCapability(candidate: ModelPlatformRegisteredCandidate, capabilityCode: string) {
  return (candidate.runtime_kind === "OLLAMA" && capabilityCode.startsWith("LLM_"))
    || (candidate.runtime_kind === "PYTORCH_PROCESS" && capabilityCode === "EMBEDDING_TEXT");
}

function canVerifyIntegrity(candidate: ModelPlatformRegisteredCandidate) {
  return (candidate.runtime_kind === "COMFYUI" || candidate.runtime_kind === "PYTORCH_PROCESS") && candidate.integrity_status !== "PASSED";
}

type ProfileAction = { profileVersionId: string; validationRunId?: string };

function RegisteredCandidateList({ items, onSmoke, smokingKey, onVerifyIntegrity, verifyingInstallationId, profileActions, onProvisionProfile, provisioningKey, onSmokeProfile, smokingProfileKey, onPublishProfile, publishingProfileKey, workflowBindingKey, workflowOptions, workflowOptionsLoading, workflowBindings, workflowBindingsLoading, workflowSelections, onOpenWorkflowBinding, onSelectWorkflow, onBindWorkflow, bindingWorkflowKey, onQueueComfySmoke, queueingComfySmokeKey }: { items: ModelPlatformRegisteredCandidate[]; onSmoke: (runtimeModelInstallationId: string, capabilityCode: string) => void; smokingKey: string | null; onVerifyIntegrity: (runtimeModelInstallationId: string) => void; verifyingInstallationId: string | null; profileActions: Record<string, ProfileAction>; onProvisionProfile: (runtimeModelInstallationId: string, capabilityCode: string, workflowBindingId?: string) => void; provisioningKey: string | null; onSmokeProfile: (key: string, profileVersionId: string) => void; smokingProfileKey: string | null; onPublishProfile: (key: string, profileVersionId: string, validationRunId: string) => void; publishingProfileKey: string | null; workflowBindingKey: string | null; workflowOptions: WorkflowVersionSummary[]; workflowOptionsLoading: boolean; workflowBindings: ModelPlatformComfyWorkflowBinding[]; workflowBindingsLoading: boolean; workflowSelections: Record<string, string>; onOpenWorkflowBinding: (key: string | null) => void; onSelectWorkflow: (key: string, workflowVersionId: string) => void; onBindWorkflow: (runtimeModelInstallationId: string, capabilityCode: string, workflowVersionId: string) => void; bindingWorkflowKey: string | null; onQueueComfySmoke: (runtimeModelInstallationId: string, capabilityCode: string, workflowBindingId: string) => void; queueingComfySmokeKey: string | null }) {
  return <section className="model-platform-candidates" aria-labelledby="model-platform-candidates-title">
    <div className="panel-heading">
      <div><p className="eyebrow">登记后的就绪度</p><h4 id="model-platform-candidates-title">已登记候选的能力门禁</h4></div>
      <span className="status-pill">{items.length} 个候选</span>
    </div>
    <p className="muted">按能力而非按文件判断可用性。安装验证和已发布 Profile 只让能力进入 V2 Assignment/预检；旧创作页面仍需完成业务级 crosswalk 与 Facade 切换验收。</p>
    {items.length === 0 ? <div className="model-platform-discovery-empty"><strong>尚无已登记候选</strong><span>先从扫描证据中登记一个存在的本机资源；登记不会自动使其可执行。</span></div> : <div className="model-platform-candidate-list">
      {items.map((candidate) => <article className="model-platform-candidate" key={candidate.runtime_model_installation_id}>
        <header>
          <div><strong>{candidate.model_title}</strong><small>{candidate.model_release_code} · {runtimeLabel(candidate.runtime_kind)} · 安装状态：{candidate.install_state} · {integrityLabel(candidate.integrity_status)}</small></div>
          <div className="model-platform-candidate__header-actions"><span className="status-pill">{readinessLabel(candidate.readiness_status)}</span>{canVerifyIntegrity(candidate) ? <button type="button" className="secondary" disabled={verifyingInstallationId !== null} onClick={() => onVerifyIntegrity(candidate.runtime_model_installation_id)}>{verifyingInstallationId === candidate.runtime_model_installation_id ? "正在验证完整性…" : "验证安装完整性"}</button> : null}</div>
        </header>
        <p>{candidate.assignable_capability_count > 0 ? `已有 ${candidate.assignable_capability_count} 项能力可由 V2 Assignment 分配；不会自动改变旧创作任务。` : "尚无可分配能力；请按下列门禁完成验证与发布。"}</p>
        {candidate.blockers.length > 0 ? <ul className="model-platform-candidate__blockers" aria-label={`${candidate.model_title} 的阻断项`}>
          {candidate.blockers.map((blocker) => <li key={blocker}>{blockerLabel(blocker)}</li>)}
        </ul> : null}
        <CandidateValidationHistory runtimeModelInstallationId={candidate.runtime_model_installation_id} />
        <ul className="model-platform-candidate__capabilities">
          {candidate.capabilities.map((capability) => {
            const key = `${candidate.runtime_model_installation_id}:${capability.code}`;
            const profileAction = profileActions[key];
            const bindingOpen = workflowBindingKey === key;
            const matchingWorkflows = workflowOptions.filter((workflow) => workflow.status === "PUBLISHED" && String(workflow.contract.capability ?? "").toUpperCase() === capability.code);
            const selectedBinding = workflowBindings.find((binding) => binding.workflow_version_id === workflowSelections[key]);
            const canBindWorkflow = candidate.runtime_kind === "COMFYUI" && candidate.integrity_status === "PASSED" && capability.workflow_schema_validated_count === 0;
            const canManageComfySmoke = candidate.runtime_kind === "COMFYUI" && candidate.integrity_status === "PASSED" && capability.workflow_schema_validated_count > 0 && capability.offering_validation_status !== "SMOKE_PASSED";
            const offeringReadyForProfile = candidate.install_state === "READY" && capability.offering_validation_status === "SMOKE_PASSED" && capability.published_profile_count === 0;
            const canProvision = canSmokeCapability(candidate, capability.code) && offeringReadyForProfile;
            const canProvisionComfy = candidate.runtime_kind === "COMFYUI" && candidate.integrity_status === "PASSED" && offeringReadyForProfile;
            const canOpenComfyProfileBinding = canProvisionComfy && !profileAction;
            return <li key={capability.code}>
            <div><strong>{capability.title}</strong><small>{capability.code} · 发现验证：{capability.offering_validation_status} · 工作流：{capability.workflow_schema_validated_count}/{capability.workflow_binding_count} 已验证 · Profile：{capability.published_profile_count}/{capability.profile_version_count} 已发布</small></div>
            <div className="model-platform-candidate__capability-action">
              <span>{readinessLabel(capability.readiness_status)}</span>
              {(canBindWorkflow || canManageComfySmoke || canOpenComfyProfileBinding) && !bindingOpen ? <button type="button" className="secondary" disabled={bindingWorkflowKey !== null} onClick={() => onOpenWorkflowBinding(key)}>{canBindWorkflow ? "绑定已发布工作流" : canOpenComfyProfileBinding ? "选择已冒烟工作流" : "继续 Comfy 冒烟"}</button> : null}
              {bindingOpen ? <div className="model-platform-workflow-binding">
                <label>已发布工作流<select aria-label={`${capability.title} 的已发布工作流`} value={workflowSelections[key] ?? ""} disabled={workflowOptionsLoading || workflowBindingsLoading || bindingWorkflowKey !== null} onChange={(event) => onSelectWorkflow(key, event.target.value)}><option value="">{workflowOptionsLoading || workflowBindingsLoading ? "正在读取工作流…" : matchingWorkflows.length ? "选择能力匹配的版本" : "没有能力匹配的已发布版本"}</option>{matchingWorkflows.map((workflow) => <option key={workflow.id} value={workflow.id}>{workflow.title} · v{workflow.version_no}</option>)}</select></label>
                <div><button type="button" className="secondary" onClick={() => onOpenWorkflowBinding(null)} disabled={bindingWorkflowKey !== null || queueingComfySmokeKey !== null || provisioningKey !== null}>取消</button>{selectedBinding ? canProvisionComfy ? <button type="button" className="secondary" disabled={!selectedBinding.capability_smoke_passed || provisioningKey !== null} onClick={() => onProvisionProfile(candidate.runtime_model_installation_id, capability.code, selectedBinding.id)}>{provisioningKey === key ? "正在创建 Profile…" : selectedBinding.capability_smoke_passed ? "创建标准 Profile" : "该工作流尚未通过真实冒烟"}</button> : <button type="button" className="secondary" disabled={queueingComfySmokeKey !== null} onClick={() => onQueueComfySmoke(candidate.runtime_model_installation_id, capability.code, selectedBinding.id)}>{queueingComfySmokeKey === key ? "正在排队…" : "排队真实 Comfy 冒烟"}</button> : <button type="button" className="secondary" disabled={!workflowSelections[key] || workflowOptionsLoading || workflowBindingsLoading || bindingWorkflowKey !== null} onClick={() => onBindWorkflow(candidate.runtime_model_installation_id, capability.code, workflowSelections[key])}>{bindingWorkflowKey === key ? "正在验证并绑定…" : "验证并绑定"}</button>}</div>
                <small>{selectedBinding ? canProvisionComfy ? selectedBinding.capability_smoke_passed ? "此 Profile 会冻结已产物验证的工作流绑定和输出合同。" : "请先为该工作流排队真实 Comfy 冒烟；其他工作流的通过记录不能替代它。" : "Worker 将执行冻结 smoke 合同；结果请在任务中心查看。" : "仅验证节点与输入 schema；不会执行图或生成产物。"}</small>
              </div> : null}
              {canSmokeCapability(candidate, capability.code) && capability.offering_validation_status !== "SMOKE_PASSED" ? <button type="button" className="secondary" disabled={smokingKey !== null} onClick={() => onSmoke(candidate.runtime_model_installation_id, capability.code)}>{smokingKey === key ? "正在冒烟验证…" : "运行能力冒烟"}</button> : null}
              {canProvision && !profileAction ? <button type="button" className="secondary" disabled={provisioningKey !== null} onClick={() => onProvisionProfile(candidate.runtime_model_installation_id, capability.code)}>{provisioningKey === key ? "正在创建 Profile…" : "创建标准 Profile"}</button> : null}
              {profileAction && !profileAction.validationRunId ? <button type="button" className="secondary" disabled={smokingProfileKey !== null} onClick={() => onSmokeProfile(key, profileAction.profileVersionId)}>{smokingProfileKey === key ? "正在验证 Profile…" : "运行 Profile smoke"}</button> : null}
              {profileAction?.validationRunId ? <button type="button" className="primary" disabled={publishingProfileKey !== null} onClick={() => onPublishProfile(key, profileAction.profileVersionId, profileAction.validationRunId!)}>{publishingProfileKey === key ? "正在发布…" : "发布为可选能力"}</button> : null}
            </div>
          </li>;
          })}
        </ul>
      </article>)}
    </div>}
  </section>;
}

/** V2 entry point. It never treats a scan candidate as an executable model. */
export function ModelPlatformCenter({ onOpenConnections }: { onOpenConnections: () => void }) {
  const queryClient = useQueryClient();
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [profileActions, setProfileActions] = useState<Record<string, ProfileAction>>({});
  const [workflowBindingKey, setWorkflowBindingKey] = useState<string | null>(null);
  const [workflowSelections, setWorkflowSelections] = useState<Record<string, string>>({});
  const overview = useQuery({
    queryKey: ["model-platform-v2", "overview"],
    queryFn: getModelPlatformOverview,
  });
  const capabilities = useQuery({
    queryKey: ["model-platform-v2", "capabilities"],
    queryFn: listModelPlatformCapabilities,
  });
  const discoveries = useQuery({
    queryKey: ["model-platform-v2", "discovery-observations"],
    queryFn: listModelPlatformDiscoveryObservations,
  });
  const candidates = useQuery({
    queryKey: ["model-platform-v2", "registered-candidates"],
    queryFn: listModelPlatformRegisteredCandidates,
  });
  const profiles = useQuery({
    queryKey: ["model-platform-v2", "profile-versions"],
    queryFn: listModelPlatformProfileVersions,
  });
  const storagePolicy = useQuery({
    queryKey: ["model-platform-v2", "storage-policy"],
    queryFn: getModelPlatformStoragePolicy,
  });
  const offlineImportTargets = useQuery({
    queryKey: ["model-platform-v2", "installation-targets"],
    queryFn: listModelPlatformInstallationTargets,
  });
  const offlineImportPlans = useQuery({
    queryKey: ["model-platform-v2", "installation-plans"],
    queryFn: listModelPlatformInstallationPlans,
  });
  const businessRollouts = useQuery({
    queryKey: ["model-platform-v2", "business-selection-rollouts"],
    queryFn: listModelPlatformBusinessSelectionRollouts,
  });
  const systemAssignments = useQuery({
    queryKey: ["model-platform-v2", "system-capability-assignments"],
    queryFn: listModelPlatformSystemCapabilityAssignments,
  });
  const workflows = useQuery({
    queryKey: ["model-platform-v2", "workflow-binding-options"],
    queryFn: () => listWorkflowVersions(),
    enabled: workflowBindingKey !== null,
  });
  const workflowBindingTarget = useMemo(() => {
    if (!workflowBindingKey) return null;
    const separator = workflowBindingKey.lastIndexOf(":");
    return separator > 0 ? { runtimeModelInstallationId: workflowBindingKey.slice(0, separator), capabilityCode: workflowBindingKey.slice(separator + 1) } : null;
  }, [workflowBindingKey]);
  const comfyBindings = useQuery({
    queryKey: ["model-platform-v2", "comfy-workflow-bindings", workflowBindingTarget?.runtimeModelInstallationId, workflowBindingTarget?.capabilityCode],
    queryFn: () => listModelPlatformComfyWorkflowBindings(workflowBindingTarget!.runtimeModelInstallationId, workflowBindingTarget!.capabilityCode),
    enabled: workflowBindingTarget !== null,
  });
  const refreshDiscovery = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "overview"] }),
      queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "discovery-observations"] }),
      queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "registered-candidates"] }),
      queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "profile-versions"] }),
    ]);
  };
  const ollamaScan = useMutation({
    mutationFn: runModelPlatformOllamaDiscovery,
    onSuccess: async ({ discovery_run: run }) => {
      setScanMessage(`Ollama 扫描完成：发现 ${run.observation_count} 条记录。`);
      await refreshDiscovery();
    },
  });
  const modelLockScan = useMutation({
    mutationFn: runModelPlatformModelLockDiscovery,
    onSuccess: async ({ discovery_runs: runs }) => {
      const observationCount = runs.reduce((sum, run) => sum + run.observation_count, 0);
      setScanMessage(`ComfyUI / PyTorch 扫描完成：发现 ${observationCount} 条记录。`);
      await refreshDiscovery();
    },
  });
  const offlineImportPlan = useMutation({
    mutationFn: createModelPlatformOfflineInstallationPlan,
    onSuccess: async ({ plan }) => {
      setScanMessage(`离线导入计划已创建：${plan.release_code}。请停止 Host 后由服务账户从 staging 执行导入。`);
      await queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "installation-plans"] });
    },
  });
  const trustedDownloadPlan = useMutation({
    mutationFn: createModelPlatformTrustedDownloadPlan,
    onSuccess: async ({ plan }) => {
      setScanMessage(`可信下载计划已创建：${plan.release_code}。请停止 Host 后由服务账户下载到 staging，再执行离线导入。`);
      await queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "installation-plans"] });
    },
  });
  const candidateRegistration = useMutation({
    mutationFn: registerModelPlatformDiscoveryObservation,
    onSuccess: async ({ candidate }) => {
      setScanMessage(candidate.created ? `已登记候选模型：${candidate.model_release_code}。下一步需要验证与发布。` : `该发现记录已登记为候选模型：${candidate.model_release_code}。`);
      await refreshDiscovery();
      await queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "overview"] });
    },
  });
  const candidateSmoke = useMutation({
    mutationFn: ({ runtimeModelInstallationId, capabilityCode }: { runtimeModelInstallationId: string; capabilityCode: string }) => smokeModelPlatformCapabilityOffering(runtimeModelInstallationId, capabilityCode),
    onSuccess: async ({ validation }) => {
      setScanMessage(validation.status === "SMOKE_PASSED"
        ? `${validation.capability_code} 能力冒烟通过${validation.runtime_active ? "，运行时已激活，可创建标准 Profile。" : validation.installation_ready ? "，该模型安装已就绪。" : "，其余声明能力仍需验证。"}`
        : `${validation.capability_code} 能力冒烟未通过，请查看运行时与模型日志。`);
      await queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "registered-candidates"] });
      await queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "profile-versions"] });
    },
  });
  const profileProvision = useMutation({
    mutationFn: ({ runtimeModelInstallationId, capabilityCode, workflowBindingId }: { runtimeModelInstallationId: string; capabilityCode: string; workflowBindingId?: string }) => provisionModelPlatformProfile(runtimeModelInstallationId, capabilityCode, workflowBindingId),
    onSuccess: async ({ profile }, variables) => {
      const key = `${variables.runtimeModelInstallationId}:${variables.capabilityCode}`;
      setProfileActions((current) => ({ ...current, [key]: { profileVersionId: profile.profile_version_id } }));
      setScanMessage(profile.created ? "标准 Profile 草稿已创建；请运行 Profile smoke 后再显式发布。" : "已恢复已有的标准 Profile 草稿；请继续运行 Profile smoke。");
      await queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "registered-candidates"] });
    },
  });
  const profileSmoke = useMutation({
    mutationFn: ({ key, profileVersionId }: { key: string; profileVersionId: string }) => smokeModelPlatformProfile(profileVersionId),
    onSuccess: ({ validation }, variables) => {
      setProfileActions((current) => ({ ...current, [variables.key]: { profileVersionId: validation.profile_version_id, validationRunId: validation.validation_run_id } }));
      setScanMessage(validation.status === "SMOKE_PASSED" ? "Profile smoke 已通过。发布前请确认它会成为系统范围内可选择的能力。" : "Profile smoke 未通过；该 Profile 不能发布。");
    },
  });
  const profilePublish = useMutation({
    mutationFn: ({ profileVersionId, validationRunId }: { key: string; profileVersionId: string; validationRunId: string }) => publishModelPlatformProfile(profileVersionId, validationRunId, "已在模型中心确认发布"),
    onSuccess: async (_result, variables) => {
      setProfileActions((current) => {
        const next = { ...current };
        delete next[variables.key];
        return next;
      });
      setScanMessage("Profile 已发布；它现在可由 V2 Assignment 解析。旧创作页面仍需通过 crosswalk、Facade 和业务级验收后才会切换。");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "overview"] }),
        queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "registered-candidates"] }),
        queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "profile-versions"] }),
      ]);
    },
  });
  const systemAssignmentSave = useMutation({
    mutationFn: putModelPlatformCapabilityAssignment,
    onSuccess: async () => {
      setScanMessage("V2 系统能力设置已保存；范围参数已版本化并写入审计。旧创作业务保持原链路，等待单独的迁移验收。");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "system-capability-assignments"] }),
        queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "business-selection-rollouts"] }),
      ]);
    },
  });
  const installationIntegrity = useMutation({
    mutationFn: verifyModelPlatformInstallationIntegrity,
    onSuccess: async ({ validation }) => {
      setScanMessage(validation.status === "INTEGRITY_PASSED"
        ? "模型组件完整性已确认；仍需完成各能力的专用 smoke 与 Profile 发布。"
        : "模型组件完整性验证未通过；请检查受控模型库和 model-lock。");
      await queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "registered-candidates"] });
    },
  });
  const comfyWorkflowBinding = useMutation({
    mutationFn: ({ runtimeModelInstallationId, capabilityCode, workflowVersionId }: { runtimeModelInstallationId: string; capabilityCode: string; workflowVersionId: string }) => bindModelPlatformComfyWorkflow(runtimeModelInstallationId, capabilityCode, workflowVersionId),
    onSuccess: async ({ workflow_binding }) => {
      const key = `${workflow_binding.runtime_model_installation_id}:${workflow_binding.capability_code}`;
      setWorkflowBindingKey(key);
      setWorkflowSelections((current) => ({ ...current, [key]: workflow_binding.workflow_version_id }));
      setScanMessage(`Comfy 工作流已完成 schema 验证并绑定：${workflow_binding.capability_code}。下一步仍需真实执行 smoke。`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "registered-candidates"] }),
        queryClient.invalidateQueries({ queryKey: ["model-platform-v2", "comfy-workflow-bindings"] }),
      ]);
    },
  });
  const comfySmokeSubmission = useMutation({
    mutationFn: ({ runtimeModelInstallationId, capabilityCode, workflowBindingId }: { runtimeModelInstallationId: string; capabilityCode: string; workflowBindingId: string }) => submitModelPlatformComfyCapabilitySmoke(runtimeModelInstallationId, capabilityCode, workflowBindingId, crypto.randomUUID()),
    onSuccess: ({ smoke_job }) => {
      setScanMessage(`Comfy 真实冒烟已进入 GPU 队列（任务 ${smoke_job.job_id}）。完成后刷新本页或在任务中心查看结果。`);
    },
  });
  const groupedCapabilities = useMemo(
    () => groupCapabilities(capabilities.data?.items ?? []),
    [capabilities.data?.items],
  );
  const persistedProfileActions = useMemo(() => profileActionsFromCatalog(profiles.data?.items ?? []), [profiles.data?.items]);
  const visibleProfileActions = useMemo(() => ({ ...persistedProfileActions, ...profileActions }), [persistedProfileActions, profileActions]);

  if (overview.isPending || capabilities.isPending || discoveries.isPending || candidates.isPending || profiles.isPending) {
    return <Skeleton label="正在读取模型平台控制面" lines={8} />;
  }
  if (overview.error || capabilities.error || discoveries.error || candidates.error || profiles.error) {
    const error = overview.error ?? capabilities.error ?? discoveries.error ?? candidates.error ?? profiles.error;
    return <ErrorState
      title="模型平台控制面暂时无法读取"
      description={`未能读取能力或状态：${String(error)}。请确认本地 API 已升级后重试。`}
      onRetry={() => { void overview.refetch(); void capabilities.refetch(); void discoveries.refetch(); void candidates.refetch(); }}
    />;
  }

  const data = overview.data?.overview;
  if (!data) {
    return <ErrorState description="模型平台没有返回概览数据，请重试。" onRetry={() => void overview.refetch()} />;
  }

  return <section className="model-platform-center" aria-labelledby="model-platform-center-title">
    <div className="model-platform-center__intro">
      <div>
        <p className="eyebrow">V2 控制面</p>
        <h3 id="model-platform-center-title">能力优先的模型中心</h3>
        <p>模型包、运行时、执行 Profile 和项目选择分别管理。扫描只产生候选证据，不会自动发布或影响创作任务。</p>
      </div>
      <button type="button" className="secondary" onClick={onOpenConnections}>进入接入与验证</button>
    </div>

    <div className="model-platform-overview" aria-label="模型平台概览">
      <OverviewCard label="能力定义" value={data.capability_count} description="平台可支持的业务操作" />
      <OverviewCard label="扫描发现" value={data.discovery_observation_count} description="尚未等同于可用模型" />
      <OverviewCard label="已登记模型" value={data.registered_model_release_count} description="已进入模型版本目录" />
      <OverviewCard label="运行时安装" value={data.runtime_installation_count} description="已配置的执行环境" />
      <OverviewCard label="已发布 Profile" value={data.published_profile_count} description="可由 V2 Assignment 解析" />
    </div>

    <section className="model-platform-lifecycle" aria-labelledby="model-platform-lifecycle-title">
      <div>
        <p className="eyebrow">状态边界</p>
        <h4 id="model-platform-lifecycle-title">从本机文件到可执行能力</h4>
      </div>
      <ol>
        {LIFECYCLE_STEPS.map((step, index) => <li key={step}><span>{index + 1}</span>{step}</li>)}
      </ol>
      <p>仅“可执行”状态可由 V2 在项目、分集、角色或镜头范围解析；失败或缺失时将阻断而非静默降级，旧页面不会被自动替换。</p>
    </section>

    <ModelStoragePolicyPanel policy={storagePolicy.data?.storage_policy} error={storagePolicy.error} />

    <OfflineImportPlanPanel targets={offlineImportTargets.data?.items} plans={offlineImportPlans.data?.items} targetsError={offlineImportTargets.error} plansError={offlineImportPlans.error} saving={offlineImportPlan.isPending} onCreate={(input) => offlineImportPlan.mutate(input)} />

    <TrustedDownloadPlanPanel enabled={storagePolicy.data?.storage_policy.online_download_default_enabled === true} targets={offlineImportTargets.data?.items} plans={offlineImportPlans.data?.items} targetsError={offlineImportTargets.error} plansError={offlineImportPlans.error} saving={trustedDownloadPlan.isPending} onCreate={(input) => trustedDownloadPlan.mutate(input)} />

    <BusinessMigrationPanel rollouts={businessRollouts.data?.items} error={businessRollouts.error} />

    <SystemCapabilityAssignmentsPanel items={systemAssignments.data?.items} error={systemAssignments.error} saving={systemAssignmentSave.isPending} onSave={(payload) => systemAssignmentSave.mutate(payload)} />

    <section className="model-platform-discovery-actions" aria-label="模型扫描操作">
      <div><strong>以本机服务身份扫描</strong><span>扫描只更新候选证据；不会下载、删除、发布或切换模型。</span></div>
      <div className="model-platform-discovery-actions__controls">
        <button type="button" className="secondary" disabled={ollamaScan.isPending || modelLockScan.isPending} onClick={() => ollamaScan.mutate()}>扫描 Ollama</button>
        <button type="button" className="secondary" disabled={ollamaScan.isPending || modelLockScan.isPending} onClick={() => modelLockScan.mutate()}>扫描 ComfyUI / PyTorch</button>
      </div>
      {scanMessage ? <p role="status">{scanMessage}</p> : null}
      {ollamaScan.error || modelLockScan.error || offlineImportPlan.error || trustedDownloadPlan.error || candidateSmoke.error || installationIntegrity.error || comfyWorkflowBinding.error || comfySmokeSubmission.error || profileProvision.error || profileSmoke.error || profilePublish.error || systemAssignmentSave.error ? <p className="model-platform-discovery-actions__error" role="alert">操作失败：{String(ollamaScan.error ?? modelLockScan.error ?? offlineImportPlan.error ?? trustedDownloadPlan.error ?? candidateSmoke.error ?? installationIntegrity.error ?? comfyWorkflowBinding.error ?? comfySmokeSubmission.error ?? profileProvision.error ?? profileSmoke.error ?? profilePublish.error ?? systemAssignmentSave.error)}。请检查运行时接入、模型库、参数合同或所需 Adapter。</p> : null}
    </section>

    <DiscoveryObservationList items={discoveries.data?.items ?? []} onRegister={(observationId) => candidateRegistration.mutate(observationId)} registeringId={candidateRegistration.isPending ? candidateRegistration.variables ?? null : null} />

    <RegisteredCandidateList items={candidates.data?.items ?? []} onSmoke={(runtimeModelInstallationId, capabilityCode) => candidateSmoke.mutate({ runtimeModelInstallationId, capabilityCode })} smokingKey={candidateSmoke.isPending ? `${candidateSmoke.variables?.runtimeModelInstallationId}:${candidateSmoke.variables?.capabilityCode}` : null} onVerifyIntegrity={(runtimeModelInstallationId) => installationIntegrity.mutate(runtimeModelInstallationId)} verifyingInstallationId={installationIntegrity.isPending ? installationIntegrity.variables ?? null : null} profileActions={visibleProfileActions} onProvisionProfile={(runtimeModelInstallationId, capabilityCode, workflowBindingId) => profileProvision.mutate({ runtimeModelInstallationId, capabilityCode, workflowBindingId })} provisioningKey={profileProvision.isPending ? `${profileProvision.variables?.runtimeModelInstallationId}:${profileProvision.variables?.capabilityCode}` : null} onSmokeProfile={(key, profileVersionId) => profileSmoke.mutate({ key, profileVersionId })} smokingProfileKey={profileSmoke.isPending ? profileSmoke.variables?.key ?? null : null} onPublishProfile={(key, profileVersionId, validationRunId) => profilePublish.mutate({ key, profileVersionId, validationRunId })} publishingProfileKey={profilePublish.isPending ? profilePublish.variables?.key ?? null : null} workflowBindingKey={workflowBindingKey} workflowOptions={workflows.data?.items ?? []} workflowOptionsLoading={workflows.isFetching} workflowBindings={comfyBindings.data?.items ?? []} workflowBindingsLoading={comfyBindings.isFetching} workflowSelections={workflowSelections} onOpenWorkflowBinding={setWorkflowBindingKey} onSelectWorkflow={(key, workflowVersionId) => setWorkflowSelections((current) => ({ ...current, [key]: workflowVersionId }))} onBindWorkflow={(runtimeModelInstallationId, capabilityCode, workflowVersionId) => comfyWorkflowBinding.mutate({ runtimeModelInstallationId, capabilityCode, workflowVersionId })} bindingWorkflowKey={comfyWorkflowBinding.isPending ? `${comfyWorkflowBinding.variables?.runtimeModelInstallationId}:${comfyWorkflowBinding.variables?.capabilityCode}` : null} onQueueComfySmoke={(runtimeModelInstallationId, capabilityCode, workflowBindingId) => comfySmokeSubmission.mutate({ runtimeModelInstallationId, capabilityCode, workflowBindingId })} queueingComfySmokeKey={comfySmokeSubmission.isPending ? `${comfySmokeSubmission.variables?.runtimeModelInstallationId}:${comfySmokeSubmission.variables?.capabilityCode}` : null} />

    <section className="model-platform-capability-catalog" aria-labelledby="model-platform-capability-title">
      <div className="panel-heading">
        <div><p className="eyebrow">能力目录</p><h4 id="model-platform-capability-title">后续页面可选择的业务能力</h4></div>
        <span className="status-pill">{capabilities.data?.count ?? 0} 项定义</span>
      </div>
      <p className="muted">后台能力不会出现在创作器中；已发布 Profile 先由 V2 预检解析，旧创作器只在完成业务级迁移后才消费对应能力。</p>
      <div className="model-platform-capability-groups">
        {groupedCapabilities.map(([family, items]) => <section key={family} className="model-platform-capability-group" aria-label={FAMILY_TITLES[family] ?? family}>
          <header><h5>{FAMILY_TITLES[family] ?? family}</h5><span>{items.length}</span></header>
          <ul>
            {items.map((item) => <li key={item.code}>
              <div><strong>{item.title}</strong><small>{item.code}</small></div>
              <span>{item.background_only ? "后台能力" : "创作可选"}</span>
            </li>)}
          </ul>
        </section>)}
      </div>
    </section>
  </section>;
}

function profileActionsFromCatalog(items: ModelPlatformProfileLifecycle[]): Record<string, ProfileAction> {
  const result: Record<string, ProfileAction> = {};
  for (const item of items) {
    if (item.lifecycle_status === "PUBLISHED") continue;
    for (const installationId of item.runtime_model_installation_ids) {
      const key = `${installationId}:${item.capability_code}`;
      if (!result[key]) {
        result[key] = {
          profileVersionId: item.profile_version_id,
          validationRunId: item.lifecycle_status === "PROFILE_SMOKE_PASSED" ? item.latest_validation_run_id ?? undefined : undefined,
        };
      }
    }
  }
  return result;
}

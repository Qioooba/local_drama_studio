import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  deriveProfileContractVersion,
  getProfileVersion,
  publishProfileContractVersion,
  validateProfileContractVersion,
  publishWorkflowVersion,
  rollbackWorkflowVersion,
  revokeWorkflowVersion,
  validateWorkflowLocal,
  type Profile,
  type ProfileVersionDetail,
  type WorkflowVersionSummary,
  type WorkflowValidation,
} from "../../generated/api";

function parseObject(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error(`${label} 必须是有效 JSON。`); }
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(`${label} 必须是 JSON 对象。`);
  return parsed as Record<string, unknown>;
}

export function ProfileConfigurationPanel({ profiles, workflows, workflowsLoading, onChanged }: { profiles: Profile[]; workflows: WorkflowVersionSummary[]; workflowsLoading: boolean; onChanged: () => void }) {
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
  const [workflowValidations, setWorkflowValidations] = useState<Record<string, WorkflowValidation>>({});
  const [workflowRevokeReasons, setWorkflowRevokeReasons] = useState<Record<string, string>>({});
  const [workflowBusy, setWorkflowBusy] = useState<string | null>(null);
  useEffect(() => {
    if (!current) return;
    setInputJson(JSON.stringify(current.input_contract, null, 2));
    setParameterJson(JSON.stringify(current.parameter_schema, null, 2));
    setOutputJson(JSON.stringify(current.output_contract, null, 2));
    setResourceJson(JSON.stringify(current.resource_policy, null, 2));
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
  const validateWorkflow = async (workflow: WorkflowVersionSummary) => {
    setWorkflowBusy(`validate:${workflow.id}`); setFeedback(null);
    try { const result = await validateWorkflowLocal(workflow.id); setWorkflowValidations((current) => ({ ...current, [workflow.id]: result.validation })); setFeedback({ kind: result.validation.status === "PASS" ? "success" : "error", message: `${workflow.code} v${workflow.version_no} 本地工作流验证：${result.validation.status}` }); }
    catch (error) { setFeedback({ kind: "error", message: `工作流验证失败：${String(error)}` }); }
    finally { setWorkflowBusy(null); }
  };
  const publishWorkflow = async (workflow: WorkflowVersionSummary) => {
    const validation = workflowValidations[workflow.id];
    if (!validation || validation.status !== "PASS") return;
    setWorkflowBusy(`publish:${workflow.id}`); setFeedback(null);
    try { await publishWorkflowVersion(workflow.id, validation.id); setFeedback({ kind: "success", message: `${workflow.code} v${workflow.version_no} 已发布` }); onChanged(); }
    catch (error) { setFeedback({ kind: "error", message: `工作流发布失败：${String(error)}` }); }
    finally { setWorkflowBusy(null); }
  };
  const revokeWorkflow = async (workflow: WorkflowVersionSummary) => {
    const reason = workflowRevokeReasons[workflow.id]?.trim() ?? "";
    if (!reason) { setFeedback({ kind: "error", message: "撤销 workflow 必须填写原因。" }); return; }
    setWorkflowBusy(`revoke:${workflow.id}`); setFeedback(null);
    try { await revokeWorkflowVersion(workflow.id, reason); setFeedback({ kind: "success", message: `${workflow.code} v${workflow.version_no} 已撤销` }); onChanged(); }
    catch (error) { setFeedback({ kind: "error", message: `工作流撤销失败：${String(error)}` }); }
    finally { setWorkflowBusy(null); }
  };
  const rollbackWorkflow = async (workflow: WorkflowVersionSummary) => {
    const validation = workflowValidations[workflow.id];
    if (!validation || validation.status !== "PASS") { setFeedback({ kind: "error", message: "回滚前必须对目标历史版本重新执行本地验证。" }); return; }
    setWorkflowBusy(`rollback:${workflow.id}`); setFeedback(null);
    try { await rollbackWorkflowVersion(workflow.id, validation.id); setFeedback({ kind: "success", message: `${workflow.code} v${workflow.version_no} 已回滚并重新发布` }); onChanged(); }
    catch (error) { setFeedback({ kind: "error", message: `工作流回滚失败：${String(error)}` }); }
    finally { setWorkflowBusy(null); }
  };
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
    {workflowsLoading ? <p className="empty-state">正在读取本地工作流版本…</p> : <div className="workflow-history">{workflows.map((workflow) => { const validation = workflowValidations[workflow.id]; const revokeReason = workflowRevokeReasons[workflow.id] ?? ""; return <article className="workflow-version" key={workflow.id}><div><strong>{workflow.code}</strong><small>v{workflow.version_no} · {String(workflow.contract.capability ?? "未声明 capability")}</small></div><span className={`status-pill${workflow.status === "PUBLISHED" ? "" : " neutral"}`}>{workflow.status}</span><code>{workflow.content_hash.slice(0, 12)}</code><small>{workflow.published_at ? `发布于 ${new Date(workflow.published_at).toLocaleString()}` : "尚未发布；验证、发布与回滚均需显式操作。"}</small><div className="workflow-actions"><button className="secondary" onClick={() => void validateWorkflow(workflow)} disabled={workflowBusy !== null}>{workflowBusy === `validate:${workflow.id}` ? "验证中…" : "本地验证"}</button>{validation?.status === "PASS" && workflow.status !== "PUBLISHED" && workflow.status !== "RETIRED" && <button className="primary-action" onClick={() => void publishWorkflow(workflow)} disabled={workflowBusy !== null}>{workflowBusy === `publish:${workflow.id}` ? "发布中…" : "发布"}</button>}{workflow.status === "RETIRED" && validation?.status === "PASS" && <button className="primary-action" onClick={() => void rollbackWorkflow(workflow)} disabled={workflowBusy !== null}>{workflowBusy === `rollback:${workflow.id}` ? "回滚中…" : "验证后回滚"}</button>}{workflow.status === "PUBLISHED" && <><input aria-label={`撤销原因 ${workflow.code} v${workflow.version_no}`} placeholder="撤销原因（必填）" value={revokeReason} onChange={(event) => setWorkflowRevokeReasons((current) => ({ ...current, [workflow.id]: event.target.value }))} /><button className="secondary" onClick={() => void revokeWorkflow(workflow)} disabled={workflowBusy !== null || !revokeReason.trim()}>{workflowBusy === `revoke:${workflow.id}` ? "撤销中…" : "撤销"}</button></>}</div>{validation && <small className={validation.status === "PASS" ? "ok-text" : "blocker-text"}>最近验证：{validation.status} · {validation.id.slice(0, 12)}</small>}</article>; })}</div>}
  </section>;
}

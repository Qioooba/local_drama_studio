import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  deriveProfileContractVersion,
  getProfileVersion,
  publishProfileContractVersion,
  publishWorkflowVersion,
  revokeWorkflowVersion,
  rollbackWorkflowVersion,
  validateProfileContractVersion,
  validateWorkflowLocal,
  type Profile,
  type ProfileVersionDetail,
  type WorkflowValidation,
  type WorkflowVersionSummary,
} from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import "./profile-configuration.css";

type ProfileConfigurationPanelProps =
  | {
      mode: "profile-contracts";
      profiles: Profile[];
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
    return <ProfileContractsTask profiles={props.profiles} onChanged={props.onChanged} />;
  }
  return (
    <WorkflowVersionsTask
      workflows={props.workflows}
      workflowsLoading={props.workflowsLoading}
      onChanged={props.onChanged}
    />
  );
}

function ProfileContractsTask({ profiles, onChanged }: { profiles: Profile[]; onChanged: () => void }) {
  const preferredId = profiles.find((item) => item.status === "PUBLISHED")?.version_id ?? profiles[0]?.version_id ?? null;
  const [selectedId, setSelectedId] = useState<string | null>(preferredId);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [inputJson, setInputJson] = useState("{}");
  const [parameterJson, setParameterJson] = useState("{}");
  const [outputJson, setOutputJson] = useState("{}");
  const [resourceJson, setResourceJson] = useState("{}");
  const [feedback, setFeedback] = useState<Feedback>(null);

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
      void activeDetail.refetch();
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

  const isDraft = current?.status === "DRAFT";

  return (
    <section className="panel profile-configuration-panel" aria-labelledby="profile-contracts-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">G7 能力配置</p>
          <h3 id="profile-contracts-title">本地能力契约与不可变版本</h3>
        </div>
        <span className="status-pill">仅本地</span>
      </div>
      <p className="muted">
        编辑只会派生新 DRAFT；本地验证不会连接 ComfyUI。执行指纹有变化时，发布必须提供真实成功媒体证据。
      </p>

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
          ) : detail.error || activeDetail.error ? (
            <div className="inline-error" role="alert">
              Profile 契约读取失败：{String(detail.error ?? activeDetail.error)}
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
              </div>

              <div className="profile-contract-fields">
                <label>
                  输入契约
                  <textarea value={inputJson} onChange={(event) => setInputJson(event.target.value)} spellCheck={false} />
                  <small>声明 transport 与语义输入槽；只允许本地 transport。</small>
                </label>
                <label>
                  参数 Schema
                  <textarea value={parameterJson} onChange={(event) => setParameterJson(event.target.value)} spellCheck={false} />
                  <small>必须明确 seed 与 determinism。</small>
                </label>
                <label>
                  输出契约
                  <textarea value={outputJson} onChange={(event) => setOutputJson(event.target.value)} spellCheck={false} />
                  <small>必须声明 media_kind；容器、编码按能力补充。</small>
                </label>
                <label>
                  资源策略
                  <textarea value={resourceJson} onChange={(event) => setResourceJson(event.target.value)} spellCheck={false} />
                  <small>GPU heavy 并发必须为 1。</small>
                </label>
              </div>

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
      setValidations((current) => ({ ...current, [workflow.id]: result.validation }));
      setFeedback({
        kind: result.validation.status === "PASS" ? "success" : "error",
        message: `${workflow.code} v${workflow.version_no} 本地工作流验证：${result.validation.status}`,
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
          <p className="eyebrow">G7 工作流历史</p>
          <h3 id="workflow-versions-title">工作流版本、验证与发布证据</h3>
        </div>
        <span className="status-pill neutral">本地验证 · 显式变更</span>
      </div>
      <p className="muted">
        这里操作同一套权威 Workflow Version API。本地验证不会连接 ComfyUI；发布、撤销和回滚都必须由用户显式触发，并保留对应验证或原因。
      </p>

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

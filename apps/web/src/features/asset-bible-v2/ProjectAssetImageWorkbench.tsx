import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { AssetBibleItem } from "./api";
import {
  findAssetImageBatchByCommandKey,
  isAssetImageBatchActive,
  planAssetImageBatch,
  submitAssetImageBatch,
  type AssetImageBatch,
  type AssetImageBatchPlan,
  type AssetImageKind,
} from "./assetImageBatchClient";
import {
  classifyAssetSubmitError,
  errorText,
  freezeAssetCommand,
  loadPendingAssetCommands,
  removePendingAssetCommand,
  savePendingAssetCommand,
  type FrozenAssetCommand,
  type FrozenAssetCommandRequest,
} from "./assetImageCommands";
import "./asset-image-batch.css";

const KINDS: AssetImageKind[] = ["CHARACTER", "SCENE", "PROP"];
const LABELS: Record<string, string> = { CHARACTER: "人物", SCENE: "场景", PROP: "道具" };

function hasHero(item: AssetBibleItem): boolean {
  return Boolean(item.asset.canonical_media_version_id || item.base_references.some((reference) => reference.reference_kind === "HERO"));
}

function commandKey(kind: string): string {
  return globalThis.crypto?.randomUUID?.() ?? `project-assets-${kind}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

type PlannedGroup = { kind: AssetImageKind; ids: string[]; plan: AssetImageBatchPlan };
type GroupReceipt = PlannedGroup & {
  projectId: string;
  idempotencyKey: string;
  expectedPlanHash: string;
  frozenRequest: FrozenAssetCommandRequest;
  submittedAt: string;
  state: "PREVIEWED" | "SUBMITTING" | "ACCEPTED" | "REJECTED" | "UNKNOWN";
  batch?: AssetImageBatch;
  error?: string;
  existingBatchId?: string;
};

export function ProjectAssetImageWorkbench({ projectId, items, onChanged }: {
  projectId: string;
  items: AssetBibleItem[];
  onChanged: () => void | Promise<void>;
}) {
  const stats = useMemo(() => KINDS.map((kind) => {
    const active = items.filter((item) => item.asset.kind === kind && item.asset.status === "ACTIVE");
    const missing = active.filter((item) => !hasHero(item));
    return {
      kind,
      total: active.length,
      missing: missing.length,
      ids: missing.map((item) => item.asset.id),
    };
  }), [items]);
  const groups = stats.filter((group) => group.ids.length > 0);
  const [plans, setPlans] = useState<PlannedGroup[]>([]);
  const [receipts, setReceipts] = useState<GroupReceipt[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const inFlightRef = useRef(false);
  const receiptsRef = useRef(receipts);
  receiptsRef.current = receipts;

  // Reload pending UNKNOWN commands after a refresh. Never auto-submit;
  // the user explicitly triggers exact recovery per command.
  useEffect(() => {
    const { commands, readError } = loadPendingAssetCommands(projectId);
    if (readError) {
      setStorageError(`待确认记录读取失败：${readError}。请先修复记录/核对服务器，不要重复提交。`);
      return;
    }
    setStorageError(null);
    if (commands.length === 0) return;
    setReceipts((current) => {
      const known = new Set(current.map((item) => item.idempotencyKey));
      const restored: GroupReceipt[] = commands
        .filter((command) => !known.has(command.idempotencyKey))
        .map((command) => frozenToUnknownReceipt(projectId, command));
      return restored.length ? [...current, ...restored] : current;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const hasUnknown = receipts.some((item) => item.state === "UNKNOWN");
  const hasSubmitting = receipts.some((item) => item.state === "SUBMITTING");

  async function runWithRefresh(action: () => Promise<void>) {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setBusy(true);
    setRefreshWarning(null);
    try {
      await action();
    } catch (error) {
      // action() is expected to classify its own submit-stage errors into
      // receipts; an escape here is a programming error, surfaced visibly
      // without inventing a receipt.
      setMessage(`操作失败：${errorText(error)}`);
    } finally {
      try {
        await onChanged();
      } catch (error) {
        setRefreshWarning(`回执已保留，刷新失败：${errorText(error)}`);
      } finally {
        inFlightRef.current = false;
        setBusy(false);
      }
    }
  }

  const generate = () => runWithRefresh(async () => {
    setMessage("");
    setStorageError(null);
    if (hasUnknown || receiptsRef.current.some((item) => item.state === "UNKNOWN")) {
      setMessage("存在结果待确认的命令，请先逐条核对回执，再创建新生成意图。");
      return;
    }
    // Skip groups already covered by an accepted in-transit batch.
    const activeKinds = new Set(
      receiptsRef.current
        .filter((item) => item.state === "ACCEPTED" && isAssetImageBatchActive(item.batch))
        .map((item) => item.kind),
    );
    const pendingGroups = groups.filter((group) => !activeKinds.has(group.kind));
    if (pendingGroups.length === 0 && groups.length > 0) {
      setMessage("所选资产已有进行中的补齐任务，请查看批次进度，不要重复提交。");
      return;
    }
    const planned = await Promise.allSettled(pendingGroups.map(async (group) => ({
      ...group,
      plan: (await planAssetImageBatch(projectId, {
        asset_kind: group.kind,
        asset_ids: group.ids,
        profile_version_id: null,
        mode: "MISSING_ONLY",
      })).plan,
    })));
    const nextPlans = planned.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
    setPlans(nextPlans);
    const planFailures: GroupReceipt[] = planned.flatMap((result, index) => result.status === "rejected" ? [{
      kind: pendingGroups[index].kind,
      ids: [...pendingGroups[index].ids],
      plan: { project_id: projectId, asset_kind: pendingGroups[index].kind, capability: "", mode: "MISSING_ONLY", profile_version_id: null, plan_hash: "", valid: false, issues: [], items: [], summary: { selected: pendingGroups[index].ids.length, ready: 0, skipped: 0, blocked: pendingGroups[index].ids.length, jobs: 0 } },
      projectId,
      idempotencyKey: commandKey(pendingGroups[index].kind),
      expectedPlanHash: "",
      frozenRequest: { asset_kind: pendingGroups[index].kind, asset_ids: [...pendingGroups[index].ids], profile_version_id: null, mode: "MISSING_ONLY" },
      submittedAt: new Date().toISOString(),
      state: "REJECTED" as const,
      error: errorText(result.reason),
    }] : []);
    const executable = nextPlans
      .filter((group) => group.plan.valid && group.plan.summary.jobs > 0)
      .map((group) => {
        const frozenRequest: FrozenAssetCommandRequest = {
          asset_kind: group.kind,
          asset_ids: [...group.ids],
          profile_version_id: null,
          mode: "MISSING_ONLY",
        };
        const idempotencyKey = commandKey(group.kind);
        return {
          ...group,
          ids: [...group.ids],
          projectId,
          idempotencyKey,
          expectedPlanHash: group.plan.plan_hash,
          frozenRequest,
          submittedAt: new Date().toISOString(),
          state: "PREVIEWED" as const,
        };
      });
    const invalid: GroupReceipt[] = nextPlans.filter((group) => !group.plan.valid || group.plan.summary.jobs <= 0).map((group) => ({
      ...group,
      ids: [...group.ids],
      projectId,
      idempotencyKey: commandKey(group.kind),
      expectedPlanHash: group.plan.plan_hash,
      frozenRequest: { asset_kind: group.kind, asset_ids: [...group.ids], profile_version_id: null, mode: "MISSING_ONLY" },
      submittedAt: new Date().toISOString(),
      state: "REJECTED" as const,
      error: group.plan.issues[0]?.message ?? "当前组没有可执行的主图任务",
    }));
    // Freeze commands before the first send so a lost response stays recoverable.
    for (const group of executable) {
      const frozen = freezeAssetCommand(projectId, group.kind, group.frozenRequest, group.expectedPlanHash, group.idempotencyKey);
      try {
        savePendingAssetCommand(frozen);
      } catch (error) {
        setStorageError(errorText(error));
        setReceipts([...invalid, ...planFailures]);
        setMessage("待确认记录写入失败，已阻止发送需要跨刷新恢复的命令。");
        return;
      }
    }
    setReceipts([...executable.map((item) => ({ ...item, state: "SUBMITTING" as const })), ...invalid, ...planFailures]);
    const submitted = await Promise.allSettled(executable.map((group) => submitAssetImageBatch(
      projectId,
      { ...group.frozenRequest },
      group.expectedPlanHash,
      group.idempotencyKey,
    )));
    const completed: GroupReceipt[] = executable.map((group, index) => {
      const outcome = submitted[index];
      if (outcome.status === "fulfilled") {
        removePendingAssetCommand(projectId, group.idempotencyKey);
        return { ...group, state: "ACCEPTED" as const, batch: outcome.value.batch, error: undefined, existingBatchId: undefined };
      }
      const classified = classifyAssetSubmitError(outcome.reason);
      if (classified.outcome === "REJECTED") {
        removePendingAssetCommand(projectId, group.idempotencyKey);
        return { ...group, state: "REJECTED" as const, error: classified.message, existingBatchId: classified.existingBatchId };
      }
      if (classified.outcome === "CONFLICT") {
        removePendingAssetCommand(projectId, group.idempotencyKey);
        return { ...group, state: "REJECTED" as const, error: classified.message, existingBatchId: classified.existingBatchId || undefined };
      }
      // UNKNOWN: keep the frozen key for exact recovery; do not mint a new key.
      return { ...group, state: "UNKNOWN" as const, error: classified.message, existingBatchId: classified.existingBatchId };
    });
    const finalReceipts = [...completed, ...invalid, ...planFailures];
    setReceipts(finalReceipts);
    const accepted = finalReceipts.filter((item) => item.state === "ACCEPTED").length;
    const rejected = finalReceipts.filter((item) => item.state === "REJECTED").length;
    const unknown = finalReceipts.filter((item) => item.state === "UNKNOWN").length;
    setMessage(`按类别提交完成：${accepted} 组已受理${rejected ? `，${rejected} 组未受理` : ""}${unknown ? `，${unknown} 组结果待确认` : ""}。`);
  });

  const recoverUnknown = (receipt: GroupReceipt) => runWithRefresh(async () => {
    setMessage("");
    const originalKey = receipt.idempotencyKey;
    const originalRequest = { ...receipt.frozenRequest, asset_ids: [...receipt.frozenRequest.asset_ids] };
    const originalPlanHash = receipt.expectedPlanHash;
    let found: AssetImageBatch | null = null;
    let lookupFailed = false;
    try {
      found = await findAssetImageBatchByCommandKey(projectId, originalKey);
    } catch (error) {
      lookupFailed = true;
      setReceipts((current) => current.map((item) => item.idempotencyKey === originalKey
        ? { ...item, state: "UNKNOWN" as const, error: `核对回执失败，仍为待确认：${errorText(error)}` }
        : item));
    }
    if (lookupFailed) return;
    if (found) {
      removePendingAssetCommand(projectId, originalKey);
      setReceipts((current) => current.map((item) => item.idempotencyKey === originalKey
        ? { ...item, state: "ACCEPTED" as const, batch: found ?? undefined, error: undefined, existingBatchId: undefined }
        : item));
      setMessage("已通过精确回执找到原批次，未重新分派。");
      return;
    }
    // Authoritative query succeeded with no batch: resubmit along the frozen
    // original key/request/planHash. Server-side atomic idempotency arbitrates
    // a concurrent original submit.
    try {
      const response = await submitAssetImageBatch(projectId, originalRequest, originalPlanHash, originalKey);
      removePendingAssetCommand(projectId, originalKey);
      setReceipts((current) => current.map((item) => item.idempotencyKey === originalKey
        ? { ...item, state: "ACCEPTED" as const, batch: response.batch, error: undefined, existingBatchId: undefined }
        : item));
    } catch (error) {
      const classified = classifyAssetSubmitError(error);
      if (classified.outcome === "REJECTED" || classified.outcome === "CONFLICT") {
        if (classified.outcome === "REJECTED") removePendingAssetCommand(projectId, originalKey);
        setReceipts((current) => current.map((item) => item.idempotencyKey === originalKey
          ? { ...item, state: "REJECTED" as const, error: classified.message, existingBatchId: classified.existingBatchId }
          : item));
        return;
      }
      setReceipts((current) => current.map((item) => item.idempotencyKey === originalKey
        ? { ...item, state: "UNKNOWN" as const, error: classified.message }
        : item));
    }
  });

  const total = groups.reduce((sum, group) => sum + group.ids.length, 0);
  const issues = plans.flatMap((group) => group.plan.issues.map((issue) => ({
    ...issue,
    kind: group.kind,
  })));

  return (
    <section className="asset-image-batch project-asset-production project-asset-production--simple" aria-labelledby="project-asset-production-title">
      <div className="asset-image-batch__heading">
        <div>
          <p className="eyebrow">AI 资产生成</p>
          <h4 id="project-asset-production-title">自动补齐核心资产主图</h4>
          <p>{stats.filter((item) => item.total > 0).map((item) => `${LABELS[item.kind]} ${item.missing}/${item.total}`).join(" · ") || "还没有可生成资产"} · AI 自动选择纯文生图配置并绑定结果；已有主图不会被覆盖。</p>
        </div>
        <div className="asset-image-batch__summary"><strong>{total}</strong><span>项待生成</span></div>
      </div>
      <div className="asset-image-batch__actions">
        <button
          type="button"
          className="primary-action"
          disabled={!total || busy || hasUnknown || hasSubmitting || Boolean(storageError)}
          onClick={() => void generate()}
        >
          {busy ? "AI 正在检查并生成…" : total ? "生成缺少的主图" : "核心资产主图已齐全"}
        </button>
        {busy && <Link to={`/system/jobs?project=${encodeURIComponent(projectId)}`}>查看任务进度</Link>}
      </div>
      {storageError && <p className="inline-error" role="alert">{storageError}</p>}
      {refreshWarning && <p className="inline-error" role="status">{refreshWarning}</p>}
      {receipts.length > 0 && <div className="asset-image-batch__receipts" aria-label="按类别提交回执">
        {receipts.map((receipt) => <article key={receipt.idempotencyKey} data-state={receipt.state}>
          <strong>{LABELS[receipt.kind]}</strong>
          <span>{receipt.state === "ACCEPTED" ? `已受理${receipt.batch ? ` · 批次 ${receipt.batch.id.slice(0, 8)}` : ""}` : receipt.state === "UNKNOWN" ? "结果待确认" : receipt.state === "REJECTED" ? "未受理" : "提交中"}</span>
          {receipt.batch?.items.some((item) => item.job_id) ? <small>任务：{receipt.batch.items.filter((item) => item.job_id).map((item) => item.job_id!.slice(0, 8)).join("、")}</small> : null}
          {receipt.existingBatchId ? <small>已有批次：{receipt.existingBatchId.slice(0, 8)}</small> : null}
          {receipt.error ? <small role={receipt.state === "UNKNOWN" ? "status" : "alert"}>{receipt.error}</small> : null}
          {receipt.state === "UNKNOWN" ? <button type="button" className="secondary" disabled={busy} onClick={() => void recoverUnknown(receipt)}>先核对回执，再继续</button> : null}
        </article>)}
      </div>}
      {issues.length > 0 && (
        <details className="asset-image-batch__plan is-blocked">
          <summary>{issues.length} 项需要处理</summary>
          <ul>{issues.map((issue, index) => <li key={`${issue.kind}-${issue.asset_id ?? index}-${issue.code}`}>{issue.asset_name ? `${issue.asset_name}：` : `${LABELS[issue.kind]}：`}{issue.message}</li>)}</ul>
        </details>
      )}
      {message && <p className="review-success" role="status" aria-live="polite">{message}</p>}
    </section>
  );
}

function frozenToUnknownReceipt(projectId: string, command: FrozenAssetCommand): GroupReceipt {
  return {
    kind: command.kind,
    ids: [...command.request.asset_ids],
    plan: {
      project_id: projectId,
      asset_kind: command.kind,
      capability: "",
      mode: "MISSING_ONLY",
      profile_version_id: command.request.profile_version_id,
      plan_hash: command.expectedPlanHash,
      valid: true,
      issues: [],
      items: [],
      summary: { selected: command.request.asset_ids.length, ready: 0, skipped: 0, blocked: 0, jobs: 0 },
    },
    projectId,
    idempotencyKey: command.idempotencyKey,
    expectedPlanHash: command.expectedPlanHash,
    frozenRequest: { ...command.request, asset_ids: [...command.request.asset_ids] },
    submittedAt: command.submittedAt,
    state: "UNKNOWN",
    error: "页面已刷新，该命令结果待确认，请先核对回执。",
  };
}

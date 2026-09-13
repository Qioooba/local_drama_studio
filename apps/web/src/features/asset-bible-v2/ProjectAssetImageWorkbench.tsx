import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { AssetBibleItem } from "./api";
import {
  planAssetImageBatch,
  listAssetImageBatches,
  submitAssetImageBatch,
  type AssetImageBatch,
  type AssetImageBatchPlan,
  type AssetImageKind,
} from "./assetImageBatchClient";
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
  idempotencyKey: string;
  state: "PREVIEWED" | "SUBMITTING" | "ACCEPTED" | "REJECTED" | "UNKNOWN";
  batch?: AssetImageBatch;
  error?: string;
};

function resultState(error: unknown): "REJECTED" | "UNKNOWN" {
  const status = typeof error === "object" && error && "status" in error ? Number((error as { status?: unknown }).status) : 0;
  return status >= 400 && status < 500 ? "REJECTED" : "UNKNOWN";
}

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

  const generate = async () => {
    setBusy(true);
    setMessage("");
    try {
      const planned = await Promise.allSettled(groups.map(async (group) => ({
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
        kind: groups[index].kind,
        ids: groups[index].ids,
        plan: { project_id: projectId, asset_kind: groups[index].kind, capability: "", mode: "MISSING_ONLY", profile_version_id: null, plan_hash: "", valid: false, issues: [], items: [], summary: { selected: groups[index].ids.length, ready: 0, skipped: 0, blocked: groups[index].ids.length, jobs: 0 } },
        idempotencyKey: commandKey(groups[index].kind),
        state: "REJECTED" as const,
        error: result.reason instanceof Error ? result.reason.message : String(result.reason),
      }] : []);
      const executable: GroupReceipt[] = nextPlans.filter((group) => group.plan.valid && group.plan.summary.jobs > 0).map((group) => ({
        ...group,
        idempotencyKey: commandKey(group.kind),
        state: "PREVIEWED",
      }));
      const invalid: GroupReceipt[] = nextPlans.filter((group) => !group.plan.valid || group.plan.summary.jobs <= 0).map((group) => ({
        ...group,
        idempotencyKey: commandKey(group.kind),
        state: "REJECTED",
        error: group.plan.issues[0]?.message ?? "当前组没有可执行的主图任务",
      }));
      setReceipts([...executable.map((item) => ({ ...item, state: "SUBMITTING" as const })), ...invalid, ...planFailures]);
      const submitted = await Promise.allSettled(executable.map((group) => submitAssetImageBatch(projectId, {
        asset_kind: group.kind,
        asset_ids: group.ids,
        profile_version_id: null,
        mode: "MISSING_ONLY",
      }, group.plan.plan_hash, group.idempotencyKey)));
      const completed = executable.map((group, index): GroupReceipt => submitted[index].status === "fulfilled"
        ? { ...group, state: "ACCEPTED", batch: submitted[index].value.batch }
        : { ...group, state: resultState(submitted[index].reason), error: submitted[index].reason instanceof Error ? submitted[index].reason.message : String(submitted[index].reason) });
      const finalReceipts = [...completed, ...invalid, ...planFailures];
      setReceipts(finalReceipts);
      const accepted = finalReceipts.filter((item) => item.state === "ACCEPTED").length;
      const rejected = finalReceipts.filter((item) => item.state === "REJECTED").length;
      const unknown = finalReceipts.filter((item) => item.state === "UNKNOWN").length;
      setMessage(`按类别提交完成：${accepted} 组已受理${rejected ? `，${rejected} 组未受理` : ""}${unknown ? `，${unknown} 组结果待确认` : ""}。`);
    } finally {
      await onChanged();
      setBusy(false);
    }
  };

  const recoverUnknown = async (receipt: GroupReceipt) => {
    setBusy(true);
    try {
      const existing = (await listAssetImageBatches(projectId, receipt.kind)).find((batch) => batch.plan_hash === receipt.plan.plan_hash);
      const response = existing ? { batch: existing } : await submitAssetImageBatch(projectId, {
        asset_kind: receipt.kind,
        asset_ids: receipt.ids,
        profile_version_id: null,
        mode: "MISSING_ONLY",
      }, receipt.plan.plan_hash, receipt.idempotencyKey);
      setReceipts((current) => current.map((item) => item.kind === receipt.kind ? { ...item, state: "ACCEPTED", batch: response.batch, error: undefined } : item));
    } catch (error) {
      setReceipts((current) => current.map((item) => item.kind === receipt.kind ? { ...item, state: resultState(error), error: error instanceof Error ? error.message : String(error) } : item));
    } finally {
      await onChanged();
      setBusy(false);
    }
  };

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
        <button type="button" className="primary-action" disabled={!total || busy} onClick={() => void generate()}>
          {busy ? "AI 正在检查并生成…" : total ? "生成缺少的主图" : "核心资产主图已齐全"}
        </button>
        {busy && <Link to={`/system/jobs?project=${encodeURIComponent(projectId)}`}>查看任务进度</Link>}
      </div>
      {receipts.length > 0 && <div className="asset-image-batch__receipts" aria-label="按类别提交回执">
        {receipts.map((receipt) => <article key={receipt.kind} data-state={receipt.state}>
          <strong>{LABELS[receipt.kind]}</strong>
          <span>{receipt.state === "ACCEPTED" ? `已受理${receipt.batch ? ` · 批次 ${receipt.batch.id.slice(0, 8)}` : ""}` : receipt.state === "UNKNOWN" ? "结果待确认" : receipt.state === "REJECTED" ? "未受理" : "提交中"}</span>
          {receipt.batch?.items.some((item) => item.job_id) ? <small>任务：{receipt.batch.items.filter((item) => item.job_id).map((item) => item.job_id!.slice(0, 8)).join("、")}</small> : null}
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

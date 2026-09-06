import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import type { AssetBibleItem } from "./api";
import {
  planAssetImageBatch,
  submitAssetImageBatch,
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
  const [message, setMessage] = useState("");

  const generate = useMutation({
    mutationFn: async () => {
      const nextPlans = await Promise.all(groups.map(async (group) => ({
        ...group,
        plan: (await planAssetImageBatch(projectId, {
          asset_kind: group.kind,
          asset_ids: group.ids,
          profile_version_id: null,
          mode: "MISSING_ONLY",
        })).plan,
      })));
      setPlans(nextPlans);
      const executable = nextPlans.filter((group) => group.plan.valid && group.plan.summary.jobs > 0);
      if (!executable.length) throw new Error("AI 没有找到当前可直接生成的核心资产，请展开异常提示。");
      return Promise.all(executable.map((group) => submitAssetImageBatch(projectId, {
        asset_kind: group.kind,
        asset_ids: group.ids,
        profile_version_id: null,
        mode: "MISSING_ONLY",
      }, group.plan.plan_hash, commandKey(group.kind))));
    },
    onSuccess: async (results) => {
      const active = results.reduce((sum, result) => sum + result.batch.summary.active, 0);
      const succeeded = results.reduce((sum, result) => sum + result.batch.summary.succeeded, 0);
      const failed = results.reduce((sum, result) => sum + result.batch.summary.failed, 0);
      setMessage(`已提交 ${active} 张主图，已完成 ${succeeded} 张${failed ? `，${failed} 张提交失败，请在对应资产类别查看原因并重试` : ""}。成功结果会自动选择为对应资产的主参考。`);
      await onChanged();
    },
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
        <button type="button" className="primary-action" disabled={!total || generate.isPending} onClick={() => generate.mutate()}>
          {generate.isPending ? "AI 正在检查并生成…" : total ? "生成缺少的主图" : "核心资产主图已齐全"}
        </button>
        {generate.isPending && <Link to={`/system/jobs?project=${encodeURIComponent(projectId)}`}>查看任务进度</Link>}
      </div>
      {issues.length > 0 && (
        <details className="asset-image-batch__plan is-blocked">
          <summary>{issues.length} 项需要处理</summary>
          <ul>{issues.map((issue, index) => <li key={`${issue.kind}-${issue.asset_id ?? index}-${issue.code}`}>{issue.asset_name ? `${issue.asset_name}：` : `${LABELS[issue.kind]}：`}{issue.message}</li>)}</ul>
        </details>
      )}
      {message && <p className="review-success" role="status" aria-live="polite">{message}</p>}
      {generate.error && <p className="inline-error" role="alert">{generate.error instanceof Error ? generate.error.message : String(generate.error)}</p>}
    </section>
  );
}

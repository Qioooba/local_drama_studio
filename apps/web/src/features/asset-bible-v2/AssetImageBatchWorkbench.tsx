import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CapabilityPicker, effectiveCapabilityProfile, useCapabilityOptions } from "../model-config/CapabilityPicker";
import type { AssetBibleItem } from "./api";
import {
  isAssetImageBatchActive,
  listAssetImageBatches,
  planAssetImageBatch,
  submitAssetImageBatch,
  type AssetImageBatchPlan,
  type AssetImageKind,
} from "./assetImageBatchClient";
import "./asset-image-batch.css";

const KIND_META: Record<AssetImageKind, { label: string; capability: string; profileCapability: string; noun: string }> = {
  CHARACTER: { label: "角色主图", capability: "IMAGE_CHARACTER", profileCapability: "IMAGE_CONCEPT", noun: "角色" },
  SCENE: { label: "场景主图", capability: "IMAGE_SCENE", profileCapability: "IMAGE_CONCEPT", noun: "场景" },
  PROP: { label: "道具主图", capability: "IMAGE_CONCEPT", profileCapability: "IMAGE_CONCEPT", noun: "道具" },
  COSTUME: { label: "服装主图", capability: "IMAGE_CONCEPT", profileCapability: "IMAGE_CONCEPT", noun: "服装" },
};

const ACTIVE_BATCH_STATES = new Set(["QUEUED", "RUNNING", "PARTIAL_RUNNING"]);

function commandKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `asset-images-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function hasHero(item: AssetBibleItem): boolean {
  return Boolean(item.asset.canonical_media_version_id || item.base_references.some((reference) => reference.reference_kind === "HERO"));
}

export function AssetImageBatchWorkbench({
  projectId,
  kind,
  items,
  onChanged,
}: {
  projectId: string;
  kind: AssetImageKind;
  items: AssetBibleItem[];
  onChanged: () => void | Promise<void>;
}) {
  const meta = KIND_META[kind];
  const queryClient = useQueryClient();
  const eligible = useMemo(() => items.filter((item) => item.asset.kind === kind && item.asset.status === "ACTIVE" && !hasHero(item)), [items, kind]);
  const eligibleKey = eligible.map((item) => `${item.asset.id}:${item.asset.revision}`).join("|");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [profileVersionId, setProfileVersionId] = useState("");
  const [plan, setPlan] = useState<AssetImageBatchPlan | null>(null);
  const [expanded, setExpanded] = useState(false);
  // CHARACTER/SCENE generation supports specialized profiles (IMAGE_CHARACTER/IMAGE_SCENE)
  // while seamlessly falling back to generic published IMAGE_CONCEPT profiles when available.
  const primaryOptions = useCapabilityOptions(meta.capability, { projectId });
  const fallbackOptions = useCapabilityOptions(meta.capability !== "IMAGE_CONCEPT" ? "IMAGE_CONCEPT" : "", { projectId });

  const rawCapabilityOptions = useMemo(() => {
    if (meta.capability === "IMAGE_CONCEPT") return primaryOptions;
    const primaryData = primaryOptions.data;
    const fallbackData = fallbackOptions.data;
    if (!fallbackData) return primaryOptions;
    if (!primaryData) return fallbackOptions;

    const seen = new Set<string>();
    const combinedOptions: typeof primaryData.options = [];
    for (const opt of [...primaryData.options, ...fallbackData.options]) {
      if (!seen.has(opt.profile_version_id)) {
        seen.add(opt.profile_version_id);
        combinedOptions.push(opt);
      }
    }
    const readySelection = primaryData.selection.ready ? primaryData.selection : fallbackData.selection;
    return {
      ...primaryOptions,
      data: {
        ...primaryData,
        options: combinedOptions,
        selection: readySelection,
        summary: {
          total_count: combinedOptions.length,
          selectable_count: combinedOptions.filter((o) => o.selectable).length,
          blocked_count: combinedOptions.filter((o) => !o.selectable).length,
        },
      },
    };
  }, [meta.capability, primaryOptions, fallbackOptions]);
  const capabilityOptions = useMemo(() => {
    const data = rawCapabilityOptions.data;
    if (!data) return rawCapabilityOptions;
    const options = data.options.filter((option) => option.supports_text_to_image !== false && option.requires_reference_image !== true);
    const preferredId = profileVersionId || data.selection.profile_version_id;
    const selected = options.find((option) => option.profile_version_id === preferredId && option.selectable)
      ?? options.find((option) => option.selectable)
      ?? null;
    return {
      ...rawCapabilityOptions,
      data: {
        ...data,
        options,
        selection: {
          ...data.selection,
          profile_version_id: selected?.profile_version_id ?? null,
          ready: Boolean(selected?.selectable),
          option: selected,
          blockers: selected
            ? data.selection.blockers
            : [{ code: "NO_TEXT_TO_IMAGE_PROFILE", message: "当前没有可直接执行的纯文生图配置" }],
        },
        summary: {
          total_count: options.length,
          selectable_count: options.filter((option) => option.selectable).length,
          blocked_count: options.filter((option) => !option.selectable).length,
        },
      },
    };
  }, [rawCapabilityOptions, profileVersionId]);
  const effectiveProfile = effectiveCapabilityProfile(capabilityOptions, profileVersionId);

  useEffect(() => {
    setSelectedIds(new Set(eligible.map((item) => item.asset.id)));
    setPlan(null);
  }, [kind, eligibleKey]);

  useEffect(() => {
    if (profileVersionId && !capabilityOptions.data?.options.some((option) => option.profile_version_id === profileVersionId)) {
      setProfileVersionId("");
      setPlan(null);
    }
  }, [capabilityOptions.data, profileVersionId]);

  const histories = useQuery({
    queryKey: ["asset-image-batches", projectId, kind],
    queryFn: () => listAssetImageBatches(projectId, kind),
    refetchInterval: (query) => isAssetImageBatchActive(query.state.data?.[0]) ? 2_000 : false,
  });
  const latest = histories.data?.[0] ?? null;

  useEffect(() => {
    if (latest?.status === "SUCCEEDED" || latest?.status === "PARTIAL_FAILED" || latest?.status === "FAILED") {
      void onChanged();
    }
  }, [latest?.id, latest?.status]);

  const request = () => ({
    asset_kind: kind,
    asset_ids: [...selectedIds],
    profile_version_id: effectiveProfile.profileVersionId || null,
    mode: "MISSING_ONLY" as const,
  });

  const preflight = useMutation({
    mutationFn: () => planAssetImageBatch(projectId, request()),
    onSuccess: ({ plan: nextPlan }) => setPlan(nextPlan),
  });
  const submit = useMutation({
    mutationFn: () => {
      if (!plan?.valid) throw new Error("请先通过生成计划检查");
      return submitAssetImageBatch(projectId, request(), plan.plan_hash, commandKey());
    },
    onSuccess: async () => {
      setPlan(null);
      await queryClient.invalidateQueries({ queryKey: ["asset-image-batches", projectId, kind] });
    },
  });

  const busy = preflight.isPending || submit.isPending || isAssetImageBatchActive(latest);
  const showBody = expanded || isAssetImageBatchActive(latest);
  const allSelected = eligible.length > 0 && eligible.every((item) => selectedIds.has(item.asset.id));
  const failedIds = latest?.items.filter((item) => item.status === "FAILED" || item.status === "CANCELLED").map((item) => item.asset_id) ?? [];
  const selectedCount = selectedIds.size;
  const error = preflight.error || submit.error || histories.error;

  return <section className="asset-image-batch" aria-labelledby={`asset-image-batch-${kind}`}>
    <div className="asset-image-batch__heading">
      <div>
        <p className="eyebrow">批量文生图</p>
        <h4 id={`asset-image-batch-${kind}`}>自动补齐本页{meta.label}</h4>
        <p>读取每项正式资产的名称与描述，为缺少主参考的{meta.noun}各生成一张图；成功结果会自动登记为锁定的主参考。</p>
      </div>
      <div className="asset-image-batch__summary" aria-label={`${eligible.length} 项缺少主图`}>
        <strong>{eligible.length}</strong><span>项缺图</span>
      </div>
    </div>

    {!showBody ? <div className="asset-image-batch__collapsed">
      <button type="button" className="secondary asset-image-batch__open" disabled={eligible.length === 0 && !latest} onClick={() => setExpanded(true)}>
        {latest ? (eligible.length ? `继续配置 ${eligible.length} 张主图` : "查看最近结果") : (eligible.length ? `配置并生成 ${eligible.length} 张主图` : `本页${meta.label}已齐全`)}
      </button>
      {latest && <span><strong>{latest.summary.succeeded}/{latest.summary.total}</strong> 最近完成 · {latest.summary.failed} 失败</span>}
    </div> : null}

    {showBody && <div className="asset-image-batch__body">
      <CapabilityPicker
        capability={meta.capability}
        label={`${meta.label}文生图方式`}
        value={profileVersionId}
        onChange={(value) => { setProfileVersionId(value); setPlan(null); }}
        query={capabilityOptions}
        disabled={busy}
        description="自动使用项目偏好；也可以固定选择一个已发布文生图版本，本批次所有图片使用同一版本。"
        migrationBusinessSurface="assets"
      />

      <div className="asset-image-batch__selection">
        <div className="asset-image-batch__selection-head">
          <label><input type="checkbox" checked={allSelected} disabled={busy || eligible.length === 0} onChange={(event) => { setSelectedIds(event.target.checked ? new Set(eligible.map((item) => item.asset.id)) : new Set()); setPlan(null); }} />选择全部缺图项</label>
          <span>已选 {selectedCount} / {eligible.length}</span>
        </div>
        {eligible.length ? <div className="asset-image-batch__asset-grid">
          {eligible.map((item) => <label key={item.asset.id} className={selectedIds.has(item.asset.id) ? "is-selected" : ""}>
            <input type="checkbox" checked={selectedIds.has(item.asset.id)} disabled={busy} onChange={(event) => { setSelectedIds((current) => { const next = new Set(current); if (event.target.checked) next.add(item.asset.id); else next.delete(item.asset.id); return next; }); setPlan(null); }} />
            <span><strong>{item.asset.name}</strong><small>{item.asset.description || "尚无描述，将使用项目故事资料和类别默认构图"}</small></span>
          </label>)}
        </div> : <p className="asset-image-batch__complete">当前类别没有缺失主图，系统不会覆盖已经选定的主参考。</p>}
      </div>

      {plan && <div className={`asset-image-batch__plan ${plan.valid ? "is-ready" : "is-blocked"}`} role="status">
        <strong>{plan.summary.ready} 张可生成 · {plan.summary.skipped} 张已跳过 · {plan.summary.blocked} 张阻塞</strong>
        {plan.issues.length > 0 && <ul>{plan.issues.map((issue, index) => <li key={`${issue.code}-${issue.asset_id ?? index}`}>{issue.asset_name ? `${issue.asset_name}：` : ""}{issue.message}</li>)}</ul>}
        {plan.items.some((item) => item.status === "READY") && <details><summary>查看系统生成的提示词</summary>{plan.items.filter((item) => item.status === "READY").map((item) => <p key={item.asset_id}><strong>{item.name}</strong><span>{item.prompt}</span></p>)}</details>}
      </div>}

      {!isAssetImageBatchActive(latest) && eligible.length > 0 && <div className="asset-image-batch__actions">
        <button type="button" className="secondary" disabled={busy || selectedCount === 0 || !effectiveProfile.ready} onClick={() => preflight.mutate()}>{preflight.isPending ? "正在检查…" : `检查 ${selectedCount} 张生成计划`}</button>
        {plan?.valid && <button type="button" className="primary-action" disabled={busy} onClick={() => submit.mutate()}>{submit.isPending ? "正在提交…" : `确认生成 ${plan.summary.jobs} 张主图`}</button>}
      </div>}

      {latest && <div className={`asset-image-batch__run state-${latest.status.toLowerCase()}`} aria-live="polite">
        <div className="asset-image-batch__run-head"><strong>{ACTIVE_BATCH_STATES.has(latest.status) ? "主图生成中" : "最近一次批量结果"}</strong><span>{latest.summary.succeeded}/{latest.summary.total} 已完成 · {latest.summary.failed} 失败</span></div>
        <div className="asset-image-batch__progress" aria-label={`已完成 ${latest.summary.succeeded} / ${latest.summary.total}`}><span style={{ width: `${latest.summary.total ? ((latest.summary.succeeded + latest.summary.superseded + latest.summary.failed) / latest.summary.total) * 100 : 0}%` }} /></div>
        <ul>{latest.items.map((item) => <li key={item.id}>
          <span><strong>{item.asset_name}</strong><small>{item.status === "SUCCEEDED" ? "已自动绑定主参考" : item.status === "SUPERSEDED" ? "检测到更新的主参考，保留生成图但未覆盖" : item.error?.message || item.progress.phase || item.job_state || "等待执行"}</small></span>
          <b>{item.status}</b>
        </li>)}</ul>
        {failedIds.length > 0 && !isAssetImageBatchActive(latest) && <button type="button" className="secondary" onClick={() => { const eligibleFailed = failedIds.filter((id) => eligible.some((item) => item.asset.id === id)); setSelectedIds(new Set(eligibleFailed)); setPlan(null); setExpanded(true); }}>只重试失败项</button>}
        {isAssetImageBatchActive(latest) && <Link to={`/system/jobs?project=${encodeURIComponent(projectId)}`}>在任务中心查看</Link>}
      </div>}
      {error && <p className="inline-error" role="alert">{errorMessage(error)}</p>}
    </div>}
  </section>;
}

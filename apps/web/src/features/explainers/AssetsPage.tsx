/**
 * 第 2 步：人物与风格 (assets) — design §B3, §B9, §B10, §C4.4, §F3.
 *
 * The page is built on the shared candidate contract, so the four actions that
 * look alike stay apart (§B9):
 *
 * * clicking a candidate only previews it (`previewedId`) — it writes nothing;
 * * 采用 writes the adoption and never implies a lock;
 * * 采用并锁定 is the separate, explicit human lock;
 * * 解锁 only changes the replacement policy and keeps the current image.
 *
 * Two numbers are shown for every object and they are never merged (§B3.1):
 * `appearance_beat_count` → 出场 N 镜, and `reference_binding_status` →
 * 未设参考图 / 已采用参考图 / 待更新.  A character that appears in four shots
 * with no reference image therefore reads both “出场 4 镜” and “未设参考图”;
 * identity slot counts and binding versions live in 高级详情.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  adoptExplainerEntityReference,
  getExplainerReferenceDesign,
  patchExplainerVisualPreferences,
  planExplainerReferenceGeneration,
  preflightExplainerPlan,
  startExplainerRun,
  submitExplainerReferenceGeneration,
  unlockExplainerEntityReference,
  type ExplainerCandidatePurpose,
  type ExplainerEntityAsset,
  type ExplainerGenerationMode,
  type ExplainerGenerationPlan,
  type ExplainerGenerationRequest,
  type ExplainerMediaCandidate,
  type ExplainerVisualPreferences,
} from "../../generated/api";
import { routes } from "../../app/routeRegistry";
import { Drawer, MediaThumb } from "../../components/ui/primitives";
import { queryKeys } from "../../query/queryKeys";
import { stableIdempotencyKey } from "../../services/commandId";
import { draftRegistry, type DraftDiscardResult, type DraftHandle, type DraftSaveResult } from "../drafts/draftRegistry";
import { MediaPicker } from "../media-picker/MediaPicker";
import { MediaCandidateCompare, MediaCandidateGrid, GenerationControls } from "./candidates";
import { InlineError, InlineOk, Panel, SettingRow, StateNotice, type PageState } from "./components";
import { useExplainerActionBar } from "./ExplainerStepActionBar";
import { EXPLAINER_STEP_STATUS_LABELS } from "./ExplainerSteps";
import { useExplainerAssets, useExplainerEntityCandidates, useExplainerOverview, useExplainerReadiness } from "./useExplainerQueries";
import { resolveOutputsForExplainer } from "./viewModels";
import "./explainers.css";
import "./explainers.pages.css";
import "./explainers.candidates.css";

type AssetCategory = "CHARACTER" | "SCENE" | "PROP" | "OTHER";

const CATEGORY_LABELS: Record<AssetCategory, string> = {
  CHARACTER: "人物",
  SCENE: "场景",
  PROP: "道具",
  OTHER: "其他（不需要固定外观）",
};

const REFERENCE_STATUS_LABELS: Record<string, string> = {
  NO_REFERENCE: "未设参考图",
  ADOPTED_REFERENCE: "已采用参考图",
  NEEDS_UPDATE: "待更新",
};

/** §B1.2: media previews use the real aspect and never crop the subject. */
export function referenceThumbnailUrl(mediaVersionId: string, size: "small" | "medium" = "small"): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=${size}&frame=poster`;
}

export function categoryOf(entity: ExplainerEntityAsset): AssetCategory {
  const kind = String(entity.asset_kind ?? "").toUpperCase();
  if (kind === "CHARACTER" || kind === "SCENE" || kind === "PROP") return kind;
  return "OTHER";
}

/** The object's real reference media version, when one is bound. */
export function adoptedReferenceMediaId(entity: ExplainerEntityAsset | null | undefined): string {
  const reference = (entity?.reference ?? null) as Record<string, unknown> | null;
  if (!reference) return "";
  return String(reference.media_version_id ?? "");
}

export function adoptedReferenceId(entity: ExplainerEntityAsset | null | undefined): string {
  const reference = (entity?.reference ?? null) as Record<string, unknown> | null;
  if (!reference) return "";
  return String(reference.id ?? "");
}

/**
 * `reference_binding_status` is the server's own fact.  Older payloads do not
 * carry it; in that case the status is derived from the real ACTIVE identity
 * bindings (never from an appearance count), and stays unknown when neither is
 * available instead of pretending “未设参考图”.
 */
export function referenceStatusOf(entity: ExplainerEntityAsset): { value: string | null; label: string } {
  const value = entity.reference_binding_status ? String(entity.reference_binding_status) : "";
  if (value) return { value, label: REFERENCE_STATUS_LABELS[value] ?? value };
  if (adoptedReferenceMediaId(entity)) return { value: "ADOPTED_REFERENCE", label: REFERENCE_STATUS_LABELS.ADOPTED_REFERENCE };
  const bindings = (entity as unknown as Record<string, unknown>).identity_bindings;
  if (Array.isArray(bindings)) {
    return bindings.length > 0
      ? { value: "ADOPTED_REFERENCE", label: REFERENCE_STATUS_LABELS.ADOPTED_REFERENCE }
      : { value: "NO_REFERENCE", label: REFERENCE_STATUS_LABELS.NO_REFERENCE };
  }
  return { value: null, label: "参考图状态未知" };
}

/** The appearance count is its own number and is never replaced by a binding count. */
export function appearanceCountOf(entity: ExplainerEntityAsset): number | null {
  const raw = (entity as unknown as Record<string, unknown>).appearance_beat_count;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

export function isReferenceMissing(entity: ExplainerEntityAsset): boolean {
  const status = referenceStatusOf(entity).value;
  return status === "NO_REFERENCE" || status === "NEEDS_UPDATE";
}

/** §B3.3: 生成缺失参考 only touches objects that are missing *and not locked*. */
export function isReferenceLocked(entity: ExplainerEntityAsset): boolean {
  const reference = (entity.reference ?? null) as Record<string, unknown> | null;
  return Boolean(reference?.is_locked) || Boolean((entity as unknown as Record<string, unknown>).locked);
}

export function needsGeneratedReference(entity: ExplainerEntityAsset): boolean {
  if (isReferenceLocked(entity)) return false;
  return isReferenceMissing(entity);
}

/** §B9: an unknown budget reads 待检查 — never 0 and never a fabricated balance. */
export function budgetNoteFor(plan: ExplainerGenerationPlan | null, candidateCount: number): string {
  if (!plan) return `本次将提交 ${candidateCount} 张图片；预算余量在提交前由服务端预检，未知时显示“待检查”。`;
  const budget = plan.budget && typeof plan.budget === "object" ? (plan.budget as Record<string, unknown>) : null;
  if (!budget) return `本次将提交 ${plan.candidate_count} 张图片；预算余量：待检查（服务端未返回预算明细）。`;
  const remaining = budget.remaining_gpu_seconds ?? budget.remaining ?? budget.max_gpu_seconds;
  if (typeof remaining === "number" && Number.isFinite(remaining)) {
    return `本次将提交 ${plan.candidate_count} 张图片；预算余量：${remaining} GPU 秒（服务端返回）。`;
  }
  return `本次将提交 ${plan.candidate_count} 张图片；预算余量：待检查（服务端未返回可读余量）。`;
}

export function ExplainerAssetsPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const assets = useExplainerAssets(projectId);
  const overview = useExplainerOverview(projectId);
  const readiness = useExplainerReadiness(projectId);

  const entities = useMemo(() => assets.data?.entities ?? [], [assets.data?.entities]);
  const categories = useMemo(() => {
    const counts: Record<AssetCategory, number> = { CHARACTER: 0, SCENE: 0, PROP: 0, OTHER: 0 };
    for (const entity of entities) counts[categoryOf(entity)] += 1;
    return counts;
  }, [entities]);

  const requestedCategory = searchParams.get("category") as AssetCategory | null;
  const firstNonEmpty = (["CHARACTER", "SCENE", "PROP", "OTHER"] as AssetCategory[]).find((item) => categories[item] > 0) ?? "CHARACTER";
  const category: AssetCategory = requestedCategory && requestedCategory in categories ? requestedCategory : firstNonEmpty;
  const filtered = useMemo(() => entities.filter((entity) => categoryOf(entity) === category), [category, entities]);

  const requestedEntityId = searchParams.get("entity");
  const selected = filtered.find((entity) => entity.entity_id === requestedEntityId)
    ?? entities.find((entity) => entity.entity_id === requestedEntityId)
    ?? filtered[0]
    ?? entities[0]
    ?? null;
  const selectedId = selected?.entity_id ?? null;

  const candidatesQuery = useExplainerEntityCandidates(projectId, selectedId);

  /* -------------------------------------------------- preview vs adopted ---- */
  // Three different states with three different names (§B12.3): the adopted
  // reference is server data, the previewed candidate is local, and the settings
  // draft is a third thing.
  const [previewedId, setPreviewedId] = useState<string | null>(null);
  const [pendingUpload, setPendingUpload] = useState<{ mediaVersionId: string; label: string } | null>(null);
  const [compareOpen, setCompareOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [viewsOpen, setViewsOpen] = useState(false);
  const [styleOpen, setStyleOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<ExplainerGenerationPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [countByEntity, setCountByEntity] = useState<Record<string, number>>({});
  const [stylePrompt, setStylePrompt] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [styleDraft, setStyleDraft] = useState<{ channelProfileVersionId: string | null; note: string } | null>(null);

  const persistedPreferences = assets.data?.visual_preferences ?? null;
  const persistedCount = persistedPreferences?.image_candidate_count ?? null;
  const videoRevision = Number((overview.data?.video as Record<string, unknown> | undefined)?.revision ?? 0);
  /** The narrow PATCH needs the real video revision; without it we say so instead of sending 0. */
  const canSaveSettings = videoRevision > 0;

  useEffect(() => {
    if (persistedPreferences?.style_prompt_override !== undefined) setStylePrompt(persistedPreferences.style_prompt_override ?? "");
    if (persistedPreferences?.negative_prompt_override !== undefined) setNegativePrompt(persistedPreferences.negative_prompt_override ?? "");
  }, [persistedPreferences]);

  /** §B9/B3.2 default: 2 for the one key character, 1 for everyone else. */
  const keyCharacterId = useMemo(() => {
    const characters = entities.filter((entity) => categoryOf(entity) === "CHARACTER");
    if (characters.length === 0) return null;
    return characters
      .slice()
      .sort((left, right) => (appearanceCountOf(right) ?? 0) - (appearanceCountOf(left) ?? 0))[0].entity_id;
  }, [entities]);

  const candidateCount = selectedId
    ? countByEntity[selectedId] ?? persistedCount ?? (selectedId === keyCharacterId ? 2 : 1)
    : persistedCount ?? 1;
  const setCandidateCount = (value: number) => {
    if (!selectedId) return;
    setCountByEntity((current) => ({ ...current, [selectedId]: value }));
  };

  /* ----------------------------------------------------------- mutations --- */
  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.explainers.all });

  const generationRequest = useCallback((count: number, extra: Partial<ExplainerGenerationRequest> = {}): ExplainerGenerationRequest => {
    if (!selected) throw new Error("请先选择一个人物 / 场景 / 道具");
    const operationId = stableIdempotencyKey("explainer-reference-operation", {
      projectId,
      entityId: selected.entity_id,
      count,
      // The newest existing candidate makes a regenerate a genuinely new batch
      // while a refresh + retry of the same batch replays the same command.
      after: (candidatesQuery.data?.candidates?.[0]?.id ?? null),
      ...extra,
    });
    return {
      operation_id: operationId,
      purpose: "REFERENCE" as ExplainerCandidatePurpose,
      mode: "TEXT_TO_IMAGE" as ExplainerGenerationMode,
      candidate_count: count,
      expected_entity_revision: Number((selected as unknown as Record<string, unknown>).revision ?? 0) || null,
      expected_reference_id: adoptedReferenceId(selected) || null,
      ...extra,
    };
  }, [candidatesQuery.data?.candidates, projectId, selected]);

  const generate = useMutation({
    mutationFn: async (count: number) => {
      const request = generationRequest(count);
      const resolved = await planExplainerReferenceGeneration(projectId, String(selected?.entity_id), request);
      setPlan(resolved);
      if (resolved.status === "BLOCKED") return { submitted: false, plan: resolved } as const;
      const receipt = await submitExplainerReferenceGeneration(
        projectId,
        String(selected?.entity_id),
        { ...request, expected_plan_hash: resolved.plan_hash },
        request.operation_id,
      );
      return { submitted: true, plan: resolved, receipt } as const;
    },
    onSuccess: async (result) => {
      setPlanError(null);
      if (!result.submitted) {
        setError(null);
        setFeedback(null);
        return;
      }
      const receipt = "receipt" in result ? result.receipt : null;
      setError(null);
      setFeedback(
        `已提交 ${receipt?.accepted_count ?? 0} / ${receipt?.requested_count ?? candidateCount} 张参考图候选（真实排队任务）。` +
        "新批次会保留旧候选，不会替换当前已采用的参考图。",
      );
      await invalidate();
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  /**
   * §C4.4: the frozen setting prompt for this entity's reference image.
   *
   * Deterministic and read-only — with the known attributes and adopted-reference
   * invariants already present this makes no model call, so it is safe to show the
   * exact text the generation command will use (including the程序-appended hard
   * view/identity requirements).
   */
  const referenceDesign = useQuery({
    queryKey: queryKeys.explainers.referenceDesign(projectId, selected?.entity_id ?? ""),
    queryFn: () => getExplainerReferenceDesign(projectId, String(selected?.entity_id), { reference_kind: "HERO" }),
    enabled: Boolean(selected?.entity_id),
    retry: false,
  });

  const planOnly = useMutation({
    mutationFn: async () => planExplainerReferenceGeneration(projectId, String(selected?.entity_id), generationRequest(candidateCount)),
    onSuccess: (result) => {
      setPlanError(null);
      setPlan(result);
    },
    onError: (mutationError) => {
      setPlan(null);
      setPlanError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const adopt = useMutation({
    mutationFn: async ({ candidateId, mediaVersionId, lock }: { candidateId?: string; mediaVersionId?: string; lock: boolean }) => {
      if (!selected) throw new Error("请先选择一个人物 / 场景 / 道具");
      return adoptExplainerEntityReference(projectId, selected.entity_id, {
        candidate_id: candidateId,
        media_version_id: mediaVersionId,
        expected_entity_revision: Number((selected as unknown as Record<string, unknown>).revision ?? 0) || null,
        expected_reference_id: adoptedReferenceId(selected) || null,
        lock,
        actor: "local-user",
      });
    },
    onSuccess: async (result, variables) => {
      const record = result as Record<string, unknown>;
      const impact = (record.impact ?? {}) as Record<string, unknown>;
      const affected = Number(impact.affected_beat_count ?? impact.beat_count ?? 0);
      setError(null);
      setPreviewedId(null);
      setPendingUpload(null);
      setFeedback(
        (variables.lock
          ? "已采用并锁定：后续一键与批量补齐不会替换这次人工锁定。"
          : "已采用该参考图；默认未锁定，仍可被明确替换。") +
        (Number.isFinite(affected) && affected > 0
          ? `受影响画面段 ${affected} 个需要更新，其余镜头不动。`
          : "服务端未返回受影响镜头数，详情见高级区。"),
      );
      await invalidate();
    },
    onError: (mutationError) => {
      setFeedback(null);
      const message = mutationError instanceof Error ? mutationError.message : String(mutationError);
      setError(
        message.includes("409") || message.includes("STALE_REVISION") || message.includes("CONFLICT")
          ? `${message}（该人物/场景已被其他操作更新，已保留你的采用意图；请刷新后重试）`
          : message,
      );
      void invalidate();
    },
  });

  const unlock = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error("请先选择一个人物 / 场景 / 道具");
      return unlockExplainerEntityReference(projectId, selected.entity_id, {
        reference_id: adoptedReferenceId(selected),
        expected_entity_revision: Number((selected as unknown as Record<string, unknown>).revision ?? 0) || null,
        actor: "local-user",
      });
    },
    onSuccess: async () => {
      setError(null);
      setFeedback("已解锁：只改变将来的替换策略，当前参考图不会被删除或清空。");
      await invalidate();
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const retryFailed = useMutation({
    mutationFn: async (candidate: ExplainerMediaCandidate) => {
      if (!selected) throw new Error("请先选择一个人物 / 场景 / 道具");
      // §B9 item 4: a technical retry keeps the original candidate/seed identity,
      // so repeating the click replays the same command instead of spending a new
      // GPU slot on a fresh lottery.
      const request: ExplainerGenerationRequest = {
        operation_id: stableIdempotencyKey("explainer-reference-technical-retry", { projectId, entityId: selected.entity_id, candidateId: candidate.id }),
        purpose: "REFERENCE",
        mode: "TEXT_TO_IMAGE",
        candidate_count: 1,
        parent_candidate_id: candidate.parent_candidate_id,
        candidate_seeds: candidate.seed === null ? null : [candidate.seed],
        expected_entity_revision: Number((selected as unknown as Record<string, unknown>).revision ?? 0) || null,
        expected_reference_id: adoptedReferenceId(selected) || null,
      };
      return submitExplainerReferenceGeneration(projectId, selected.entity_id, request, request.operation_id);
    },
    onSuccess: async (receipt) => {
      setError(null);
      setFeedback(`已按原输入与 seed 提交技术重试：${receipt.accepted_count} / ${receipt.requested_count} 项（不计入新创作候选）。`);
      await invalidate();
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const saveSettings = useMutation({
    mutationFn: async () => {
      /**
       * The generated client still declares `visual_strategy` (it is pending
       * regeneration by its owner).  The product no longer has a 片段策略 choice: an
       * explainer picture is always produced by real AI 图生视频, so the field is
       * deliberately omitted instead of being written with a removed value.
       */
      const visualPreferences: Partial<ExplainerVisualPreferences> = {
        image_candidate_count: candidateCount,
        style_prompt_override: stylePrompt.trim() || null,
        negative_prompt_override: negativePrompt.trim() || null,
      };
      return patchExplainerVisualPreferences(projectId, {
        expected_revision: videoRevision,
        visual_preferences: visualPreferences,
        ...(styleDraft ? { channel_profile_version_id: styleDraft.channelProfileVersionId } : {}),
      });
    },
    onSuccess: async () => {
      setError(null);
      setStyleDraft(null);
      setFeedback("已保存设定：生成新设定版本，正在跑的任务继续使用旧快照；历史版本不会被改写。");
      await invalidate();
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const detectEntities = useMutation({
    mutationFn: async () => {
      const outputs = resolveOutputsForExplainer(
        overview.data?.editions,
        (overview.data?.video as Record<string, unknown> | undefined)?.source_locale as string | undefined,
        (overview.data?.video as Record<string, unknown> | undefined)?.aspect_ratio as string | undefined,
      );
      const report = await preflightExplainerPlan(projectId, { outputs });
      if (!report.executable) {
        const first = report.blockers[0];
        throw new Error(`预检未通过：${first ? `${first.message}（${first.next_step || first.code}）` : "存在阻塞项"}`);
      }
      return startExplainerRun(
        projectId,
        { plan_hash: report.plan_hash, outputs, start_workflow: true },
        stableIdempotencyKey("explainer-run", { projectId, planHash: report.plan_hash }),
      );
    },
    onSuccess: async (run) => {
      setError(null);
      setFeedback(
        `已提交提取与识别阶段：运行 ${run.id}（${run.projected_status}）。` +
        "识别只会复用已存在的共享资产；完成前这里不会显示任何虚构的实体。",
      );
      await invalidate();
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  const generateMissing = useMutation({
    mutationFn: async (targets: ExplainerEntityAsset[]) => {
      let submitted = 0;
      for (const entity of targets) {
        const request: ExplainerGenerationRequest = {
          operation_id: stableIdempotencyKey("explainer-reference-operation", {
            projectId,
            entityId: entity.entity_id,
            count: 1,
            missing: true,
            expectedEntityRevision: Number((entity as unknown as Record<string, unknown>).revision ?? 0) || null,
          }),
          purpose: "REFERENCE",
          mode: "TEXT_TO_IMAGE",
          candidate_count: 1,
          expected_entity_revision: Number((entity as unknown as Record<string, unknown>).revision ?? 0) || null,
          expected_reference_id: adoptedReferenceId(entity) || null,
        };
        const resolved = await planExplainerReferenceGeneration(projectId, entity.entity_id, request);
        if (resolved.status === "BLOCKED") continue;
        const receipt = await submitExplainerReferenceGeneration(
          projectId,
          entity.entity_id,
          { ...request, expected_plan_hash: resolved.plan_hash },
          request.operation_id,
        );
        submitted += receipt.accepted_count;
      }
      return { submitted, targets: targets.length };
    },
    onSuccess: async (result) => {
      setError(null);
      setFeedback(
        result.submitted > 0
          ? `已为 ${result.targets} 个缺参考图对象提交 ${result.submitted} 张图片；已锁定与已采用的对象不会被动到。`
          : "没有对象被提交：缺项可能已被其他操作处理，或服务端预检判定当前不可执行。",
      );
      await invalidate();
    },
    onError: (mutationError) => {
      setFeedback(null);
      setError(mutationError instanceof Error ? mutationError.message : String(mutationError));
    },
  });

  /* ------------------------------------------------- settings draft owner -- */
  const baselineRef = useRef({ count: 1, style: "", negative: "" });
  const draftHandleRef = useRef<DraftHandle | null>(null);
  const versionRef = useRef(0);
  const [draftVersion, setDraftVersion] = useState(0);
  const settingsSignature = `${candidateCount}|${stylePrompt.trim()}|${negativePrompt.trim()}`;
  const signatureRef = useRef(settingsSignature);

  useEffect(() => {
    if (persistedPreferences) {
      baselineRef.current = {
        count: persistedPreferences.image_candidate_count ?? 1,
        style: persistedPreferences.style_prompt_override ?? "",
        negative: persistedPreferences.negative_prompt_override ?? "",
      };
    }
  }, [persistedPreferences]);

  const isDirtyNow = () =>
    candidateCount !== baselineRef.current.count
    || stylePrompt.trim() !== baselineRef.current.style
    || negativePrompt.trim() !== baselineRef.current.negative;

  useEffect(() => {
    if (signatureRef.current === settingsSignature) return;
    signatureRef.current = settingsSignature;
    versionRef.current += 1;
    setDraftVersion(versionRef.current);
  }, [settingsSignature]);

  const saveSettingsRef = useRef<(expectedVersion: number) => Promise<DraftSaveResult>>(async () => ({ status: "blocked", reason: "设置草稿尚未就绪。" }));
  const discardSettingsRef = useRef<(expectedVersion: number) => Promise<DraftDiscardResult>>(async () => ({ status: "blocked", reason: "设置草稿尚未就绪。" }));
  saveSettingsRef.current = async (expectedVersion) => {
    try {
      await saveSettings.mutateAsync();
    } catch (failure) {
      return { status: "blocked", reason: failure instanceof Error ? failure.message : String(failure) };
    }
    const handle = draftHandleRef.current;
    if (handle) draftRegistry.update(handle, { version: expectedVersion, dirty: false });
    return { status: "saved", savedVersion: expectedVersion };
  };
  discardSettingsRef.current = async (expectedVersion) => {
    setCandidateCount(baselineRef.current.count);
    setStylePrompt(baselineRef.current.style);
    setNegativePrompt(baselineRef.current.negative);
    const handle = draftHandleRef.current;
    if (handle) draftRegistry.update(handle, { version: expectedVersion, dirty: false });
    return { status: "discarded", discardedVersion: expectedVersion };
  };

  useEffect(() => {
    const handle = draftRegistry.register({
      ownerId: `explainer-asset-settings:${projectId}:${selectedId ?? "none"}`,
      entityKey: selected ? `「${selected.name}」生成设置` : "人物与风格设置",
      version: versionRef.current,
      dirty: isDirtyNow(),
      save: (expectedVersion: number) => saveSettingsRef.current(expectedVersion),
      discard: (expectedVersion: number) => discardSettingsRef.current(expectedVersion),
    });
    draftHandleRef.current = handle;
    return () => {
      const live = draftHandleRef.current;
      if (live && live.token === handle.token) {
        draftRegistry.unregister(handle);
        draftHandleRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, selectedId]);

  useEffect(() => {
    const handle = draftHandleRef.current;
    if (!handle) return;
    draftRegistry.update(handle, { version: draftVersion, dirty: isDirtyNow(), entityKey: selected ? `「${selected.name}」生成设置` : "人物与风格设置" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftVersion, settingsSignature]);

  /* --------------------------------------------------------------- state --- */
  const video = (overview.data?.video ?? null) as Record<string, unknown> | null;
  const resolvedStyle = (assets.data?.resolved_style ?? null) as Record<string, unknown> | null;
  const profile = (assets.data?.channel_profile_version ?? null) as Record<string, unknown> | null;
  const styleName = String(profile?.title ?? resolvedStyle?.title ?? resolvedStyle?.name ?? "服务端默认风格版本");
  const missingReferences = entities.filter((entity) => categoryOf(entity) !== "OTHER" && needsGeneratedReference(entity));
  // Counted from the server's own binding status, so the bar never claims a reference
  // that does not exist.
  const adoptedReferences = entities.filter((entity) => referenceStatusOf(entity).value === "ADOPTED_REFERENCE");

  const state = useMemo<PageState | null>(() => {
    if (assets.isPending && !assets.data) return { kind: "loading", message: "正在载入人物、场景与风格…" };
    if (assets.isError && !assets.data) {
      return {
        kind: "failed",
        title: "无法载入人物与风格",
        body: assets.error instanceof Error ? assets.error.message : "未知错误",
        action: <button type="button" onClick={() => { void assets.refetch(); }}>重新读取</button>,
      };
    }
    if (overview.data && (overview.data.capability_snapshot?.probed === false)) {
      return {
        kind: "no_capability",
        title: "尚未接入能力探查",
        body: "无法确认本地图像模型与工作流版本，因此不会排队出图；上传与选用已有图片仍可用。",
        action: <Link to={assets.data ? routes.systemCapabilities(projectId) : routes.systemCapabilities()}>前往能力与模型</Link>,
      };
    }
    if (entities.length === 0) {
      return {
        kind: "empty",
        title: "还没有人物与场景资产",
        body: "先从讲稿识别：识别会生成实体建议并复用已存在的共享资产；在识别完成前这里不会显示虚构的名单。",
        action: <button type="button" disabled={detectEntities.isPending} onClick={() => detectEntities.mutate()}>从讲稿识别人物与场景</button>,
      };
    }
    if (missingReferences.length > 0) {
      return {
        kind: "partial",
        title: `${missingReferences.length} 个对象还没有可用的参考图`,
        body: "只补缺失且未锁定的对象；已采用的参考图与人工锁定不会被自动替换。",
      };
    }
    return null;
  }, [assets.data, assets.error, assets.isError, assets.isPending, detectEntities, entities.length, missingReferences.length, overview.data, projectId]);

  const currentMediaId = pendingUpload?.mediaVersionId
    ? pendingUpload.mediaVersionId
    : previewedId
      ? String((candidatesQuery.data?.candidates ?? []).find((item) => item.id === previewedId)?.media_version_id ?? adoptedReferenceMediaId(selected))
      : adoptedReferenceMediaId(selected);
  const currentIsPreview = Boolean(pendingUpload) || (Boolean(previewedId) && currentMediaId !== adoptedReferenceMediaId(selected));
  const readyCandidates = (candidatesQuery.data?.candidates ?? []).filter((candidate) => candidate.status === "READY" && candidate.media_version_id);

  const readinessStep = readiness.data?.steps.find((step) => step.page === "assets") ?? null;

  /* ---------------------------------------------------------- action bar --- */
  useExplainerActionBar({
    primary: missingReferences.length > 0
      ? {
        // The batch total is stated before the click (§B3.3/B9): one image per
        // missing object this pass submits.
        label: `生成缺失参考（${missingReferences.length} 项 · ${missingReferences.length} 张）`,
        onClick: () => generateMissing.mutate(missingReferences),
        busy: generateMissing.isPending,
        disabled: missingReferences.length === 0,
        disabledReason: null,
      }
      : {
        label: "下一步：配音",
        onClick: () => navigate(routes.explainerPage(projectId, "audio")),
        disabled: entities.length === 0,
        disabledReason: entities.length === 0 ? "先从讲稿识别人物与场景，才能进入配音。" : null,
        busy: false,
      },
    save: isDirtyNow() && canSaveSettings
      ? { label: "保存设定", onClick: () => saveSettings.mutate(), busy: saveSettings.isPending }
      : null,
    summary: entities.length > 0
      ? [
        `${entities.length} 个对象`,
        `${adoptedReferences.length} 个已有参考图`,
        `${missingReferences.length} 个缺参考图`,
        readinessStep ? EXPLAINER_STEP_STATUS_LABELS[readinessStep.status] : null,
      ].filter(Boolean).join(" · ")
      : "尚未建立人物与场景资产",
  });

  /* -------------------------------------------------------------- render --- */
  const gridError = candidatesQuery.isError
    ? (candidatesQuery.error instanceof Error ? candidatesQuery.error.message : "未知错误")
    : null;

  return <div className="explainer-page">
    {/* Compact style strip (§B3.1) — never a second navigation. */}
    <div className="explainer-style-strip">
      <span>当前风格：项目默认（{styleName}）</span>
      {profile?.version_no !== undefined ? <span className="badge">v{String(profile.version_no)}</span> : null}
      {!assets.data && assets.isError ? <span className="explainer-impact-note">更新失败：显示的是上一次读取的风格</span> : null}
      <span className="explainer-style-strip__actions">
        <button type="button" onClick={() => setStyleOpen(true)}>更换风格</button>
        <button type="button" disabled={detectEntities.isPending} onClick={() => detectEntities.mutate()}>
          {detectEntities.isPending ? "正在提交…" : "从讲稿识别人物与场景"}
        </button>
        <button type="button" onClick={() => setAddOpen(true)}>＋ 添加人物·场景·道具</button>
      </span>
    </div>
    {assets.data && styleDraft ? <p className="muted">待保存风格：{styleDraft.note}（保存设定后生效）</p> : null}

    {/* The server's own reason for the step status.  It distinguishes “还有对象缺参考图”
        from “不需要参考图”, so the bar never reads as a false blocker. */}
    {readinessStep?.blocked_reason ? (
      <p className="explainer-impact-note" role="status">
        {readinessStep.label}：{EXPLAINER_STEP_STATUS_LABELS[readinessStep.status]} · {readinessStep.blocked_reason}
      </p>
    ) : null}

    {assets.data && assets.isError ? (
      <p className="explainer-inline-error" role="alert">
        更新失败：{assets.error instanceof Error ? assets.error.message : "未知错误"}。以下是上一次成功读取的资产，不代表当前状态。
        <button type="button" onClick={() => { void assets.refetch(); }}>重新读取</button>
      </p>
    ) : null}

    <StateNotice state={state} />
    <InlineOk message={feedback} />
    <InlineError message={error} />

    <div className="explainer-category-switch" role="group" aria-label="对象分类（内容筛选）">
      {(["CHARACTER", "SCENE", "PROP", "OTHER"] as AssetCategory[]).map((item) => {
        if (item === "OTHER" && categories[item] === 0) return null;
        return (
          <button
            key={item}
            type="button"
            aria-pressed={category === item}
            disabled={categories[item] === 0}
            onClick={() => setSearchParams((params) => {
              const next = new URLSearchParams(params);
              next.set("category", item);
              next.delete("entity");
              return next;
            }, { replace: true })}
          >
            {CATEGORY_LABELS[item]} {categories[item]}
          </button>
        );
      })}
      <span className="muted">这是内容筛选，不是第二套制作步骤。</span>
    </div>

    {entities.length > 0 ? (
      <div className="explainer-three-pane">
        {/* ------------------------------------------------- left: objects -- */}
        <Panel title="对象" subtitle="出场镜头数与参考图状态是两个独立事实。">
          <div className="explainer-asset-list">
            {filtered.length === 0 ? <p className="muted">该分类下没有对象。</p> : null}
            {filtered.map((entity) => {
              const status = referenceStatusOf(entity);
              const appearances = appearanceCountOf(entity);
              const isCurrent = entity.entity_id === selectedId;
              const mediaId = adoptedReferenceMediaId(entity);
              return (
                <button
                  type="button"
                  key={entity.entity_id}
                  className={`explainer-asset-card${isCurrent ? " selected" : ""}`}
                  aria-current={isCurrent ? "true" : undefined}
                  onClick={() => {
                    setPreviewedId(null);
                    setPendingUpload(null);
                    setPlan(null);
                    setFeedback(null);
                    setSearchParams((params) => {
                      const next = new URLSearchParams(params);
                      next.set("entity", entity.entity_id);
                      next.set("category", categoryOf(entity));
                      return next;
                    }, { replace: true });
                  }}
                >
                  <span className="explainer-asset-card__thumb">
                    <MediaThumb
                      src={mediaId ? referenceThumbnailUrl(mediaId, "small") : null}
                      alt={`${entity.name} 参考缩略图`}
                      emptyLabel="缺图"
                      aspectRatio="1 / 1"
                      objectFit="cover"
                    />
                  </span>
                  <span className="explainer-asset-card__meta">
                    <strong title={entity.name}>{entity.name}</strong>
                    {/* Card facts only (design §B3.1): the long missing-asset explanation
                        lives in the detail pane and would otherwise push the two numbers
                        out of the fixed-height card. */}
                    <small className="muted">
                      {CATEGORY_LABELS[categoryOf(entity)]}
                      {entity.fictional ? " · 虚构" : " · 真实"}
                      {entity.reference === null && !entity.story_asset_id ? " · 尚无共享资产" : ""}
                    </small>
                    <span className="explainer-asset-card__numbers">
                      <span>{appearances === null ? "出场未统计" : `出场 ${appearances} 镜`}</span>
                      <span className={status.value === "NO_REFERENCE" || status.value === "NEEDS_UPDATE" ? "is-missing" : status.value === "ADOPTED_REFERENCE" ? "is-adopted" : ""}>
                        {status.label}
                      </span>
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
          <p className="explainer-note">
            角色出场 4 镜但没设参考图时会同时显示“出场 4 镜”和“未设参考图”，不会被显示成从未使用。
          </p>
        </Panel>

        {/* ------------------------------------- centre: reference + grid --- */}
        <Panel
          title={selected ? `当前参考图 · ${selected.name}` : "当前参考图"}
          subtitle="点候选只预览；采用才改变当前选择。"
          actions={selected ? <span className={`badge ${isReferenceMissing(selected) ? "warn" : ""}`}>{referenceStatusOf(selected).label}</span> : null}
        >
          {selected ? (
            <>
              <div className="explainer-reference-stage" role="group" aria-label="当前参考图操作">
                <div className="explainer-reference-stage__media">
                  {currentMediaId ? (
                    <MediaThumb
                      src={referenceThumbnailUrl(currentMediaId, "medium")}
                      alt={`${selected.name} ${currentIsPreview ? "正在预览的候选" : "当前采用的参考图"}`}
                      aspectRatio="1 / 1"
                      objectFit="contain"
                      loading="eager"
                    />
                  ) : (
                    <p className="muted">这个人物的参考图还没有生成或采用。</p>
                  )}
                </div>
                <small className="muted">
                  {currentIsPreview
                    ? "正在预览候选（尚未采用）：只有点击“采用”才会改变生成依赖。"
                    : currentMediaId
                      ? "当前采用的参考图（生成依赖使用这个不可变媒体版本）。"
                      : "缺少参考图：生成任务会按具体缺项提示，不会拿占位图冒充。"}
                </small>
                <div className="explainer-actions">
                  <button
                    type="button"
                    className="primary-action"
                    disabled={!currentIsPreview || adopt.isPending}
                    title={!currentIsPreview ? "先预览一个候选，预览不等于采用。" : undefined}
                    onClick={() => {
                      if (pendingUpload) {
                        adopt.mutate({ mediaVersionId: pendingUpload.mediaVersionId, lock: false });
                        return;
                      }
                      if (previewedId) adopt.mutate({ candidateId: previewedId, lock: false });
                    }}
                  >
                    采用
                  </button>
                  <button
                    type="button"
                    disabled={!currentIsPreview || adopt.isPending}
                    onClick={() => {
                      if (pendingUpload) {
                        adopt.mutate({ mediaVersionId: pendingUpload.mediaVersionId, lock: true });
                        return;
                      }
                      if (previewedId) adopt.mutate({ candidateId: previewedId, lock: true });
                    }}
                  >
                    采用并锁定
                  </button>
                  <button
                    type="button"
                    disabled={!adoptedReferenceId(selected) || unlock.isPending}
                    title={adoptedReferenceId(selected) ? undefined : "当前没有已采用的参考图可解锁。"}
                    onClick={() => unlock.mutate()}
                  >
                    解锁
                  </button>
                  <button
                    type="button"
                    disabled={!adoptedReferenceId(selected)}
                    aria-expanded={viewsOpen}
                    onClick={() => setViewsOpen((open) => !open)}
                  >
                    更多视角
                  </button>
                </div>
              </div>

              {pendingUpload ? (
                <p className="explainer-note" role="status">
                  刚选择的上传/媒体库文件还是候选（{pendingUpload.label}）：点击“采用”后才进入生成依赖。
                  <button type="button" onClick={() => setPendingUpload(null)}>取消这个候选</button>
                </p>
              ) : null}

              <MediaCandidateGrid
                page={candidatesQuery.data ?? null}
                loading={candidatesQuery.isPending}
                error={gridError}
                previewedId={previewedId}
                onPreview={(candidate) => {
                  setPendingUpload(null);
                  setPreviewedId(candidate ? candidate.id : null);
                }}
                onAdopt={(candidate) => adopt.mutate({ candidateId: candidate.id, lock: false })}
                onAdoptAndLock={(candidate) => adopt.mutate({ candidateId: candidate.id, lock: true })}
                onUnlock={() => unlock.mutate()}
                onRegenerate={() => {
                  // §B9: a regenerate is a new creative batch and defaults back
                  // to one image; the count is stated on the button before it runs.
                  setCandidateCount(1);
                  generate.mutate(1);
                }}
                onRetryFailed={(candidate) => retryFailed.mutate(candidate)}
                onReload={() => { void candidatesQuery.refetch(); }}
                onUpload={() => setPickerOpen(true)}
                onCompare={() => setCompareOpen(true)}
                nextBatchCount={candidateCount}
                title="参考图候选"
                emptyHint="点击“生成参考图”会在服务端预检后创建真实任务；也可以先上传或从媒体库选择，上传结果只会成为候选。"
              />

              {/* `feedback` and `error` are already rendered once at page level (see the
                  InlineOk/InlineError pair above the category switch).  Repeating them
                  here printed every notice twice; only the panel-specific `planError`
                  is shown at this level. */}
              <InlineError message={planError} />
            </>
          ) : <p className="muted">选择一个对象查看它的参考图与候选。</p>}
        </Panel>

        {/* ------------------------------------------ right: object details -- */}
        <div className="explainer-stack">
          <Panel title="对象设定" subtitle="外观描述来自已提取的设定与已采用参考。">
            {selected ? (
              <>
                <SettingRow label="名称" value={selected.name} />
                <SettingRow label="编号（系统产生）" value={selected.code} />
                <SettingRow label="人物真实性" value={selected.fictional ? "虚构人物" : "真实人物"} />
                <SettingRow label="人物参考来源" value="自动生成（可上传 / 从媒体库选择）" />
                <SettingRow label="外观描述" value={String((selected as unknown as Record<string, unknown>).visual_description ?? selected.missing_reason ?? "已由提取结果与参考图决定")} />
                {selected.missing_reason ? (
                  <p className="explainer-note warn">参考图：{selected.missing_reason}</p>
                ) : null}
                <p className="explainer-note">
                  年龄感、发型服装与识别特征属于实体状态版本；本机尚未提供直接编辑解说实体状态的接口，
                  因此这里只显示服务端事实，不显示一个存不进去的输入框。
                </p>
                <details className="explainer-advanced-detail">
                  <summary>高级详情（身份槽位与绑定版本）</summary>
                  <dl>
                    <dt>身份槽位状态</dt>
                    <dd>{selected.identity_input_status ?? "未声明"}</dd>
                    <dt>参考绑定 ID</dt>
                    <dd>{adoptedReferenceId(selected) || "尚未绑定"}</dd>
                    <dt>参考媒体版本</dt>
                    <dd>{adoptedReferenceMediaId(selected) || "尚未绑定"}</dd>
                    <dt>状态版本数</dt>
                    <dd>{selected.state_revisions?.length ?? 0}</dd>
                    <dt>候选计数</dt>
                    <dd>
                      REFERENCE {selected.candidate_counts?.REFERENCE ?? 0} · KEYFRAME {selected.candidate_counts?.KEYFRAME ?? 0} · VISUAL {selected.candidate_counts?.VISUAL ?? 0}
                    </dd>
                    <dt>共享资产</dt>
                    <dd>{selected.story_asset_id ?? "尚未建立共享资产（采用第一张参考图时会自动建立或复用同名资产）"}</dd>
                  </dl>
                  {selected.state_revisions && selected.state_revisions.length > 0 ? (
                    <>
                      <span className="muted">状态版本（基础场景与时态，例如日景 / 夜景）</span>
                      <div className="explainer-table-wrap">
                        <table className="explainer-table">
                          <thead><tr><th>版本</th><th>年龄</th><th>服装 / 状态</th><th>有效期</th></tr></thead>
                          <tbody>
                            {selected.state_revisions.map((revision) => (
                              <tr key={String(revision.id)}>
                                <td>v{String(revision.revision_no ?? "?")}</td>
                                <td>{revision.age === null || revision.age === undefined ? "—" : String(revision.age)}</td>
                                <td>{String(revision.wardrobe || revision.condition || revision.description || "—")}</td>
                                <td>{String(revision.valid_from_story_time ?? "?")} → {String(revision.valid_to_story_time ?? "?")}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  ) : null}
                </details>
              </>
            ) : <p className="muted">选择对象后显示。</p>}
          </Panel>

          {selected ? (
            <GenerationControls
              purpose={"REFERENCE" as ExplainerCandidatePurpose}
              candidateCounts={[1, 2, 4]}
              candidateCount={candidateCount}
              onCandidateCountChange={setCandidateCount}
              plan={plan}
              planPending={planOnly.isPending}
              planError={planError}
              onPlan={() => planOnly.mutate()}
              onSubmit={() => generate.mutate(candidateCount)}
              submitPending={generate.isPending}
              submitDisabledReason={null}
              budgetNote={budgetNoteFor(plan, candidateCount)}
            >
              <p className="explainer-note">
                生成参考图会追加候选，不替换已采用图；再生成默认 1 张，并且必须在按钮旁显示本次提交张数。
                全片画面一律由真实 AI 图生视频产出，因此这里不再提供片段策略选择。
              </p>
              <details className="explainer-details">
                <summary>设定提示词（程序编译，可核对）</summary>
                {referenceDesign.isError ? (
                  <p className="explainer-note warn">
                    设定提示词读取失败：{referenceDesign.error instanceof Error ? referenceDesign.error.message : "未知错误"}；
                    这不影响已有参考图，也不代表缺少资产。
                  </p>
                ) : null}
                {referenceDesign.data ? (
                  <>
                    <SettingRow
                      label="正向设定"
                      value={
                        <code className="explainer-prompt-text">
                          {String(
                            (referenceDesign.data as Record<string, unknown>).description_prompt ??
                              (referenceDesign.data as Record<string, unknown>).prompt ??
                              "服务端未返回提示词",
                          )}
                        </code>
                      }
                    />
                    <SettingRow
                      label="负向设定"
                      value={
                        <code className="explainer-prompt-text">
                          {String((referenceDesign.data as Record<string, unknown>).negative_prompt ?? "无")}
                        </code>
                      }
                    />
                    <SettingRow
                      label="已采用参考不变项"
                      value={`${String(
                        (referenceDesign.data as Record<string, unknown>).adopted_reference_count ?? 0,
                      )} 条`}
                    />
                    <SettingRow
                      label="未解决约束"
                      value={
                        Array.isArray((referenceDesign.data as Record<string, unknown>).unresolved_constraints) &&
                        ((referenceDesign.data as Record<string, unknown>).unresolved_constraints as unknown[]).length
                          ? JSON.stringify((referenceDesign.data as Record<string, unknown>).unresolved_constraints)
                          : "无"
                      }
                    />
                    <p className="explainer-note">
                      这段提示词由程序按固定顺序编译（资产类型 → 已知外观 → 用户设定 → 已采用参考不变项 → 风格 → 视角硬要求），
                      读取它不会调用模型；最终类型、视角硬约束与栏目风格仍由服务端统一追加。
                    </p>
                  </>
                ) : null}
              </details>
              {!canSaveSettings ? (
                <p className="explainer-note warn">
                  作品版本尚未读取，暂时不能保存设定（保存需要真实的作品 revision，不能发送 0）。
                  <button type="button" onClick={() => { void overview.refetch(); }}>重新读取作品</button>
                </p>
              ) : null}
            </GenerationControls>
          ) : null}

          <Panel title="更多视角 / 身份参考详情" subtitle="默认不强制三视图。">
            {selected ? (
              <>
                <p className="muted">
                  默认只需要一张足够识别的主参考。当前对象的身份槽位状态：{selected.identity_input_status ?? "未声明"}。
                  本机尚未接入解说实体的多视角生成批次，因此这里不会伪称已经生成正面 / 左侧 / 右侧视图。
                </p>
                {viewsOpen ? (
                  <div className="explainer-view-clause">
                    {["主参考", "正面", "左侧", "右侧", "背面", "面部特写"].map((slot) => (
                      <span key={slot}>{slot === "主参考" && adoptedReferenceId(selected) ? `${slot}（已采用）` : `${slot}（未生成）`}</span>
                    ))}
                  </div>
                ) : (
                  <p className="muted">点击“更多视角”展开共享槽位列表；附加视角只在当前工作流确实需要时才生成。</p>
                )}
              </>
            ) : <p className="muted">选择对象后显示。</p>}
          </Panel>
        </div>
      </div>
    ) : null}

    <MediaCandidateCompare
      open={compareOpen}
      candidates={readyCandidates}
      onClose={() => setCompareOpen(false)}
    />

    <Drawer open={pickerOpen} title="上传 / 从媒体库选择" onClose={() => setPickerOpen(false)}>
      <div className="explainer-workspace">
        <p className="muted">选择或上传的结果只会成为候选；点击“采用”后才进入生成依赖。</p>
        <MediaPicker
          projectId={projectId}
          value=""
          mediaKind="IMAGE"
          onChange={(mediaVersionId, item) => {
            setPendingUpload({ mediaVersionId, label: item ? `${item.stage} · v${item.version_no}` : "已选择的媒体版本" });
            setPreviewedId(null);
            setPickerOpen(false);
          }}
        />
      </div>
    </Drawer>

    <Drawer open={styleOpen} title="更换风格" onClose={() => setStyleOpen(false)}>
      <div className="explainer-workspace">
        <p className="muted">
          风格修改会生成新的风格版本：历史任务继续使用它冻结的旧版本，不会被改写。
        </p>
        <div className="explainer-actions">
          <button
            type="button"
            onClick={() => {
              setStyleDraft({ channelProfileVersionId: null, note: "项目默认（服务端默认风格版本）" });
              setStyleOpen(false);
            }}
          >
            项目默认（服务端默认风格版本）
          </button>
          {profile ? (
            <button
              type="button"
              onClick={() => {
                setStyleDraft({ channelProfileVersionId: String(profile.id), note: `保持当前冻结版本 v${String(profile.version_no ?? "?")}` });
                setStyleOpen(false);
              }}
            >
              保持当前冻结版本 v{String(profile.version_no ?? "?")}
            </button>
          ) : null}
        </div>
        <label className="explainer-field">
          <span>自定义风格描述</span>
          <textarea aria-label="自定义风格描述" value={stylePrompt} onChange={(event) => setStylePrompt(event.target.value)} />
        </label>
        <label className="explainer-field">
          <span>避免出现的内容</span>
          <textarea aria-label="避免出现的内容" value={negativePrompt} onChange={(event) => setNegativePrompt(event.target.value)} />
        </label>
        <p className="explainer-note">
          已发布的栏目风格模板列表需要服务端目录接口（当前未提供），所以这里不硬写任何风格品牌；
          可选项只有服务端默认与当前冻结版本，自定义部分会随“保存设定”写入本作品的视觉偏好。
        </p>
      </div>
    </Drawer>

    <Drawer open={addOpen} title="添加人物 · 场景 · 道具" onClose={() => setAddOpen(false)}>
      <div className="explainer-workspace">
        <p className="muted">
          人物与场景由讲稿的提取与识别阶段建立，识别会复用已存在的共享资产（不会重复创建同名对象）。
          本机尚未提供直接新建解说实体的接口，因此这里不提供一个存不进去的表单。
        </p>
        <div className="explainer-actions">
          <button type="button" disabled={detectEntities.isPending} onClick={() => { setAddOpen(false); detectEntities.mutate(); }}>
            提交识别阶段（复用已有资产）
          </button>
          <Link className="explainer-issue-link" to={routes.explainerPage(projectId, "script")}>去第 1 步补充讲稿</Link>
        </div>
      </div>
    </Drawer>
  </div>;
}

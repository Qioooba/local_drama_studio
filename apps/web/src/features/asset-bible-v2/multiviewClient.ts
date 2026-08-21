import { createStoryAssetReference, type AssetBibleItem, type StoryAssetReference } from "./api";

export type MultiViewKind = "FRONT" | "LEFT" | "RIGHT";
export type MultiViewSettings = {
  asset_state_id: string | null;
  profile_version_id: string | null;
  consistency_strength: "LOW" | "MEDIUM" | "HIGH";
  background: "CLEAN" | "TRANSPARENT" | "ORIGINAL";
};

export type MultiViewBlocker = {
  code: string;
  message: string;
  suggested_action: string | null;
  details: Record<string, unknown>;
};

export type MultiViewPreflight = {
  asset_id: string;
  project_id: string;
  capability: "IMAGE_MULTI_VIEW";
  status: "READY" | "BLOCKED";
  ready: boolean;
  blockers: MultiViewBlocker[];
  hero: { reference_id: string; media_version_id: string; asset_state_id: string | null; revision: number } | null;
  profile_resolution: Record<string, unknown> & { profile_version_id: string | null; input_role: string | null };
  views: Array<{ reference_kind: MultiViewKind; yaw_deg: number; semantic_output: MultiViewKind }>;
  plan_hash: string;
  would_persist_intent: false;
  would_create_variants: number;
  would_create_jobs: number;
};

export type MultiViewOutput = {
  media_version_id: string;
  source_artifact_id: string;
  rel_path: string;
  mime_type: string;
  sha256: string;
  integrity_status: string;
};

export type MultiViewItem = {
  reference_kind: MultiViewKind;
  yaw_deg: number;
  variant_id: string;
  variant_no: number;
  variant_status: string;
  job_id: string | null;
  job_state: string | null;
  progress: { percent?: number; phase?: string; node?: string; eta_seconds?: number; [key: string]: unknown };
  error: { code: string | null; detail: string | null } | null;
  outputs: MultiViewOutput[];
};

export type MultiViewBatch = {
  intent_id: string;
  status: string;
  created_at: string;
  completed_count: number;
  failed_count: number;
  total_count: number;
  items: MultiViewItem[];
};

type MultiViewSubmitResult = {
  intent: { id: string; status: string; [key: string]: unknown };
  items: Array<{ reference_kind: MultiViewKind; variant: Record<string, unknown>; job: Record<string, unknown> }>;
  idempotent_replay: boolean;
};

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  const body = await response.json().catch(() => null) as { error?: { message?: string; code?: string } } | T | null;
  if (!response.ok) {
    const error = (body as { error?: { message?: string; code?: string } } | null)?.error;
    throw new Error(error?.message ?? error?.code ?? `本机 API 请求失败（${response.status}）`);
  }
  return body as T;
}

export function preflightAssetMultiView(assetId: string, settings: MultiViewSettings): Promise<{ preflight: MultiViewPreflight }> {
  return requestJson(`/story-assets/${encodeURIComponent(assetId)}/generate-multiview:preflight`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
}

export function submitAssetMultiView(assetId: string, settings: MultiViewSettings, planHash: string): Promise<MultiViewSubmitResult> {
  return requestJson(`/story-assets/${encodeURIComponent(assetId)}/generate-multiview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...settings, plan_hash: planHash, idempotency_key: crypto.randomUUID() }),
  });
}

export async function getAssetMultiViewHistory(assetId: string): Promise<MultiViewBatch[]> {
  const result = await requestJson<{ asset_detail: AssetBibleItem }>(`/story-assets/${encodeURIComponent(assetId)}/detail`, { method: "GET" });
  return result.asset_detail.multiview_generations ?? [];
}

export function bindMultiViewReference(assetId: string, assetStateId: string | null, kind: MultiViewKind, mediaVersionId: string): Promise<{ reference: StoryAssetReference }> {
  return createStoryAssetReference(assetId, {
    media_version_id: mediaVersionId,
    reference_kind: kind,
    asset_state_id: assetStateId,
    label: `三视图生成 · ${kind}`,
  });
}

export function isMultiViewBatchActive(batch: MultiViewBatch): boolean {
  return !["SUCCEEDED", "FAILED", "PARTIAL_FAILED", "CANCELLED"].includes(batch.status);
}

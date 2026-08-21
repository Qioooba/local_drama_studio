import { createStoryAssetReference, type AssetBibleItem, type StoryAssetReference } from "./api";
import type { MultiViewBlocker, MultiViewOutput, MultiViewSettings } from "./multiviewClient";

export type ExpressionKind = "NEUTRAL" | "HAPPY" | "SAD" | "ANGRY" | "SURPRISED" | "FEARFUL" | "DISGUSTED" | "DETERMINED" | "CRYING";
export type ExpressionPreflight = {
  asset_id: string; project_id: string; capability: "IMAGE_EXPRESSION"; status: "READY" | "BLOCKED"; ready: boolean;
  blockers: MultiViewBlocker[]; hero: { media_version_id: string } | null; profile_resolution: Record<string, unknown>;
  plan_hash: string; would_create_jobs: number; would_create_variants: number;
};
export type ExpressionItem = {
  slot_kind: ExpressionKind; reference_kind: "EXPRESSION_GRID"; job_state: string | null;
  progress: { percent?: number; phase?: string; [key: string]: unknown }; error: { code: string | null; detail: string | null } | null;
  outputs: MultiViewOutput[];
};
export type ExpressionBatch = {
  intent_id: string; status: string; created_at: string; completed_count: number; failed_count: number; total_count: number; items: ExpressionItem[];
};

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  const body = await response.json().catch(() => null) as { error?: { message?: string; code?: string } } | T | null;
  if (!response.ok) {
    const error = (body as { error?: { message?: string; code?: string } } | null)?.error;
    throw new Error(error?.message ?? error?.code ?? `本地 API 请求失败（${response.status}）`);
  }
  return body as T;
}

export function preflightAssetExpression(assetId: string, settings: MultiViewSettings): Promise<{ preflight: ExpressionPreflight }> {
  return requestJson(`/story-assets/${encodeURIComponent(assetId)}/generate-expression:preflight`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
}
export function submitAssetExpression(assetId: string, settings: MultiViewSettings, planHash: string) {
  return requestJson(`/story-assets/${encodeURIComponent(assetId)}/generate-expression`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...settings, plan_hash: planHash, idempotency_key: crypto.randomUUID() }) });
}
export async function getAssetExpressionHistory(assetId: string): Promise<ExpressionBatch[]> {
  const result = await requestJson<{ asset_detail: AssetBibleItem }>(`/story-assets/${encodeURIComponent(assetId)}/detail`, { method: "GET" });
  return result.asset_detail.expression_generations ?? [];
}
export function bindExpressionReference(assetId: string, assetStateId: string | null, kind: ExpressionKind, mediaVersionId: string): Promise<{ reference: StoryAssetReference }> {
  return createStoryAssetReference(assetId, { media_version_id: mediaVersionId, reference_kind: "EXPRESSION_GRID", asset_state_id: assetStateId, label: `表情九宫格 · ${kind}` });
}
export const isExpressionBatchActive = (batch: ExpressionBatch) => !["SUCCEEDED", "FAILED", "PARTIAL_FAILED", "CANCELLED"].includes(batch.status);

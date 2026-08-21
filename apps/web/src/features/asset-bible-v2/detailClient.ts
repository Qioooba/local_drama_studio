import { createStoryAssetReference, type AssetBibleItem, type StoryAssetReference } from "./api";
import type { MultiViewBlocker, MultiViewOutput, MultiViewSettings } from "./multiviewClient";

export type DetailKind = "FACE_CLOSEUP" | "COSTUME_DETAIL" | "DISTINCTIVE_DETAIL";
export type DetailPreflight = {
  asset_id: string; project_id: string; capability: "IMAGE_EDIT"; status: "READY" | "BLOCKED"; ready: boolean;
  blockers: MultiViewBlocker[]; hero: { media_version_id: string } | null; profile_resolution: Record<string, unknown>;
  plan_hash: string; would_create_jobs: number; would_create_variants: number;
};
export type DetailItem = {
  slot_kind: DetailKind; reference_kind: "CLOSEUP" | "DETAIL"; job_state: string | null;
  progress: { percent?: number; phase?: string; [key: string]: unknown }; error: { code: string | null; detail: string | null } | null;
  outputs: MultiViewOutput[];
};
export type DetailBatch = { intent_id: string; status: string; created_at: string; completed_count: number; failed_count: number; total_count: number; items: DetailItem[] };

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  const body = await response.json().catch(() => null) as { error?: { message?: string; code?: string } } | T | null;
  if (!response.ok) { const error = (body as { error?: { message?: string; code?: string } } | null)?.error; throw new Error(error?.message ?? error?.code ?? `本地 API 请求失败（${response.status}）`); }
  return body as T;
}
export const preflightAssetDetail = (assetId: string, settings: MultiViewSettings): Promise<{ preflight: DetailPreflight }> => requestJson(`/story-assets/${encodeURIComponent(assetId)}/generate-detail:preflight`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
export const submitAssetDetail = (assetId: string, settings: MultiViewSettings, planHash: string) => requestJson(`/story-assets/${encodeURIComponent(assetId)}/generate-detail`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...settings, plan_hash: planHash, idempotency_key: crypto.randomUUID() }) });
export async function getAssetDetailHistory(assetId: string): Promise<DetailBatch[]> {
  const result = await requestJson<{ asset_detail: AssetBibleItem }>(`/story-assets/${encodeURIComponent(assetId)}/detail`, { method: "GET" });
  return result.asset_detail.detail_generations ?? [];
}
export function bindDetailReference(assetId: string, assetStateId: string | null, kind: DetailKind, mediaVersionId: string): Promise<{ reference: StoryAssetReference }> {
  return createStoryAssetReference(assetId, { media_version_id: mediaVersionId, reference_kind: kind === "FACE_CLOSEUP" ? "CLOSEUP" : "DETAIL", asset_state_id: assetStateId, label: `近景细节 · ${kind}` });
}
export const isDetailBatchActive = (batch: DetailBatch) => !["SUCCEEDED", "FAILED", "PARTIAL_FAILED", "CANCELLED"].includes(batch.status);

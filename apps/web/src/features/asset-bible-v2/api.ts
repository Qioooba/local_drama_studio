export type StoryAssetReference = { id: string; project_id: string; story_asset_id: string; asset_state_id: string | null; media_version_id: string; reference_kind: string; label: string; priority: number; is_locked: number | boolean; yaw_deg: number | null; pitch_deg: number | null; status: string; revision: number };
export type StoryAssetState = { id: string; code: string; label: string; state_kind: string; description: string; state: Record<string, unknown>; references: StoryAssetReference[] };
export type AssetBibleItem = {
  asset: { id: string; kind: string; code: string; name: string; description: string; status: string; revision: number; canonical_media_version_id: string | null };
  states: StoryAssetState[];
  base_references: StoryAssetReference[];
  active_state_id: string | null;
  voice: { voice_profile_version_id: string; voice_code: string; voice_title: string; status: string } | null;
  usage: { episode_ids: string[]; episodes: string[]; shots: Array<{ binding_id?: string; role_in_shot?: string; asset_state_id?: string | null; shot_id?: string; shot_code?: string; shot_status?: string; episode_id?: string; episode_code?: string; scene_id?: string | null; scene_code?: string | null; scene_title?: string | null } & Record<string, unknown>>; shot_count: number };
  readiness: { level: "READY" | "BASIC" | "EMPTY" | "STALE"; missing: string[] };
  multiview_generations?: import("./multiviewClient").MultiViewBatch[];
  expression_generations?: import("./expressionClient").ExpressionBatch[];
  detail_generations?: import("./detailClient").DetailBatch[];
};
export type AssetBible = { project_id: string; asset_count: number; items: AssetBibleItem[] };
export type AssetReferenceMediaVersion = {
  id: string;
  media_asset_id: string;
  version_no: number;
  take_no: number | null;
  stage: string;
  mime_type: string;
  byte_size: number;
  duration_ms: number | null;
  sha256: string;
  integrity_status: string;
  created_at: string;
  media_kind: string;
  probe: Record<string, unknown>;
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  return generatedRequestJson<T>(`/api/v1${path}`, init);
}

export function getAssetBible(projectId: string): Promise<{ bible: AssetBible }> {
  return requestJson(`/projects/${encodeURIComponent(projectId)}/asset-bible`);
}

export function getAssetReferenceMediaVersion(mediaVersionId: string): Promise<{ media_version: AssetReferenceMediaVersion }> {
  return requestJson(`/media-versions/${encodeURIComponent(mediaVersionId)}`);
}

export function createStoryAssetState(assetId: string, payload: { code: string; label: string; state_kind?: string; description?: string }): Promise<{ state: StoryAssetState }> {
  return requestJson(`/story-assets/${encodeURIComponent(assetId)}/states`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

export function createStoryAssetReference(assetId: string, payload: { media_version_id: string; reference_kind: string; asset_state_id?: string | null; label?: string; priority?: number; is_locked?: boolean }): Promise<{ reference: StoryAssetReference }> {
  return requestJson(`/story-assets/${encodeURIComponent(assetId)}/references`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}
import { requestJson as generatedRequestJson } from "../../generated/api";

/**
 * Client for Character Identity Pack Versioning and Slot Management (PR-CUR-007).
 */

export interface CharacterIdentityPackSlot {
  id: string;
  pack_version_id: string;
  slot_kind: "FRONT" | "LEFT" | "RIGHT" | "BACK" | "FACE" | "HALF_BODY" | "FULL_BODY" | "EXPRESSION" | string;
  media_version_id: string;
  is_primary: number | boolean;
  generation_profile_version_id?: string | null;
  authorization_id?: string | null;
  current_authorization_id?: string | null;
  authorization_status?: string | null;
  license_status?: string | null;
  integrity_status?: string;
  sha256?: string;
  byte_size?: number;
  media_kind?: string;
  mime_type?: string;
  created_at: string;
}

export interface CharacterIdentityPackVersion {
  id: string;
  pack_id: string;
  project_id: string;
  story_asset_id: string;
  asset_state_id: string | null;
  version_no: number;
  status: "DRAFT" | "READY_FOR_REVIEW" | "APPROVED" | "REJECTED" | "SUPERSEDED" | "RETIRED";
  slots_map?: Record<string, string>;
  slots?: CharacterIdentityPackSlot[];
  generator_job_id?: string | null;
  approval_metadata?: {
    approved_at?: string;
    approved_by?: string;
    comment?: string;
    slots_count?: number;
    has_three_view?: boolean;
  };
  missing_required_slots?: string[];
  approval_blockers?: Array<{ code: string; slot_kind: string | null; message: string }>;
  approval_ready?: boolean;
  content_hash?: string | null;
  retired_at?: string | null;
  retired_by?: string | null;
  retired_reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CharacterIdentityPack {
  id: string;
  project_id: string;
  story_asset_id: string;
  asset_state_id: string | null;
  code: string;
  name: string;
  description: string;
  status: "DRAFT" | "ACTIVE" | "ARCHIVED" | "RETIRED";
  current_version_id: string | null;
  current_version_no?: number | null;
  current_version_status?: string | null;
  current_slots?: Record<string, string>;
  versions?: CharacterIdentityPackVersion[];
  created_at: string;
  updated_at: string;
}

export interface ShotCharacterPackBinding {
  shot_id: string;
  story_asset_id: string;
  asset_state_id: string | null;
  identity_pack_version_id: string | null;
  character_name: string;
  character_code: string;
  pack_id?: string | null;
  pack_name?: string | null;
  pack_code?: string | null;
  bound_version_no?: number | null;
  bound_version_status?: string | null;
  bound_slots?: Record<string, string>;
  latest_approved_version_id?: string | null;
  is_stale: boolean;
  stale_reason?: string | null;
}

export interface IdentityPackVersionComparison {
  pack_id: string;
  base: { id: string; version_no: number; status: string; content_hash?: string | null };
  target: { id: string; version_no: number; status: string; content_hash?: string | null };
  slots: {
    added: string[];
    removed: string[];
    changed: Array<{ slot_kind: string; before_media_version_id: string; after_media_version_id: string }>;
    unchanged: string[];
  };
  has_changes: boolean;
}

export interface IdentityPackVersionImpact {
  pack_version_id: string;
  pack_id: string;
  version_no: number;
  status: string;
  is_current: boolean;
  shots: Array<{ shot_id: string; shot_code: string; episode_id: string; episode_code: string; story_asset_id: string; role_in_shot: string }>;
  generation_variants: Array<{ id: string; status: string; is_stale: number | boolean; stale_reason?: string | null; job_id?: string | null; job_state?: string | null }>;
  summary: { shot_binding_count: number; generation_variant_count: number; running_job_count: number };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  const body = (await response.json().catch(() => null)) as { error?: { message?: string; code?: string } } | null;
  if (!response.ok) {
    throw new Error(body?.error?.message ?? body?.error?.code ?? `本机 API 请求失败（${response.status}）`);
  }
  return body as T;
}

export function listCharacterIdentityPacks(storyAssetId: string): Promise<{ items: CharacterIdentityPack[] }> {
  return requestJson(`/story-assets/${encodeURIComponent(storyAssetId)}/identity-packs`);
}

export function createCharacterIdentityPack(
  storyAssetId: string,
  payload: { project_id: string; code: string; name: string; description?: string; asset_state_id?: string | null }
): Promise<{ pack: CharacterIdentityPack }> {
  return requestJson(`/story-assets/${encodeURIComponent(storyAssetId)}/identity-packs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getCharacterIdentityPack(packId: string): Promise<{ pack: CharacterIdentityPack }> {
  return requestJson(`/character-identity-packs/${encodeURIComponent(packId)}`);
}

export function createCharacterIdentityPackVersion(
  packId: string,
  fromVersionId?: string
): Promise<{ version: CharacterIdentityPackVersion }> {
  const query = fromVersionId ? `?from_version_id=${encodeURIComponent(fromVersionId)}` : "";
  return requestJson(`/character-identity-packs/${encodeURIComponent(packId)}/versions${query}`, {
    method: "POST",
  });
}

export function getCharacterIdentityPackVersion(versionId: string): Promise<{ version: CharacterIdentityPackVersion }> {
  return requestJson(`/character-identity-pack-versions/${encodeURIComponent(versionId)}`);
}

export function setCharacterIdentityPackSlot(
  versionId: string,
  payload: { slot_kind: string; media_version_id: string; is_primary?: boolean; generation_profile_version_id?: string | null }
): Promise<{ version: CharacterIdentityPackVersion }> {
  return requestJson(`/character-identity-pack-versions/${encodeURIComponent(versionId)}/slots`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function removeCharacterIdentityPackSlot(
  versionId: string,
  slotKind: string
): Promise<{ version: CharacterIdentityPackVersion }> {
  return requestJson(`/character-identity-pack-versions/${encodeURIComponent(versionId)}/slots/${encodeURIComponent(slotKind)}`, {
    method: "DELETE",
  });
}

export function approveCharacterIdentityPackVersion(
  versionId: string,
  comment: string = ""
): Promise<{ version: CharacterIdentityPackVersion }> {
  return requestJson(`/character-identity-pack-versions/${encodeURIComponent(versionId)}:approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ comment }),
  });
}

export function compareCharacterIdentityPackVersions(
  baseVersionId: string,
  targetVersionId: string
): Promise<{ comparison: IdentityPackVersionComparison }> {
  const search = new URLSearchParams({ target_version_id: targetVersionId });
  return requestJson(`/character-identity-pack-versions/${encodeURIComponent(baseVersionId)}:compare?${search}`);
}

export function getCharacterIdentityPackVersionImpact(
  versionId: string
): Promise<{ impact: IdentityPackVersionImpact }> {
  return requestJson(`/character-identity-pack-versions/${encodeURIComponent(versionId)}/impact`);
}

export function retireCharacterIdentityPackVersion(
  versionId: string,
  reason: string
): Promise<{ version: CharacterIdentityPackVersion }> {
  return requestJson(`/character-identity-pack-versions/${encodeURIComponent(versionId)}:retire`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

export function getShotCharacterPacks(shotId: string): Promise<{ items: ShotCharacterPackBinding[] }> {
  return requestJson(`/shots/${encodeURIComponent(shotId)}/character-identity-packs`);
}

export function bindShotCharacterPack(
  shotId: string,
  payload: { story_asset_id: string; pack_version_id: string }
): Promise<{ binding: { shot_id: string; story_asset_id: string; identity_pack_version_id: string } }> {
  return requestJson(`/shots/${encodeURIComponent(shotId)}/character-identity-packs:bind`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

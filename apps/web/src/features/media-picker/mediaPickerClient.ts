export type MediaCatalogueItem = {
  media_version_id: string;
  media_asset_id: string;
  version_no: number;
  take_no: number;
  stage: string;
  source_name: string | null;
  mime_type: string;
  byte_size: number;
  duration_ms: number | null;
  updated_at: string;
  purpose: string;
  media_kind: string;
  identity_references?: Array<{ character_name: string; slot_kind: string; pack_version_no: number; pack_version_id: string; story_asset_id: string }>;
};

export function mediaDisplayName(item: MediaCatalogueItem): string {
  const views: Record<string, string> = { FRONT: "正面", LEFT: "左侧", RIGHT: "右侧", BACK: "背面", FACE: "面部" };
  return item.identity_references?.length
    ? item.identity_references.map((reference) => `${reference.character_name} · ${views[reference.slot_kind] ?? reference.slot_kind} · 已批准身份包 v${reference.pack_version_no}`).join(" / ")
    : item.source_name || "未命名媒体";
}

export async function listProjectMedia(projectId: string, query: string, mediaKind = "IMAGE"): Promise<MediaCatalogueItem[]> {
  const search = new URLSearchParams({ q: query, media_kind: mediaKind, limit: "60" });
  return (await requestJson<{ items: MediaCatalogueItem[] }>(`/api/v1/projects/${encodeURIComponent(projectId)}/media-catalogue?${search}`)).items;
}

export async function uploadProjectMediaFile(projectId: string, file: File): Promise<string> {
  const body = await requestJson<{ media: { media_version_id: string } }>(`/api/v1/projects/${encodeURIComponent(projectId)}/media:upload`, {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream", "X-File-Name": encodeURIComponent(file.name) },
    body: file,
  });
  return body.media.media_version_id;
}

export const uploadProjectImage = uploadProjectMediaFile;

export async function importProjectImagePath(projectId: string, sourcePath: string): Promise<string> {
  const body = await requestJson<{ media: { media_version_id: string } }>("/api/v1/media:import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project_id: projectId,
      source_path: sourcePath,
      purpose: "ASSET_REFERENCE",
      owner_type: "PROJECT",
      owner_id: projectId,
      media_kind: "IMAGE",
      stage: "IMPORTED",
    }),
  });
  return body.media.media_version_id;
}

export function mediaThumbnailUrl(mediaVersionId: string): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=small&frame=poster`;
}
import { requestJson } from "../../generated/api";

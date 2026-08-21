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
};

async function errorMessage(response: Response): Promise<string> {
  const body = await response.json().catch(() => null) as { error?: { message?: string; code?: string } } | null;
  return body?.error?.message ?? body?.error?.code ?? `本机 API 请求失败（${response.status}）`;
}

export async function listProjectMedia(projectId: string, query: string, mediaKind = "IMAGE"): Promise<MediaCatalogueItem[]> {
  const search = new URLSearchParams({ q: query, media_kind: mediaKind, limit: "60" });
  const response = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/media-catalogue?${search}`);
  if (!response.ok) throw new Error(await errorMessage(response));
  return ((await response.json()) as { items: MediaCatalogueItem[] }).items;
}

export async function uploadProjectImage(projectId: string, file: File): Promise<string> {
  const response = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/media:upload`, {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream", "X-File-Name": encodeURIComponent(file.name) },
    body: file,
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  const body = await response.json() as { media: { media_version_id: string } };
  return body.media.media_version_id;
}

export function mediaThumbnailUrl(mediaVersionId: string): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=small&frame=poster`;
}

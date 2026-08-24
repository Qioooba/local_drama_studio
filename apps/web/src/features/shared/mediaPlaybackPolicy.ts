import type { SyntheticEvent } from "react";

export function mediaProxyUrl(mediaVersionId: string): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/proxy`;
}

export function mediaContentUrl(mediaVersionId: string): string {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/content`;
}

/** Fall back once when a historical proxy has not been materialized yet. */
export function fallbackToOriginalVideo(event: SyntheticEvent<HTMLVideoElement>): boolean {
  const video = event.currentTarget;
  const original = video.dataset.originalSrc;
  if (!original || video.getAttribute("src") === original) return false;
  video.src = original;
  video.load();
  return true;
}

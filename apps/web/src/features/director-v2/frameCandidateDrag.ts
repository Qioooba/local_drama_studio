import type { ShotStudioCandidate } from "../../generated/api";

export const FRAME_CANDIDATE_MIME = "application/x-director-frame-candidate";

export type FrameCandidateTransfer = {
  media_version_id: string;
  media_kind?: ShotStudioCandidate["media_kind"];
  integrity_status?: ShotStudioCandidate["integrity_status"];
  is_stale?: ShotStudioCandidate["is_stale"];
  stale_reason?: ShotStudioCandidate["stale_reason"];
};

export function frameCandidateIssue(candidate: FrameCandidateTransfer) {
  if (!candidate.media_kind || !["IMAGE", "VIDEO"].includes(candidate.media_kind)) return "只有图片或视频候选可作为帧来源";
  if (candidate.integrity_status !== "VERIFIED") return "候选尚未通过完整性校验";
  if (candidate.is_stale) return `候选已失效${candidate.stale_reason ? `：${candidate.stale_reason}` : ""}`;
  return null;
}

export function readFrameCandidate(dataTransfer: DataTransfer): FrameCandidateTransfer | null {
  try {
    const value = JSON.parse(dataTransfer.getData(FRAME_CANDIDATE_MIME)) as Partial<FrameCandidateTransfer>;
    if (!value.media_version_id || typeof value.media_version_id !== "string") return null;
    return {
      media_version_id: value.media_version_id,
      media_kind: value.media_kind ?? null,
      integrity_status: value.integrity_status ?? null,
      is_stale: Boolean(value.is_stale),
      stale_reason: value.stale_reason ?? null,
    };
  } catch {
    return null;
  }
}

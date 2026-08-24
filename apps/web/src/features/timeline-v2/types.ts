import type { AudioBinding, TimelineItemRequest, TimelineRevision } from "../../generated/api";

export type TimelineShotDraft = {
  shotId: string;
  code: string;
  mediaVersionId: string;
  durationUs: number;
  transition: "CUT" | "DISSOLVE" | "FADE";
  selectionSource: "CURRENT_MEDIA" | "SELECTION_READ_MODEL" | "MANUAL" | "MISSING";
  continuityStatus: string;
};

export type TimelineUpstream = {
  shots: TimelineShotDraft[];
  audio: AudioBinding[];
  subtitleRevisionId: string | null;
};

export function upstreamFingerprint(upstream: TimelineUpstream) {
  return JSON.stringify({
    shots: upstream.shots.map((shot) => [shot.shotId, shot.mediaVersionId, shot.durationUs]),
    audio: upstream.audio.map((item) => [item.id, item.media_version_id, item.start_us, item.end_us]),
    subtitle_revision_id: upstream.subtitleRevisionId,
  });
}

export function revisionUpstreamFingerprint(revision: TimelineRevision | null | undefined) {
  const value = revision?.input_snapshot?.upstream_selection_fingerprint;
  return typeof value === "string" ? value : null;
}

export function buildTimelineItems(shots: TimelineShotDraft[], audio: AudioBinding[]): TimelineItemRequest[] {
  let cursor = 0;
  const video = shots.map((shot, index) => {
    const start = cursor;
    cursor += shot.durationUs;
    return {
      track_type: "VIDEO",
      media_version_id: shot.mediaVersionId,
      start_us: start,
      end_us: cursor,
      parameters: { shot_id: shot.shotId, shot_code: shot.code, transition_in: index === 0 ? "CUT" : shot.transition },
    };
  });
  const audioItems = audio.map((item) => ({
    track_type: item.track_type,
    media_version_id: item.media_version_id,
    start_us: item.start_us,
    end_us: item.end_us,
    parameters: {
      audio_binding_id: item.id,
      gain_db: item.gain_db,
      loop_enabled: item.loop_enabled,
      fade_in_us: item.fade_in_us,
      fade_out_us: item.fade_out_us,
    },
  }));
  // SubtitleRevision is an immutable sibling aggregate, not media. Keep its ID
  // in input_snapshot and pass it explicitly to Jianying export; a media-less
  // timeline item would correctly be rejected by the export integrity check.
  return [...video, ...audioItems];
}

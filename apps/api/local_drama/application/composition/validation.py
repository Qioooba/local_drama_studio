"""Media-aware validation of a resolved :class:`RenderManifest`.

Every check in this module answers the same question: *can this exact manifest
be rendered into an exact number of frames right now, without hiding anything?*
The manifest itself already carries hashes, frame ranges, sample ranges and the
declared silence, so validation is a pure function of the manifest plus a media
lookup that the caller resolved from immutable ``media_versions`` rows.

What this module deliberately does NOT do:

* it never reads SQLite: ``media_lookup`` is passed in, keyed by
  ``media_version_id``, so the caller decides which immutable revisions are in
  scope and this function cannot reach "the latest" row by accident;
* it never repairs a manifest and never raises for a domain problem: it returns
  every finding so the policy layer decides what a WARNING means.  Only a
  malformed *argument* (a non-manifest, a non-mapping lookup) raises;
* it never runs FFmpeg, never measures loudness and never guesses a true peak —
  loudness/true-peak fields are reported as ``unmeasured`` unless a real
  measurement is supplied;
* it never authorises ``-shortest``: any request to hide a gap with
  shortest-selection semantics is reported as the blocker
  ``SHORTEST_SEMANTICS_FORBIDDEN``.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from local_drama.application.composition.manifest import (
    CODE_SHORTEST_SEMANTICS_FORBIDDEN,
    ManifestClip,
    RenderManifest,
    ValidationFinding,
    ValidationReport,
    as_interval,
    blocker,
    declared_silence,
    merge_interval_sets,
    track_intervals,
    warning,
)
from local_drama.domain.explainers.contracts import Ratio

__all__ = [
    "CODE_CONCAT_REENCODE_REQUIRED",
    "CODE_HASH_MISMATCH",
    "CODE_INTEGRITY_NOT_VERIFIED",
    "CODE_MEDIA_MISSING",
    "CODE_MISSING_NARRATION",
    "CODE_NARRATION_SHORTER_THAN_VIDEO",
    "CODE_SOURCE_RANGE_OUTSIDE_MEDIA",
    "CODE_SUBTITLE_OUTSIDE_FILM",
    "CODE_UNDECLARED_SILENCE",
    "CODE_VIDEO_TRACK_GAP",
    "DEFAULT_LOUDNESS_TARGET_LUFS",
    "DEFAULT_TRUE_PEAK_TARGET_DBTP",
    "concat_compatibility",
    "validate_clip_against_media",
    "validate_manifest",
    "validate_mix",
]

#: Product defaults (design §11.4).  These are *product* defaults, not a
#: platform loudness standard: a platform profile may legally override them.
DEFAULT_LOUDNESS_TARGET_LUFS = -16.0
DEFAULT_LOUDNESS_TOLERANCE_LU = 1.0
DEFAULT_TRUE_PEAK_TARGET_DBTP = -1.0

CODE_MEDIA_MISSING = "MEDIA_MISSING"
CODE_HASH_MISMATCH = "MEDIA_HASH_MISMATCH"
CODE_INTEGRITY_NOT_VERIFIED = "MEDIA_INTEGRITY_NOT_VERIFIED"
CODE_SOURCE_RANGE_OUTSIDE_MEDIA = "SOURCE_RANGE_OUTSIDE_MEDIA"
CODE_VIDEO_TRACK_GAP = "VIDEO_TRACK_MISSING_FRAMES"
CODE_NARRATION_SHORTER_THAN_VIDEO = "NARRATION_SHORTER_THAN_VIDEO"
CODE_MISSING_NARRATION = "NARRATION_SEGMENT_MISSING"
CODE_UNDECLARED_SILENCE = "UNDECLARED_SILENCE"
CODE_SUBTITLE_OUTSIDE_FILM = "SUBTITLE_OUTSIDE_FILM"
CODE_CONCAT_REENCODE_REQUIRED = "CONCAT_REENCODE_REQUIRED"
CODE_TRACK_OVERLAP = "TRACK_FRAME_OVERLAP"

INTEGRITY_VERIFIED = "VERIFIED"

#: Keys compared when two encoded streams are candidates for stream copy.
_CODEC_KEYS: tuple[str, ...] = (
    "video_codec",
    "codec",
    "video_codec_tag",
    "profile",
    "level",
    "pix_fmt",
    "time_base",
    "video_time_base",
    "extradata_hash",
    "extradata_sha256",
    "sample_rate_hz",
    "audio_codec",
    "channel_layout",
    "sample_fmt",
    "audio_time_base",
)


def _media_number(media: Mapping[str, Any], key: str) -> float | None:
    value = media.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _media_duration_us(media: Mapping[str, Any]) -> int | None:
    for key in ("duration_us",):
        value = _media_number(media, key)
        if value is not None:
            return int(value)
    value = _media_number(media, "duration_ms")
    if value is not None:
        return int(round(value * 1000))
    return None


def _media_sample_rate(media: Mapping[str, Any]) -> int | None:
    value = _media_number(media, "sample_rate_hz")
    if value is None:
        value = _media_number(media, "audio_sample_rate_hz")
    return None if value is None or value <= 0 else int(value)


def _integrity_verified(media: Mapping[str, Any]) -> bool:
    value = media.get("integrity_status")
    if value is None:
        # Absent is not the same as verified; the caller must say so explicitly.
        return False
    return str(value).strip().upper() == INTEGRITY_VERIFIED


# --------------------------------------------------------------------------- #
# per-clip checks
# --------------------------------------------------------------------------- #
def validate_clip_against_media(clip: ManifestClip, media: Mapping[str, Any] | None) -> list[ValidationFinding]:
    """Check one clip against the immutable media row it froze.

    Reported problems: the media row is missing, its sha256 no longer matches
    the manifest, its integrity is not ``VERIFIED``, the requested source window
    runs past the media duration, or the clip's declared sample range cannot fit
    the media's own sample rate.
    """

    findings: list[ValidationFinding] = []
    clip_ref = {"clip_id": clip.clip_id, "track": clip.track}
    if clip.media_version_id is None:
        # A generated canvas (infographic, colour, declared silence carrier)
        # legitimately has no media row; validation of *that* belongs to the
        # manifest's own tiling checks.
        if clip.media_sha256:
            findings.append(
                blocker(
                    CODE_MEDIA_MISSING,
                    "条目声明了媒体哈希却没有 media_version_id",
                    **clip_ref,
                )
            )
        return findings
    if media is None:
        findings.append(
            blocker(
                CODE_MEDIA_MISSING,
                "清单引用的媒体版本不存在或不属于本作品",
                media_version_id=clip.media_version_id,
                **clip_ref,
            )
        )
        return findings

    expected_hash = str(clip.media_sha256 or "")
    actual_hash = str(media.get("sha256") or "")
    if expected_hash and actual_hash and expected_hash != actual_hash:
        findings.append(
            blocker(
                CODE_HASH_MISMATCH,
                "媒体哈希与清单冻结的不一致，禁止继续渲染",
                media_version_id=clip.media_version_id,
                expected_sha256=expected_hash,
                actual_sha256=actual_hash,
                **clip_ref,
            )
        )
    elif expected_hash and not actual_hash:
        findings.append(
            blocker(
                CODE_HASH_MISMATCH,
                "媒体版本缺少 sha256，无法证明与清单冻结的一致",
                media_version_id=clip.media_version_id,
                **clip_ref,
            )
        )
    elif not expected_hash:
        findings.append(
            warning(
                CODE_HASH_MISMATCH,
                "条目未声明 media_sha256，无法校验完整性",
                media_version_id=clip.media_version_id,
                **clip_ref,
            )
        )

    if not _integrity_verified(media):
        findings.append(
            blocker(
                CODE_INTEGRITY_NOT_VERIFIED,
                "媒体完整性状态不是 VERIFIED，禁止用于渲染",
                media_version_id=clip.media_version_id,
                integrity_status=media.get("integrity_status"),
                **clip_ref,
            )
        )

    duration_us = _media_duration_us(media)
    if duration_us is not None and clip.source_out_us is not None:
        if int(clip.source_out_us) > duration_us:
            findings.append(
                blocker(
                    CODE_SOURCE_RANGE_OUTSIDE_MEDIA,
                    "条目请求的源区间超出了媒体时长",
                    media_version_id=clip.media_version_id,
                    source_in_us=clip.source_in_us,
                    source_out_us=int(clip.source_out_us),
                    media_duration_us=duration_us,
                    **clip_ref,
                )
            )
    if clip.source_out_us is None:
        findings.append(
            warning(
                "SOURCE_RANGE_UNDECLARED",
                "条目未声明源区间；无法证明其落在媒体时长内",
                media_version_id=clip.media_version_id,
                **clip_ref,
            )
        )

    media_rate = _media_sample_rate(media)
    if media_rate is not None and clip.sample_start is not None and clip.sample_end_exclusive is not None:
        # The clip's sample range is expressed at the manifest rate; convert back
        # to the media's own rate before comparing, so a 48 kHz manifest and a
        # 44.1 kHz take do not produce a false overflow.
        if clip.source_in_us is not None and clip.source_out_us is not None and duration_us:
            span_us = int(clip.source_out_us) - int(clip.source_in_us)
            media_samples = int(round(span_us * media_rate / 1_000_000))
            declared = int(clip.sample_end_exclusive) - int(clip.sample_start)
            if declared > media_samples + max(1, media_rate // 100):
                findings.append(
                    warning(
                        "SAMPLE_RANGE_EXCEEDS_MEDIA",
                        "条目的采样区间长于媒体在该源区间内可提供的采样数",
                        media_version_id=clip.media_version_id,
                        declared_samples=declared,
                        media_samples=media_samples,
                        media_sample_rate_hz=media_rate,
                        **clip_ref,
                    )
                )

    dims = (_media_number(media, "width"), _media_number(media, "height"))
    if clip.track.upper() in {"VIDEO", "OVERLAY"} and all(value is not None for value in dims):
        if float(dims[0] or 0) <= 0 or float(dims[1] or 0) <= 0:
            findings.append(
                blocker(
                    "MEDIA_GEOMETRY_INVALID",
                    "媒体声明的画幅尺寸无效",
                    media_version_id=clip.media_version_id,
                    **clip_ref,
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# stream-copy compatibility
# --------------------------------------------------------------------------- #
def concat_compatibility(
    left_signature: Mapping[str, Any] | None,
    right_signature: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Decide whether two encoded chunks may be joined with ``-c copy``.

    Stream copy is legal only when codec, codec extradata, timebase, pixel
    format and the audio parameters all agree.  Anything that differs — or that
    is absent on either side, because an unknown parameter cannot be proven
    compatible — forces an explicit re-encode.
    """

    left = dict(left_signature or {})
    right = dict(right_signature or {})
    differences: list[dict[str, Any]] = []
    for key in _CODEC_KEYS:
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value is None and right_value is None:
            continue
        if left_value != right_value:
            differences.append({"field": key, "left": left_value, "right": right_value})
    if not left or not right:
        differences.append({"field": "signature", "left": bool(left), "right": bool(right)})
    compatible = not differences
    return {
        "compatible": compatible,
        "reencode_required": not compatible,
        "differences": differences,
        "checked_fields": list(_CODEC_KEYS),
        "stream_copy_forbidden_reason": (
            None
            if compatible
            else "编解码参数不一致，必须显式重编码，不能用流复制掩盖"
        ),
    }


# --------------------------------------------------------------------------- #
# manifest validation
# --------------------------------------------------------------------------- #
def _track_sequence(clips: Sequence[ManifestClip], track: str) -> list[ManifestClip]:
    wanted = track.upper()
    ordered = [clip for clip in clips if clip.track.upper() == wanted]
    return sorted(ordered, key=lambda clip: (int(clip.start_frame), int(clip.end_frame_exclusive)))


def _first_gap(intervals: Sequence[tuple[int, int]], total_frames: int) -> tuple[int, int] | None:
    cursor = 0
    for start, end in intervals:
        if start > cursor:
            return (cursor, min(start, total_frames))
        cursor = max(cursor, end)
        if cursor >= total_frames:
            return None
    if cursor < total_frames:
        return (cursor, total_frames)
    return None


def _declared_silence_intervals(manifest: RenderManifest) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for entry in declared_silence(manifest.meta):
        if "start_frame" in entry and "end_frame_exclusive" in entry:
            intervals.append(as_interval(entry, label="declared_silence[]"))
    return merge_interval_sets(intervals)


def _declared_silence_samples(manifest: RenderManifest) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for entry in declared_silence(manifest.meta):
        start = entry.get("sample_start")
        end = entry.get("sample_end_exclusive")
        if start is None or end is None:
            continue
        if int(end) > int(start):
            intervals.append((int(start), int(end)))
    return merge_interval_sets(intervals)


def _subtitle_cues(track: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    cues = track.get("cues")
    if cues is None:
        return []
    if isinstance(cues, Mapping):
        cues = [cues]
    if not isinstance(cues, (list, tuple)):
        return []
    return [cue for cue in cues if isinstance(cue, Mapping)]


def validate_manifest(
    manifest: RenderManifest,
    *,
    media_lookup: Mapping[str, Mapping[str, Any]],
) -> ValidationReport:
    """Full media-aware validation of one manifest.

    Detects: missing media, hash mismatch, integrity not ``VERIFIED``, source
    ranges outside the media duration, non-monotonic or overlapping frames inside
    a track, gaps in the video track, subtitle cues outside the film, a video
    track shorter than the narration, narration missing for a linked segment,
    undeclared silence, ``total_frames`` disagreeing with the video track, and
    any shortest-selection request (``SHORTEST_SEMANTICS_FORBIDDEN``).
    """

    if not isinstance(manifest, RenderManifest):
        raise TypeError("validate_manifest 需要 RenderManifest 实例")
    if not isinstance(media_lookup, Mapping):
        raise TypeError("media_lookup 必须是 media_version_id -> 媒体信息 的映射")

    findings: list[ValidationFinding] = []
    total_frames = int(manifest.total_frames)
    sample_rate = int(manifest.audio_sample_rate_hz)

    # ---------------------------------------------------------------- media rows
    for clip in manifest.clips:
        media = None
        if clip.media_version_id is not None:
            media = media_lookup.get(str(clip.media_version_id))
        findings.extend(validate_clip_against_media(clip, media))

    # ------------------------------------------------- shortest-selection semantics
    for clip in manifest.clips:
        if clip.declares_shortest_selection():
            findings.append(
                blocker(
                    CODE_SHORTEST_SEMANTICS_FORBIDDEN,
                    "清单条目要求用最短流语义掩盖缺口；缺失的音频必须被诊断，合法静音必须显式声明",
                    clip_id=clip.clip_id,
                    track=clip.track,
                )
            )
    for key in ("shortest", "use_shortest", "shortest_selection", "hide_gap_with_shortest"):
        if manifest.mix.get(key) is True:
            findings.append(
                blocker(
                    CODE_SHORTEST_SEMANTICS_FORBIDDEN,
                    "混音计划要求用最短流语义掩盖缺口；这被禁止",
                    mix_key=key,
                )
            )

    # ---------------------------------------------------------------- per track
    for track in ("VIDEO", "NARRATION", "BGM", "SFX", "SUBTITLE", "OVERLAY"):
        clips = _track_sequence(manifest.clips, track)
        if not clips:
            continue
        cursor: int | None = None
        for clip in clips:
            start, end = int(clip.start_frame), int(clip.end_frame_exclusive)
            if cursor is not None and start < cursor:
                findings.append(
                    blocker(
                        CODE_TRACK_OVERLAP,
                        f"轨道 {track} 存在重叠帧区间，渲染会出现同一帧两次",
                        track=track,
                        previous_end=cursor,
                        clip_id=clip.clip_id,
                        start_frame=start,
                    )
                )
            elif cursor is not None and start > cursor:
                findings.append(
                    warning(
                        "TRACK_FRAME_GAP",
                        f"轨道 {track} 的相邻条目之间有未声明的空洞",
                        track=track,
                        gap=[cursor, start],
                        clip_id=clip.clip_id,
                    )
                )
            cursor = max(cursor or 0, end)

    video_merged = track_intervals(manifest.clips, "VIDEO")
    video_clips = _track_sequence(manifest.clips, "VIDEO")
    if not video_clips:
        findings.append(blocker("NO_VIDEO_TRACK", "清单缺少视频轨，无法渲染"))
    elif total_frames > 0:
        if video_merged != [(0, total_frames)]:
            gap = _first_gap(video_merged, total_frames)
            findings.append(
                blocker(
                    CODE_VIDEO_TRACK_GAP,
                    "视频轨没有铺满整片：存在空洞或超出片长",
                    coverage=[list(item) for item in video_merged],
                    total_frames=total_frames,
                    first_gap=None if gap is None else [gap[0], gap[1]],
                )
            )
        video_frames = sum(clip.frames for clip in video_clips)
        if video_frames != total_frames:
            findings.append(
                blocker(
                    "TOTAL_FRAMES_MISMATCH",
                    "total_frames 与视频轨的实际帧数不一致",
                    total_frames=total_frames,
                    video_track_frames=video_frames,
                )
            )

    # ------------------------------------------------------- narration coverage
    narration_clips = _track_sequence(manifest.clips, "NARRATION")
    narration_frames = sum(clip.frames for clip in narration_clips)
    narration_merged = track_intervals(manifest.clips, "NARRATION")
    declared_frames = _declared_silence_intervals(manifest)
    if total_frames > 0:
        if video_clips and narration_frames < total_frames:
            uncovered = _first_gap(merge_interval_sets([*narration_merged, *declared_frames]), total_frames)
            if uncovered is not None:
                findings.append(
                    blocker(
                        CODE_NARRATION_SHORTER_THAN_VIDEO,
                        "旁白短于画面且未声明静音：缺失的旁白必须被诊断，不能用最短流或截断画面掩盖",
                        total_frames=total_frames,
                        narration_frames=narration_frames,
                        first_uncovered_frame=uncovered[0],
                        first_uncovered_end=uncovered[1],
                    )
                )
        if narration_clips:
            for clip in narration_clips:
                if not str(clip.narration_segment_id or "").strip():
                    findings.append(
                        blocker(
                            CODE_MISSING_NARRATION,
                            "旁白条目没有关联 narration_segment_id，无法证明它覆盖了解说句",
                            clip_id=clip.clip_id,
                            start_frame=int(clip.start_frame),
                            end_frame_exclusive=int(clip.end_frame_exclusive),
                        )
                    )
        for entry in declared_silence(manifest.meta):
            if "start_frame" not in entry or "end_frame_exclusive" not in entry:
                start_sample = entry.get("sample_start")
                end_sample = entry.get("sample_end_exclusive")
                if start_sample is None or end_sample is None:
                    findings.append(
                        blocker(
                            CODE_UNDECLARED_SILENCE,
                            "静音声明缺少帧区间或采样区间，无法判定它覆盖哪一段",
                            entry=dict(entry),
                        )
                    )
            if not str(entry.get("reason") or "").strip():
                findings.append(
                    blocker(
                        CODE_UNDECLARED_SILENCE,
                        "静音声明缺少原因，静音必须是显式且可解释的决定",
                        entry=dict(entry),
                    )
                )

    # ------------------------------------------------------------------ silence
    for track in ("BGM", "SFX"):
        clips = _track_sequence(manifest.clips, track)
        if not clips or total_frames <= 0:
            continue
        covered = merge_interval_sets([*track_intervals(manifest.clips, track), *declared_frames])
        gap = _first_gap(covered, total_frames)
        if gap is not None:
            findings.append(
                blocker(
                    CODE_UNDECLARED_SILENCE,
                    f"轨道 {track} 的静音区间未声明；所有音频静音都必须由 declare_silence 声明",
                    track=track,
                    gap=[gap[0], gap[1]],
                )
            )
    if not narration_clips and video_clips and total_frames > 0:
        covered = merge_interval_sets(declared_frames)
        gap = _first_gap(covered, total_frames)
        if gap is not None:
            findings.append(
                blocker(
                    CODE_UNDECLARED_SILENCE,
                    "整片没有旁白也没有声明静音；缺失旁白必须被诊断，静音必须显式声明",
                    first_gap=[gap[0], gap[1]],
                )
            )

    # ---------------------------------------------------------------- subtitles
    for index, track in enumerate(manifest.subtitle_tracks):
        locale = str(track.get("locale") or track.get("language") or "")
        for cue in _subtitle_cues(track):
            start = cue.get("start_frame")
            end = cue.get("end_frame_exclusive")
            if start is None or end is None:
                findings.append(
                    blocker(
                        "SUBTITLE_CUE_INVALID",
                        "字幕条目缺少帧区间",
                        track_index=index,
                        locale=locale,
                        cue_id=cue.get("cue_id"),
                    )
                )
                continue
            start_frame, end_frame = int(start), int(end)
            if end_frame <= start_frame:
                findings.append(
                    blocker(
                        "SUBTITLE_CUE_INVALID",
                        "字幕条目必须满足 end_frame_exclusive > start_frame",
                        track_index=index,
                        locale=locale,
                        cue_id=cue.get("cue_id"),
                        start_frame=start_frame,
                        end_frame_exclusive=end_frame,
                    )
                )
            elif start_frame < 0 or end_frame > total_frames:
                findings.append(
                    blocker(
                        CODE_SUBTITLE_OUTSIDE_FILM,
                        "字幕条目超出了成片范围",
                        track_index=index,
                        locale=locale,
                        cue_id=cue.get("cue_id"),
                        start_frame=start_frame,
                        end_frame_exclusive=end_frame,
                        total_frames=total_frames,
                    )
                )

    # ---------------------------------------------------------------------- mix
    if manifest.mix:
        mix_report = validate_mix(
            mix=manifest.mix,
            total_samples=int(manifest.total_samples),
            sample_rate_hz=sample_rate,
            fps=manifest.fps,
        )
        findings.extend(mix_report.findings)
    elif narration_clips:
        findings.append(
            warning(
                "MIX_UNDECLARED",
                "清单有旁白轨却没有混音计划，无法证明旁白确实进入成片音轨",
                narration_clips=len(narration_clips),
            )
        )

    return ValidationReport(tuple(findings))


# --------------------------------------------------------------------------- #
# mix validation
# --------------------------------------------------------------------------- #
def _mix_segments(mix: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = mix.get("segments")
    if raw is None:
        raw = mix.get("narration")
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [segment for segment in raw if isinstance(segment, Mapping)]


def _segment_sample_range(
    segment: Mapping[str, Any],
    *,
    sample_rate_hz: int,
    fps: Ratio | None = None,
) -> tuple[int, int] | None:
    """Resolve a mix segment's sample range, exactly.

    An explicit ``sample_start``/``sample_end_exclusive`` pair wins.  Otherwise
    the declared frame range is converted once with the manifest's exact rational
    frame rate, never by accumulating a float seconds value.
    """

    start = segment.get("sample_start")
    end = segment.get("sample_end_exclusive")
    if start is not None and end is not None:
        return (int(start), int(end))
    start_frame = segment.get("start_frame")
    end_frame = segment.get("end_frame_exclusive")
    if start_frame is None or end_frame is None or fps is None:
        return None
    return (
        fps.samples_for_frames(int(start_frame), int(sample_rate_hz)),
        fps.samples_for_frames(int(end_frame), int(sample_rate_hz)),
    )


def validate_mix(
    *,
    mix: Mapping[str, Any],
    total_samples: int,
    sample_rate_hz: int,
    fps: Ratio | None = None,
) -> ValidationReport:
    """Validate the mix plan without measuring anything.

    Checks that every declared narration segment is covered exactly once, that
    every silence region in the mix is declared (with a reason, in the manifest's
    own declaration list or inline), and that loudness / true-peak fields are
    either genuinely measured or explicitly reported as ``unmeasured``.  No
    measurement is invented here: ``-16 LUFS ±1`` and ``true peak ≤ -1 dBTP`` are
    the product's documented defaults (design §11.4), not a platform standard.
    """

    if not isinstance(mix, Mapping):
        raise TypeError("mix 必须是映射")
    if int(sample_rate_hz) <= 0:
        raise ValueError("sample_rate_hz 必须为正")
    if int(total_samples) < 0:
        raise ValueError("total_samples 不能为负")

    findings: list[ValidationFinding] = []
    rate = int(sample_rate_hz)
    total = int(total_samples)

    declared_sample_ranges = merge_interval_sets(
        [
            as_interval((entry["sample_start"], entry["sample_end_exclusive"]), label="declared_silence[]")
            for entry in (mix.get("declared_silence") or [])
            if isinstance(entry, Mapping)
            and entry.get("sample_start") is not None
            and entry.get("sample_end_exclusive") is not None
        ]
    )
    declared_reasons: dict[tuple[int, int], str] = {}
    for entry in mix.get("declared_silence") or []:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("sample_start") is None or entry.get("sample_end_exclusive") is None:
            continue
        key = (int(entry["sample_start"]), int(entry["sample_end_exclusive"]))
        declared_reasons[key] = str(entry.get("reason") or "")

    expected_segments: list[str] = [
        str(segment_id) for segment_id in (mix.get("expected_segment_ids") or []) if str(segment_id).strip()
    ]
    seen: dict[str, int] = {}
    for index, segment in enumerate(_mix_segments(mix)):
        segment_id = str(segment.get("narration_segment_id") or segment.get("segment_id") or "")
        if not segment_id:
            findings.append(
                blocker(
                    CODE_MISSING_NARRATION,
                    "混音计划的旁白片段没有 narration_segment_id",
                    segment_index=index,
                )
            )
            continue
        seen[segment_id] = seen.get(segment_id, 0) + 1
        sample_range = _segment_sample_range(segment, sample_rate_hz=rate, fps=fps)
        if sample_range is None:
            findings.append(
                blocker(
                    "MIX_SEGMENT_RANGE_MISSING",
                    "混音计划的旁白片段缺少采样区间；不能用浮点累加推断位置",
                    segment_index=index,
                    narration_segment_id=segment_id,
                )
            )
            continue
        start, end = sample_range
        if end <= start:
            findings.append(
                blocker(
                    "MIX_SEGMENT_RANGE_INVALID",
                    "混音计划的旁白片段采样区间必须满足 end > start",
                    narration_segment_id=segment_id,
                    sample_start=start,
                    sample_end_exclusive=end,
                )
            )
        if start < 0 or end > total:
            findings.append(
                blocker(
                    "MIX_SEGMENT_OUTSIDE_FILM",
                    "混音计划的旁白片段超出了成片采样总数",
                    narration_segment_id=segment_id,
                    sample_start=start,
                    sample_end_exclusive=end,
                    total_samples=total,
                )
            )
        if not str(segment.get("take_id") or segment.get("narration_take_id") or "").strip():
            findings.append(
                warning(
                    "MIX_SEGMENT_TAKE_UNDECLARED",
                    "混音计划的旁白片段没有冻结 take_id",
                    narration_segment_id=segment_id,
                )
            )
        if segment.get("shortest") is True or segment.get("use_shortest") is True:
            findings.append(
                blocker(
                    CODE_SHORTEST_SEMANTICS_FORBIDDEN,
                    "混音片段要求用最短流语义掩盖缺口；这被禁止",
                    narration_segment_id=segment_id,
                )
            )

    for segment_id, count in seen.items():
        if count > 1:
            findings.append(
                blocker(
                    "MIX_SEGMENT_DUPLICATED",
                    "同一旁白片段在混音计划中出现了多次",
                    narration_segment_id=segment_id,
                    occurrences=count,
                )
            )
    for segment_id in expected_segments:
        if segment_id not in seen:
            findings.append(
                blocker(
                    CODE_MISSING_NARRATION,
                    "混音计划缺少该旁白片段；缺失旁白必须被诊断，不能用最短流掩盖",
                    narration_segment_id=segment_id,
                )
            )

    covered = merge_interval_sets(
        [
            sample_range
            for segment in _mix_segments(mix)
            if (sample_range := _segment_sample_range(segment, sample_rate_hz=rate, fps=fps)) is not None
        ]
    )
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for start, end in merge_interval_sets([*covered, *declared_sample_ranges]):
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total:
        gaps.append((cursor, total))
    for gap in gaps:
        findings.append(
            blocker(
                CODE_UNDECLARED_SILENCE,
                "混音计划存在未声明的静音区间；静音必须显式声明",
                sample_start=gap[0],
                sample_end_exclusive=gap[1],
            )
        )
    for key, reason in declared_reasons.items():
        if not reason.strip():
            findings.append(
                blocker(
                    CODE_UNDECLARED_SILENCE,
                    "混音计划的静音声明缺少原因",
                    sample_start=key[0],
                    sample_end_exclusive=key[1],
                )
            )

    declared_frames = _declared_frame_silence(mix)
    for entry in declared_frames:
        if not str(entry.get("reason") or "").strip():
            findings.append(
                blocker(
                    CODE_UNDECLARED_SILENCE,
                    "混音计划的静音声明缺少原因",
                    entry=dict(entry),
                )
            )

    loudness = mix.get("measured_loudness_lufs")
    if loudness is None:
        findings.append(
            warning(
                "LOUDNESS_UNMEASURED",
                "未提供响度测量，loudnorm 目标尚未被验证",
                target_lufs=float(mix.get("loudness_target_lufs") or DEFAULT_LOUDNESS_TARGET_LUFS),
                tolerance_lu=float(mix.get("loudness_tolerance_lu") or DEFAULT_LOUDNESS_TOLERANCE_LU),
                measurement_status="unmeasured",
                product_default_not_platform_standard=True,
            )
        )
    else:
        target = float(mix.get("loudness_target_lufs") or DEFAULT_LOUDNESS_TARGET_LUFS)
        tolerance = float(mix.get("loudness_tolerance_lu") or DEFAULT_LOUDNESS_TOLERANCE_LU)
        if abs(float(loudness) - target) > tolerance:
            findings.append(
                warning(
                    "LOUDNESS_OUT_OF_BAND",
                    "实测响度超出产品默认区间",
                    measured_lufs=float(loudness),
                    target_lufs=target,
                    tolerance_lu=tolerance,
                )
            )
    true_peak = mix.get("measured_true_peak_dbtp")
    if true_peak is None:
        findings.append(
            warning(
                "TRUE_PEAK_UNMEASURED",
                "未提供真峰值测量，真峰值上限尚未被验证",
                target_dbtp=float(mix.get("true_peak_target_dbtp") or DEFAULT_TRUE_PEAK_TARGET_DBTP),
                measurement_status="unmeasured",
                product_default_not_platform_standard=True,
            )
        )
    else:
        target_peak = float(mix.get("true_peak_target_dbtp") or DEFAULT_TRUE_PEAK_TARGET_DBTP)
        if float(true_peak) > target_peak:
            findings.append(
                blocker(
                    "TRUE_PEAK_EXCEEDED",
                    "实测真峰值超过产品默认上限，交付前必须处理",
                    measured_dbtp=float(true_peak),
                    target_dbtp=target_peak,
                )
            )

    if not _mix_segments(mix) and total > 0:
        findings.append(
            warning(
                "MIX_EMPTY",
                "混音计划没有任何旁白片段；整片静音必须是明确的设计决定",
                total_samples=total,
            )
        )
    return ValidationReport(tuple(findings))


def _declared_frame_silence(mix: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries = mix.get("declared_silence")
    if not isinstance(entries, (list, tuple)):
        return []
    return [entry for entry in entries if isinstance(entry, Mapping)]

"""Deterministic RenderManifest construction and exact time arithmetic.

The manifest is the single input truth for the explainer render core.  It is a
frozen, fully canonical description of one composition revision: exact rational
frame rate, half-open frame ranges per clip and per track, audio sample ranges,
subtitle tracks, the mix plan and the blocked render's chunking.  Every value
that FFmpeg will later act on is derived by integer arithmetic here, so a
downstream command builder never has to guess and never has to re-round.

What this module deliberately does NOT do:

* it never opens a database, reads a row, or resolves a "latest" candidate —
  every identifier in a manifest is supplied by the caller and is immutable;
* it never invokes FFmpeg, ffprobe, or any subprocess;
* it never decides which media is *good* (that is
  :mod:`local_drama.application.composition.validation`, which needs a media
  lookup and is therefore separate);
* it never invents a duration: a manifest states either the measured natural
  narration length or an exact target frame count, and legal silence must be
  declared explicitly through :func:`declare_silence`;
* it never accumulates subtitle or audio timing through repeated float
  addition: cues and mixes are declared in integer frames/samples and mapped
  once through :func:`map_source_time_to_frames`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Iterable, Mapping, Sequence

from local_drama.domain.explainers.contracts import (
    AspectRatio,
    DurationPolicy,
    ExplainerContractError,
    Ratio,
    content_hash,
)

MANIFEST_KIND = "localdrama.composition.render_manifest.v1"
CHUNK_PLAN_KIND = "localdrama.composition.chunk_plan.v1"

__all__ = [
    "CHUNK_PLAN_KIND",
    "CODE_MANIFEST_MEDIA_MISMATCH",
    "CODE_SHORTEST_SEMANTICS_FORBIDDEN",
    "CODE_UNKNOWN_TRACK",
    "MANIFEST_KIND",
    "ManifestChunkSpec",
    "ManifestClip",
    "RenderManifest",
    "Severity",
    "ValidationFinding",
    "ValidationReport",
    "as_interval",
    "build_manifest",
    "coverage_intervals",
    "declare_silence",
    "declared_silence",
    "derive_target_frames",
    "duration_policy_outcome",
    "ensure_track_tiling",
    "find_free_interval",
    "frame_window_for_range",
    "manifest_clip",
    "map_source_time_to_frames",
    "merge_interval_sets",
    "plan_chunks",
    "render_clip_spec",
    "subtitle_cue",
    "subtract_overlap",
    "track_intervals",
]

#: Tracks that the renderer understands.  Anything else is a domain violation.
LEGAL_TRACKS: frozenset[str] = frozenset(
    {"VIDEO", "NARRATION", "BGM", "SFX", "SUBTITLE", "OVERLAY"}
)

#: Item kinds from ``composition_items.item_kind`` (migration 0102).
LEGAL_ITEM_KINDS: frozenset[str] = frozenset(
    {
        "VIDEO_CLIP",
        "IMAGE_CLIP",
        "MOTION_CLIP",
        "INFOGRAPHIC",
        "AUDIO_CLIP",
        "SUBTITLE_CUE",
        "TEXT_LAYER",
    }
)

#: Framing authority for a clip that must be letterboxed into the canvas.
LEGAL_FITS: frozenset[str] = frozenset({"LETTERBOX", "COVER", "CROP"})

LEGAL_TRANSITIONS: frozenset[str] = frozenset(
    {"CUT", "FADE", "DISSOLVE", "WIPE", "SLIDE", "ZOOM"}
)

TRANSITION_KIND_TO_XFADE: Mapping[str, str] = {
    "CUT": "cut",
    "FADE": "fade",
    "DISSOLVE": "dissolve",
    "WIPE": "wipeleft",
    "SLIDE": "slideleft",
    "ZOOM": "zoomin",
}

#: Validation finding codes shared with the repository's job projections.
CODE_MANIFEST_MEDIA_MISMATCH = "MANIFEST_MEDIA_MISMATCH"
CODE_SHORTEST_SEMANTICS_FORBIDDEN = "SHORTEST_SEMANTICS_FORBIDDEN"
CODE_UNKNOWN_TRACK = "MANIFEST_UNKNOWN_TRACK"


class Severity(StrEnum):
    """Finding severity.  ``BLOCKER`` stops a render; ``WARNING`` does not."""

    BLOCKER = "BLOCKER"
    WARNING = "WARNING"


# --------------------------------------------------------------------------- #
# findings and reports
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ValidationFinding:
    """One structured finding.  User-facing text is Chinese, codes are stable."""

    code: str
    severity: Severity
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": Severity(self.severity).value,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ValidationReport:
    """The complete set of findings.  ``ok`` is the only pass/fail authority."""

    findings: tuple[ValidationFinding, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(finding.severity is Severity.BLOCKER for finding in self.findings)

    @property
    def blockers(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.BLOCKER)

    @property
    def warnings(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.WARNING)

    def codes(self) -> tuple[str, ...]:
        return tuple(finding.code for finding in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [finding.as_dict() for finding in self.findings],
            "blocker_codes": [finding.code for finding in self.blockers],
            "warning_codes": [finding.code for finding in self.warnings],
        }


def blocker(code: str, message: str, **details: Any) -> ValidationFinding:
    return ValidationFinding(code, Severity.BLOCKER, message, details)


def warning(code: str, message: str, **details: Any) -> ValidationFinding:
    return ValidationFinding(code, Severity.WARNING, message, details)


def _raise(code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
    raise ExplainerContractError(code, message, details)


# --------------------------------------------------------------------------- #
# interval helpers
# --------------------------------------------------------------------------- #
def as_interval(value: object, *, label: str = "interval") -> tuple[int, int]:
    """Accept ``[start, end)`` as a 2-item sequence or a mapping."""

    if isinstance(value, Mapping):
        if "start_frame" in value and "end_frame_exclusive" in value:
            return (int(value["start_frame"]), int(value["end_frame_exclusive"]))
        if "start" in value and "end" in value:
            return (int(value["start"]), int(value["end"]))
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return (int(value[0]), int(value[1]))
    _raise("SCHEMA_INVALID", f"{label} 必须是 [start, end) 区间", {"value": repr(value)})
    raise AssertionError("unreachable")  # pragma: no cover - _raise always raises


def merge_interval_sets(intervals: Iterable[Sequence[int] | Mapping[str, Any]]) -> list[tuple[int, int]]:
    """Merge half-open intervals into a sorted, non-touching, non-overlapping set.

    A zero-length interval is not a frame and is therefore dropped, so an empty
    result means "no frame is claimed by any input".
    """

    prepared: list[tuple[int, int]] = []
    for index, interval in enumerate(intervals):
        start, end = as_interval(interval, label=f"intervals[{index}]")
        if end < start:
            _raise("SCHEMA_INVALID", "帧区间必须满足 end >= start", {"start": start, "end": end})
        if end == start:
            continue
        prepared.append((start, end))
    prepared.sort()
    merged: list[tuple[int, int]] = []
    for start, end in prepared:
        if merged and start <= merged[-1][1]:
            last_start, last_end = merged[-1]
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract_overlap(
    a: Sequence[Sequence[int] | Mapping[str, Any]],
    b: Sequence[Sequence[int] | Mapping[str, Any]],
) -> list[tuple[int, int]]:
    """Return ``A \\ B`` as merged half-open intervals.

    Used to prove an explicit cross-chunk handle overlap is deducted exactly
    once: ``subtract_overlap(handle_regions, core_tiles)`` must be empty when the
    handles live only inside the declared tile seams.
    """

    left = merge_interval_sets(a)
    right = merge_interval_sets(b)
    result: list[tuple[int, int]] = []
    right_index = 0
    for start, end in left:
        cursor = start
        while right_index < len(right) and right[right_index][1] <= cursor:
            right_index += 1
        probe = right_index
        while probe < len(right) and right[probe][0] < end:
            r_start, r_end = right[probe]
            if r_start > cursor:
                result.append((cursor, min(r_start, end)))
            cursor = max(cursor, r_end)
            if cursor >= end:
                break
            probe += 1
        if cursor < end:
            result.append((cursor, end))
    return result


def coverage_intervals(clips: Iterable[Any]) -> list[tuple[int, int]]:
    """Merged coverage of clips that carry ``start_frame``/``end_frame_exclusive``."""

    intervals: list[tuple[int, int]] = []
    for clip in clips:
        intervals.append((int(clip.start_frame), int(clip.end_frame_exclusive)))
    return merge_interval_sets(intervals)


def track_intervals(clips: Iterable[Any], track: str) -> list[tuple[int, int]]:
    """Merged coverage of one track, in declared order."""

    wanted = str(track).upper()
    return coverage_intervals([clip for clip in clips if str(clip.track).upper() == wanted])


def find_free_interval(
    intervals: Sequence[Sequence[int] | Mapping[str, Any]],
    total_frames: int,
) -> tuple[int, int] | None:
    """First gap inside ``[0, total_frames)`` not covered by ``intervals``."""

    if total_frames < 0:
        _raise("SCHEMA_INVALID", "总帧数不能为负", {"total_frames": total_frames})
    cursor = 0
    for start, end in merge_interval_sets(intervals):
        if start > cursor:
            return (cursor, min(start, total_frames))
        cursor = max(cursor, end)
        if cursor >= total_frames:
            return None
    if cursor < total_frames:
        return (cursor, total_frames)
    return None


# --------------------------------------------------------------------------- #
# clip specification (the input to manifest construction)
# --------------------------------------------------------------------------- #
def render_clip_spec(
    *,
    clip_id: str,
    track: str,
    item_kind: str,
    start_frame: int,
    end_frame_exclusive: int,
    media_version_id: str | None = None,
    media_sha256: str | None = None,
    source_in_us: int | None = None,
    source_out_us: int | None = None,
    sample_start: int | None = None,
    sample_end_exclusive: int | None = None,
    transform: Mapping[str, Any] | None = None,
    layer: Mapping[str, Any] | None = None,
    subtitle: Mapping[str, Any] | None = None,
    audio: Mapping[str, Any] | None = None,
    transition: Mapping[str, Any] | None = None,
    beat_id: str | None = None,
    narration_segment_id: str | None = None,
    narration_take_id: str | None = None,
    render_type_planned: str | None = None,
    render_type_actual: str | None = None,
    media_kind: str | None = None,
) -> dict[str, Any]:
    """Build one already-resolved clip specification.

    This is the structured replacement for a composition item row: the caller
    has already chosen the media, the beat and the take, and nothing here will
    look up "the latest" of anything.
    """

    return {
        "clip_id": str(clip_id),
        "track": str(track).upper(),
        "item_kind": str(item_kind).upper(),
        "media_version_id": media_version_id,
        "media_sha256": media_sha256,
        "start_frame": int(start_frame),
        "end_frame_exclusive": int(end_frame_exclusive),
        "source_in_us": None if source_in_us is None else int(source_in_us),
        "source_out_us": None if source_out_us is None else int(source_out_us),
        "sample_start": None if sample_start is None else int(sample_start),
        "sample_end_exclusive": None if sample_end_exclusive is None else int(sample_end_exclusive),
        "transform": dict(transform or {}),
        "layer": dict(layer or {}),
        "subtitle": dict(subtitle or {}),
        "audio": dict(audio or {}),
        "transition": dict(transition or {}),
        "beat_id": beat_id,
        "narration_segment_id": narration_segment_id,
        "narration_take_id": narration_take_id,
        "render_type_planned": render_type_planned,
        "render_type_actual": render_type_actual,
        "media_kind": media_kind,
    }


def manifest_clip(
    *,
    clip_id: str,
    track: str,
    item_kind: str,
    start_frame: int,
    end_frame_exclusive: int,
    fps: Ratio,
    sample_rate_hz: int,
    media_version_id: str | None = None,
    media_sha256: str | None = None,
    source_in_us: int | None = None,
    source_out_us: int | None = None,
    sample_start: int | None = None,
    sample_end_exclusive: int | None = None,
    transform: Mapping[str, Any] | None = None,
    layer: Mapping[str, Any] | None = None,
    subtitle: Mapping[str, Any] | None = None,
    audio: Mapping[str, Any] | None = None,
    transition: Mapping[str, Any] | None = None,
    beat_id: str | None = None,
    narration_segment_id: str | None = None,
    narration_take_id: str | None = None,
    render_type_planned: str | None = None,
    render_type_actual: str | None = None,
    media_kind: str | None = None,
) -> ManifestClip:
    """Convenience constructor that derives audio sample bounds exactly."""

    if sample_start is None and track in {"NARRATION", "BGM", "SFX"}:
        sample_start = fps.samples_for_frames(start_frame, sample_rate_hz)
    if sample_end_exclusive is None and track in {"NARRATION", "BGM", "SFX"}:
        sample_end_exclusive = fps.samples_for_frames(end_frame_exclusive, sample_rate_hz)
    return ManifestClip(
        clip_id=clip_id,
        track=track,
        item_kind=item_kind,
        media_version_id=media_version_id,
        media_sha256=media_sha256,
        start_frame=start_frame,
        end_frame_exclusive=end_frame_exclusive,
        source_in_us=source_in_us,
        source_out_us=source_out_us,
        sample_start=sample_start,
        sample_end_exclusive=sample_end_exclusive,
        transform=dict(transform or {}),
        layer=dict(layer or {}),
        subtitle=dict(subtitle or {}),
        audio=dict(audio or {}),
        transition=dict(transition or {}),
        beat_id=beat_id,
        narration_segment_id=narration_segment_id,
        narration_take_id=narration_take_id,
        render_type_planned=render_type_planned,
        render_type_actual=render_type_actual,
        media_kind=media_kind,
    )


def subtitle_cue(
    *,
    locale: str,
    text: str,
    start_frame: int,
    end_frame_exclusive: int,
    cue_id: str | None = None,
    segment_id: str | None = None,
    style: Mapping[str, Any] | None = None,
    word_timings: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """One subtitle cue already mapped onto the final frame grid.

    Cue boundaries are integer frames computed once from sample/word-level
    alignment; the renderer never re-derives them by float addition.
    """

    if not str(text).strip():
        _raise("SCHEMA_INVALID", "字幕文本不能为空", {"locale": locale})
    if int(end_frame_exclusive) <= int(start_frame):
        _raise(
            "SCHEMA_INVALID",
            "字幕区间必须满足 end_frame_exclusive > start_frame",
            {"start_frame": start_frame, "end_frame_exclusive": end_frame_exclusive},
        )
    return {
        "cue_id": cue_id,
        "locale": str(locale),
        "text": str(text),
        "start_frame": int(start_frame),
        "end_frame_exclusive": int(end_frame_exclusive),
        "segment_id": segment_id,
        "style": dict(style or {}),
        "word_timings": [dict(word) for word in word_timings],
    }


def declare_silence(
    *,
    frame_range: Sequence[int],
    reason: str,
    sample_range: Sequence[int] | None = None,
    scope: str = "GLOBAL",
    owner_id: str | None = None,
) -> dict[str, Any]:
    """Declare legal silence.

    Silence is never inferred from a missing input: an absent narration or an
    under-length video is a diagnosis, and a legal quiet region must be written
    down here so validation can tell the two apart.
    """

    start, end = as_interval(frame_range, label="frame_range")
    if end <= start:
        _raise(
            "SCHEMA_INVALID",
            "静音区间必须满足 end_frame_exclusive > start_frame",
            {"frame_range": [start, end]},
        )
    if not str(reason).strip():
        _raise("SCHEMA_INVALID", "声明静音必须给出原因", {"frame_range": [start, end]})
    sample_pair: list[int] | None = None
    if sample_range is not None:
        sample_start, sample_end = as_interval(sample_range, label="sample_range")
        if sample_end <= sample_start:
            _raise(
                "SCHEMA_INVALID",
                "静音采样区间必须满足 end > start",
                {"sample_range": [sample_start, sample_end]},
            )
        sample_pair = [sample_start, sample_end]
    return {
        "kind": "DECLARED_SILENCE",
        "scope": str(scope),
        "owner_id": owner_id,
        "start_frame": start,
        "end_frame_exclusive": end,
        "sample_start": None if sample_pair is None else sample_pair[0],
        "sample_end_exclusive": None if sample_pair is None else sample_pair[1],
        "reason": str(reason),
        "declared": True,
    }


def declared_silence(value: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Read declared silence from ``meta``, tolerating older manifests."""

    if not isinstance(value, Mapping):
        return []
    raw = value.get("declared_silence")
    if raw is None:
        raw = value.get("silence")
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    collected: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            collected.append(dict(item))
    return collected


# --------------------------------------------------------------------------- #
# exact time arithmetic
# --------------------------------------------------------------------------- #
def map_source_time_to_frames(
    *,
    source_in_us: int,
    source_out_us: int,
    fps: Ratio,
    target_start_frame: int,
) -> tuple[int, int]:
    """Map a microsecond source window onto the output frame grid.

    Integer maths only: ``frames = round(microseconds * fps_num / (fps_den *
    1e6))`` with the half-up round performed in integers, so 30000/1001 and
    24000/1001 never accumulate a float error.  The result is half-open and
    always at least one frame long, so a non-empty source window never becomes
    either a zero-length clip or a silently doubled frame.
    """

    for value, label in ((source_in_us, "source_in_us"), (source_out_us, "source_out_us")):
        if int(value) < 0:
            _raise("SCHEMA_INVALID", f"{label} 不能为负", {label: int(value)})
    if int(source_out_us) < int(source_in_us):
        _raise(
            "SCHEMA_INVALID",
            "源区间必须满足 source_out_us >= source_in_us",
            {"source_in_us": int(source_in_us), "source_out_us": int(source_out_us)},
        )
    if int(target_start_frame) < 0:
        _raise("SCHEMA_INVALID", "目标起始帧不能为负", {"target_start_frame": int(target_start_frame)})
    span_us = int(source_out_us) - int(source_in_us)
    if span_us <= 0:
        return (int(target_start_frame), int(target_start_frame))
    denominator = fps.den * 1_000_000
    numerator = span_us * fps.num
    frames = (numerator + denominator // 2) // denominator
    frames = max(1, int(frames))
    start = int(target_start_frame)
    return (start, start + frames)


def frame_window_for_range(
    *,
    source_in_us: int,
    source_out_us: int,
    fps: Ratio,
    target_start_frame: int,
) -> tuple[int, int]:
    """Alias of :func:`map_source_time_to_frames` used by chunk planning."""

    return map_source_time_to_frames(
        source_in_us=source_in_us,
        source_out_us=source_out_us,
        fps=fps,
        target_start_frame=target_start_frame,
    )


# --------------------------------------------------------------------------- #
# chunk planning
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ManifestChunkSpec:
    """One render block: a core tile plus explicit handles on each side.

    ``[start_frame, end_frame_exclusive)`` is the block's *core*: the tiles of
    all blocks tile ``[0, total_frames)`` exactly.  ``handle_out_frames`` and
    ``handle_in_frames`` describe the extra frames this block may decode so an
    in-block transition or a cross-block seam has material on both sides; the
    seam overlap equals ``previous.handle_out_frames + this.handle_in_frames``
    and is subtracted exactly once when the blocks are joined.
    """

    chunk_no: int
    start_frame: int
    end_frame_exclusive: int
    handle_in_frames: int = 0
    handle_out_frames: int = 0
    item_ids: tuple[str, ...] = ()
    transition: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if int(self.chunk_no) < 0:
            _raise("SCHEMA_INVALID", "chunk_no 不能为负", {"chunk_no": int(self.chunk_no)})
        if int(self.start_frame) < 0:
            _raise("SCHEMA_INVALID", "分块起始帧不能为负", {"start_frame": int(self.start_frame)})
        if int(self.end_frame_exclusive) <= int(self.start_frame):
            _raise(
                "SCHEMA_INVALID",
                "分块区间必须满足 end_frame_exclusive > start_frame",
                {"start_frame": int(self.start_frame), "end_frame_exclusive": int(self.end_frame_exclusive)},
            )
        if int(self.handle_in_frames) < 0 or int(self.handle_out_frames) < 0:
            _raise("SCHEMA_INVALID", "分块 handle 不能为负")

    @property
    def core_frames(self) -> int:
        return int(self.end_frame_exclusive) - int(self.start_frame)

    @property
    def decode_start_frame(self) -> int:
        return max(0, int(self.start_frame) - int(self.handle_in_frames))

    @property
    def decode_end_frame_exclusive(self) -> int:
        return int(self.end_frame_exclusive) + int(self.handle_out_frames)

    @property
    def decode_frames(self) -> int:
        return self.decode_end_frame_exclusive - self.decode_start_frame

    def output_frame_count(self, total_frames: int | None = None) -> int:
        """Exact number of frames this block contributes to the joined film.

        A block's contribution is exactly its half-open core
        ``[start_frame, end_frame_exclusive)`` — the same tile the plan is proven
        to lay down exactly once.  Handles are *decode-window material*: they
        widen the window so a seam has frames on both sides, and because they are
        never part of any block's output the seam overlap is subtracted from the
        film by construction rather than by a later correction.  That is what
        makes ``sum(block frames) == total_frames`` an invariant instead of an
        arithmetic accident.
        """

        del total_frames  # the core tile is already clipped by the plan
        return int(self.end_frame_exclusive) - int(self.start_frame)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_no": int(self.chunk_no),
            "start_frame": int(self.start_frame),
            "end_frame_exclusive": int(self.end_frame_exclusive),
            "handle_in_frames": int(self.handle_in_frames),
            "handle_out_frames": int(self.handle_out_frames),
            "item_ids": [str(item) for item in self.item_ids],
            "transition": dict(self.transition),
            "core_frames": self.core_frames,
            "decode_start_frame": self.decode_start_frame,
            "decode_end_frame_exclusive": self.decode_end_frame_exclusive,
        }


def plan_chunks(
    *,
    total_frames: int,
    fps: Ratio,
    target_seconds: float = 60.0,
    min_seconds: float = 30.0,
    max_seconds: float = 90.0,
    chapter_boundaries: Sequence[int] = (),
    transition_frames: int = 0,
) -> tuple[ManifestChunkSpec, ...]:
    """Split ``[0, total_frames)`` into 30–90 s blocks snapped to chapters.

    Guarantees, all exact integer properties:

    * the block cores are contiguous and non-overlapping and their union is
      exactly ``[0, total_frames)`` (``merge_interval_sets`` proves it);
    * every block that is not the film's last is at least
      ``fps.frames_for_seconds(min_seconds)`` long, and no block exceeds
      ``fps.frames_for_seconds(max_seconds)`` unless the remaining film is
      shorter than that maximum;
    * a block ends exactly on a chapter boundary whenever one falls inside the
      block's legal length window;
    * the seam overlap between neighbouring blocks (``handle_out`` of the left
      block plus ``handle_in`` of the right block) equals ``transition_frames``,
      so a cross-block transition is deducted exactly once.
    """

    if int(total_frames) <= 0:
        return ()
    if int(transition_frames) < 0:
        _raise("SCHEMA_INVALID", "transition_frames 不能为负", {"transition_frames": int(transition_frames)})
    if not (0 < float(min_seconds) <= float(target_seconds) <= float(max_seconds)):
        _raise(
            "SCHEMA_INVALID",
            "分块时长参数必须满足 0 < min_seconds <= target_seconds <= max_seconds",
            {
                "min_seconds": float(min_seconds),
                "target_seconds": float(target_seconds),
                "max_seconds": float(max_seconds),
            },
        )

    total = int(total_frames)
    min_frames = max(1, fps.frames_for_seconds(float(min_seconds)))
    max_frames = max(min_frames, fps.frames_for_seconds(float(max_seconds)))
    target_frames = min(max_frames, max(min_frames, fps.frames_for_seconds(float(target_seconds))))

    boundaries = sorted(
        {
            int(boundary)
            for boundary in chapter_boundaries
            if 0 < int(boundary) < total
        }
    )

    boundaries_out: list[int] = []
    start = 0
    while start < total:
        remaining = total - start
        if remaining <= max_frames:
            boundaries_out.append(total)
            break
        accumulated = 0
        cursor = start
        while cursor < total and accumulated < target_frames:
            accumulated += 1
            cursor += 1
        lower = min(start + min_frames, total)
        upper = min(start + max_frames, total)
        candidates = [value for value in boundaries if lower <= value <= upper]
        end = candidates[-1] if candidates else min(cursor, upper)
        if end <= start:
            end = upper
        boundaries_out.append(end)
        start = end

    starts = [0, *boundaries_out[:-1]]
    chunks: list[ManifestChunkSpec] = []
    half = max(0, int(transition_frames)) // 2
    for index, (chunk_start, chunk_end) in enumerate(zip(starts, boundaries_out, strict=True)):
        handle_in = 0 if index == 0 else half
        handle_out = 0 if index == len(boundaries_out) - 1 else max(0, int(transition_frames)) - half
        transition = (
            {}
            if int(transition_frames) == 0
            else {
                "kind": "CROSS_BLOCK_HANDLES",
                "overlap_frames": int(transition_frames),
                "handle_in_frames": handle_in,
                "handle_out_frames": handle_out,
            }
        )
        chunks.append(
            ManifestChunkSpec(
                chunk_no=index,
                start_frame=chunk_start,
                end_frame_exclusive=chunk_end,
                handle_in_frames=handle_in,
                handle_out_frames=handle_out,
                item_ids=(),
                transition=transition,
            )
        )

    merged = merge_interval_sets([(chunk.start_frame, chunk.end_frame_exclusive) for chunk in chunks])
    if merged != [(0, total)]:
        _raise(
            "SCHEMA_INVALID",
            "分块必须精确铺满 [0, total_frames)，不得有缝隙或重叠",
            {"merged": [list(item) for item in merged], "total_frames": total},
        )
    return tuple(chunks)


# --------------------------------------------------------------------------- #
# manifest value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ManifestClip:
    """One resolved clip on exactly one track, with exact frame/sample bounds."""

    clip_id: str
    track: str
    item_kind: str
    media_version_id: str | None
    media_sha256: str | None
    start_frame: int
    end_frame_exclusive: int
    source_in_us: int | None
    source_out_us: int | None
    sample_start: int | None
    sample_end_exclusive: int | None
    transform: Mapping[str, Any] = field(default_factory=dict)
    layer: Mapping[str, Any] = field(default_factory=dict)
    subtitle: Mapping[str, Any] = field(default_factory=dict)
    audio: Mapping[str, Any] = field(default_factory=dict)
    transition: Mapping[str, Any] = field(default_factory=dict)
    beat_id: str | None = None
    narration_segment_id: str | None = None
    narration_take_id: str | None = None
    render_type_planned: str | None = None
    render_type_actual: str | None = None
    media_kind: str | None = None

    def __post_init__(self) -> None:
        if not str(self.clip_id).strip():
            _raise("SCHEMA_INVALID", "clip_id 不能为空")
        track = str(self.track).upper()
        if track not in LEGAL_TRACKS:
            _raise(CODE_UNKNOWN_TRACK, f"未知轨道：{self.track!r}", {"track": self.track})
        item_kind = str(self.item_kind).upper()
        if item_kind not in LEGAL_ITEM_KINDS:
            _raise("SCHEMA_INVALID", f"未知条目类型：{self.item_kind!r}", {"item_kind": self.item_kind})
        if int(self.start_frame) < 0:
            _raise("SCHEMA_INVALID", "start_frame 不能为负", {"clip_id": self.clip_id})
        if int(self.end_frame_exclusive) <= int(self.start_frame):
            _raise(
                "SCHEMA_INVALID",
                "clips 必须满足 end_frame_exclusive > start_frame",
                {"clip_id": self.clip_id, "start_frame": self.start_frame, "end_frame_exclusive": self.end_frame_exclusive},
            )
        for value, label in ((self.source_in_us, "source_in_us"), (self.source_out_us, "source_out_us")):
            if value is not None and int(value) < 0:
                _raise("SCHEMA_INVALID", f"{label} 不能为负", {"clip_id": self.clip_id, label: int(value)})
        if self.source_in_us is not None and self.source_out_us is not None:
            if int(self.source_out_us) < int(self.source_in_us):
                _raise(
                    "SCHEMA_INVALID",
                    "source_out_us 必须不早于 source_in_us",
                    {"clip_id": self.clip_id, "source_in_us": int(self.source_in_us), "source_out_us": int(self.source_out_us)},
                )
        if (self.source_in_us is None) != (self.source_out_us is None):
            _raise(
                "SCHEMA_INVALID",
                "source_in_us 与 source_out_us 必须同时存在或同时为空",
                {"clip_id": self.clip_id},
            )
        for value, label in ((self.sample_start, "sample_start"), (self.sample_end_exclusive, "sample_end_exclusive")):
            if value is not None and int(value) < 0:
                _raise("SCHEMA_INVALID", f"{label} 不能为负", {"clip_id": self.clip_id, label: int(value)})
        if self.sample_start is not None and self.sample_end_exclusive is not None:
            if int(self.sample_end_exclusive) < int(self.sample_start):
                _raise(
                    "SCHEMA_INVALID",
                    "sample_end_exclusive 必须不早于 sample_start",
                    {"clip_id": self.clip_id},
                )
        if (self.sample_start is None) != (self.sample_end_exclusive is None):
            _raise(
                "SCHEMA_INVALID",
                "sample_start 与 sample_end_exclusive 必须同时存在或同时为空",
                {"clip_id": self.clip_id},
            )
        fit = str((self.transform or {}).get("fit") or "").upper()
        if fit and fit not in LEGAL_FITS:
            _raise(
                "SCHEMA_INVALID",
                "transform.fit 必须是 LETTERBOX、COVER 或 CROP",
                {"clip_id": self.clip_id, "fit": fit},
            )
        kind = str((self.transition or {}).get("kind") or "CUT").upper()
        if kind not in LEGAL_TRANSITIONS:
            _raise(
                "SCHEMA_INVALID",
                f"未知转场类型：{kind}",
                {"clip_id": self.clip_id, "transition": kind},
            )

    @property
    def frames(self) -> int:
        return int(self.end_frame_exclusive) - int(self.start_frame)

    @property
    def samples(self) -> int:
        if self.sample_start is None or self.sample_end_exclusive is None:
            return 0
        return int(self.sample_end_exclusive) - int(self.sample_start)

    @property
    def transition_kind(self) -> str:
        return str((self.transition or {}).get("kind") or "CUT").upper()

    def declares_shortest_selection(self) -> bool:
        """True when a nested payload asks FFmpeg to hide a gap with ``-shortest``."""

        for payload in (self.audio, self.transition, self.layer, self.transform):
            if not isinstance(payload, Mapping):
                continue
            for key in ("shortest", "shortest_selection", "use_shortest", "hide_gap_with_shortest"):
                if payload.get(key) is True:
                    return True
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "clip_id": str(self.clip_id),
            "track": str(self.track).upper(),
            "item_kind": str(self.item_kind).upper(),
            "media_version_id": self.media_version_id,
            "media_sha256": self.media_sha256,
            "start_frame": int(self.start_frame),
            "end_frame_exclusive": int(self.end_frame_exclusive),
            "source_in_us": None if self.source_in_us is None else int(self.source_in_us),
            "source_out_us": None if self.source_out_us is None else int(self.source_out_us),
            "sample_start": None if self.sample_start is None else int(self.sample_start),
            "sample_end_exclusive": None if self.sample_end_exclusive is None else int(self.sample_end_exclusive),
            "transform": dict(self.transform),
            "layer": dict(self.layer),
            "subtitle": dict(self.subtitle),
            "audio": dict(self.audio),
            "transition": dict(self.transition),
            "beat_id": self.beat_id,
            "narration_segment_id": self.narration_segment_id,
            "narration_take_id": self.narration_take_id,
            "render_type_planned": self.render_type_planned,
            "render_type_actual": self.render_type_actual,
            "media_kind": self.media_kind,
        }


@dataclass(frozen=True)
class RenderManifest:
    """The only input truth for a deterministic render.

    ``clips`` is the complete, already-resolved description of every track;
    ``chunks`` is the block plan with explicit handles; ``mix`` declares which
    narration segment lands on which sample range and which silence is legal;
    ``meta`` carries the declared silence, encoder policy and any free-form
    provenance.  ``manifest_hash`` is a canonical content hash of all of it.
    """

    edition_id: str
    composition_revision_id: str
    aspect_ratio: AspectRatio
    width: int
    height: int
    fps: Ratio
    total_frames: int
    audio_sample_rate_hz: int
    total_samples: int
    duration_policy: DurationPolicy
    target_frames: int | None
    clips: tuple[ManifestClip, ...]
    chunks: tuple[ManifestChunkSpec, ...]
    subtitle_tracks: tuple[Mapping[str, Any], ...] = ()
    mix: Mapping[str, Any] = field(default_factory=dict)
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.edition_id).strip():
            _raise("SCHEMA_INVALID", "edition_id 不能为空")
        if not str(self.composition_revision_id).strip():
            _raise("SCHEMA_INVALID", "composition_revision_id 不能为空")
        aspect = self.aspect_ratio if isinstance(self.aspect_ratio, AspectRatio) else AspectRatio(str(self.aspect_ratio))
        object.__setattr__(self, "aspect_ratio", aspect)
        policy = self.duration_policy if isinstance(self.duration_policy, DurationPolicy) else DurationPolicy(str(self.duration_policy))
        object.__setattr__(self, "duration_policy", policy)
        if not isinstance(self.fps, Ratio):
            _raise("SCHEMA_INVALID", "fps 必须是 Ratio 有理数")
        if int(self.width) <= 0 or int(self.height) <= 0:
            _raise("SCHEMA_INVALID", "画布宽高必须为正", {"width": self.width, "height": self.height})
        if int(self.total_frames) < 0:
            _raise("SCHEMA_INVALID", "total_frames 不能为负", {"total_frames": self.total_frames})
        if int(self.audio_sample_rate_hz) <= 0:
            _raise("SCHEMA_INVALID", "音频采样率必须为正")
        if int(self.total_samples) < 0:
            _raise("SCHEMA_INVALID", "total_samples 不能为负")
        object.__setattr__(self, "clips", tuple(self.clips))
        object.__setattr__(self, "chunks", tuple(self.chunks))
        object.__setattr__(self, "subtitle_tracks", tuple(dict(track) for track in self.subtitle_tracks))
        object.__setattr__(self, "mix", dict(self.mix))
        object.__setattr__(self, "meta", dict(self.meta))

    # ---------------------------------------------------------------- derived
    @property
    def duration_seconds(self) -> float:
        return self.fps.seconds_for_frames(int(self.total_frames))

    @property
    def total_frames_exact_from_samples(self) -> int:
        """Frame count implied by ``total_samples``, so the two can be compared."""

        return int(self.total_samples) * self.fps.num // (self.fps.den * int(self.audio_sample_rate_hz))

    def clips_for_track(self, track: str) -> tuple[ManifestClip, ...]:
        wanted = str(track).upper()
        return tuple(clip for clip in self.clips if str(clip.track).upper() == wanted)

    @property
    def video_clips(self) -> tuple[ManifestClip, ...]:
        return self.clips_for_track("VIDEO")

    @property
    def narration_clips(self) -> tuple[ManifestClip, ...]:
        return self.clips_for_track("NARRATION")

    @property
    def narration_frames(self) -> int:
        return sum(clip.frames for clip in self.narration_clips)

    @property
    def evidence_gap_intervals(self) -> list[tuple[int, int]]:
        """Frames the caller explicitly attached to a gap diagnosis."""

        gaps: list[tuple[int, int]] = []
        for item in self.meta.get("evidence_gaps", []) or []:
            gaps.append(as_interval(item, label="meta.evidence_gaps[]"))
        return merge_interval_sets(gaps)

    @property
    def declared_silence(self) -> list[dict[str, Any]]:
        return declared_silence(self.meta)

    @property
    def render_kind(self) -> str:
        return str(self.meta.get("kind") or MANIFEST_KIND)

    def as_dict(self) -> dict[str, Any]:
        """Fully canonical, order-stable encoding of this manifest."""

        return {
            "manifest_kind": MANIFEST_KIND,
            "edition_id": str(self.edition_id),
            "composition_revision_id": str(self.composition_revision_id),
            "aspect_ratio": self.aspect_ratio.value,
            "width": int(self.width),
            "height": int(self.height),
            "fps": self.fps.as_dict(),
            "total_frames": int(self.total_frames),
            "duration_seconds": round(self.fps.seconds_for_frames(int(self.total_frames)), 6),
            "audio_sample_rate_hz": int(self.audio_sample_rate_hz),
            "total_samples": int(self.total_samples),
            "duration_policy": self.duration_policy.value,
            "target_frames": None if self.target_frames is None else int(self.target_frames),
            "clips": [clip.as_dict() for clip in self.clips],
            "chunks": [chunk.as_dict() for chunk in self.chunks],
            "subtitle_tracks": [dict(track) for track in self.subtitle_tracks],
            "mix": dict(self.mix),
            "meta": dict(self.meta),
        }

    @property
    def manifest_hash(self) -> str:
        return content_hash(self.as_dict())

    def with_meta(self, **overrides: Any) -> RenderManifest:
        """Return a copy with ``meta`` entries replaced (used to declare silence)."""

        merged = {**dict(self.meta), **overrides}
        return RenderManifest(
            edition_id=self.edition_id,
            composition_revision_id=self.composition_revision_id,
            aspect_ratio=self.aspect_ratio,
            width=self.width,
            height=self.height,
            fps=self.fps,
            total_frames=self.total_frames,
            audio_sample_rate_hz=self.audio_sample_rate_hz,
            total_samples=self.total_samples,
            duration_policy=self.duration_policy,
            target_frames=self.target_frames,
            clips=self.clips,
            chunks=self.chunks,
            subtitle_tracks=self.subtitle_tracks,
            mix=self.mix,
            meta=merged,
        )

    def declare_silence(self, *, frame_range: Sequence[int], reason: str, sample_range: Sequence[int] | None = None) -> RenderManifest:
        """Return a copy that declares one legal silence region."""

        declared = list(self.declared_silence)
        declared.append(declare_silence(frame_range=frame_range, reason=reason, sample_range=sample_range))
        return self.with_meta(declared_silence=declared)

    # -------------------------------------------------------------- structural
    def validate(self) -> ValidationReport:
        """Structural self-check that needs no media lookup.

        Returns every finding instead of raising so the caller decides whether a
        warning is acceptable.  Media-dependent checks live in
        :func:`local_drama.application.composition.validation.validate_manifest`.
        """

        findings: list[ValidationFinding] = []
        for track in ("VIDEO", "NARRATION", "BGM", "SFX", "SUBTITLE", "OVERLAY"):
            selected = self.clips_for_track(track)
            if not selected:
                continue
            try:
                ensure_track_tiling(
                    [(clip.start_frame, clip.end_frame_exclusive) for clip in selected],
                    total_frames=None,
                )
            except ExplainerContractError as error:
                findings.append(
                    blocker(
                        str(error.code),
                        f"轨道 {track} 的帧区间不单调或相互重叠",
                        track=track,
                        detail=error.details,
                    )
                )
        if not self.video_clips:
            findings.append(blocker("NO_VIDEO_TRACK", "清单缺少视频轨，无法渲染"))
        if int(self.total_frames) > 0:
            video_merged = track_intervals(self.clips, "VIDEO")
            if video_merged and video_merged != [(0, int(self.total_frames))]:
                gap = find_free_interval(video_merged, int(self.total_frames))
                findings.append(
                    blocker(
                        "VIDEO_TRACK_GAP",
                        "视频轨没有铺满整片，存在空洞",
                        coverage=[list(item) for item in video_merged],
                        first_gap=None if gap is None else [gap[0], gap[1]],
                    )
                )
            for track in ("NARRATION", "BGM", "SFX"):
                selected = self.clips_for_track(track)
                if not selected:
                    continue
                gap = find_free_interval(track_intervals(self.clips, track), int(self.total_frames))
                if gap is not None:
                    findings.append(
                        warning(
                            f"{track}_TRACK_GAP",
                            f"轨道 {track} 存在未覆盖区间；必须显式声明静音，否则校验会报阻塞",
                            track=track,
                            gap=[gap[0], gap[1]],
                        )
                    )
        if self.chunks:
            merged = merge_interval_sets([(chunk.start_frame, chunk.end_frame_exclusive) for chunk in self.chunks])
            if merged != [(0, int(self.total_frames))]:
                findings.append(
                    blocker(
                        "CHUNK_TILING_INVALID",
                        "分块没有精确铺满 [0, total_frames)",
                        merged=[list(item) for item in merged],
                        total_frames=int(self.total_frames),
                    )
                )
            handle_regions = [
                (chunk.start_frame - chunk.handle_in_frames, chunk.start_frame + chunk.handle_out_frames)
                for chunk in self.chunks
                if chunk.handle_in_frames or chunk.handle_out_frames
            ]
            outside = subtract_overlap(
                handle_regions,
                [(chunk.start_frame, chunk.end_frame_exclusive) for chunk in self.chunks],
            )
            if outside:
                findings.append(
                    blocker(
                        "CHUNK_HANDLE_OUTSIDE_FILM",
                        "跨块 handle 超出了整片范围，重叠会被重复计入",
                        outside=[list(item) for item in outside],
                    )
                )
            contributed = sum(chunk.output_frame_count(int(self.total_frames)) for chunk in self.chunks)
            if contributed != int(self.total_frames):
                findings.append(
                    blocker(
                        "CHUNK_FRAME_ACCOUNTING_INVALID",
                        "分块贡献的帧数之和不等于整片帧数：handle 重叠会被重复或漏算",
                        total_frames=int(self.total_frames),
                        chunk_contribution=contributed,
                    )
                )
            for chunk in self.chunks:
                if chunk.transition and str(chunk.transition.get("kind") or "").upper() == "CROSS_BLOCK_HANDLES":
                    findings.append(
                        warning(
                            "CROSS_BLOCK_TRANSITION_IS_HARD_CUT",
                            "跨块转场以显式 handle 提供素材；本版在接缝处仍是硬切，"
                            "handle 重叠已从成片帧数中扣除，不会被重复计入",
                            chunk_no=chunk.chunk_no,
                            overlap_frames=int(chunk.transition.get("overlap_frames") or 0),
                            handle_in_frames=chunk.handle_in_frames,
                            handle_out_frames=chunk.handle_out_frames,
                        )
                    )
        if self.duration_policy is DurationPolicy.FIXED_FRAMES:
            if self.target_frames is None:
                findings.append(blocker("TARGET_FRAMES_MISSING", "FIXED_FRAMES 必须声明 target_frames"))
            elif int(self.target_frames) != int(self.total_frames):
                findings.append(
                    blocker(
                        "TARGET_FRAMES_MISMATCH",
                        "FIXED_FRAMES 的 total_frames 与 target_frames 不一致",
                        total_frames=int(self.total_frames),
                        target_frames=int(self.target_frames),
                    )
                )
        for clip in self.clips:
            if clip.declares_shortest_selection():
                findings.append(
                    blocker(
                        CODE_SHORTEST_SEMANTICS_FORBIDDEN,
                        "清单要求用最短流语义掩盖缺口；这被禁止，必须先诊断缺失的音频或不足的画面",
                        clip_id=clip.clip_id,
                        track=clip.track,
                    )
                )
        return ValidationReport(tuple(findings))


def ensure_track_tiling(
    intervals: Iterable[Sequence[int]],
    *,
    total_frames: int | None = None,
) -> None:
    """Raise unless intervals are positive, strictly monotonic and gapless.

    Order is significant: this checks that a *track's* clips were emitted in
    playback order with no overlap and no hole between neighbours.  A leading
    offset is only legal when ``total_frames`` is ``None`` (a track may legally
    start later when the caller declares the silence).
    """

    cursor: int | None = None
    for index, interval in enumerate(intervals):
        start, end = as_interval(interval, label=f"intervals[{index}]")
        if end <= start:
            _raise("SCHEMA_INVALID", "帧区间必须满足 end > start", {"start": start, "end": end, "index": index})
        if cursor is None:
            if total_frames is not None and start != 0:
                _raise(
                    "SCHEMA_INVALID",
                    "轨道必须从第 0 帧开始；前置空洞必须由显式静音声明",
                    {"start": start, "index": index},
                )
        elif start != cursor:
            _raise(
                "SCHEMA_INVALID",
                "轨道帧区间必须连续且无重叠",
                {"expected_start": cursor, "actual_start": start, "index": index},
            )
        cursor = end
    if cursor is not None and total_frames is not None and cursor != int(total_frames):
        _raise(
            "SCHEMA_INVALID",
            "轨道没有铺满整片长度",
            {"expected_end": int(total_frames), "actual_end": cursor},
        )


# --------------------------------------------------------------------------- #
# duration policy
# --------------------------------------------------------------------------- #
def derive_target_frames(
    *,
    duration_policy: DurationPolicy,
    fps: Ratio,
    target_seconds: int | None,
    tolerance_percent: float = 0.0,
) -> int | None:
    """Exact target frame count for ``FIXED_FRAMES`` / ``USE_SOURCE_TARGET``."""

    if duration_policy is DurationPolicy.NATURAL_NARRATION:
        return None
    if target_seconds is None:
        _raise(
            "SCHEMA_INVALID",
            "该时长政策必须给出 target_seconds",
            {"duration_policy": str(duration_policy)},
        )
    return fps.frames_for_seconds(int(target_seconds))


def duration_policy_outcome(
    *,
    policy: DurationPolicy,
    fps: Ratio,
    measured_narration_frames: int,
    target_frames: int | None,
    tolerance_percent: float,
) -> dict[str, Any]:
    """Decide whether a policy is satisfied, and how.

    ``FIXED_FRAMES`` is deliberately never satisfied by shortening audio: when
    the measured narration is short of the target the only legal answer is
    ``NEEDS_HANDLES`` (designed head/tail handles and declared silence), and when
    the narration overruns the target the answer is ``BLOCKED``.  The
    ``may_truncate_audio`` / ``may_stretch_narration`` flags are always ``False``
    and exist so a caller cannot mistake this helper for permission.
    """

    base: dict[str, Any] = {
        "may_truncate_audio": False,
        "may_stretch_narration": False,
        "fps": fps.as_dict(),
        "measured_narration_frames": int(measured_narration_frames),
    }
    if int(measured_narration_frames) < 0:
        _raise("SCHEMA_INVALID", "已测量的旁白帧数不能为负", {"measured_narration_frames": int(measured_narration_frames)})
    if not 0 <= float(tolerance_percent) <= 25:
        _raise("SCHEMA_INVALID", "容差必须在 0–25% 之间", {"tolerance_percent": float(tolerance_percent)})

    if policy is DurationPolicy.FIXED_FRAMES:
        if target_frames is None:
            return {
                **base,
                "status": "BLOCKED",
                "achieved_frames": int(measured_narration_frames),
                "delta_frames": 0,
                "reason": "FIXED_FRAMES 缺少 target_frames，无法判定",
            }
        target = int(target_frames)
        delta = int(measured_narration_frames) - target
        if delta == 0:
            return {
                **base,
                "status": "PASS",
                "achieved_frames": int(measured_narration_frames),
                "delta_frames": 0,
                "reason": "实测旁白帧数与目标帧数一致",
            }
        if delta > 0:
            return {
                **base,
                "status": "BLOCKED",
                "achieved_frames": int(measured_narration_frames),
                "delta_frames": delta,
                "reason": "旁白长于目标；禁止裁掉音频，必须修改讲稿或改用 NATURAL_NARRATION",
            }
        return {
            **base,
            "status": "NEEDS_HANDLES",
            "achieved_frames": int(measured_narration_frames),
            "delta_frames": delta,
            "reason": "旁白短于目标；只能用设计好的头/尾 handle 与显式静音补足，禁止拉伸旁白或补黑帧",
        }

    if policy is DurationPolicy.USE_SOURCE_TARGET:
        if target_frames is None:
            return {
                **base,
                "status": "BLOCKED",
                "achieved_frames": int(measured_narration_frames),
                "delta_frames": 0,
                "reason": "USE_SOURCE_TARGET 缺少来源目标时长",
            }
        target = int(target_frames)
        delta = int(measured_narration_frames) - target
        tolerance_frames = _tolerance_frames(target, float(tolerance_percent))
        if abs(delta) <= tolerance_frames:
            return {
                **base,
                "status": "PASS",
                "achieved_frames": int(measured_narration_frames),
                "delta_frames": delta,
                "reason": "实测旁白在来源目标时长的容差内",
            }
        if delta < 0:
            return {
                **base,
                "status": "NEEDS_HANDLES",
                "achieved_frames": int(measured_narration_frames),
                "delta_frames": delta,
                "reason": "旁白短于来源目标；用设计 handle 与显式静音补齐，禁止拉伸旁白",
            }
        return {
            **base,
            "status": "BLOCKED",
            "achieved_frames": int(measured_narration_frames),
            "delta_frames": delta,
            "reason": "旁白超出容差且长于来源目标；禁止裁掉音频",
        }

    # NATURAL_NARRATION: the measured length is the truth.
    tolerance_frames = _tolerance_frames(int(target_frames or 0), float(tolerance_percent))
    delta = 0 if target_frames is None else int(measured_narration_frames) - int(target_frames)
    if target_frames is not None and abs(delta) > tolerance_frames:
        return {
            **base,
            "status": "NEEDS_HANDLES",
            "achieved_frames": int(measured_narration_frames),
            "delta_frames": delta,
            "reason": "实测旁白偏离参考目标且超出容差；以实测总长为准，并由管线决定是否补充镜头",
        }
    return {
        **base,
        "status": "PASS",
        "achieved_frames": int(measured_narration_frames),
        "delta_frames": delta,
        "reason": "NATURAL_NARRATION 接受实测旁白总长",
    }


def _tolerance_frames(target_frames: int, tolerance_percent: float) -> int:
    if target_frames <= 0 or tolerance_percent <= 0:
        return 0
    return int(target_frames * tolerance_percent / 100.0)


# --------------------------------------------------------------------------- #
# manifest construction
# --------------------------------------------------------------------------- #
def build_manifest(
    *,
    edition_id: str,
    composition_revision_id: str,
    aspect_ratio: AspectRatio | str,
    fps: Ratio,
    total_frames: int,
    audio_sample_rate_hz: int = 48_000,
    duration_policy: DurationPolicy | str = DurationPolicy.NATURAL_NARRATION,
    target_frames: int | None = None,
    clips: Sequence[ManifestClip | Mapping[str, Any]],
    chunks: Sequence[ManifestChunkSpec | Mapping[str, Any]] | None = None,
    subtitle_tracks: Sequence[Mapping[str, Any]] = (),
    mix: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    width: int | None = None,
    height: int | None = None,
    validate_structure: bool = True,
    validate_hook: Callable[[RenderManifest], ValidationReport] | None = None,
) -> RenderManifest:
    """Assemble and structurally validate a :class:`RenderManifest`.

    ``clips`` must already be resolved by the caller: this function never fetches
    media, never picks a candidate and never re-derives a frame boundary.  What
    it does guarantee is that every track is monotonic and gapless, that the
    chunk plan tiles the film exactly, and that ``total_samples`` matches the
    declared frame count at the declared sample rate.
    """

    policy = duration_policy if isinstance(duration_policy, DurationPolicy) else DurationPolicy(str(duration_policy))
    aspect = aspect_ratio if isinstance(aspect_ratio, AspectRatio) else AspectRatio(str(aspect_ratio))
    resolved_width, resolved_height = (width, height) if width and height else aspect.pixels

    resolved_clips: list[ManifestClip] = []
    for index, clip in enumerate(clips):
        if isinstance(clip, ManifestClip):
            resolved_clips.append(clip)
        elif isinstance(clip, Mapping):
            try:
                resolved_clips.append(ManifestClip(**dict(clip)))
            except TypeError as error:
                _raise(
                    "SCHEMA_INVALID",
                    f"clips[{index}] 字段与 ManifestClip 不匹配",
                    {"reason": str(error)},
                )
        else:
            _raise("SCHEMA_INVALID", f"clips[{index}] 必须是 ManifestClip 或映射")

    sample_rate = int(audio_sample_rate_hz)
    total_samples = fps.samples_for_frames(int(total_frames), sample_rate)

    resolved_chunks: tuple[ManifestChunkSpec, ...]
    if chunks is None:
        resolved_chunks = plan_chunks(total_frames=int(total_frames), fps=fps)
    else:
        collected: list[ManifestChunkSpec] = []
        for index, chunk in enumerate(chunks):
            if isinstance(chunk, ManifestChunkSpec):
                collected.append(chunk)
            elif isinstance(chunk, Mapping):
                try:
                    collected.append(ManifestChunkSpec(**dict(chunk)))
                except TypeError as error:
                    _raise(
                        "SCHEMA_INVALID",
                        f"chunks[{index}] 字段与 ManifestChunkSpec 不匹配",
                        {"reason": str(error)},
                    )
            else:
                _raise("SCHEMA_INVALID", f"chunks[{index}] 必须是 ManifestChunkSpec 或映射")
        resolved_chunks = tuple(collected)

    manifest = RenderManifest(
        edition_id=str(edition_id),
        composition_revision_id=str(composition_revision_id),
        aspect_ratio=aspect,
        width=int(resolved_width),
        height=int(resolved_height),
        fps=fps,
        total_frames=int(total_frames),
        audio_sample_rate_hz=sample_rate,
        total_samples=total_samples,
        duration_policy=policy,
        target_frames=None if target_frames is None else int(target_frames),
        clips=tuple(resolved_clips),
        chunks=resolved_chunks,
        subtitle_tracks=tuple(subtitle_tracks),
        mix=dict(mix or {}),
        meta=dict(meta or {}),
    )

    if not validate_structure:
        return manifest

    report = manifest.validate()
    if validate_hook is not None:
        extra = validate_hook(manifest)
        report = ValidationReport(tuple(report.findings) + tuple(extra.findings))
    if not report.ok:
        first = report.blockers[0]
        _raise(
            "SCHEMA_INVALID",
            f"清单结构校验未通过：{first.message}",
            {
                "code": first.code,
                "blockers": [finding.as_dict() for finding in report.blockers],
                "warnings": [finding.as_dict() for finding in report.warnings],
            },
        )
    return manifest

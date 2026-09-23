"""Pure, exactly-testable chunk slice arithmetic.

The FFmpeg builder must never re-derive time arithmetic while it concatenates
strings: that is how a long clip split across two blocks was read twice from its
own head.  This module answers one question for one clip and one block — *which
part of the source, placed where* — with integer maths only, so the answer can be
proved in a unit test and the builder can only consume it.

Coordinate systems
------------------

* the **film grid** is the output frame grid of the whole film:
  ``[0, total_frames)``;
* a clip occupies the half-open film interval ``[clip.start_frame,
  clip.end_frame_exclusive)``, and its own local frame ``k`` is film frame
  ``clip.start_frame + k``;
* a block decodes ``[chunk.decode_start_frame, chunk.decode_end_frame_exclusive)``
  (its core widened by handles) and *outputs* its core ``[chunk.start_frame,
  chunk.end_frame_exclusive)``.

For the global clip interval ``[Cs, Ce)`` and the block decode interval
``[Ds, De)`` the contributing part is ``L = max(Cs, Ds)`` to ``R = min(Ce, De)``;
when ``L >= R`` the clip does not participate in the block at all.  The frames
this block must read from that clip are ``[L - Cs, R - Cs)`` of the clip's own
local frames, and the source read must advance by exactly ``L - Cs`` frames —
that advancement is the fix for "the second block repeats the first block's
picture".

Source time is derived from the clip's own frame span and its source window
(``source_in_us``/``source_out_us``), so a clip whose source window is shorter or
longer than its film interval still maps deterministically: the per-frame source
step is ``(source_out_us - source_in_us) / clip.frames`` in rational arithmetic,
and the source offset is rounded half-up in integers exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from local_drama.application.composition.manifest import (
    ManifestChunkSpec,
    ManifestClip,
    Ratio,
    RenderManifest,
)

__all__ = [
    "ClipSlicePlan",
    "US_PER_SECOND",
    "compute_chunk_slices",
    "clip_local_frames_for_window",
]

US_PER_SECOND = 1_000_000


def _require_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} 必须是整数")
    return int(value)


@dataclass(frozen=True)
class ClipSlicePlan:
    """The exact part of one clip that one block must decode, and where it sits.

    ``local_start_frame`` / ``local_end_frame_exclusive`` are *clip-local*: they
    are measured from ``clip.start_frame``, so the builder never has to redo the
    intersection, and the source offset is derived from them rather than from the
    block's own start.
    """

    clip_id: str
    track: str
    item_kind: str
    media_version_id: str | None
    clip_start_frame: int
    clip_end_frame_exclusive: int
    intersection_start_frame: int
    intersection_end_frame_exclusive: int
    local_start_frame: int
    local_end_frame_exclusive: int
    output_frame_count: int
    output_offset_frames: int
    source_in_us: int | None
    source_out_us: int | None
    source_read_start_us: int | None
    source_read_end_us: int | None
    local_source_start_us: int
    local_source_span_us: int
    covers_decode_end: bool

    @property
    def source_advance_us(self) -> int:
        """How far into the source this block starts reading, in microseconds."""

        if self.source_read_start_us is None or self.source_in_us is None:
            return 0
        return int(self.source_read_start_us) - int(self.source_in_us)

    def as_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "track": self.track,
            "item_kind": self.item_kind,
            "media_version_id": self.media_version_id,
            "clip_start_frame": int(self.clip_start_frame),
            "clip_end_frame_exclusive": int(self.clip_end_frame_exclusive),
            "intersection_start_frame": int(self.intersection_start_frame),
            "intersection_end_frame_exclusive": int(self.intersection_end_frame_exclusive),
            "local_start_frame": int(self.local_start_frame),
            "local_end_frame_exclusive": int(self.local_end_frame_exclusive),
            "output_frame_count": int(self.output_frame_count),
            "output_offset_frames": int(self.output_offset_frames),
            "source_in_us": self.source_in_us,
            "source_out_us": self.source_out_us,
            "source_read_start_us": self.source_read_start_us,
            "source_read_end_us": self.source_read_end_us,
            "local_source_start_us": int(self.local_source_start_us),
            "local_source_span_us": int(self.local_source_span_us),
            "covers_decode_end": bool(self.covers_decode_end),
        }


def clamp_window_to_decode(
    *,
    clip_start_frame: int,
    clip_end_frame_exclusive: int,
    decode_start_frame: int,
    decode_end_frame_exclusive: int,
) -> tuple[int, int] | None:
    """Intersect one clip's film interval with a block's decode interval."""

    start = max(int(clip_start_frame), int(decode_start_frame))
    end = min(int(clip_end_frame_exclusive), int(decode_end_frame_exclusive))
    if start >= end:
        return None
    return (start, end)


def clip_local_frames_for_window(
    *,
    clip_start_frame: int,
    clip_end_frame_exclusive: int,
    decode_start_frame: int,
    decode_end_frame_exclusive: int,
) -> tuple[int, int] | None:
    """The clip-local half-open frame window this block must read."""

    window = clamp_window_to_decode(
        clip_start_frame=clip_start_frame,
        clip_end_frame_exclusive=clip_end_frame_exclusive,
        decode_start_frame=decode_start_frame,
        decode_end_frame_exclusive=decode_end_frame_exclusive,
    )
    if window is None:
        return None
    start, end = window
    return (start - int(clip_start_frame), end - int(clip_start_frame))


def _source_us_for_local_frames(
    *,
    clip: ManifestClip,
    local_frame: int,
    fps: Ratio,
) -> int:
    """Source microsecond offset for a clip-local frame, rounded once, half-up.

    ``clip.frames == 0`` cannot happen (``ManifestClip`` refuses empty clips), so
    the per-frame source step is always defined.  A clip without a declared
    source window maps one film frame to one source frame at the manifest rate.
    """

    if clip.source_in_us is not None and clip.source_out_us is not None:
        span_us = int(clip.source_out_us) - int(clip.source_in_us)
        numerator = span_us * int(local_frame)
        return int(clip.source_in_us) + (numerator + clip.frames // 2) // clip.frames
    return int(clip.source_in_us or 0) + _us_for_frames(local_frame, fps)


def _us_for_frames(frames: int, fps: Ratio) -> int:
    """Half-up rational microseconds for a frame count (never float)."""

    numerator = int(frames) * fps.den * US_PER_SECOND
    return (numerator + fps.num // 2) // max(1, fps.num)


def compute_chunk_slices(
    *,
    manifest: RenderManifest,
    chunk: ManifestChunkSpec,
    tracks: Sequence[str] | None = None,
) -> tuple[ClipSlicePlan, ...]:
    """Plan every clip that contributes to one block, in manifest order.

    ``tracks`` selects the tracks to plan (default: every track present), because
    a narration or BGM item must never be compiled into the picture graph.  The
    returned plans are ordered by film position so concatenation reproduces the
    source order without any further sorting.
    """

    if not isinstance(manifest, RenderManifest):
        raise TypeError("compute_chunk_slices 需要 RenderManifest")
    if not isinstance(chunk, ManifestChunkSpec):
        raise TypeError("compute_chunk_slices 需要 ManifestChunkSpec")
    wanted = None if tracks is None else {str(track).upper() for track in tracks}

    decode_start = int(chunk.decode_start_frame)
    decode_end = min(int(chunk.decode_end_frame_exclusive), int(manifest.total_frames))
    if decode_end <= decode_start:
        return ()

    plans: list[ClipSlicePlan] = []
    for clip in manifest.clips:
        if wanted is not None and str(clip.track).upper() not in wanted:
            continue
        local = clip_local_frames_for_window(
            clip_start_frame=int(clip.start_frame),
            clip_end_frame_exclusive=int(clip.end_frame_exclusive),
            decode_start_frame=decode_start,
            decode_end_frame_exclusive=decode_end,
        )
        if local is None:
            continue
        local_start, local_end = local
        clip_start = int(clip.start_frame)
        clip_end = int(clip.end_frame_exclusive)
        source_in = None if clip.source_in_us is None else int(clip.source_in_us)
        source_out = None if clip.source_out_us is None else int(clip.source_out_us)
        if source_in is None or source_out is None:
            span_us = _us_for_frames(clip.frames, manifest.fps)
            source_read_start = source_in
            source_read_end = None if source_in is None else source_in + span_us
        else:
            source_read_start = _source_us_for_local_frames(
                clip=clip, local_frame=local_start, fps=manifest.fps
            )
            source_read_end = _source_us_for_local_frames(
                clip=clip, local_frame=local_end, fps=manifest.fps
            )
        local_source_start_us = (
            0 if source_read_start is None or source_in is None else source_read_start - source_in
        )
        local_source_span_us = (
            0
            if source_read_start is None or source_read_end is None
            else max(0, source_read_end - source_read_start)
        )
        plans.append(
            ClipSlicePlan(
                clip_id=str(clip.clip_id),
                track=str(clip.track).upper(),
                item_kind=str(clip.item_kind).upper(),
                media_version_id=clip.media_version_id,
                clip_start_frame=clip_start,
                clip_end_frame_exclusive=clip_end,
                intersection_start_frame=clip_start + local_start,
                intersection_end_frame_exclusive=clip_start + local_end,
                local_start_frame=local_start,
                local_end_frame_exclusive=local_end,
                output_frame_count=local_end - local_start,
                output_offset_frames=max(0, clip_start + local_start - decode_start),
                source_in_us=source_in,
                source_out_us=source_out,
                source_read_start_us=source_read_start,
                source_read_end_us=source_read_end,
                local_source_start_us=int(local_source_start_us),
                local_source_span_us=int(local_source_span_us),
                covers_decode_end=clip_end >= decode_end,
            )
        )
    return tuple(plans)


def slice_dicts(plans: Sequence[ClipSlicePlan]) -> list[dict[str, Any]]:
    """Convenience projection for command notes and diagnostics."""

    return [plan.as_dict() for plan in plans]


def track_kinds(manifest: RenderManifest) -> Mapping[str, tuple[str, ...]]:
    """Map every track to the item kinds declared on it (diagnostics only)."""

    collected: dict[str, list[str]] = {}
    for clip in manifest.clips:
        collected.setdefault(str(clip.track).upper(), []).append(str(clip.item_kind).upper())
    return {track: tuple(kinds) for track, kinds in collected.items()}

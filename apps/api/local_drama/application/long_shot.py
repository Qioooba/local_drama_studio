"""Long-shot (>15s) segment planning aligned with the H3 frame grid.

P1-9: a take longer than the H3 single-shot limit is generated as several
segments and later concatenated by the timeline renderer.  This module only
plans; it never touches the database, media files or FFmpeg.

Frame grid semantics
--------------------
The H3 model snaps frame counts to the ``17k+5`` grid (``h3_workflows._frame_count``
rounds ``duration * 24`` then walks the target up until ``frames % 17 == 5``).
That function intentionally is NOT imported here:

* this module belongs to the timeline layer while ``h3_workflows`` belongs to
  the generation layer; importing it would couple the two and risk an import
  cycle once the generation layer starts consuming plans;
* the snap rule is a three-line pure function, cheap to keep in sync locally.

Every segment's frame count therefore satisfies ``frames % 17 == 5`` (or equals
the single-segment snap), matching what ``build_t2va``/``build_fl2va`` would
accept as the ``length`` input.

Continuation anchors
--------------------
Segment N's tail frame IS segment N+1's head frame
(``LAST_FRAME_TO_FIRST_FRAME``), so adjacent segments overlap by exactly one
frame.  ``plan_segments`` expresses this as ``segments[i+1].start_seconds ==
segments[i].end_seconds - 1/fps`` and records the anchor on every segment
after the first.
"""

from __future__ import annotations

import math
from typing import Any

DEFAULT_MAX_SEGMENT_SECONDS = 15.0
DEFAULT_FPS = 24.0
_GRID_PATTERN = "17k+5"


def grid_frame_count(frames: float) -> int:
    """Snap a raw frame count onto the H3 17k+5 grid.

    Mirrors ``h3_workflows._frame_count`` exactly (same rounding and same
    remainder walk) but operates on frames instead of seconds, so callers can
    snap per-segment budgets.  A single segment planned as
    ``grid_frame_count(duration_seconds * fps)`` is byte-identical to what the
    H3 builder would compute for the same duration.
    """
    target = round(frames)
    remainder = target % 17
    if remainder != 5:
        target += (5 - remainder) % 17
    return target


def plan_segments(
    duration_seconds: float,
    max_segment_seconds: float = DEFAULT_MAX_SEGMENT_SECONDS,
    fps: float = DEFAULT_FPS,
) -> dict[str, Any]:
    """Plan a long take as ordered segments with continuation anchors.

    ``duration_seconds <= max_segment_seconds`` collapses to a single segment
    (the H3 builder then treats it as an ordinary shot).  Longer takes are cut
    into ``ceil(duration / max_segment_seconds)`` segments; each segment's
    frame budget is snapped onto the 17k+5 grid, and if the snapped sum minus
    the shared anchor frames would fall short of the requested duration, the
    last segment grows by whole grid steps so the plan always covers the
    request.

    Returns a plan dict with ``segments`` where every item carries:
    ``segment_no``, ``start_seconds``, ``end_seconds``, ``frames`` and a
    ``continuation`` anchor (``None`` for the first segment).
    """
    if not duration_seconds > 0:
        raise ValueError("duration_seconds 必须为正数")
    if not max_segment_seconds > 0:
        raise ValueError("max_segment_seconds 必须为正数")
    if not fps > 0:
        raise ValueError("fps 必须为正数")

    requested_frames = round(duration_seconds * fps)
    segment_count = max(1, math.ceil(duration_seconds / max_segment_seconds))

    if segment_count == 1:
        frames = grid_frame_count(duration_seconds * fps)
        duration = frames / fps
        return {
            "schema_version": "localdrama.long-shot-plan.v1",
            "duration_seconds": round(duration, 6),
            "requested_duration_seconds": round(duration_seconds, 6),
            "segment_count": 1,
            "grid": {"pattern": _GRID_PATTERN, "fps": fps, "max_segment_seconds": max_segment_seconds},
            "continuation": "LAST_FRAME_TO_FIRST_FRAME",
            "segments": [
                {
                    "segment_no": 1,
                    "start_seconds": 0.0,
                    "end_seconds": round(duration, 6),
                    "frames": frames,
                    "continuation": None,
                }
            ],
        }

    nominal_frames = (duration_seconds / segment_count) * fps
    frames_per_segment = [grid_frame_count(nominal_frames) for _ in range(segment_count)]
    # Coverage guarantee: with (K-1) shared anchor frames the effective total is
    # sum(frames) - (K-1); grow the last segment until it reaches the request.
    while sum(frames_per_segment) - (segment_count - 1) < requested_frames:
        frames_per_segment[-1] += 17

    segments: list[dict[str, Any]] = []
    cursor_frames = 0
    for index, segment_frames in enumerate(frames_per_segment, start=1):
        start_seconds = cursor_frames / fps
        end_seconds = (cursor_frames + segment_frames) / fps
        segments.append(
            {
                "segment_no": index,
                "start_seconds": round(start_seconds, 6),
                "end_seconds": round(end_seconds, 6),
                "frames": segment_frames,
                "continuation": (
                    None
                    if index == 1
                    else {
                        "from_segment_no": index - 1,
                        "mode": "LAST_FRAME_TO_FIRST_FRAME",
                        "shared_frame_count": 1,
                    }
                ),
            }
        )
        # The next segment reuses this segment's tail frame as its head frame.
        cursor_frames += segment_frames - 1

    total_frames = sum(frames_per_segment) - (segment_count - 1)
    return {
        "schema_version": "localdrama.long-shot-plan.v1",
        "duration_seconds": round(total_frames / fps, 6),
        "requested_duration_seconds": round(duration_seconds, 6),
        "segment_count": segment_count,
        "grid": {"pattern": _GRID_PATTERN, "fps": fps, "max_segment_seconds": max_segment_seconds},
        "continuation": "LAST_FRAME_TO_FIRST_FRAME",
        "segments": segments,
    }

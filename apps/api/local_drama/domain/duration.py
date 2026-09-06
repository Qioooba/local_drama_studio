"""Shared target-duration rules for project and episode planning."""

from __future__ import annotations

DEFAULT_PROJECT_TARGET_DURATION_MS = 120_000
MIN_TARGET_DURATION_MS = 1
MAX_TARGET_DURATION_MS = 86_400_000
# Small timestamp/planning arithmetic can differ by a fraction of a second;
# larger target-plan differences must remain visible and require re-planning.
TARGET_DURATION_TECHNICAL_TOLERANCE_MS = 1_000


def normalize_target_duration_ms(value: int | None, *, default: int = DEFAULT_PROJECT_TARGET_DURATION_MS) -> int:
    """Return a validated duration in milliseconds.

    ``None`` means "use the project's explicit default" at a call site.  A
    concrete value is always retained as the episode's resolved value; there
    is deliberately no lookup of another episode here.
    """

    resolved = default if value is None else value
    if isinstance(resolved, bool) or not isinstance(resolved, int):
        raise ValueError("target_duration_ms must be an integer")
    if resolved < MIN_TARGET_DURATION_MS or resolved > MAX_TARGET_DURATION_MS:
        raise ValueError(
            f"target_duration_ms must be between {MIN_TARGET_DURATION_MS} and {MAX_TARGET_DURATION_MS}"
        )
    return resolved

"""Evidence-only local p50/p90 generation duration estimates."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any

from local_drama.application.ports.generation_estimates import GenerationEstimateHistoryPort


def _number(mapping: dict[str, Any], *keys: str) -> float | None:
    indexed = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        value = indexed.get(key.lower())
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed > 0:
            return parsed
    return None


def _dimensions(snapshot_json: str | None) -> dict[str, float | int | None]:
    try:
        snapshot = json.loads(snapshot_json or "{}")
    except (TypeError, json.JSONDecodeError):
        snapshot = {}
    semantic = snapshot.get("semantic_inputs", {}) if isinstance(snapshot, dict) else {}
    if not isinstance(semantic, dict):
        semantic = {}
    width = _number(semantic, "width", "output_width")
    height = _number(semantic, "height", "output_height")
    resolution = semantic.get("resolution") or semantic.get("RESOLUTION")
    if isinstance(resolution, dict):
        width = width or _number(resolution, "width", "w")
        height = height or _number(resolution, "height", "h")
    elif isinstance(resolution, str):
        match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", resolution)
        if match:
            width = width or float(match.group(1))
            height = height or float(match.group(2))
    return {
        "width": int(width) if width is not None and width.is_integer() else None,
        "height": int(height) if height is not None and height.is_integer() else None,
        "duration_seconds": _number(semantic, "duration_seconds", "duration", "video_duration_seconds"),
        "frame_count": int(value) if (value := _number(semantic, "frame_count", "frames", "num_frames")) is not None and value.is_integer() else None,
        "steps": int(value) if (value := _number(semantic, "steps", "num_steps", "sampling_steps")) is not None and value.is_integer() else None,
    }


def _elapsed_seconds(started_at: Any, finished_at: Any) -> float | None:
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        elapsed = (finish - start).total_seconds()
    except (TypeError, ValueError):
        return None
    return elapsed if math.isfinite(elapsed) and elapsed > 0 else None


def _gpu_class(row: dict[str, Any]) -> str | None:
    persisted = str(row.get("resource_key") or "").strip()
    if persisted:
        return persisted
    channel = str(row.get("channel") or "").strip().upper()
    return "GPU_H3_HEAVY" if channel in {"GPU_H3", "GPU", "VIDEO_GPU"} else (f"CHANNEL:{channel}" if channel else None)


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


class GenerationEstimateService:
    MINIMUM_SAMPLE_COUNT = 3

    def __init__(self, history: GenerationEstimateHistoryPort) -> None:
        self.history = history

    def estimate(
        self,
        *,
        profile_version_id: str,
        width: int | None = None,
        height: int | None = None,
        duration_seconds: float | None = None,
        frame_count: int | None = None,
        steps: int | None = None,
        gpu_class: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        facts = self.history.load_successful_attempts(profile_version_id=profile_version_id, limit=limit)
        requested = {
            "width": width, "height": height, "duration_seconds": duration_seconds,
            "frame_count": frame_count, "steps": steps,
        }
        samples: list[float] = []
        for row in facts["items"]:
            dimensions = _dimensions(row.get("input_snapshot_json"))
            if any(value is not None and dimensions[key] != value for key, value in requested.items()):
                continue
            if gpu_class is not None and _gpu_class(row) != gpu_class:
                continue
            elapsed = _elapsed_seconds(row.get("started_at"), row.get("finished_at"))
            if elapsed is not None:
                samples.append(elapsed)

        if not facts["schema_available"]:
            reason = "SCHEMA_UNAVAILABLE"
        elif not samples:
            reason = "NO_MATCHING_HISTORY"
        elif len(samples) < self.MINIMUM_SAMPLE_COUNT:
            reason = "INSUFFICIENT_SAMPLES"
        else:
            reason = None
        available = reason is None
        return {
            "status": "AVAILABLE" if available else "NO_LOCAL_ESTIMATE",
            "reason": reason,
            "dimensions": {
                "profile_version_id": profile_version_id,
                **requested,
                "gpu_class": gpu_class,
                "gpu_hardware_model": None,
                "gpu_hardware_model_known": False,
            },
            "sample_count": len(samples),
            "minimum_sample_count": self.MINIMUM_SAMPLE_COUNT,
            "p50_seconds": round(_nearest_rank(samples, 0.5), 3) if available else None,
            "p90_seconds": round(_nearest_rank(samples, 0.9), 3) if available else None,
            "evidence": {
                "source": "LOCAL_SUCCEEDED_JOB_ATTEMPTS",
                "most_recent_first": True,
                "candidate_limit": limit,
                "candidate_count": len(facts["items"]),
                "gpu_dimension_source": "JOB_RESOURCE_LEASE_CLASS_OR_CHANNEL",
                "gpu_hardware_model_recorded": False,
            },
            "audit": {
                "read_only": True, "writes_performed": 0,
                "query_count": int(facts["query_count"]), "query_limit": limit,
            },
            "local_only": True,
            "network_contacted": False,
        }

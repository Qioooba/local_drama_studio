"""SEGMENTED_EPISODE_COMPOSE job handler: fingerprint-guarded segmented episode render."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from local_drama.application.worker_handlers._timeline_ports import SegmentedEpisodeTimelineJobPort
from local_drama.domain.errors import DomainRuleError

AtomicWriter = Callable[[Path, Callable[[Path], object]], None]


def run_segmented_compose_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    timeline_factory: Callable[[], SegmentedEpisodeTimelineJobPort],
    atomic_writer: AtomicWriter,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    timeline_revision_id = str(snapshot.get("timeline_revision_id") or "")
    segments = snapshot.get("segments")
    expected = str(snapshot.get("compose_fingerprint") or "")
    if (
        job["subject_type"] != "TIMELINE_REVISION"
        or str(job["subject_id"]) != timeline_revision_id
        or not isinstance(segments, list)
        or not expected
    ):
        raise DomainRuleError("SEGMENTED_COMPOSE_JOB_SNAPSHOT_INVALID", "分段合成 Job 缺少不可变时间线或分段输入")
    timeline = timeline_factory()
    current = timeline.preflight_segmented_episode_render(timeline_revision_id, segments)
    if str(current["compose_fingerprint"]) != expected:
        raise DomainRuleError("COMPOSE_INPUT_STALE", "分段合成入队后输入已变化，请重新预检并提交")
    render = timeline.render_segmented_episode(
        timeline_revision_id,
        segments,
        force_rerender=bool(snapshot.get("force_rerender")),
        actor="segmented-compose-worker",
    )
    report = {
        "schema_version": "localdrama.segmented-compose-report.v1",
        "job_id": str(job["id"]),
        "timeline_revision_id": timeline_revision_id,
        "compose_fingerprint": expected,
        "render_version_id": str(render["id"]),
        "render_sha256": str(render["sha256"]),
        "local_only": True,
        "network_contacted": False,
    }
    output = output_root / "segmented-compose-report.json"
    atomic_writer(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
    return "SEGMENTED_EPISODE_COMPOSE_REPORT", output.relative_to(work_root).as_posix()

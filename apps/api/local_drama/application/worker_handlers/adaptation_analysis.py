"""Worker handler for one long-form adaptation analysis node."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Protocol


class AdaptationAnalysisPort(Protocol):
    def execute_node(self, job: dict[str, Any], *, on_progress: Callable[[dict[str, Any]], None]) -> dict[str, Any]: ...


AtomicWriter = Callable[[Path, Callable[[Path], object]], None]
ProgressCallback = Callable[[dict[str, Any]], None]


def run_adaptation_analysis_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    analysis: AdaptationAnalysisPort,
    atomic_writer: AtomicWriter,
    on_progress: ProgressCallback,
) -> tuple[str, str]:
    result = analysis.execute_node(job, on_progress=on_progress)
    output = output_root / "adaptation-analysis-node-report.json"
    atomic_writer(
        output,
        lambda target: target.write_text(
            json.dumps({"schema_version": "adaptation-analysis-node-report/v1", "job_id": job["id"], **result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        ),
    )
    return "ADAPTATION_ANALYSIS_REPORT", output.relative_to(work_root).as_posix()

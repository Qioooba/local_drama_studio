"""Typed dispatch boundary for worker job execution.

The persistent queue owns claiming/completion; concrete handlers own media
work.  This module only maps a declared job type to one handler, which keeps
queue orchestration independent from the growing set of worker capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from local_drama.domain.errors import DomainRuleError


@dataclass(frozen=True)
class WorkerExecution:
    kind: str
    relative_path: str
    report: dict[str, Any] | None = None
    produced_bytes: int = 0
    additional_artifacts: tuple[tuple[str, str], ...] = ()
    after_artifacts_registered: Callable[[tuple[dict[str, Any], ...]], None] | None = None


WorkerHandler = Callable[[dict[str, Any], Path], WorkerExecution]


class WorkerJobDispatcher:
    def __init__(self, handlers: Mapping[str, WorkerHandler]) -> None:
        self._handlers = dict(handlers)

    @property
    def supported_types(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def execute(self, job: dict[str, Any], output_root: Path) -> WorkerExecution:
        job_type = str(job.get("type") or "")
        handler = self._handlers.get(job_type)
        if handler is None:
            raise DomainRuleError(
                "JOB_TYPE_UNSUPPORTED",
                "当前本地 worker 不支持该 Job 类型",
                {"type": job_type},
            )
        return handler(job, output_root)

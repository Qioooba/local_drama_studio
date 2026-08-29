"""Worker implementations for immutable Model Platform V2 snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.execution_job_links import WorkerExecutionSnapshot

WorkerExecutionImplementation = Callable[[WorkerExecutionSnapshot, Path], tuple[str, str]]


@dataclass(frozen=True, slots=True)
class WorkerExecutionHandlerDescriptor:
    """An implementation that may consume one explicitly frozen handler."""

    code: str
    version: str
    capability_code: str
    adapter_codes: frozenset[str]
    execute: WorkerExecutionImplementation


class WorkerExecutionHandlerRegistry:
    """Exact handler boundary used after a V2 Job has been claimed.

    Submission declares support; this registry proves the local worker process
    actually contains the corresponding implementation.  It never resolves a
    live Profile, runtime URL, or model path.
    """

    def __init__(self, descriptors: Iterable[WorkerExecutionHandlerDescriptor] = ()) -> None:
        self._items = tuple(descriptors)
        keys = [(item.code, item.version) for item in self._items]
        if len(keys) != len(set(keys)):
            raise ValueError("A V2 worker handler code/version may be registered only once")

    def execute(self, snapshot: WorkerExecutionSnapshot, output_root: Path) -> tuple[str, str]:
        for item in self._items:
            if item.code != snapshot.handler_code or item.version != snapshot.handler_version:
                continue
            capability_matches = item.capability_code == "*" or item.capability_code.upper() == snapshot.capability_code.upper()
            if not capability_matches or snapshot.adapter_code not in item.adapter_codes:
                raise DomainRuleError(
                    "MP_EXECUTION_WORKER_HANDLER_MISMATCH",
                    "冻结执行快照与本机 V2 Worker handler 的能力或 adapter 声明不匹配。",
                    {
                        "handler_code": snapshot.handler_code,
                        "handler_version": snapshot.handler_version,
                        "capability_code": snapshot.capability_code,
                        "adapter_code": snapshot.adapter_code,
                    },
                )
            return item.execute(snapshot, output_root)
        raise DomainRuleError(
            "MP_EXECUTION_WORKER_HANDLER_UNAVAILABLE",
            "该 V2 Job 的冻结 handler 未安装在当前 Worker，不能执行。",
            {"handler_code": snapshot.handler_code, "handler_version": snapshot.handler_version},
        )

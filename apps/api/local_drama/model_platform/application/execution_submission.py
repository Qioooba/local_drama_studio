"""Atomic V2 execution submission from a fresh preview to one durable Job."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from local_drama.application.jobs import JobService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_handlers import ExecutionHandlerRegistry
from local_drama.model_platform.application.execution_job_links import ExecutionJobLink, ExecutionJobLinkService
from local_drama.model_platform.application.execution_planning import (
    ExecutionPlanningService,
    ExecutionPreviewRequest,
)
from local_drama.model_platform.application.execution_snapshots import ExecutionSnapshot, ExecutionSnapshotDraft, ExecutionSnapshotService


@dataclass(frozen=True, slots=True)
class ExecutionSubmission:
    job: dict[str, Any]
    execution_snapshot_id: str
    execution_snapshot_hash: str
    handler_code: str
    handler_version: str
    idempotent_replay: bool


class ExecutionSubmissionService:
    """The only application service allowed to enqueue V2 model work.

    A submit repeats the preview calculation, requires the user-confirmed
    resolution hash, then freezes its result before a Job can become visible.
    The registry deliberately has no implicit fallbacks: a published Profile
    does not imply that a Worker can execute it.
    """

    def __init__(self, database: Database, handlers: ExecutionHandlerRegistry | None = None) -> None:
        self.database = database
        self.planning = ExecutionPlanningService(database)
        self.handlers = handlers or ExecutionHandlerRegistry()
        self.snapshots = ExecutionSnapshotService(database)
        self.links = ExecutionJobLinkService(database)
        self.jobs = JobService(database)

    def submit(
        self,
        request: ExecutionPreviewRequest,
        idempotency_key: str,
        *,
        after_linked_in_transaction: Callable[[Any, dict[str, Any], ExecutionSnapshot], None] | None = None,
    ) -> ExecutionSubmission:
        preview = self.planning.preview(request)
        self.planning.assert_submit_fresh(preview, request.expected_resolution_hash)
        if preview.execution_profile_version_id is None or preview.adapter_code is None:
            raise DomainRuleError("MP_EXECUTION_NOT_READY", "当前能力没有可提交的 V2 Profile。")
        handler = self.handlers.resolve(preview.capability_code, preview.adapter_code)
        scheduler_snapshot = {"execution_snapshot_id": None, "content_hash": None}
        if handler.gpu_runtime:
            scheduler_snapshot["scheduler_runtime"] = handler.gpu_runtime.strip().upper()
        scope = _job_scope(request.scope)
        draft = ExecutionSnapshotDraft(
            capability_code=preview.capability_code,
            execution_profile_version_id=preview.execution_profile_version_id,
            resolved_parameters=preview.resolved_parameters,
            semantic_inputs=request.semantic_inputs,
            resolution={
                "resolution_hash": preview.resolution_hash,
                "resolution_reason": preview.resolution_reason,
                "assignment_chain": list(preview.assignment_chain),
            },
            network_policy=preview.network_policy,
        )
        with self.database.transaction() as connection:
            snapshot = self.snapshots.create_in_transaction(connection, draft)
            scheduler_snapshot["execution_snapshot_id"] = snapshot.id
            scheduler_snapshot["content_hash"] = snapshot.content_hash
            job = self.jobs.create_job_in_transaction(
                connection,
                scope["project_id"],
                "MODEL_PLATFORM_EXECUTION",
                "MODEL_PLATFORM_EXECUTION",
                snapshot.id,
                handler.worker_channel,
                scheduler_snapshot,
                idempotency_key,
                # jobs.execution_profile_version_id is a legacy FK.  V2
                # profile provenance lives only in the immutable snapshot and
                # its dedicated one-to-one link, never in the legacy column.
                execution_profile_version_id=None,
                subject_kind="MODEL_PLATFORM_EXECUTION",
                scope_kind=scope["scope_kind"],
                scope_project_id=scope["project_id"],
                scope_episode_id=scope["episode_id"],
                scope_shot_id=scope["shot_id"],
                stage_code="MODEL_PLATFORM_EXECUTION",
            )
            replay = self.links.link_or_verify_in_transaction(
                connection,
                ExecutionJobLink(str(job["id"]), snapshot.id, handler.code, handler.version),
            )
            if after_linked_in_transaction is not None:
                after_linked_in_transaction(connection, job, snapshot)
        return ExecutionSubmission(
            job=job,
            execution_snapshot_id=snapshot.id,
            execution_snapshot_hash=snapshot.content_hash,
            handler_code=handler.code,
            handler_version=handler.version,
            idempotent_replay=bool(job.get("idempotent_replay")) or replay,
        )


def _job_scope(scope: CapabilityScopeContext) -> dict[str, str | None]:
    if scope.project_id:
        return {
            "project_id": scope.project_id,
            "scope_kind": "PROJECT",
            "episode_id": scope.episode_id,
            "shot_id": scope.shot_id,
        }
    if scope.episode_id or scope.shot_id:
        raise DomainRuleError("MP_EXECUTION_SCOPE_INVALID", "分集或镜头执行必须同时声明所属项目。")
    return {"project_id": None, "scope_kind": "SYSTEM", "episode_id": None, "shot_id": None}

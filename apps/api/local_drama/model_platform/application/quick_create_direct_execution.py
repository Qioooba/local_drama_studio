"""Explicit, cutover-only V2 direct image execution for Quick Create.

This is intentionally separate from ``QuickGenerationService``.  It owns no
legacy profile, workflow, or quick-generation-run reference, and every queued
operation is a normal immutable V2 execution snapshot plus V2 Job link.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.execution_submission import ExecutionSubmissionService
from local_drama.model_platform.application.production_execution_registry import production_execution_handlers
from local_drama.model_platform.application.quick_create_readiness import direct_image_profile_contract_blocker

_CAPABILITY = "IMAGE_CONCEPT"


@dataclass(frozen=True, slots=True)
class QuickCreateV2DirectImagePreview:
    capability_code: str
    execution_profile_version_id: str | None
    resolution_hash: str
    executable: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuickCreateV2DirectImageSubmission:
    job_id: str
    execution_snapshot_id: str
    execution_snapshot_hash: str
    handler_code: str
    handler_version: str
    idempotent_replay: bool


@dataclass(frozen=True, slots=True)
class QuickCreateV2DirectImageStatus:
    """A browser-safe projection of one direct V2 image execution.

    The caller gets neither a sandbox path nor a generic Job detail payload.
    The only artifact reference is the existing controlled download endpoint.
    """

    job_id: str
    state: str
    progress: Mapping[str, Any]
    error_code: str | None
    error_detail_redacted: str | None
    execution_snapshot_id: str
    execution_snapshot_hash: str
    artifacts: tuple[Mapping[str, str], ...]


class QuickCreateV2DirectImageService:
    """Submit one compatible image capability without a V1 fallback path."""

    def __init__(
        self,
        database: Database,
        *,
        planning: ExecutionPlanningService | None = None,
        submissions: ExecutionSubmissionService | None = None,
        profile_contract_blocker: Callable[[Database, str], str | None] = direct_image_profile_contract_blocker,
    ) -> None:
        self.database = database
        self.planning = planning or ExecutionPlanningService(database)
        self.submissions = submissions or ExecutionSubmissionService(database, production_execution_handlers())
        self.profile_contract_blocker = profile_contract_blocker

    def preview(self, *, prompt: str, run_overrides: Mapping[str, Any] | None = None) -> QuickCreateV2DirectImagePreview:
        request = _request(prompt, run_overrides)
        preview = self.planning.preview(request)
        blockers = list(preview.blockers)
        if preview.execution_profile_version_id is not None:
            blocker = self.profile_contract_blocker(self.database, preview.execution_profile_version_id)
            if blocker is not None:
                blockers.append(blocker)
        return QuickCreateV2DirectImagePreview(
            capability_code=_CAPABILITY,
            execution_profile_version_id=preview.execution_profile_version_id,
            resolution_hash=preview.resolution_hash,
            executable=not blockers and preview.execution_profile_version_id is not None,
            blockers=tuple(blockers),
        )

    def submit(
        self,
        *,
        prompt: str,
        run_overrides: Mapping[str, Any] | None,
        expected_resolution_hash: str,
        idempotency_key: str,
    ) -> QuickCreateV2DirectImageSubmission:
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "V2 快速生成提交必须提供 Idempotency-Key。")
        direct_preview = self.preview(prompt=prompt, run_overrides=run_overrides)
        if not direct_preview.executable:
            raise DomainRuleError(
                "QUICK_CREATE_V2_DIRECT_IMAGE_NOT_READY",
                "当前 V2 文生图 Profile 未满足单能力 Quick Create 切流合同。",
                {"blockers": list(direct_preview.blockers)},
            )
        request = _request(prompt, run_overrides, expected_resolution_hash=expected_resolution_hash)
        self.planning.assert_submit_fresh(self.planning.preview(request), expected_resolution_hash)
        submitted = self.submissions.submit(request, f"quick-create-v2:image:{idempotency_key}")
        return QuickCreateV2DirectImageSubmission(
            job_id=str(submitted.job["id"]),
            execution_snapshot_id=submitted.execution_snapshot_id,
            execution_snapshot_hash=submitted.execution_snapshot_hash,
            handler_code=submitted.handler_code,
            handler_version=submitted.handler_version,
            idempotent_replay=submitted.idempotent_replay,
        )

    def status(self, job_id: str) -> QuickCreateV2DirectImageStatus:
        """Read only the exact direct-image command shape owned by this surface.

        A generic ``IMAGE_CONCEPT`` V2 Job is deliberately not enough: it must
        have been submitted with this command's namespaced idempotency key,
        System scope, a Comfy V1 adapter, and an immutable V2 execution link.
        This prevents Quick Create from becoming a back door into unrelated
        model-platform or legacy execution records.
        """

        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT job.id AS job_id, job.state, job.progress_json,
                          job.last_error_code, job.last_error_detail_redacted,
                          link.execution_snapshot_id, snapshot.content_hash AS execution_snapshot_hash
                   FROM jobs job
                   JOIN mp_execution_job_links link ON link.job_id=job.id
                   JOIN mp_execution_snapshots snapshot ON snapshot.id=link.execution_snapshot_id
                   JOIN mp_capability_definitions capability ON capability.id=snapshot.capability_definition_id
                   WHERE job.id=?
                     AND job.type='MODEL_PLATFORM_EXECUTION'
                     AND job.scope_kind='SYSTEM'
                     AND job.idempotency_key LIKE 'quick-create-v2:image:%'
                     AND capability.code='IMAGE_CONCEPT'
                     AND snapshot.adapter_code='comfy.workflow.v1'""",
                (job_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("QUICK_CREATE_V2_DIRECT_IMAGE_JOB_NOT_FOUND", "V2 单次文生图任务不存在。")
            artifacts = connection.execute(
                """SELECT artifact.id, artifact.kind
                   FROM artifacts artifact
                   JOIN job_attempts attempt ON attempt.id=artifact.job_attempt_id
                   WHERE attempt.job_id=?
                     AND artifact.status='VERIFIED'
                     AND artifact.kind='COMFY_OUTPUT'
                   ORDER BY artifact.created_at, artifact.id""",
                (job_id,),
            ).fetchall()
        return QuickCreateV2DirectImageStatus(
            job_id=str(row["job_id"]),
            state=str(row["state"]),
            progress=_json_object(row["progress_json"]),
            error_code=str(row["last_error_code"]) if row["last_error_code"] else None,
            error_detail_redacted=str(row["last_error_detail_redacted"]) if row["last_error_detail_redacted"] else None,
            execution_snapshot_id=str(row["execution_snapshot_id"]),
            execution_snapshot_hash=str(row["execution_snapshot_hash"]),
            artifacts=tuple({
                "artifact_id": str(artifact["id"]),
                "kind": str(artifact["kind"]),
                "download_url": f"/api/v1/artifacts/{artifact['id']}/download",
            } for artifact in artifacts),
        )


def _request(
    prompt: str,
    run_overrides: Mapping[str, Any] | None,
    *,
    expected_resolution_hash: str | None = None,
) -> ExecutionPreviewRequest:
    normalized = prompt.strip()
    if not 2 <= len(normalized) <= 2000:
        raise DomainRuleError("QUICK_CREATE_V2_PROMPT_REQUIRED", "V2 文生图描述必须是 2—2000 个字符。")
    return ExecutionPreviewRequest(
        capability_code=_CAPABILITY,
        scope=CapabilityScopeContext(),
        semantic_inputs={"PROMPT": normalized},
        run_overrides=dict(run_overrides or {}),
        expected_resolution_hash=expected_resolution_hash,
    )


def _json_object(value: object) -> Mapping[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

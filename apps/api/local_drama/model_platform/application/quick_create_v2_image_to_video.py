"""V2-only image-to-video handoff for a selected Quick Create candidate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.execution_submission import ExecutionSubmissionService
from local_drama.model_platform.application.production_execution_registry import production_execution_handlers
from local_drama.model_platform.application.quick_create_readiness import image_to_video_profile_contract_blocker
from local_drama.model_platform.application.quick_create_v2_runs import QuickCreateV2RunService, QuickCreateV2SelectedImage

_CAPABILITY = "VIDEO_I2V"


@dataclass(frozen=True, slots=True)
class QuickCreateV2ImageToVideoPreview:
    run_id: str
    selected_image_artifact_id: str
    execution_profile_version_id: str | None
    resolution_hash: str
    executable: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QuickCreateV2ImageToVideoSubmission:
    run_id: str
    job_id: str
    execution_snapshot_id: str
    execution_snapshot_hash: str
    handler_code: str
    handler_version: str
    idempotent_replay: bool


class QuickCreateV2ImageToVideoService:
    """Submit exactly one I2V stage from a frozen V2 selected image artifact."""

    def __init__(
        self,
        database: Database,
        *,
        runs: QuickCreateV2RunService | None = None,
        planning: ExecutionPlanningService | None = None,
        submissions: ExecutionSubmissionService | None = None,
        profile_contract_blocker: Callable[[Database, str], str | None] = image_to_video_profile_contract_blocker,
    ) -> None:
        self.database = database
        self.runs = runs or QuickCreateV2RunService(database)
        self.planning = planning or ExecutionPlanningService(database)
        self.submissions = submissions or ExecutionSubmissionService(database, production_execution_handlers())
        self.profile_contract_blocker = profile_contract_blocker

    def preview(self, *, run_id: str) -> QuickCreateV2ImageToVideoPreview:
        source = self.runs.selected_image_for_video(run_id)
        preview = self.planning.preview(_request(source))
        blockers = list(preview.blockers)
        if preview.execution_profile_version_id is not None:
            blocker = self.profile_contract_blocker(self.database, preview.execution_profile_version_id)
            if blocker is not None:
                blockers.append(blocker)
        return QuickCreateV2ImageToVideoPreview(
            run_id=source.run_id,
            selected_image_artifact_id=source.artifact_id,
            execution_profile_version_id=preview.execution_profile_version_id,
            resolution_hash=preview.resolution_hash,
            executable=preview.execution_profile_version_id is not None and not blockers,
            blockers=tuple(dict.fromkeys(blockers)),
        )

    def submit(
        self,
        *,
        run_id: str,
        expected_resolution_hash: str,
        idempotency_key: str,
    ) -> QuickCreateV2ImageToVideoSubmission:
        if not idempotency_key or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "V2 图生视频提交必须提供 Idempotency-Key。")
        source = self.runs.selected_image_for_video(run_id)
        preview = self.planning.preview(_request(source))
        blockers = list(preview.blockers)
        if preview.execution_profile_version_id is not None:
            blocker = self.profile_contract_blocker(self.database, preview.execution_profile_version_id)
            if blocker is not None:
                blockers.append(blocker)
        if blockers or preview.execution_profile_version_id is None:
            raise DomainRuleError(
                "QUICK_CREATE_V2_I2V_NOT_READY",
                "当前 V2 图生视频 Profile 未满足受控工件交接合同。",
                {"blockers": list(dict.fromkeys(blockers))},
            )
        self.planning.assert_submit_fresh(preview, expected_resolution_hash)
        request = _request(source, expected_resolution_hash=expected_resolution_hash)
        submitted = self.submissions.submit(
            request,
            f"quick-create-v2:run:{source.run_id}:video:{idempotency_key}",
            after_linked_in_transaction=lambda connection, job, snapshot: self.runs.attach_step_in_transaction(
                connection,
                source.run_id,
                step_no=self.runs.next_step_no_in_transaction(connection, source.run_id),
                step_kind="VIDEO_I2V",
                capability_code=_CAPABILITY,
                execution_snapshot_id=snapshot.id,
                job_id=str(job["id"]),
                input_artifact_id=source.artifact_id,
                payload={"selected_image_step_id": source.selected_step_id, "resolution_hash": expected_resolution_hash},
            ),
        )
        return QuickCreateV2ImageToVideoSubmission(
            run_id=source.run_id,
            job_id=str(submitted.job["id"]),
            execution_snapshot_id=submitted.execution_snapshot_id,
            execution_snapshot_hash=submitted.execution_snapshot_hash,
            handler_code=submitted.handler_code,
            handler_version=submitted.handler_version,
            idempotent_replay=submitted.idempotent_replay,
        )


def _request(source: QuickCreateV2SelectedImage, *, expected_resolution_hash: str | None = None) -> ExecutionPreviewRequest:
    return ExecutionPreviewRequest(
        capability_code=_CAPABILITY,
        scope=CapabilityScopeContext(),
        semantic_inputs={"PROMPT": source.prompt, "FIRST_FRAME": {"artifact_id": source.artifact_id}},
        run_overrides={},
        expected_resolution_hash=expected_resolution_hash,
    )

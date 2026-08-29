"""V2-only image candidate submission for the image-to-video aggregate."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.execution_snapshots import ExecutionSnapshot
from local_drama.model_platform.application.execution_submission import ExecutionSubmissionService
from local_drama.model_platform.application.production_execution_registry import production_execution_handlers
from local_drama.model_platform.application.quick_create_readiness import candidate_image_profile_contract_blocker
from local_drama.model_platform.application.quick_create_v2_runs import QuickCreateV2Run, QuickCreateV2RunService

_CAPABILITY = "IMAGE_CONCEPT"


@dataclass(frozen=True, slots=True)
class QuickCreateV2ImageCandidatePreview:
    ordinal: int
    seed: int
    resolution_hash: str


@dataclass(frozen=True, slots=True)
class QuickCreateV2ImageCandidatePlan:
    execution_profile_version_id: str | None
    executable: bool
    blockers: tuple[str, ...]
    candidates: tuple[QuickCreateV2ImageCandidatePreview, ...]


@dataclass(frozen=True, slots=True)
class QuickCreateV2ImageCandidateSubmission:
    run: QuickCreateV2Run
    job_ids: tuple[str, ...]
    execution_snapshot_ids: tuple[str, ...]


class QuickCreateV2ImageCandidateService:
    """Freeze every candidate as one V2 execution and one aggregate Step."""

    def __init__(
        self,
        database: Database,
        *,
        runs: QuickCreateV2RunService | None = None,
        planning: ExecutionPlanningService | None = None,
        submissions: ExecutionSubmissionService | None = None,
        profile_contract_blocker: Callable[[Database, str], str | None] = candidate_image_profile_contract_blocker,
        seed_factory: Callable[[], int] | None = None,
    ) -> None:
        self.database = database
        self.runs = runs or QuickCreateV2RunService(database)
        self.planning = planning or ExecutionPlanningService(database)
        self.submissions = submissions or ExecutionSubmissionService(database, production_execution_handlers())
        self.profile_contract_blocker = profile_contract_blocker
        self.seed_factory = seed_factory or (lambda: secrets.randbelow(2_147_483_647))

    def preview(self, *, prompt: str, candidate_count: int) -> QuickCreateV2ImageCandidatePlan:
        if not 1 <= candidate_count <= 8:
            raise DomainRuleError("QUICK_CREATE_V2_CANDIDATE_COUNT_INVALID", "V2 图片候选数必须在 1—8 之间。")
        candidates: list[QuickCreateV2ImageCandidatePreview] = []
        profile_id: str | None = None
        blockers: list[str] = []
        used_seeds: set[int] = set()
        for ordinal in range(1, candidate_count + 1):
            seed = self._next_seed(used_seeds)
            preview = self.planning.preview(_request(prompt, seed))
            profile_id = profile_id or preview.execution_profile_version_id
            blockers.extend(preview.blockers)
            if preview.execution_profile_version_id is not None:
                blocker = self.profile_contract_blocker(self.database, preview.execution_profile_version_id)
                if blocker is not None:
                    blockers.append(blocker)
            candidates.append(QuickCreateV2ImageCandidatePreview(ordinal, seed, preview.resolution_hash))
        return QuickCreateV2ImageCandidatePlan(
            execution_profile_version_id=profile_id,
            executable=profile_id is not None and not blockers,
            blockers=tuple(dict.fromkeys(blockers)),
            candidates=tuple(candidates),
        )

    def submit(
        self,
        *,
        prompt: str,
        expected_candidates: Sequence[QuickCreateV2ImageCandidatePreview],
        idempotency_key: str,
    ) -> QuickCreateV2ImageCandidateSubmission:
        candidates = tuple(expected_candidates)
        if not candidates or len(candidates) > 8 or [item.ordinal for item in candidates] != list(range(1, len(candidates) + 1)):
            raise DomainRuleError("QUICK_CREATE_V2_CANDIDATE_PLAN_INVALID", "V2 图片候选预检序列无效。")
        if len({item.seed for item in candidates}) != len(candidates):
            raise DomainRuleError("QUICK_CREATE_V2_CANDIDATE_PLAN_INVALID", "V2 图片候选不能使用重复 Seed。")
        previews = [(item, self.planning.preview(_request(prompt, item.seed))) for item in candidates]
        blockers: list[str] = []
        for _item, preview in previews:
            blockers.extend(preview.blockers)
            if preview.execution_profile_version_id is None:
                continue
            blocker = self.profile_contract_blocker(self.database, preview.execution_profile_version_id)
            if blocker is not None:
                blockers.append(blocker)
        if blockers:
            raise DomainRuleError("QUICK_CREATE_V2_IMAGE_CANDIDATES_NOT_READY", "当前 V2 图片候选合同未满足。", {"blockers": list(dict.fromkeys(blockers))})
        for item, preview in previews:
            self.planning.assert_submit_fresh(preview, item.resolution_hash)
        run = self.runs.create(
            mode="TEXT_TO_IMAGE_TO_VIDEO",
            prompt=prompt,
            idempotency_key=idempotency_key,
            input_payload={"candidate_count": len(candidates), "seeds": [item.seed for item in candidates]},
            plan={"schema_version": "localdrama.quick-create-v2-image-candidates.v1", "candidates": [
                {"ordinal": item.ordinal, "seed": item.seed, "resolution_hash": item.resolution_hash} for item in candidates
            ]},
        )
        jobs: list[str] = []
        snapshots: list[str] = []
        for item, _preview in previews:
            request = _request(prompt, item.seed, expected_resolution_hash=item.resolution_hash)

            def after_linked_in_transaction(
                connection: Any,
                job: dict[str, Any],
                snapshot: ExecutionSnapshot,
                item: QuickCreateV2ImageCandidatePreview = item,
            ) -> None:
                self.runs.attach_step_in_transaction(
                    connection,
                    run.id,
                    step_no=item.ordinal,
                    step_kind="IMAGE_CANDIDATE",
                    capability_code=_CAPABILITY,
                    execution_snapshot_id=snapshot.id,
                    job_id=str(job["id"]),
                    selection_rank=item.ordinal,
                    payload={"seed": item.seed, "resolution_hash": item.resolution_hash},
                )

            submitted = self.submissions.submit(
                request,
                f"quick-create-v2:run:{run.id}:image:{item.ordinal}",
                after_linked_in_transaction=after_linked_in_transaction,
            )
            jobs.append(str(submitted.job["id"]))
            snapshots.append(submitted.execution_snapshot_id)
        return QuickCreateV2ImageCandidateSubmission(run, tuple(jobs), tuple(snapshots))

    def _next_seed(self, used: set[int]) -> int:
        seed = self.seed_factory()
        while seed in used:
            seed = self.seed_factory()
        used.add(seed)
        return seed


def _request(prompt: str, seed: int, *, expected_resolution_hash: str | None = None) -> ExecutionPreviewRequest:
    normalized = prompt.strip()
    if not 2 <= len(normalized) <= 2000 or not 0 <= seed < 2_147_483_647:
        raise DomainRuleError("QUICK_CREATE_V2_PROMPT_REQUIRED", "V2 图片候选描述或 Seed 无效。")
    return ExecutionPreviewRequest(
        capability_code=_CAPABILITY,
        scope=CapabilityScopeContext(),
        semantic_inputs={"PROMPT": normalized, "SEED": seed},
        run_overrides={},
        expected_resolution_hash=expected_resolution_hash,
    )

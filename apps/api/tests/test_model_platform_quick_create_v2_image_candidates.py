from __future__ import annotations

from types import SimpleNamespace

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.quick_create_v2_image_candidates import QuickCreateV2ImageCandidateService
from local_drama.model_platform.application.quick_create_v2_runs import QuickCreateV2Run


class _Planning:
    def __init__(self) -> None:
        self.asserted: list[tuple[int, str]] = []

    def preview(self, request):
        return SimpleNamespace(
            execution_profile_version_id="image-profile-v2",
            resolution_hash=f"hash-{request.semantic_inputs['SEED']}",
            blockers=(),
        )

    def assert_submit_fresh(self, preview, resolution_hash: str) -> None:
        self.asserted.append((int(resolution_hash.removeprefix("hash-")), resolution_hash))
        assert preview.resolution_hash == resolution_hash


class _Runs:
    def __init__(self) -> None:
        self.created = []
        self.steps = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return QuickCreateV2Run("run-v2", "TEXT_TO_IMAGE_TO_VIDEO", "PLANNED", False)

    def attach_step_in_transaction(self, _connection, run_id: str, **kwargs) -> str:
        self.steps.append((run_id, kwargs))
        return f"step-{kwargs['step_no']}"


class _Submissions:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, request, idempotency_key: str, *, after_linked_in_transaction):
        ordinal = len(self.calls) + 1
        self.calls.append((request, idempotency_key))
        after_linked_in_transaction(None, {"id": f"job-{ordinal}"}, SimpleNamespace(id=f"snapshot-{ordinal}"))
        return SimpleNamespace(job={"id": f"job-{ordinal}"}, execution_snapshot_id=f"snapshot-{ordinal}")


def test_v2_image_candidate_batch_submits_each_candidate_through_v2_job_and_step() -> None:
    runs, planning, submissions = _Runs(), _Planning(), _Submissions()
    service = QuickCreateV2ImageCandidateService(
        database=None,  # type: ignore[arg-type]
        runs=runs,  # type: ignore[arg-type]
        planning=planning,  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        profile_contract_blocker=lambda _database, _profile: None,
        seed_factory=iter((101, 202)).__next__,
    )

    plan = service.preview(prompt="雨夜的橘猫", candidate_count=2)
    submitted = service.submit(prompt="雨夜的橘猫", expected_candidates=plan.candidates, idempotency_key="quick-v2-i2v-1")

    assert plan.executable is True
    assert [item.seed for item in plan.candidates] == [101, 202]
    assert submitted.job_ids == ("job-1", "job-2")
    assert [call[0].semantic_inputs for call in submissions.calls] == [
        {"PROMPT": "雨夜的橘猫", "SEED": 101},
        {"PROMPT": "雨夜的橘猫", "SEED": 202},
    ]
    assert [call[1] for call in submissions.calls] == [
        "quick-create-v2:run:run-v2:image:1", "quick-create-v2:run:run-v2:image:2",
    ]
    assert [step[1]["step_kind"] for step in runs.steps] == ["IMAGE_CANDIDATE", "IMAGE_CANDIDATE"]
    assert runs.created[0]["mode"] == "TEXT_TO_IMAGE_TO_VIDEO"
    assert planning.asserted == [(101, "hash-101"), (202, "hash-202")]


def test_v2_image_candidate_batch_refuses_duplicate_seed_plan_before_any_job() -> None:
    submissions = _Submissions()
    service = QuickCreateV2ImageCandidateService(
        database=None,  # type: ignore[arg-type]
        runs=_Runs(),  # type: ignore[arg-type]
        planning=_Planning(),  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        profile_contract_blocker=lambda _database, _profile: None,
    )

    duplicate = service.preview(prompt="雨夜的橘猫", candidate_count=1).candidates[0]
    with pytest.raises(DomainRuleError) as raised:
        service.submit(prompt="雨夜的橘猫", expected_candidates=(duplicate, duplicate), idempotency_key="quick-v2-i2v-2")

    assert raised.value.code == "QUICK_CREATE_V2_CANDIDATE_PLAN_INVALID"
    assert submissions.calls == []

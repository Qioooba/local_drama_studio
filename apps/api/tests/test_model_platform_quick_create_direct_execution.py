from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.quick_create_direct_execution import QuickCreateV2DirectImageService


class _Planning:
    def __init__(self, *, blockers: tuple[str, ...] = ()) -> None:
        self.blockers = blockers
        self.asserted: list[str] = []

    def preview(self, request):
        assert request.capability_code == "IMAGE_CONCEPT"
        assert request.semantic_inputs == {"PROMPT": "雨夜的橘猫"}
        return SimpleNamespace(execution_profile_version_id="profile-v2", resolution_hash="a" * 64, blockers=self.blockers)

    def assert_submit_fresh(self, preview, expected_resolution_hash: str) -> None:
        assert preview.resolution_hash == expected_resolution_hash
        self.asserted.append(expected_resolution_hash)


class _Submissions:
    def __init__(self) -> None:
        self.requests = []

    def submit(self, request, idempotency_key: str):
        self.requests.append((request, idempotency_key))
        return SimpleNamespace(
            job={"id": "job-v2"},
            execution_snapshot_id="snapshot-v2",
            execution_snapshot_hash="b" * 64,
            handler_code="comfy.workflow.v2",
            handler_version="v1",
            idempotent_replay=False,
        )


def test_direct_quick_image_uses_only_v2_snapshot_submission_contract() -> None:
    planning = _Planning()
    submissions = _Submissions()
    service = QuickCreateV2DirectImageService(
        database=None,  # type: ignore[arg-type]
        planning=planning,  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        profile_contract_blocker=lambda _database, _profile: None,
    )

    preview = service.preview(prompt="雨夜的橘猫")
    submitted = service.submit(
        prompt="雨夜的橘猫",
        run_overrides={"width": 768},
        expected_resolution_hash="a" * 64,
        idempotency_key="quick-v2-1",
    )

    assert preview.executable is True
    assert preview.blockers == ()
    assert submitted.job_id == "job-v2"
    assert planning.asserted == ["a" * 64]
    assert submissions.requests[0][0].run_overrides == {"width": 768}
    assert submissions.requests[0][1] == "quick-create-v2:image:quick-v2-1"


def test_direct_quick_image_refuses_incompatible_profile_before_job_submission() -> None:
    submissions = _Submissions()
    service = QuickCreateV2DirectImageService(
        database=None,  # type: ignore[arg-type]
        planning=_Planning(),  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        profile_contract_blocker=lambda _database, _profile: "QUICK_CREATE_V2_PROMPT_SLOT_REQUIRED",
    )

    with pytest.raises(DomainRuleError) as blocked:
        service.submit(
            prompt="雨夜的橘猫",
            run_overrides={},
            expected_resolution_hash="a" * 64,
            idempotency_key="quick-v2-2",
        )

    assert blocked.value.code == "QUICK_CREATE_V2_DIRECT_IMAGE_NOT_READY"
    assert submissions.requests == []


def test_direct_quick_image_status_only_projects_its_own_v2_job_and_verified_artifacts() -> None:
    class _Connection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[str]]] = []

        def execute(self, statement, parameters):
            self.calls.append((statement, parameters))
            if len(self.calls) == 1:
                return SimpleNamespace(fetchone=lambda: {
                    "job_id": "job-v2", "state": "SUCCEEDED", "progress_json": '{"percent":100}',
                    "last_error_code": None, "last_error_detail_redacted": None,
                    "execution_snapshot_id": "snapshot-v2", "execution_snapshot_hash": "a" * 64,
                })
            return SimpleNamespace(fetchall=lambda: [{"id": "artifact-v2", "kind": "COMFY_OUTPUT"}])

    class _Database:
        def __init__(self) -> None:
            self.connection = _Connection()

        @contextmanager
        def connect(self):
            yield self.connection

    database = _Database()
    service = QuickCreateV2DirectImageService(database)  # type: ignore[arg-type]
    status = service.status("job-v2")

    assert status.state == "SUCCEEDED"
    assert status.progress == {"percent": 100}
    assert status.artifacts == ({
        "artifact_id": "artifact-v2", "kind": "COMFY_OUTPUT", "download_url": "/api/v1/artifacts/artifact-v2/download",
    },)
    statement = database.connection.calls[0][0]
    assert "quick-create-v2:image:%" in statement
    assert "job.scope_kind='SYSTEM'" in statement
    assert "snapshot.adapter_code='comfy.workflow.v1'" in statement
    assert "quick_generation_runs" not in statement

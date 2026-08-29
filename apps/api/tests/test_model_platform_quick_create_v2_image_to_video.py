from __future__ import annotations

from types import SimpleNamespace

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.quick_create_v2_image_to_video import QuickCreateV2ImageToVideoService
from local_drama.model_platform.application.quick_create_v2_runs import QuickCreateV2SelectedImage


class _Planning:
    def __init__(self) -> None:
        self.asserted: list[str] = []

    def preview(self, request):
        assert request.semantic_inputs == {"PROMPT": "雨夜的橘猫", "FIRST_FRAME": {"artifact_id": "artifact-image-v2"}}
        return SimpleNamespace(execution_profile_version_id="i2v-profile-v2", resolution_hash="i2v-hash", blockers=())

    def assert_submit_fresh(self, preview, resolution_hash: str) -> None:
        assert preview.resolution_hash == resolution_hash
        self.asserted.append(resolution_hash)


class _Runs:
    def __init__(self) -> None:
        self.steps = []

    def selected_image_for_video(self, run_id: str):
        assert run_id == "run-v2"
        return QuickCreateV2SelectedImage(run_id, "雨夜的橘猫", "image-step-v2", "artifact-image-v2")

    def next_step_no_in_transaction(self, _connection, _run_id: str) -> int:
        return 3

    def attach_step_in_transaction(self, _connection, run_id: str, **kwargs) -> str:
        self.steps.append((run_id, kwargs))
        return "video-step-v2"


class _Submissions:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, request, idempotency_key: str, *, after_linked_in_transaction):
        self.calls.append((request, idempotency_key))
        after_linked_in_transaction(None, {"id": "video-job-v2"}, SimpleNamespace(id="video-snapshot-v2"))
        return SimpleNamespace(
            job={"id": "video-job-v2"}, execution_snapshot_id="video-snapshot-v2", execution_snapshot_hash="snapshot-hash",
            handler_code="comfy", handler_version="v1", idempotent_replay=False,
        )


def test_i2v_submits_only_selected_v2_artifact_and_attaches_video_step() -> None:
    runs, planning, submissions = _Runs(), _Planning(), _Submissions()
    service = QuickCreateV2ImageToVideoService(
        database=None,  # type: ignore[arg-type]
        runs=runs,  # type: ignore[arg-type]
        planning=planning,  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        profile_contract_blocker=lambda _database, _profile: None,
    )

    preview = service.preview(run_id="run-v2")
    submitted = service.submit(run_id="run-v2", expected_resolution_hash=preview.resolution_hash, idempotency_key="i2v-v2-1")

    assert preview.executable is True
    assert submitted.job_id == "video-job-v2"
    assert [item[1] for item in submissions.calls] == ["quick-create-v2:run:run-v2:video:i2v-v2-1"]
    assert runs.steps[0][1]["step_kind"] == "VIDEO_I2V"
    assert runs.steps[0][1]["input_artifact_id"] == "artifact-image-v2"
    assert planning.asserted == ["i2v-hash"]


def test_i2v_refuses_submission_when_profile_contract_blocks() -> None:
    submissions = _Submissions()
    service = QuickCreateV2ImageToVideoService(
        database=None,  # type: ignore[arg-type]
        runs=_Runs(),  # type: ignore[arg-type]
        planning=_Planning(),  # type: ignore[arg-type]
        submissions=submissions,  # type: ignore[arg-type]
        profile_contract_blocker=lambda _database, _profile: "QUICK_CREATE_V2_I2V_VIDEO_OUTPUT_REQUIRED",
    )

    with pytest.raises(DomainRuleError) as raised:
        service.submit(run_id="run-v2", expected_resolution_hash="i2v-hash", idempotency_key="i2v-v2-2")

    assert raised.value.code == "QUICK_CREATE_V2_I2V_NOT_READY"
    assert submissions.calls == []

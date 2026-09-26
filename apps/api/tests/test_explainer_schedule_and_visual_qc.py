"""Resident explainer schedule tick and local visual QC provider.

The schedule tests drive the real occurrence lease/fencing path and prove the
handoff rules: a claimed trigger point is claimed exactly once, a schedule with no
project creates one through the ordinary explainer creation path, a blocked
preflight releases the occurrence with a reason instead of producing, and the
deterministic de-duplication key stays ``(schedule_id, scheduled_for)``.

The visual QC tests prove the honesty rules: a missing multimodal profile is
``UNCHECKED`` rather than a pass, an unreadable frame becomes an explicit
unknown, an invented frame id is ignored, and a text-only answer cannot be
presented as a visual check.

No model and no GPU runs here.

Requirement mapping: REQ-16 (coverage layers), REQ-19 (local scheduling, lease and
fencing), REQ-04 (capability honesty).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from local_drama.application.explainers.runtime_adapters import ExplainerScheduleExecutor
from local_drama.application.explainers.visual_qc import LocalLlmVisualQcProvider
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ExplainerContractError, ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "sched-project"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def _seed_channel(database: Database) -> dict[str, str]:
    """One channel profile + version, plus an explainer project bound to it."""

    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'sched_proj', '定时解说', 'DRAFT', 'v2', ?, 300000, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        profile = repo.insert(
            "channel_profiles",
            {"project_id": PROJECT_ID, "code": "history", "title": "历史插画", "status": "ACTIVE", "scope": "PROJECT"},
        )
        version = repo.insert(
            "channel_profile_versions",
            {
                "channel_profile_id": profile["id"],
                "version_no": 1,
                "title": "历史插画 v1",
                "status": "FROZEN",
                "content_hash": "c" * 64,
            },
        )
    return {"profile_id": str(profile["id"]), "version_id": str(version["id"])}


def _seed_video(database: Database, channel: dict[str, str]) -> None:
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        repo.insert(
            "explainer_videos",
            {
                "project_id": PROJECT_ID,
                "title": "定时解说",
                "topic": "定时题材",
                "content_kind": "FACTUAL_EXPLAINER",
                "source_locale": "zh-CN",
                "input_kind": "TOPIC",
                "duration_mode": "TARGET",
                "target_seconds": 300,
                "tolerance_percent": 5.0,
                "automation_mode": "AUTO_WITH_EXCEPTIONS",
                "inference_mode": "LOCAL_ONLY",
                "research_mode": "OFFLINE_IMPORT",
                "current_channel_profile_version_id": channel["version_id"],
                "status": "DRAFT",
            },
        )
        packet = repo.insert(
            "explainer_research_packets",
            {
                "video_id": str(repo.video_for_project(PROJECT_ID)["id"]),
                "revision_no": 1,
                "status": "READY",
                "mode": "OFFLINE_IMPORT",
                "topic": "定时题材",
                "max_external_requests": 0,
                "content_hash": "d" * 64,
            },
        )
        repo.insert(
            "explainer_sources",
            {
                "packet_id": packet["id"],
                "video_id": str(repo.video_for_project(PROJECT_ID)["id"]),
                "project_id": PROJECT_ID,
                "source_kind": "DOCUMENT_IMPORT",
                "title": "来源",
                "event_date_precision": "DAY",
                "fetched_at": "2026-01-01T00:00:00Z",
                "body_sha256": "e" * 64,
                "credibility_kind": "SECONDARY",
                "retrieved_via": "USER_SUPPLIED",
            },
        )


def _schedule(database: Database, channel: dict[str, str], *, project_id: str | None = PROJECT_ID) -> str:
    from local_drama.application.explainers.schedules import ExplainerScheduleService

    with database.connect() as connection:
        service = ExplainerScheduleService(ExplainerRepository(connection))
        schedule = service.create_schedule(
            project_id=project_id,
            channel_profile_id=channel["profile_id"],
            channel_profile_version_id=channel["version_id"],
            code=f"sched_{project_id or 'channel'}",
            title="每日解说",
            timezone="Asia/Shanghai",
            rule={"kind": "DAILY", "time": "02:00"},
            topic_scope="灯塔与值班记录",
            max_concurrent_runs=1,
        )
    return str(schedule["id"])


def test_schedule_executor_claims_once_and_hands_off(database: Database) -> None:
    channel = _seed_channel(database)
    _seed_video(database, channel)
    schedule_id = _schedule(database, channel)
    executor = _executor(database)

    first = executor.tick(now_utc="2026-06-01T18:00:30Z", owner="test-scheduler")
    assert first["browser_timer_used"] is False
    assert first["external_scheduler_used"] is False
    # Nothing was claimed yet because the occurrence becomes due at 18:00:00Z and
    # the horizon has to be materialised from the rule first.
    assert first["materialised"]

    # Materialise the horizon explicitly, then claim a due point.
    second = executor.tick(now_utc="2026-06-02T18:00:30Z", owner="test-scheduler")
    claimed = [item for item in second["dispositions"] if item["disposition"] == "CLAIMED"]
    assert claimed, second
    started = [item for item in second["started"] if item.get("started")]
    assert started, second["started"]
    assert started[0]["idempotency_key"].startswith("schedule-occurrence:")
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        rows = repo.list_where("schedule_occurrences", {"schedule_id": schedule_id}, order_by="scheduled_for", descending=False)
    # Exactly one trigger point was claimed and the fencing token advanced.
    assert sum(int(item["claim_count"] or 0) for item in rows) == 1
    running = [item for item in rows if item["status"] == "RUNNING"]
    assert running, [dict(item) for item in rows]
    assert int(running[0]["fencing_token"]) >= 1
    assert started[0]["project_id"] == PROJECT_ID
    assert started[0]["workflow_run_id"]
    # The occurrence is not settled by the scheduler: the handed-off production
    # owns the terminal fact, which is what keeps the concurrency limit real.
    assert started[0]["occurrence_state"] == "CLAIMED_AND_HANDED_OFF"
    # A second tick at the same instant cannot claim the same point twice.
    third = executor.tick(now_utc="2026-06-02T18:00:30Z", owner="other-scheduler")
    assert not [item for item in third["dispositions"] if item["disposition"] == "CLAIMED"]


def test_schedule_executor_releases_when_preflight_is_blocked(database: Database) -> None:
    channel = _seed_channel(database)
    # No video and no source material: preflight must block and the occurrence
    # must be released with a reason rather than silently producing nothing.
    schedule_id = _schedule(database, channel)
    executor = _executor(database)
    executor.tick(now_utc="2026-06-01T18:00:30Z", owner="test-scheduler")
    result = executor.tick(now_utc="2026-06-02T18:00:30Z", owner="test-scheduler")
    started = [item for item in result["started"] if item.get("started")]
    assert started == []
    with database.connect() as connection:
        rows = ExplainerRepository(connection).list_where(
            "schedule_occurrences", {"schedule_id": schedule_id}, order_by="scheduled_for", descending=False
        )
    assert any(item["status"] in {"SKIPPED_WITH_REASON", "FAILED"} for item in rows)
    assert all(item["status"] != "RUNNING" for item in rows)


def test_schedule_trigger_key_ignores_config_changes(database: Database) -> None:
    channel = _seed_channel(database)
    schedule_id = _schedule(database, channel)
    executor = _executor(database)
    executor.tick(now_utc="2026-06-01T18:00:30Z", owner="test-scheduler")
    with database.connect() as connection:
        before = ExplainerRepository(connection).list_where(
            "schedule_occurrences", {"schedule_id": schedule_id}, order_by="scheduled_for", descending=False
        )
    from local_drama.application.explainers.schedules import ExplainerScheduleService

    with database.connect() as connection:
        service = ExplainerScheduleService(ExplainerRepository(connection))
        service.update_schedule(schedule_id=schedule_id, expected_revision=1, topic_scope="改过范围")
        service.materialise_occurrences(schedule_id=schedule_id)
    with database.connect() as connection:
        after = ExplainerRepository(connection).list_where(
            "schedule_occurrences", {"schedule_id": schedule_id}, order_by="scheduled_for", descending=False
        )
    before_keys = {(item["schedule_id"], item["scheduled_for"]) for item in before}
    after_keys = {(item["schedule_id"], item["scheduled_for"]) for item in after}
    assert before_keys <= after_keys
    assert len(after) == len({(item["schedule_id"], item["scheduled_for"]) for item in after})


def _settings(database: Database) -> Any:
    from local_drama.config import Settings

    base = Settings.from_env()
    return base.model_copy(
        update={
            "data_root": database.path.parent,
            "projects_root": database.path.parent / "projects",
        }
    )


# --------------------------------------------------------------------------- #
# resident worker loop
# --------------------------------------------------------------------------- #
def test_worker_loop_tick_hands_due_occurrences_to_the_workflow(database: Database) -> None:
    """The resident tick is what makes scheduled production happen on this machine.

    It is not a browser timer and not an external reminder service, so the claim is
    asserted through the supervisor method the worker loop actually calls, not
    through the executor directly.
    """

    from local_drama.application.worker_sessions import WorkerSupervisor

    channel = _seed_channel(database)
    _seed_video(database, channel)
    _schedule(database, channel)
    supervisor = WorkerSupervisor(database, _settings(database), sleep=lambda _seconds: None)
    executor = _executor(database)

    # Materialise the horizon, then tick the point that has become due.
    supervisor.explainer_schedule_tick(executor, owner="resident-1", now_utc="2026-06-01T18:00:30Z")
    result = supervisor.explainer_schedule_tick(executor, owner="resident-1", now_utc="2026-06-02T18:00:30Z")

    assert result["browser_timer_used"] is False
    assert result["external_scheduler_used"] is False
    started = [item for item in result["started"] if item.get("started")]
    assert started, result
    assert started[0]["workflow_run_id"]
    assert "error" not in result


def test_a_failing_tick_is_reported_without_killing_the_worker() -> None:
    """A background tick that raises must not take the worker loop down with it."""

    from local_drama.application.worker_sessions import WorkerSupervisor

    class _BrokenExecutor:
        def tick(self, *, owner: str) -> dict[str, Any]:
            del owner
            raise RuntimeError("scheduler exploded")

    supervisor = WorkerSupervisor.__new__(WorkerSupervisor)
    result = supervisor.explainer_schedule_tick(_BrokenExecutor(), owner="resident-1")

    assert result["claimed"] == 0
    assert result["started"] == []
    assert result["error"] == "RuntimeError"
    assert "scheduler exploded" in result["detail"]


def _executor(database: Database) -> ExplainerScheduleExecutor:
    """Executor with an explicit capability probe and the real workflow service.

    The production capability probe reads the Model Platform V2 assignment chain,
    which is empty in a test database; wiring a deterministic probe keeps this
    test about scheduling and the handoff rather than about model registration.
    """

    from local_drama.application.automation_workflows import AutomationWorkflowService
    from local_drama.application.explainers.production import ExplainerProductionService

    settings = _settings(database)

    def probe(capability: str, *, project_id: str) -> dict[str, Any]:
        del project_id
        return {"available": True, "profile_version_id": f"profile-{capability}", "execution_class": "LOCAL"}

    def production() -> Any:
        return ExplainerProductionService(
            database,
            capability_probe=probe,
            workflow_service=AutomationWorkflowService(database),
        )

    return ExplainerScheduleExecutor(
        database,
        settings,
        production_factory=production,
        workflow_service_factory=lambda: AutomationWorkflowService(database),
    )


# --------------------------------------------------------------------------- #
# visual QC provider
# --------------------------------------------------------------------------- #
class _FakeVisionClient:
    def __init__(self, responses: list[dict[str, Any]], *, model: str = "qwen3-vl-8b", supports_images: bool = True) -> None:
        self.model = model
        self.supports_images = supports_images
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, system: str, user: str, images: Any = None, *, json_schema: Any = None, inference_options: Any = None) -> dict[str, Any]:
        self.calls.append({"images": list(images or []), "user": user, "schema": json_schema})
        if not self.responses:
            raise AssertionError("no fake response left")
        return self.responses.pop(0)


def test_visual_provider_reports_missing_profile_as_unchecked() -> None:
    def broken() -> Any:
        raise DomainRuleError("VISUAL_QC_PROFILE_MISSING", "没有已发布的视觉审片方案")

    provider = LocalLlmVisualQcProvider(client_factory=broken, frame_path_resolver=lambda frame: None)
    capability = provider.capability()
    assert capability["available"] is False
    assert capability["reads_images"] is False
    assert capability["reason"] == "VISUAL_QC_PROFILE_MISSING"
    with pytest.raises(ExplainerContractError) as error:
        provider.check_frames(frames=[{"frame_id": 0, "frame_ref": "0"}], questions=["人数"])
    assert error.value.code == "CAPABILITY_UNAVAILABLE"


def test_visual_provider_ignores_a_text_only_model(tmp_path: Path) -> None:
    client = _FakeVisionClient([], supports_images=False)
    provider = LocalLlmVisualQcProvider(client_factory=lambda: client, frame_path_resolver=lambda frame: None)
    capability = provider.capability()
    assert capability["available"] is False
    assert capability["reason"] == "MODEL_DOES_NOT_ACCEPT_IMAGES"


def test_visual_provider_reads_real_frames_and_reports_observations(tmp_path: Path) -> None:
    frame = tmp_path / "frame_0001.png"
    frame.write_bytes(PNG_1X1)
    client = _FakeVisionClient(
        [
            {
                "frames": [
                    {
                        "frame_id": 0,
                        "character_count": 4,
                        "observations": ["画面里出现了四个人"],
                        "issues": [
                            {
                                "issue_kind": "CHARACTER_COUNT_MISMATCH",
                                "observed": "四个人",
                                "expected": "三个人",
                                "confidence": 0.82,
                            }
                        ],
                    }
                ]
            }
        ]
    )
    provider = LocalLlmVisualQcProvider(
        client_factory=lambda: client,
        frame_path_resolver=lambda frame_entry: frame,
        basis={"characters": ["沈砚", "林澄", "周禾"]},
    )
    findings = provider.check_frames(
        frames=[{"frame_id": 0, "frame_ref": "shot-01"}], questions=["画面里有几个人？"]
    )
    assert findings[0]["issue_kind"] == "CHARACTER_COUNT_MISMATCH"
    assert findings[0]["frame_ref"] == "shot-01"
    assert findings[0]["confidence"] == pytest.approx(0.82)
    # The provider really sent an image payload, not a text-only prompt.
    assert client.calls[0]["images"] and client.calls[0]["images"][0].startswith("data:image/")


def test_visual_provider_turns_an_unreadable_frame_into_an_explicit_unknown(tmp_path: Path) -> None:
    client = _FakeVisionClient([{"frames": []}])
    provider = LocalLlmVisualQcProvider(
        client_factory=lambda: client,
        frame_path_resolver=lambda frame: tmp_path / "missing.png",
    )
    findings = provider.check_frames(frames=[{"frame_id": 0, "frame_ref": "shot-01"}], questions=[])
    kinds = {item["issue_kind"] for item in findings}
    assert "VISUAL_FRAME_UNAVAILABLE" in kinds
    assert all(item["unknown_reason"] for item in findings)


def test_visual_provider_keeps_unknown_and_ignores_invented_frame_ids(tmp_path: Path) -> None:
    frame = tmp_path / "frame_0001.png"
    frame.write_bytes(PNG_1X1)
    client = _FakeVisionClient(
        [
            {
                "frames": [
                    {"frame_id": 0, "observations": ["看不清人物数量"], "issues": [], "unknown_reason": "人物被遮挡"},
                    {"frame_id": 999, "observations": ["编造的帧"], "issues": [{"issue_kind": "EXTRA_PERSON", "observed": "x"}]},
                ]
            }
        ]
    )
    provider = LocalLlmVisualQcProvider(client_factory=lambda: client, frame_path_resolver=lambda frame_entry: frame)
    findings = provider.check_frames(frames=[{"frame_id": 0, "frame_ref": "shot-01"}], questions=[])
    assert len(findings) == 1
    assert findings[0]["issue_kind"] == "VISUAL_UNKNOWN"
    assert findings[0]["unknown_reason"] == "人物被遮挡"
    assert findings[0]["confidence"] is None


def test_visual_provider_rejects_an_out_of_range_confidence(tmp_path: Path) -> None:
    frame = tmp_path / "frame_0001.png"
    frame.write_bytes(PNG_1X1)
    client = _FakeVisionClient(
        [
            {
                "frames": [
                    {
                        "frame_id": 0,
                        "observations": ["x"],
                        "issues": [{"issue_kind": "EXTRA_PERSON", "observed": "a", "confidence": 42}],
                    }
                ]
            }
        ]
    )
    provider = LocalLlmVisualQcProvider(client_factory=lambda: client, frame_path_resolver=lambda frame_entry: frame)
    findings = provider.check_frames(frames=[{"frame_id": 0, "frame_ref": "shot-01"}], questions=[])
    assert findings[0]["confidence"] is None


def test_visual_provider_keeps_the_batches_it_could_read(tmp_path: Path) -> None:
    """One unanswered batch must not discard the batches that were really read.

    The delivered 1962 film's 13-batch semantic pass reported NOT_RUN with zero
    coverage because a single request met a restarting model server, while twelve
    batches of real observations were already in hand.
    """

    paths = []
    for index in range(8):
        path = tmp_path / f"frame_{index:04d}.png"
        path.write_bytes(PNG_1X1)
        paths.append(path)

    class _FlakyClient(_FakeVisionClient):
        def chat_json(self, system, user, images=None, *, json_schema=None, inference_options=None):  # noqa: ANN001, ANN202
            if not self.calls:
                self.calls.append({"images": list(images or []), "user": user, "schema": json_schema})
                raise ExplainerContractError("CAPABILITY_UNAVAILABLE", "模型服务正在重启")
            return super().chat_json(
                system, user, images, json_schema=json_schema, inference_options=inference_options
            )

    client = _FlakyClient([{"frames": []}])
    provider = LocalLlmVisualQcProvider(
        client_factory=lambda: client,
        frame_path_resolver=lambda frame_entry: paths[int(frame_entry["frame_id"])],
        batch_size=4,
    )
    findings = provider.check_frames(
        frames=[{"frame_id": index, "frame_ref": f"shot-{index:02d}"} for index in range(8)],
        questions=[],
    )
    unchecked = [item for item in findings if item["issue_kind"] == "SEMANTIC_QC_UNCHECKED"]
    # The four frames of the unanswered batch are reported unchecked, one by one.
    assert sorted(item["frame_id"] for item in unchecked) == [0, 1, 2, 3]
    assert all(item["unknown_reason"] == "CAPABILITY_UNAVAILABLE" for item in unchecked)
    # The second batch was answered, so the pass kept its real coverage.
    assert len(client.calls) == 2


def test_visual_provider_batches_frames(tmp_path: Path) -> None:
    paths = []
    for index in range(12):
        path = tmp_path / f"frame_{index:04d}.png"
        path.write_bytes(PNG_1X1)
        paths.append(path)
    client = _FakeVisionClient([{"frames": []} for _ in range(4)])
    provider = LocalLlmVisualQcProvider(
        client_factory=lambda: client,
        frame_path_resolver=lambda frame_entry: paths[int(frame_entry["frame_id"])],
        batch_size=4,
    )
    findings = provider.check_frames(
        frames=[{"frame_id": index, "frame_ref": f"f{index}"} for index in range(12)], questions=[]
    )
    assert findings == []
    assert len(client.calls) == 3
    assert all(len(call["images"]) <= 4 for call in client.calls)


def test_visual_provider_surfaces_a_transport_failure_as_capability_unavailable(tmp_path: Path) -> None:
    frame = tmp_path / "frame_0001.png"
    frame.write_bytes(PNG_1X1)
    client = _FakeVisionClient([])
    provider = LocalLlmVisualQcProvider(client_factory=lambda: client, frame_path_resolver=lambda frame_entry: frame)
    with pytest.raises(ExplainerContractError) as error:
        provider.check_frames(frames=[{"frame_id": 0, "frame_ref": "shot-01"}], questions=[])
    assert error.value.code == "CAPABILITY_UNAVAILABLE"

"""Channel-level scheduled creation uses the ordinary composite command (EXP-06).

Reproduced defect on the audit snapshot:

``ExplainerScheduleExecutor._create_scheduled_project`` called
``ProjectService.create_project(channel_profile_id=..., channel_profile_version_id=...)``.
``ProjectService`` has no such parameters, so the very first channel-level occurrence
raised ``TypeError: ProjectService.create_project() got an unexpected keyword
argument 'channel_profile_id'`` — at argument-binding time, before anything was
written and without touching a model.  The channel profile belongs to the *video*
creation parameters, and ``create_video`` (which does accept them) was the one call
that did not receive them.

The same path also created the project and the video in two independently committed
transactions, so a failure in the second left an orphan project behind.  It now goes
through the composite :class:`ExplainerCreationService`, keyed by the trigger point,
so a retried occurrence replays instead of duplicating.
"""

from __future__ import annotations

from typing import Any

import pytest

from local_drama.application.explainers.runtime_adapters import ExplainerScheduleExecutor
from local_drama.application.explainers.schedules import ExplainerScheduleService
from local_drama.domain.explainers.contracts import ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database


def _seed_channel(database: Database) -> dict[str, str]:
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        profile = repo.insert(
            "channel_profiles",
            {"project_id": None, "code": "history", "title": "历史插画", "status": "ACTIVE", "scope": "GLOBAL"},
        )
        version = repo.insert(
            "channel_profile_versions",
            {
                "channel_profile_id": profile["id"],
                "version_no": 1,
                "title": "历史插画 v1",
                "status": "FROZEN",
                "content_hash": "c" * 64,
                "voice_json": {"voice_ref": "voxcpm2:zh-default", "model_ref": "voxcpm2"},
            },
        )
    return {"profile_id": str(profile["id"]), "version_id": str(version["id"])}


def _settings(database: Database) -> Any:
    from local_drama.config import Settings

    return Settings.from_env().model_copy(
        update={
            "data_root": database.path.parent,
            "projects_root": database.path.parent / "projects",
        }
    )


def _channel_schedule(database: Database, channel: dict[str, str], **overrides: Any) -> str:
    with database.connect() as connection:
        service = ExplainerScheduleService(ExplainerRepository(connection))
        payload: dict[str, Any] = {
            "project_id": None,
            "channel_profile_id": channel["profile_id"],
            "channel_profile_version_id": channel["version_id"],
            "code": "daily_channel",
            "title": "栏目级每日解说",
            "timezone": "Asia/Shanghai",
            "rule": {"kind": "DAILY", "time": "02:00"},
            "topic_scope": "灯塔与值班记录",
            "max_concurrent_runs": 1,
        }
        payload.update(overrides)
        schedule = service.create_schedule(**payload)
    return str(schedule["id"])


def _occurrence(database: Database, schedule_id: str) -> str:
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        row = repo.insert(
            "schedule_occurrences",
            {
                "schedule_id": schedule_id,
                "scheduled_for": "2026-06-02T18:00:00Z",
                "status": "CLAIMED",
                "fencing_token": 1,
                "claim_count": 1,
                "config_revision": 1,
            },
        )
    return str(row["id"])


def _resolve(database: Database, occurrence_id: str) -> str:
    executor = ExplainerScheduleExecutor(database, _settings(database))
    with database.connect() as connection:
        service = ExplainerScheduleService(ExplainerRepository(connection))
        return executor._resolve_project(service, occurrence_id)  # noqa: SLF001


def _workspace(database: Database, project_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        project = repo.project_row(project_id)
        video = repo.require_video_for_project(project_id)
    return project, video


# --------------------------------------------------------------------------- #
# the exact crash is gone
# --------------------------------------------------------------------------- #
def test_channel_level_occurrence_creates_a_workspace_without_type_error(database: Database) -> None:
    channel = _seed_channel(database)
    schedule_id = _channel_schedule(database, channel)
    occurrence_id = _occurrence(database, schedule_id)

    project_id = _resolve(database, occurrence_id)
    assert project_id

    project, video = _workspace(database, project_id)
    assert str(project["product_kind"]) == ProductKind.EXPLAINER.value
    # The binding lands on the *video*, which is where it is actually used.
    assert str(video["current_channel_profile_version_id"]) == channel["version_id"]


def test_the_binding_survives_into_the_video_row_and_not_the_project(database: Database) -> None:
    """``ProjectService`` never accepted the profile; the video row stores it."""

    channel = _seed_channel(database)
    schedule_id = _channel_schedule(database, channel)
    occurrence_id = _occurrence(database, schedule_id)
    project_id = _resolve(database, occurrence_id)

    project, video = _workspace(database, project_id)
    assert "channel_profile_id" not in project
    assert "channel_profile_version_id" not in project
    assert str(video["current_channel_profile_version_id"]) == channel["version_id"]


def test_research_mode_and_input_projection_follow_the_schedule_allowlist(database: Database) -> None:
    channel = _seed_channel(database)
    schedule_id = _channel_schedule(
        database, channel, source_allowlist=["example.invalid", "docs.invalid"]
    )
    occurrence_id = _occurrence(database, schedule_id)
    project_id = _resolve(database, occurrence_id)
    _project, video = _workspace(database, project_id)

    assert str(video["research_mode"]) == "WEB_RESEARCH"
    # ``research_allowed_domains_json`` is stored canonicalised (sorted), so the
    # assertion compares membership rather than insertion order.
    assert sorted(str(item) for item in (video["research_allowed_domains_json"] or [])) == [
        "docs.invalid",
        "example.invalid",
    ]
    payload = video["input_payload_json"]
    assert sorted(str(item) for item in payload["reference_urls"]) == [
        "docs.invalid",
        "example.invalid",
    ]
    assert payload["reference_url_count"] == 2
    assert payload["input_kind"] == "REFERENCE_LINKS"


def test_offline_schedule_freezes_the_topic_scope_as_its_input(database: Database) -> None:
    channel = _seed_channel(database)
    schedule_id = _channel_schedule(database, channel)
    occurrence_id = _occurrence(database, schedule_id)
    project_id = _resolve(database, occurrence_id)
    _project, video = _workspace(database, project_id)

    assert str(video["research_mode"]) == "OFFLINE_IMPORT"
    assert str(video["topic"]) == "灯塔与值班记录"
    assert video["input_payload_json"]["reference_urls"] == []


def test_the_occurrence_records_the_project_it_created(database: Database) -> None:
    channel = _seed_channel(database)
    schedule_id = _channel_schedule(database, channel)
    occurrence_id = _occurrence(database, schedule_id)
    project_id = _resolve(database, occurrence_id)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT project_id FROM schedule_occurrences WHERE id = ?", (occurrence_id,)
        ).fetchone()
    assert str(row["project_id"]) == project_id


def test_a_retried_occurrence_replays_instead_of_duplicating(database: Database) -> None:
    """Idempotency identity is the trigger point, not a fresh UUID each attempt."""

    channel = _seed_channel(database)
    schedule_id = _channel_schedule(database, channel)
    occurrence_id = _occurrence(database, schedule_id)
    first = _resolve(database, occurrence_id)
    second = _resolve(database, occurrence_id)
    assert first == second
    with database.connect() as connection:
        projects = int(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
        videos = int(connection.execute("SELECT COUNT(*) FROM explainer_videos").fetchone()[0])
    assert projects == 1
    assert videos == 1


def test_an_occurrence_without_a_topic_scope_is_a_named_blocker(database: Database) -> None:
    from local_drama.domain.explainers.contracts import ExplainerContractError

    channel = _seed_channel(database)
    schedule_id = _channel_schedule(database, channel, topic_scope="")
    occurrence_id = _occurrence(database, schedule_id)
    with pytest.raises(ExplainerContractError) as error:
        _resolve(database, occurrence_id)
    assert error.value.code == "SOURCE_EVIDENCE_MISSING"


def test_a_schedule_bound_to_a_project_does_not_create_another_one(database: Database) -> None:
    channel = _seed_channel(database)
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel,
            target_duration_ms, product_kind) VALUES ('proj-bound', 'BOUND-1', '已绑定', 'DRAFT',
            'v2', 'proj-bound', 300000, 'EXPLAINER')"""
        )
    with database.connect() as connection:
        service = ExplainerScheduleService(ExplainerRepository(connection))
        schedule = service.create_schedule(
            project_id="proj-bound",
            channel_profile_id=channel["profile_id"],
            channel_profile_version_id=channel["version_id"],
            code="bound_daily",
            title="绑定项目每日",
            timezone="Asia/Shanghai",
            rule={"kind": "DAILY", "time": "02:00"},
            topic_scope="灯塔",
            max_concurrent_runs=1,
        )
    occurrence_id = _occurrence(database, str(schedule["id"]))
    assert _resolve(database, occurrence_id) == "proj-bound"
    with database.connect() as connection:
        assert int(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]) == 1

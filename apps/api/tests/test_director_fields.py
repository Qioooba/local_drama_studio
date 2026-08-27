from __future__ import annotations

import json

import pytest

from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.shot_studio import ShotStudioQueryService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import REQUIRED_SHOT_FIELDS, missing_shot_fields
from local_drama.infrastructure.database.shot_studio_command_repository import shot_studio_command_service
from local_drama.infrastructure.database.shot_studio_repository import SqliteShotStudioReadRepository


def _published_camera_profile(workspace, database, support: str) -> str:
    source = ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"][0]
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED', parameter_schema_json=? WHERE id=?",
            (json.dumps({"capabilities": {"camera": {"support": support}}}), source["version_id"]),
        )
    return str(source["version_id"])


def test_shot_studio_lists_exact_missing_director_fields(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="director_fields",
        title="Director fields",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    shot_studio_command_service(database).save_draft_revision(
        str(shot["id"]), {"shot_type": "CLOSEUP", "composition": "center", "dialogue": "", "environment": ""}
    )

    studio = ShotStudioQueryService(SqliteShotStudioReadRepository(database)).studio(
        str(episode["id"]), str(shot["id"])
    )
    fields = studio["current_shot"]["current_revision"]["fields"]
    assert missing_shot_fields(fields) == ["subject_action", "camera_plan", "target_duration_ms", "continuity", "creative_intent"]
    blocker_codes = {item["code"] for item in studio["current_shot"]["blockers"]}
    assert {"DIRECTOR_FIELDS_MISSING", "SHOT_NOT_PRODUCTION_READY"} <= blocker_codes


def test_ready_transition_preserves_specific_missing_fields_then_accepts_complete_revision(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="director_ready",
        title="Director ready",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    shot_studio_command_service(database).save_draft_revision(str(shot["id"]), {"shot_type": "CLOSEUP"})
    with pytest.raises(DomainRuleError) as error:
        shot_studio_command_service(database).mark_ready_shot(str(shot["id"]))
    assert error.value.code == "SHOT_NOT_PRODUCTION_READY"
    assert error.value.details["missing_fields"] == list(REQUIRED_SHOT_FIELDS[1:])

    complete = {field: "" if field in {"dialogue", "environment"} else 4_000 if field == "target_duration_ms" else field for field in REQUIRED_SHOT_FIELDS}
    profile_id = _published_camera_profile(workspace, database, "NATIVE")
    complete["camera_plan"] = {
        "mode": "NATIVE",
        "shot_type": "CLOSEUP",
        "movement": "PUSH_IN",
        "prompt_text": "",
        "direction": "FORWARD",
        "intensity": 0.5,
        "curve": "EASE_IN_OUT",
        "profile_version_id": profile_id,
    }
    shot_studio_command_service(database).save_draft_revision(str(shot["id"]), complete, freeze=True)
    assert shot_studio_command_service(database).mark_ready_shot(str(shot["id"]))["status"] == "READY"
    studio = ShotStudioQueryService(SqliteShotStudioReadRepository(database)).studio(
        str(episode["id"]), str(shot["id"])
    )
    assert missing_shot_fields(studio["current_shot"]["current_revision"]["fields"]) == []
    blocker_codes = {item["code"] for item in studio["current_shot"]["blockers"]}
    assert "DIRECTOR_FIELDS_MISSING" not in blocker_codes
    assert "SHOT_NOT_PRODUCTION_READY" not in blocker_codes
    assert {"PROFILE_NOT_BOUND", "PRODUCTION_PLAN_NOT_BOUND", "DELIVERY_TARGET_NOT_BOUND"} <= blocker_codes


def test_ready_rejects_legacy_or_unsupported_camera_plan(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="camera_structured",
        title="Camera structured",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    complete = {field: "" if field in {"dialogue", "environment"} else 4_000 if field == "target_duration_ms" else field for field in REQUIRED_SHOT_FIELDS}
    with pytest.raises(DomainRuleError) as legacy:
        shot_studio_command_service(database).save_draft_revision(str(shot["id"]), complete, freeze=True)
    assert legacy.value.code == "CAMERA_PLAN_STRUCTURED_REQUIRED"
    profile_id = _published_camera_profile(workspace, database, "UNSUPPORTED")
    complete["camera_plan"] = {
        "mode": "UNSUPPORTED",
        "shot_type": "CLOSEUP",
        "movement": "ORBIT",
        "prompt_text": "",
        "direction": "CLOCKWISE",
        "intensity": 0.7,
        "curve": "LINEAR",
        "profile_version_id": profile_id,
    }
    shot_studio_command_service(database).save_draft_revision(str(shot["id"]), complete, freeze=True)
    with pytest.raises(DomainRuleError) as unsupported:
        shot_studio_command_service(database).mark_ready_shot(str(shot["id"]))
    assert unsupported.value.code == "CAMERA_PLAN_UNSUPPORTED"

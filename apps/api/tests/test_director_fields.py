from __future__ import annotations

import json

import pytest

from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.read_models import ProductionReadModelService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import REQUIRED_SHOT_FIELDS


def _published_camera_profile(workspace, database, support: str) -> str:
    source = ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"][0]
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED', parameter_schema_json=? WHERE id=?",
            (json.dumps({"capabilities": {"camera": {"support": support}}}), source["version_id"]),
        )
    return str(source["version_id"])


def test_production_read_model_lists_exact_missing_director_fields(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="director_fields", title="Director fields", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    projects.create_shot_revision(str(shot["id"]), {"shot_type": "CLOSEUP", "composition": "center", "dialogue": "", "environment": ""})

    item = ProductionReadModelService(database).episode(str(episode["id"]))["items"][0]
    assert item["missing_director_fields"] == ["subject_action", "camera_plan", "target_duration_ms", "continuity", "creative_intent"]
    assert item["production_readiness"]["state"] == "DIRECTED"
    assert item["production_readiness"]["missing_fields"] == item["missing_director_fields"]
    assert "SHOT_NOT_PRODUCTION_READY" in item["production_readiness"]["blockers"]


def test_ready_transition_preserves_specific_missing_fields_then_accepts_complete_revision(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="director_ready", title="Director ready", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    projects.create_shot_revision(str(shot["id"]), {"shot_type": "CLOSEUP"})
    with pytest.raises(DomainRuleError) as error:
        projects.mark_shot_production_ready(str(shot["id"]))
    assert error.value.code == "SHOT_NOT_PRODUCTION_READY"
    assert error.value.details["missing_fields"] == list(REQUIRED_SHOT_FIELDS[1:])

    complete = {field: "" if field in {"dialogue", "environment"} else 4_000 if field == "target_duration_ms" else field for field in REQUIRED_SHOT_FIELDS}
    profile_id = _published_camera_profile(workspace, database, "NATIVE")
    complete["camera_plan"] = {
        "mode": "NATIVE", "shot_type": "CLOSEUP", "movement": "PUSH_IN", "prompt_text": "",
        "direction": "FORWARD", "intensity": 0.5, "curve": "EASE_IN_OUT", "profile_version_id": profile_id,
    }
    projects.create_shot_revision(str(shot["id"]), complete, freeze=True)
    assert projects.mark_shot_production_ready(str(shot["id"]))["status"] == "READY"
    ready_item = ProductionReadModelService(database).episode(str(episode["id"]))["items"][0]
    assert ready_item["production_readiness"] == {"state": "PRODUCTION_READY", "missing_fields": [], "blockers": ["PROFILE_NOT_BOUND", "PRODUCTION_PLAN_NOT_BOUND", "DELIVERY_TARGET_NOT_BOUND"]}


def test_ready_rejects_legacy_or_unsupported_camera_plan(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="camera_structured", title="Camera structured", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    complete = {field: "" if field in {"dialogue", "environment"} else 4_000 if field == "target_duration_ms" else field for field in REQUIRED_SHOT_FIELDS}
    with pytest.raises(DomainRuleError) as legacy:
        projects.create_shot_revision(str(shot["id"]), complete, freeze=True)
    assert legacy.value.code == "CAMERA_PLAN_STRUCTURED_REQUIRED"
    profile_id = _published_camera_profile(workspace, database, "UNSUPPORTED")
    complete["camera_plan"] = {
        "mode": "UNSUPPORTED", "shot_type": "CLOSEUP", "movement": "ORBIT", "prompt_text": "",
        "direction": "CLOCKWISE", "intensity": 0.7, "curve": "LINEAR", "profile_version_id": profile_id,
    }
    projects.create_shot_revision(str(shot["id"]), complete, freeze=True)
    with pytest.raises(DomainRuleError) as unsupported:
        projects.mark_shot_production_ready(str(shot["id"]))
    assert unsupported.value.code == "CAMERA_PLAN_UNSUPPORTED"

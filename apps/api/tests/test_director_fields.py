from __future__ import annotations

import pytest

from local_drama.application.projects import ProjectService
from local_drama.application.read_models import ProductionReadModelService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import REQUIRED_SHOT_FIELDS


def test_production_read_model_lists_exact_missing_director_fields(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(code="director_fields", title="Director fields", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    projects.create_shot_revision(str(shot["id"]), {"shot_type": "CLOSEUP", "composition": "center", "dialogue": "", "environment": ""})

    item = ProductionReadModelService(database).episode(str(episode["id"]))["items"][0]
    assert item["missing_director_fields"] == ["subject_action", "camera_plan", "target_duration_ms", "continuity", "creative_intent"]


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
    projects.create_shot_revision(str(shot["id"]), complete, freeze=True)
    assert projects.mark_shot_production_ready(str(shot["id"]))["status"] == "READY"

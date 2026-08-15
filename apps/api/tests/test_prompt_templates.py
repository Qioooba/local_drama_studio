from __future__ import annotations

import pytest

from local_drama.application.projects import ProjectService
from local_drama.application.prompts import PromptService
from local_drama.domain.errors import DomainRuleError


def _project(workspace, database):
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(code="prompt_template", title="Prompt template", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    episode = service.list_episodes(str(service.list_seasons(str(project["id"]))[0]["id"]))[0]
    shot = service.create_shot(str(episode["id"]), "S001", 4_000)
    return project, shot


def _structured(expanded: str) -> dict[str, object]:
    return {"source_fields": {"subject_action": "turn"}, "template_text": "{subject_action}, cinematic", "expanded_text": expanded, "negative_text": "flicker", "language": "en", "model_profile_version_id": "profile-v1"}


def test_generation_template_freezes_required_structure_and_lists_latest_revision(workspace, database) -> None:
    project, shot = _project(workspace, database)
    service = PromptService(database)
    created = service.create_prompt(str(project["id"]), "SHOT", str(shot["id"]), "GENERATION_TEMPLATE", "Shot prompt", "turn, cinematic", _structured("turn, cinematic"))
    child = service.branch_revision(str(created["revision"]["id"]), "turn slowly, cinematic", _structured("turn slowly, cinematic"))

    items = service.list_prompts(str(project["id"]), "SHOT", str(shot["id"]))
    assert len(items) == 1
    assert items[0]["revision_id"] == child["id"]
    assert items[0]["revision_no"] == 2
    assert items[0]["structured"]["negative_text"] == "flicker"
    assert items[0]["revision_status"] == "FROZEN"


def test_generation_template_rejects_missing_fields_and_expansion_mismatch(workspace, database) -> None:
    project, shot = _project(workspace, database)
    service = PromptService(database)
    with pytest.raises(DomainRuleError) as missing:
        service.create_prompt(str(project["id"]), "SHOT", str(shot["id"]), "GENERATION_TEMPLATE", "Bad", "expanded", {"template_text": "x"})
    assert missing.value.code == "PROMPT_TEMPLATE_FIELDS_REQUIRED"
    with pytest.raises(DomainRuleError) as mismatch:
        service.create_prompt(str(project["id"]), "SHOT", str(shot["id"]), "GENERATION_TEMPLATE", "Bad", "different", _structured("expanded"))
    assert mismatch.value.code == "PROMPT_EXPANSION_MISMATCH"

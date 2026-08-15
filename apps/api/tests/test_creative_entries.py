from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.creative_entries import CreativeEntryService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, code: str):
    return ProjectService(database, workspace.projects_root).create_project(code=code, title=code, episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)


def test_creative_entry_revisions_compare_and_restore_without_rewriting_history(workspace, database) -> None:
    project = _project(workspace, database, "creative_history")
    service = CreativeEntryService(database)
    entry = service.create(str(project["id"]), "CHARACTER", "CHAR_MOTHER", "母亲", {"appearance": "短发", "costume": "蓝衣"}, "建立人物")
    first_id = str(entry["current_revision_id"])
    second = service.save_revision(str(entry["id"]), {"appearance": "短发", "costume": "灰衣", "age": 52}, "调整服装")
    comparison = service.compare(str(entry["id"]), first_id, str(second["id"]))
    assert comparison["read_only"] is True
    assert comparison["changes"] == [{"field": "age", "before": None, "after": 52}, {"field": "costume", "before": "蓝衣", "after": "灰衣"}]

    restored = service.restore(str(entry["id"]), first_id, "恢复初版")
    assert restored["revision_no"] == 3
    assert restored["restored_from_revision_id"] == first_id
    assert restored["content"] == {"appearance": "短发", "costume": "蓝衣"}
    revisions = service.revisions(str(entry["id"]))
    assert [item["revision_no"] for item in revisions] == [3, 2, 1]
    assert service.get_revision(first_id)["content"] == {"appearance": "短发", "costume": "蓝衣"}


def test_creative_entry_rejects_duplicate_unchanged_and_cross_entry_restore(workspace, database) -> None:
    project = _project(workspace, database, "creative_guards")
    service = CreativeEntryService(database)
    first = service.create(str(project["id"]), "PROP", "PROP_LETTER", "信件", {"state": "sealed"}, "建立道具")
    second = service.create(str(project["id"]), "STYLE", "STYLE_MAIN", "主风格", {"palette": "warm"}, "建立风格")
    with pytest.raises(DomainRuleError) as duplicate:
        service.create(str(project["id"]), "PROP", "PROP_LETTER", "重复", {"state": "open"}, "重复")
    assert duplicate.value.code == "CREATIVE_ENTRY_CODE_CONFLICT"
    with pytest.raises(DomainRuleError) as unchanged:
        service.save_revision(str(first["id"]), {"state": "sealed"}, "无变化")
    assert unchanged.value.code == "CREATIVE_REVISION_UNCHANGED"
    with pytest.raises(DomainRuleError) as scope:
        service.restore(str(first["id"]), str(second["current_revision_id"]), "错误回退")
    assert scope.value.code == "CREATIVE_REVISION_SCOPE_INVALID"


def test_creative_entry_api_lists_compares_and_restores(workspace, database) -> None:
    project = _project(workspace, database, "creative_api")
    service = CreativeEntryService(database)
    entry = service.create(str(project["id"]), "SERIES_BIBLE", "BIBLE_MAIN", "故事圣经", {"theme": "family"}, "初版")
    second = service.save_revision(str(entry["id"]), {"theme": "family", "tone": "warm"}, "补充基调")
    with TestClient(create_app(workspace)) as client:
        listed = client.get("/api/v1/creative-entries", params={"project_id": project["id"], "kind": "SERIES_BIBLE"})
        compared = client.get(f"/api/v1/creative-entries/{entry['id']}/compare", params={"left_revision_id": entry["current_revision_id"], "right_revision_id": second["id"]})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["revision_no"] == 2
    assert compared.status_code == 200
    assert compared.json()["comparison"]["changes"][0]["field"] == "tone"

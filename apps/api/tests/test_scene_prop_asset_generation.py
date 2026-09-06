from __future__ import annotations

from typing import Any

from local_drama.application.projects import ProjectService
from local_drama.application.scene_prop_asset_generation import ScenePropAssetGenerationService


class _UnavailableLLMClient:
    model = "test-disabled"
    provider = "TEST_DISABLED"

    def chat_json(self, *, system: str, user: str) -> dict[str, Any]:
        del system, user
        raise RuntimeError("LLM intentionally disabled for heuristic isolation test")


class _UnavailableLLMProvider:
    def client(self, **_: Any) -> _UnavailableLLMClient:
        return _UnavailableLLMClient()


def _project(workspace, database, *, code: str, title: str) -> dict[str, Any]:
    project = ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=title,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=120_000,
        allow_unconfigured_capabilities=True,
    )
    return dict(project)


def _names(result: dict[str, Any], kind: str) -> set[str]:
    return {str(item["name"]) for item in result[kind]}


def test_extraction_isolated_between_projects_with_different_scripts(workspace, database) -> None:
    """A project's script entities never become another project's asset candidates."""
    first = _project(workspace, database, code="mist_bridge_echo", title="雾桥回声")
    second = _project(workspace, database, code="red_tide_cipher", title="赤潮密令")
    service = ScenePropAssetGenerationService(database, workspace, llm=_UnavailableLLMProvider())

    first_result = service.extract_and_create_all_assets(
        str(first["id"]),
        script_text=(
            "沈舟说道：别让铜钟落入潮水。\n"
            "沈舟手持铜钟，来到北门旧站。\n"
            "沈舟说道：我们稍后汇合。"
        ),
    )
    second_result = service.extract_and_create_all_assets(
        str(second["id"]),
        script_text=(
            "周野说道：数据芯片已经解锁。\n"
            "周野手持数据芯片，进入海港实验室。\n"
            "周野说道：实验室灯光稳定。"
        ),
    )

    assert first_result["project_id"] == str(first["id"])
    assert second_result["project_id"] == str(second["id"])
    assert _names(first_result, "characters") == {"沈舟"}
    assert _names(first_result, "scenes") == {"北门旧站"}
    assert _names(first_result, "props") == {"铜钟"}
    assert _names(second_result, "characters") == {"周野"}
    assert _names(second_result, "scenes") == {"海港实验室"}
    assert _names(second_result, "props") == {"数据芯片"}

    with database.connect() as connection:
        first_rows = connection.execute(
            "SELECT project_id,kind,name FROM story_assets WHERE project_id=? ORDER BY kind,name",
            (str(first["id"]),),
        ).fetchall()
        second_rows = connection.execute(
            "SELECT project_id,kind,name FROM story_assets WHERE project_id=? ORDER BY kind,name",
            (str(second["id"]),),
        ).fetchall()
    assert {str(row["name"]) for row in first_rows} == {"沈舟", "北门旧站", "铜钟"}
    assert {str(row["name"]) for row in second_rows} == {"周野", "海港实验室", "数据芯片"}
    assert all(str(row["project_id"]) == str(first["id"]) for row in first_rows)
    assert all(str(row["project_id"]) == str(second["id"]) for row in second_rows)


def test_same_generic_prop_suffix_does_not_cross_project_boundary(workspace, database) -> None:
    """Suffix grouping is vocabulary-driven and still scoped to each project."""
    first = _project(workspace, database, code="glass_tide", title="玻璃潮汐")
    second = _project(workspace, database, code="fog_signal", title="雾中信号")
    service = ScenePropAssetGenerationService(database, workspace, llm=_UnavailableLLMProvider())

    first_result = service.extract_and_create_all_assets(
        str(first["id"]),
        script_text=(
            "苏澄说道：青铜灯还在。\n"
            "苏澄手持青铜灯，来到南岸仓库。\n"
            "苏澄说道：灯光指向出口。"
        ),
    )
    second_result = service.extract_and_create_all_assets(
        str(second["id"]),
        script_text=(
            "乔岳说道：银纹灯已经点亮。\n"
            "乔岳手持银纹灯，来到北坡车站。\n"
            "乔岳说道：车站没有其他人。"
        ),
    )

    assert _names(first_result, "props") == {"青铜灯"}
    assert _names(second_result, "props") == {"银纹灯"}
    assert _names(first_result, "props").isdisjoint(_names(second_result, "props"))

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT project_id,name FROM story_assets WHERE kind='PROP' ORDER BY project_id,name",
        ).fetchall()
    assert {(str(row["project_id"]), str(row["name"])) for row in rows} == {
        (str(first["id"]), "青铜灯"),
        (str(second["id"]), "银纹灯"),
    }

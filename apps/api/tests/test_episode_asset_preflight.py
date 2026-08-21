from __future__ import annotations

from local_drama.application.commands.asset_bible import AssetBibleCommandService
from local_drama.application.commands.director_recipes import DirectorRecipeCommandService
from local_drama.application.commands.qc_policies import QcPolicyCommandService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.story_assets import StoryAssetService
from local_drama.infrastructure.database.asset_bible_repository import SqliteAssetBibleRepository
from local_drama.infrastructure.database.director_recipe_repository import SqliteDirectorRecipeRepository
from local_drama.infrastructure.database.qc_policy_repository import SqliteQcPolicyRepository
from tests.test_asset_bible import PNG


def test_preflight_resolves_effective_asset_state_and_recipe_reference_requirements(workspace, database) -> None:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="episode_asset_gate", title="Episode asset gate", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    assets = StoryAssetService(database, workspace)
    character = assets.create_asset(project_id, "CHARACTER", "LEAD", "主角")
    assets.bind_asset_to_shot(str(shot["id"]), str(character["id"]))
    source = workspace.work_root / "episode-asset-gate.png"
    source.write_bytes(PNG)
    media_version_id = str(MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")["media_version_id"])

    with database.transaction() as connection:
        asset_commands = AssetBibleCommandService(SqliteAssetBibleRepository(connection))
        state = asset_commands.create_state(project_id, str(character["id"]), "RAIN", "雨中", "WEATHER")
        asset_commands.set_shot_asset_state(shot_id=str(shot["id"]), asset_id=str(character["id"]), asset_state_id=str(state["id"]))
        asset_commands.add_reference(project_id, str(character["id"]), media_version_id, "HERO", asset_state_id=str(state["id"]), is_locked=True)
        policy = QcPolicyCommandService(SqliteQcPolicyRepository(connection)).put(
            project_id=project_id, owner_type="PROJECT", owner_id=project_id, stage="VIDEO",
            policy={"technical": "STRICT"}, max_auto_rerolls=1, auto_reroll_categories=["TECHNICAL"],
        )
        recipe_commands = DirectorRecipeCommandService(SqliteDirectorRecipeRepository(connection))
        recipe = recipe_commands.create(
            project_id=project_id, code="episode_gate", title="Episode gate",
            recipe={
                "aspect_ratio": "16:9",
                    "shot_planning": {"avg_duration_ms": 4000, "dialogue_coverage": "ALL"},
                "asset_policy": {"character_required_refs": ["HERO", "FRONT"]},
                    "generation": {"image": {"capability": "IMAGE"}, "video": {"capability": "I2V"}},
                "qc_policy_ref": {"policy_version_id": str(policy["policy_version_id"])},
            },
        )
        recipe_commands.bind(project_id=project_id, recipe_version_id=str(recipe["versions"][0]["id"]))

    service = EpisodeProductionRunService(database, workspace)
    blocked = service.preflight(str(episode["id"]), tts_enabled=False, min_free_disk_bytes=1)
    reference_check = next(item for item in blocked["checks"] if item["code"] == "ASSET_REFERENCE_REQUIREMENTS_MISSING")
    assert reference_check["status"] == "BLOCKED"
    assert reference_check["evidence"]["missing"][0]["missing_reference_kinds"] == ["FRONT"]
    assert reference_check["evidence"]["missing"][0]["effective_asset_state_id"] == state["id"]

    with database.transaction() as connection:
        AssetBibleCommandService(SqliteAssetBibleRepository(connection)).add_reference(
            project_id, str(character["id"]), media_version_id, "FRONT",
            asset_state_id=str(state["id"]), is_locked=True,
        )
    ready = service.preflight(str(episode["id"]), tts_enabled=False, min_free_disk_bytes=1)
    reference_check = next(item for item in ready["checks"] if item["code"] == "ASSET_REFERENCE_REQUIREMENTS_MISSING")
    assert reference_check["status"] == "PASS"
    assert blocked["input_fingerprint"] != ready["input_fingerprint"]

    with database.transaction() as connection:
        connection.execute("UPDATE shots SET archived_at='2026-08-20T00:00:00Z' WHERE id=?", (str(shot["id"]),))
    archived = service.preflight(str(episode["id"]), tts_enabled=False, min_free_disk_bytes=1)
    plan_check = next(item for item in archived["checks"] if item["code"] == "SCRIPT_SHOT_PLAN_MISSING")
    assert plan_check["evidence"]["shot_count"] == 0

"""Asset Bible commands + read model + API contract tests.

Covers handbook P2-03 (commands invariants), P2-04 (asset-bible aggregate),
and the new API facade.  Uses the real SQLite adapter through the service
layer, matching the existing story-asset test style.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.asset_image_generation import AssetImageGenerationBatchService
from local_drama.application.asset_multiview import AssetDetailService, AssetExpressionService, AssetMultiViewService
from local_drama.application.commands.asset_bible import AssetBibleCommandService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.queries.asset_bible import AssetBibleQueryService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.asset_bible_repository import SqliteAssetBibleRepository
from local_drama.infrastructure.service_composition import build_asset_image_batch, build_asset_image_completion
from local_drama.main import create_app

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def _project(workspace, database, code: str):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 1_000)
    return projects, project, episode, shot


def _image(workspace, database, project_id: str, name: str = "ref.png") -> str:
    source = workspace.work_root / name
    source.write_bytes(PNG)
    return str(MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")["media_version_id"])


def _service(database) -> tuple[AssetBibleCommandService, AssetBibleQueryService]:
    with database.connect() as connection:
        repository = SqliteAssetBibleRepository(connection)
    command = AssetBibleCommandService(repository)
    query = AssetBibleQueryService(repository)
    return command, query


def _write_service(database, fn):
    with database.transaction() as connection:
        repository = SqliteAssetBibleRepository(connection)
        return fn(AssetBibleCommandService(repository))


def _query_service(database, fn):
    with database.connect() as connection:
        repository = SqliteAssetBibleRepository(connection)
        return fn(AssetBibleQueryService(repository))


def _published_asset_image_profile(workspace, database) -> str:
    profile = next(item for item in ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"] if item["capability"] == "VIDEO_T2V")
    workflow = WorkflowService(database, workspace).register_package(
        "asset_image_batch_submit",
        "Asset image batch submit",
        {"1": {"class_type": "SaveImage", "inputs": {"prompt": "", "seed": 0}}},
        {},
        {"PROMPT": {"node_id": "1", "input": "prompt"}, "SEED": {"node_id": "1", "input": "seed"}},
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (workflow["id"],))
        connection.execute(
            """UPDATE execution_profile_versions SET capability='IMAGE_CHARACTER',status='PUBLISHED',workflow_version_id=?,
            input_contract_json=?,revision=revision+1 WHERE id=?""",
            (
                workflow["id"],
                json.dumps(
                    {
                        "input_slots": {
                            "PROMPT": {"min": 1, "max": 1},
                            "OUTPUT_PREFIX": {"min": 0, "max": 1},
                        }
                    }
                ),
                profile["version_id"],
            ),
        )
    return str(profile["version_id"])


def _published_image_profile_by_contract(
    workspace,
    database,
    code: str,
    *,
    capability: str,
    input_slots: dict,
    updated_at: str = "2026-01-01T00:00:00+00:00",
) -> str:
    workflow = WorkflowService(database, workspace).register_package(
        code,
        code,
        {"1": {"class_type": "SaveImage", "inputs": {"prompt": "", "seed": 0}}},
        {},
        {"PROMPT": {"node_id": "1", "input": "prompt"}, "SEED": {"node_id": "1", "input": "seed"}},
    )
    profile_id = f"profile-{code}"
    version_id = f"version-{code}"
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (workflow["id"],))
        connection.execute(
            """INSERT INTO execution_profiles
            (id, code, title, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'test', 1, 'v2')""",
            (profile_id, code, code),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, workflow_version_id, model_bundle_json,
             input_contract_json, parameter_schema_json, status, manifest_sha256, capability_json, worker_policy,
             runtime_version_id, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, ?, '{"model":"local","provider":"LOCAL"}', ?, '{}', 'PUBLISHED', NULL, '{}',
                    'ONE_LOCAL_LLM_TASK', NULL, '2026-01-01T00:00:00+00:00', ?, 'test', 1, 'v2')""",
            (version_id, profile_id, capability, workflow["id"], json.dumps({"input_slots": input_slots}), updated_at),
        )
    return version_id


def test_state_crud_and_asset_scope(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_state_crud")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_A", "阿宁")

    created = _write_service(database, lambda s: s.create_state(project_id, str(asset["id"]), "INJURED", "受伤", "INJURY", "袖口破损"))
    assert created["code"] == "INJURED"
    assert created["state_kind"] == "INJURY"

    listed = _query_service(database, lambda s: s.list_states(str(asset["id"])))
    assert [item["code"] for item in listed] == ["INJURED"]

    updated = _write_service(database, lambda s: s.update_state(str(created["id"]), 1, label="受伤（轻）"))
    assert updated["label"] == "受伤（轻）"
    assert updated["revision"] == 2

    with pytest.raises(DomainRuleError) as conflict:
        _write_service(database, lambda s: s.update_state(str(created["id"]), 1, label="过期"))
    assert conflict.value.code == "STORY_ASSET_STATE_REVISION_CONFLICT"

    with pytest.raises(DomainRuleError) as wrong_kind:
        _write_service(database, lambda s: s.create_state(project_id, str(asset["id"]), "X1", "坏状态", "NOT_A_KIND"))
    assert wrong_kind.value.code == "STORY_ASSET_STATE_KIND_INVALID"


def test_reference_add_scope_and_hero_projection(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_ref_scope")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_H", "主角")
    media = _image(workspace, database, project_id, "hero.png")

    reference = _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), media, "HERO"))
    assert reference["reference_kind"] == "HERO"
    # HERO projection updates canonical for compatibility with legacy code.
    fresh = assets.get_asset(str(asset["id"]))
    assert fresh["canonical_media_version_id"] == media
    assert fresh["revision"] == 2

    # Cross-project media rejected: asset belongs to `project`, media to `other_project`.
    _, other_project, _, _ = _project(workspace, database, "bible_ref_other")
    other_media = _image(workspace, database, str(other_project["id"]), "other-hero.png")
    with pytest.raises(DomainRuleError) as cross:
        _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), other_media, "FRONT"))
    assert cross.value.code == "STORY_ASSET_MEDIA_SCOPE_INVALID"

    # Invalid kind rejected.
    with pytest.raises(DomainRuleError) as bad_kind:
        _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), media, "NOT_A_KIND"))
    assert bad_kind.value.code == "STORY_ASSET_REFERENCE_KIND_INVALID"


def test_reference_archive_keeps_media_history(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_ref_archive")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_B", "角色乙")
    media = _image(workspace, database, project_id, "archive.png")

    reference = _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), media, "FRONT"))
    archived = _write_service(database, lambda s: s.archive_reference(str(reference["id"]), 1))
    assert archived["status"] == "ARCHIVED"
    # Media version itself still exists (lineage preserved).
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM media_versions WHERE id=?", (media,)).fetchone() is not None
    listed = _query_service(database, lambda s: s.list_references(str(asset["id"])))
    assert listed == []


def test_episode_and_shot_state_bindings(workspace, database) -> None:
    _, project, episode, shot = _project(workspace, database, "bible_bindings")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_C", "角色丙")
    state = _write_service(database, lambda s: s.create_state(project_id, str(asset["id"]), "INJURED", "受伤", "INJURY"))

    binding = _write_service(database, lambda s: s.set_episode_asset_state(episode_id=str(episode["id"]), story_asset_id=str(asset["id"]), asset_state_id=str(state["id"])))
    assert binding["asset_state_id"] == state["id"]

    resolved = _write_service(database, lambda s: s.episode_asset_state(str(episode["id"]), str(asset["id"])))
    assert resolved is not None
    assert resolved["state_label"] == "受伤"

    # Shot-level override requires the asset to be bound to the shot first.
    assets.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "main")
    shot_binding = _write_service(database, lambda s: s.set_shot_asset_state(shot_id=str(shot["id"]), asset_id=str(asset["id"]), asset_state_id=str(state["id"])))
    assert shot_binding["asset_state_id"] == state["id"]
    states = _write_service(database, lambda s: s.shot_asset_states(str(shot["id"])))
    assert states[0]["asset_state_id"] == state["id"]


def test_asset_bible_read_model_groups_references_by_state(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_read_model")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_D", "角色丁")
    hero = _image(workspace, database, project_id, "read-hero.png")
    front = _image(workspace, database, project_id, "read-front.png")

    _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), hero, "HERO"))
    base_state = _write_service(database, lambda s: s.create_state(project_id, str(asset["id"]), "BASE", "基础造型", "BASE"))
    _write_service(database, lambda s: s.add_reference(project_id, str(asset["id"]), front, "FRONT", asset_state_id=str(base_state["id"])))

    bible = _query_service(database, lambda s: s.asset_bible(project_id))
    assert bible["asset_count"] == 1
    detail = bible["items"][0]
    assert detail["asset"]["name"] == "角色丁"
    assert detail["readiness"]["missing"] == ["LEFT", "RIGHT"]
    base_refs = [item for state in detail["states"] if state["code"] == "BASE" for item in state["references"]]
    assert len(base_refs) == 1
    assert base_refs[0]["reference_kind"] == "FRONT"


def test_asset_bible_api_full_flow(workspace, database) -> None:
    _, project, episode, shot = _project(workspace, database, "bible_api")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_API", "接口角色")
    media = _image(workspace, database, project_id, "api.png")

    with TestClient(create_app(workspace)) as client:
        state = client.post(
            f"/api/v1/story-assets/{asset['id']}/states",
            json={"code": "INJURED", "label": "受伤", "state_kind": "INJURY"},
        )
        reference = client.post(
            f"/api/v1/story-assets/{asset['id']}/references",
            json={"media_version_id": media, "reference_kind": "HERO"},
        )
        bible = client.get(f"/api/v1/projects/{project_id}/asset-bible")
        detail = client.get(f"/api/v1/story-assets/{asset['id']}/detail")
        refs = client.get(f"/api/v1/story-assets/{asset['id']}/references")
        episode_bind = client.post(
            f"/api/v1/episodes/{episode['id']}/asset-state-bindings",
            json={"story_asset_id": asset["id"], "asset_state_id": state.json()["state"]["id"]},
        )
        assets.bind_asset_to_shot(str(shot["id"]), str(asset["id"]), "main")
        shot_bind = client.post(
            f"/api/v1/shots/{shot['id']}/asset-state-bindings",
            json={"asset_id": asset["id"], "asset_state_id": state.json()["state"]["id"]},
        )
        archived = client.post(
            f"/api/v1/story-asset-references/{reference.json()['reference']['id']}:archive",
            json={"expected_revision": 1},
        )

    assert state.status_code == 201
    assert state.json()["state"]["state_kind"] == "INJURY"
    assert reference.status_code == 201
    assert reference.json()["reference"]["reference_kind"] == "HERO"
    assert bible.status_code == 200
    assert bible.json()["bible"]["asset_count"] == 1
    assert bible.json()["bible"]["items"][0]["asset"]["code"] == "CHAR_API"
    assert detail.status_code == 200
    assert detail.json()["asset_detail"]["readiness"]["level"] in {"BASIC", "READY"}
    assert refs.status_code == 200
    assert len(refs.json()["items"]) == 1
    assert episode_bind.status_code == 201
    assert shot_bind.status_code == 201
    assert archived.status_code == 201
    assert archived.json()["reference"]["status"] == "ARCHIVED"


def test_asset_bible_api_rejects_scope_violations(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_api_scope")
    project_id = str(project["id"])
    _, other_project, _, _ = _project(workspace, database, "bible_api_other")
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_S", "作用域角色")
    other_media = _image(workspace, database, str(other_project["id"]), "other.png")

    with TestClient(create_app(workspace)) as client:
        cross = client.post(
            f"/api/v1/story-assets/{asset['id']}/references",
            json={"media_version_id": other_media, "reference_kind": "FRONT"},
        )
        missing = client.post(
            f"/api/v1/story-assets/{asset['id']}/states",
            json={"code": "X", "label": "坏", "state_kind": "NOPE"},
        )
    assert cross.status_code == 422
    assert cross.json()["error"]["code"] == "STORY_ASSET_MEDIA_SCOPE_INVALID"
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "STORY_ASSET_STATE_KIND_INVALID"


def test_expression_preflight_uses_real_capability_and_never_queues_when_unavailable(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_expression_preflight")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_EXPR", "Expression Character")
    hero = _image(workspace, database, project_id, "expression-hero.png")
    _write_service(database, lambda service: service.add_reference(project_id, str(asset["id"]), hero, "HERO", is_locked=True))

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/story-assets/{asset['id']}/generate-expression:preflight",
            json={"consistency_strength": "HIGH", "background": "CLEAN"},
        )

    assert response.status_code == 200
    preflight = response.json()["preflight"]
    assert preflight["capability"] == "IMAGE_EXPRESSION"
    assert "ASSET_EXPRESSION_CAPABILITY_UNAVAILABLE" in {item["code"] for item in preflight["blockers"]}
    assert preflight["ready"] is False
    assert preflight["hero"]["media_version_id"] == hero
    assert preflight["would_create_jobs"] == 0
    assert any("IMAGE_EXPRESSION" in blocker["message"] for blocker in preflight["blockers"])
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_intents WHERE purpose='ASSET_EXPRESSION_GRID'").fetchone()[0] == 0


def test_expression_slots_freeze_hero_and_use_expression_grid_reference_kind() -> None:
    service = object.__new__(AssetExpressionService)
    plan = service._variant_plan(
        kind="HAPPY",
        yaw=0.0,
        prompt="happy joyful expression",
        hero_media_version_id="hero-version-1",
        input_role="CHARACTER_REFERENCE",
        profile_version_id="expression-profile-v3",
        asset_state_id="state-1",
        consistency_strength="HIGH",
        background="CLEAN",
        seed_index=1,
    )
    assert plan.profile_version_id == "expression-profile-v3"
    assert plan.parameter_set["SLOT_KIND"] == "HAPPY"
    assert plan.parameter_set["OUTPUT_REFERENCE_KIND"] == "EXPRESSION_GRID"
    assert plan.parameter_set["ASSET_STATE_ID"] == "state-1"
    assert plan.bindings[0].role == "CHARACTER_REFERENCE"
    assert plan.bindings[0].media_version_id == "hero-version-1"


def test_multiview_requested_slots_are_unique_canonical_and_reject_unknown_values() -> None:
    service = object.__new__(AssetMultiViewService)
    request = service._normalized_request(
        asset_state_id=None,
        profile_version_id=None,
        consistency_strength="HIGH",
        background="CLEAN",
        requested_slots=["right", "front", "right"],
    )
    assert request["requested_slots"] == ["FRONT", "RIGHT"]
    with pytest.raises(DomainRuleError) as caught:
        service._normalized_request(
            asset_state_id=None,
            profile_version_id=None,
            consistency_strength="HIGH",
            background="CLEAN",
            requested_slots=["DIAGONAL"],
        )
    assert caught.value.code == "ASSET_GENERATION_SLOT_INVALID"


def test_multiview_profile_accepts_required_prompt_alongside_one_required_image_slot() -> None:
    class _Result:
        def fetchone(self):
            return {
                "status": "PUBLISHED",
                "node_bindings_json": json.dumps({
                    "FIRST_FRAME": {"node_id": "7", "input": "image"},
                    "PROMPT": {"node_id": "9", "input": "prompt"},
                }),
                "content_json": json.dumps({
                    "7": {"inputs": {"image": "placeholder.png"}},
                    "9": {"inputs": {"prompt": "turnaround"}},
                }),
            }

    class _Connection:
        def execute(self, _sql, _params):
            return _Result()

    profile = {
        "workflow_version_id": "workflow-v2",
        "input_contract_json": json.dumps({
            "transport": "LOOPBACK_HTTP",
            "input_slots": {
                "FIRST_FRAME": {"min": 1, "max": 1},
                "PROMPT": {"min": 1, "max": 1},
            },
        }),
    }

    role, reason = AssetMultiViewService._profile_input_role(_Connection(), profile)

    assert role == "FIRST_FRAME"
    assert reason is None


def test_multiview_left_prompt_requires_pure_orthographic_profile(workspace, database) -> None:
    service = AssetMultiViewService(database, workspace)
    left_prompt = next(text for kind, _yaw, text in service.SPECS if kind == "LEFT")
    plan = service._variant_plan(
        kind="LEFT",
        yaw=-90.0,
        prompt=left_prompt,
        hero_media_version_id="hero-media",
        input_role="FIRST_FRAME",
        profile_version_id="profile-v1",
        asset_state_id=None,
        consistency_strength="HIGH",
        background="CLEAN",
        seed_index=1,
    )

    prompt = plan.parameter_set["PROMPT"]
    assert "strict orthographic left profile" in prompt
    assert "exactly minus 90 degrees" in prompt
    assert "not three-quarter" in prompt
    assert "no crouching" in prompt
    assert "arms relaxed straight beside the body" in prompt
    assert "both hands empty" in prompt


def test_multiview_accepts_only_complete_local_llm_prompt_bundles() -> None:
    bundle = AssetMultiViewService._normalize_prompt_bundle({
        "source": "LOCAL_LLM",
        "provider": "llama.cpp",
        "model": "local-model",
        "items": {
            "FRONT": {"positive_prompt": "strict front prompt", "negative_prompt": "three-quarter view"},
            "LEFT": {"positive_prompt": "strict left prompt", "negative_prompt": "front view"},
        },
    }, ["FRONT", "LEFT"])
    assert bundle is not None
    assert bundle["source"] == "LOCAL_LLM"
    assert bundle["items"]["LEFT"]["negative_prompt"] == "front view"

    with pytest.raises(DomainRuleError) as caught:
        AssetMultiViewService._normalize_prompt_bundle({
            "source": "MANUAL",
            "items": {"FRONT": {"positive_prompt": "front", "negative_prompt": "bad"}},
        }, ["FRONT"])
    assert caught.value.code == "ASSET_MULTI_VIEW_PROMPT_BUNDLE_INVALID"


def test_multiview_prompt_normalization_removes_repetition_and_caps_runaway_lists() -> None:
    repeated = ", ".join(["wrong camera angle", "identity drift", "hair strand", "hair strand"] + [f"noise {index}" for index in range(80)])
    bundle = AssetMultiViewService._normalize_prompt_bundle({
        "source": "LOCAL_LLM",
        "provider": "llama.cpp",
        "model": "local-model",
        "items": {"FRONT": {"positive_prompt": "strict front, strict front, preserve HERO", "negative_prompt": repeated}},
    }, ["FRONT"])

    assert bundle is not None
    assert bundle["items"]["FRONT"]["positive_prompt"] == "strict front, preserve HERO"
    assert bundle["items"]["FRONT"]["negative_prompt"].count("hair strand") == 1
    assert len(bundle["items"]["FRONT"]["negative_prompt"].split(", ")) == 40
    assert len(bundle["items"]["FRONT"]["negative_prompt"]) <= 1200


def test_multiview_variant_freezes_positive_and_negative_prompts(workspace, database) -> None:
    service = AssetMultiViewService(database, workspace)
    plan = service._variant_plan(
        kind="RIGHT",
        yaw=90.0,
        prompt="model generated positive",
        negative_prompt="model generated negative",
        hero_media_version_id="hero-media",
        input_role="FIRST_FRAME",
        profile_version_id="profile-v1",
        asset_state_id=None,
        consistency_strength="HIGH",
        background="CLEAN",
        seed_index=2,
    )
    assert "model generated positive" in plan.parameter_set["PROMPT"]
    assert plan.parameter_set["NEGATIVE_PROMPT"] == "model generated negative"

    reroll = service._variant_plan(
        kind="RIGHT", yaw=90.0, prompt="model generated positive", negative_prompt="model generated negative",
        hero_media_version_id="hero-media", input_role="FIRST_FRAME", profile_version_id="profile-v1",
        asset_state_id=None, consistency_strength="HIGH", background="CLEAN", seed_index=2, seed_offset=1009,
    )
    assert reroll.explicit_seed == plan.explicit_seed + 1009


def test_detail_preflight_is_honest_about_missing_image_edit_profile(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_detail_preflight")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_DETAIL", "Detail Character")
    hero = _image(workspace, database, project_id, "detail-hero.png")
    _write_service(database, lambda service: service.add_reference(project_id, str(asset["id"]), hero, "HERO", is_locked=True))
    with TestClient(create_app(workspace)) as client:
        response = client.post(f"/api/v1/story-assets/{asset['id']}/generate-detail:preflight", json={"consistency_strength": "HIGH", "background": "CLEAN"})
    assert response.status_code == 200
    preflight = response.json()["preflight"]
    assert preflight["capability"] == "IMAGE_EDIT"
    assert "ASSET_DETAIL_CAPABILITY_UNAVAILABLE" in {item["code"] for item in preflight["blockers"]}
    assert preflight["ready"] is False
    assert preflight["would_create_jobs"] == 0
    assert any("IMAGE_EDIT" in blocker["message"] for blocker in preflight["blockers"])


def test_detail_slots_map_to_closeup_and_detail_without_changing_hero() -> None:
    service = object.__new__(AssetDetailService)
    common = dict(yaw=0.0, hero_media_version_id="hero-version-2", input_role="REFERENCE_IMAGE", profile_version_id="image-edit-v4", asset_state_id=None, consistency_strength="HIGH", background="CLEAN")
    face = service._variant_plan(kind="FACE_CLOSEUP", prompt="face", seed_index=0, **common)
    costume = service._variant_plan(kind="COSTUME_DETAIL", prompt="costume", seed_index=1, **common)
    assert face.parameter_set["OUTPUT_REFERENCE_KIND"] == "CLOSEUP"
    assert costume.parameter_set["OUTPUT_REFERENCE_KIND"] == "DETAIL"
    assert face.bindings[0].media_version_id == costume.bindings[0].media_version_id == "hero-version-2"
    assert face.explicit_seed != costume.explicit_seed


def test_asset_image_batch_prompt_policy_is_kind_specific() -> None:
    character = AssetImageGenerationBatchService.prompt_for("CHARACTER", "阿宁", "短发剑客")
    scene = AssetImageGenerationBatchService.prompt_for("SCENE", "古城雨巷", "青石路与灯笼")
    prop = AssetImageGenerationBatchService.prompt_for("PROP", "照骨灯", "青铜灯盏")
    costume = AssetImageGenerationBatchService.prompt_for("COSTUME", "宗门常服", "青灰交领")
    assert "单人" in character and "阿宁" in character
    assert "无人物" in scene and "古城雨巷" in scene
    assert "单一道具" in prop and "照骨灯" in prop
    assert "版型" in costume and "宗门常服" in costume


def test_asset_image_batch_prefers_lightweight_visual_prompt() -> None:
    direct = AssetImageGenerationBatchService.visual_description(
        json.dumps({"visual_prompt": "银发、玄色窄袖、琥珀色眼睛"}, ensure_ascii=False),
        "男主角",
    )
    compatible = AssetImageGenerationBatchService.visual_description(
        json.dumps({"text_dossier": {"visual_prompt": "红伞、旧竹柄"}}, ensure_ascii=False),
        "一把伞",
    )
    fallback = AssetImageGenerationBatchService.visual_description("{broken", "青石雨巷")

    assert direct == "银发、玄色窄袖、琥珀色眼睛"
    assert compatible == "红伞、旧竹柄"
    assert fallback == "青石雨巷"


def test_asset_image_batch_plan_skips_existing_hero_and_reports_missing_profile(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_asset_image_plan")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    missing = assets.create_asset(project_id, "CHARACTER", "CHAR_MISSING", "缺图角色", description="白衣少年")
    complete = assets.create_asset(project_id, "CHARACTER", "CHAR_COMPLETE", "有图角色")
    hero = _image(workspace, database, project_id, "batch-existing-hero.png")
    _write_service(database, lambda service: service.add_reference(project_id, str(complete["id"]), hero, "HERO", is_locked=True))

    plan = build_asset_image_batch(database, workspace).plan(
        project_id,
        asset_kind="CHARACTER",
        asset_ids=[str(missing["id"]), str(complete["id"])],
    )

    assert plan["capability"] == "IMAGE_CHARACTER"
    assert plan["summary"] == {"selected": 2, "ready": 0, "skipped": 1, "blocked": 1, "jobs": 0}
    assert plan["valid"] is False
    assert next(item for item in plan["items"] if item["asset_id"] == complete["id"])["status"] == "SKIPPED"
    blocked = next(item for item in plan["items"] if item["asset_id"] == missing["id"])
    assert blocked["status"] == "BLOCKED"
    assert "白衣少年" in blocked["prompt"]
    assert {item["code"] for item in blocked["blockers"]} == {"ASSET_IMAGE_PROFILE_REQUIRED"}


def test_asset_image_batch_api_is_read_only_during_plan(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_asset_image_api")
    project_id = str(project["id"])
    asset = StoryAssetService(database, workspace).create_asset(project_id, "SCENE", "SCENE_ONE", "雨夜街道")
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/asset-image-batches:plan",
            json={"asset_kind": "SCENE", "asset_ids": [asset["id"]], "profile_version_id": None, "mode": "MISSING_ONLY"},
        )
    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["capability"] == "IMAGE_SCENE"
    assert plan["mutated"] is False
    assert plan["runtime_contacted"] is False
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM asset_image_generation_batches").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM generation_intents WHERE purpose='ASSET_HERO_IMAGE'").fetchone()[0] == 0


def test_asset_image_plan_rejects_missing_workflow_semantics_without_creating_rows(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_image_semantics")
    asset = StoryAssetService(database, workspace).create_asset(str(project["id"]), "CHARACTER", "CHAR_ONE", "林晚")
    profile_id = _published_asset_image_profile(workspace, database)
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET node_bindings_json='{}' WHERE id=(SELECT workflow_version_id FROM execution_profile_versions WHERE id=?)", (profile_id,))
    plan = build_asset_image_batch(database, workspace).plan(str(project["id"]), asset_kind="CHARACTER", asset_ids=[str(asset["id"])], profile_version_id=profile_id)
    assert plan["valid"] is False
    assert plan["summary"]["jobs"] == 0
    assert plan["items"][0]["status"] == "BLOCKED"
    assert plan["issues"][0]["code"] == "WORKFLOW_SEMANTIC_BINDING_REQUIRED"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM asset_image_generation_batches").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM generation_intents WHERE purpose='ASSET_HERO_IMAGE'").fetchone()[0] == 0


def test_asset_image_batch_submit_uses_independent_generation_jobs_and_replays(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_asset_image_submit")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    first = assets.create_asset(project_id, "CHARACTER", "CHAR_BATCH_A", "甲", description="黑衣刀客")
    second = assets.create_asset(project_id, "CHARACTER", "CHAR_BATCH_B", "乙", description="白衣医者")
    profile_version_id = _published_asset_image_profile(workspace, database)

    service = build_asset_image_batch(database, workspace)
    asset_ids = [str(first["id"]), str(second["id"])]
    plan = service.plan(project_id, asset_kind="CHARACTER", asset_ids=asset_ids, profile_version_id=profile_version_id)
    assert plan["valid"] is True
    submitted = service.submit(
        project_id,
        asset_kind="CHARACTER",
        asset_ids=asset_ids,
        profile_version_id=profile_version_id,
        expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="asset-image-submit-1",
    )
    replay = service.submit(
        project_id,
        asset_kind="CHARACTER",
        asset_ids=asset_ids,
        profile_version_id=profile_version_id,
        expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="asset-image-submit-1",
    )
    assert submitted["summary"] == {"total": 2, "succeeded": 0, "superseded": 0, "failed": 0, "active": 2}
    assert {item["status"] for item in submitted["items"]} == {"QUEUED"}
    assert replay["id"] == submitted["id"]
    assert replay["idempotent_replay"] is True
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_intents WHERE purpose='ASSET_HERO_IMAGE'").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM asset_image_generation_batch_items WHERE batch_id=?", (submitted["id"],)).fetchone()[0] == 2


def test_asset_image_completion_promotes_and_auto_binds_first_hero(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_asset_image_complete")
    project_id = str(project["id"])
    asset = StoryAssetService(database, workspace).create_asset(project_id, "CHARACTER", "CHAR_AUTO_HERO", "自动主图")
    profile_version_id = _published_asset_image_profile(workspace, database)
    service = build_asset_image_batch(database, workspace)
    plan = service.plan(project_id, asset_kind="CHARACTER", asset_ids=[str(asset["id"])], profile_version_id=profile_version_id)
    batch = service.submit(
        project_id,
        asset_kind="CHARACTER",
        asset_ids=[str(asset["id"])],
        profile_version_id=profile_version_id,
        expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="asset-image-complete-1",
    )
    job_id = str(batch["items"][0]["job_id"])
    jobs = JobService(database, workspace)
    claim = jobs.claim("asset-image-test-worker", ["GPU_H3"])
    assert claim is not None and claim["job"]["id"] == job_id
    output = workspace.work_root / "jobs" / job_id / "generated.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(PNG)
    artifact = jobs.register_artifact(str(claim["attempt"]["id"]), "COMFY_OUTPUT", output.relative_to(workspace.work_root).as_posix())
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "asset-image-test-worker", success=True)

    finalized = build_asset_image_completion(database, workspace).finalize_job(job_id, [artifact])
    replay = build_asset_image_completion(database, workspace).finalize_job(job_id, [artifact])
    detail = _query_service(database, lambda query: query.asset_detail(str(asset["id"])))
    assert finalized is not None and finalized["status"] == "SUCCEEDED"
    assert replay is not None and replay["idempotent_replay"] is True
    assert detail["asset"]["canonical_media_version_id"] == finalized["media_version_id"]
    assert [reference["reference_kind"] for reference in detail["base_references"]] == ["HERO"]
    assert detail["base_references"][0]["is_locked"] in {1}
    with database.connect() as connection:
        derivative = connection.execute(
            "SELECT state FROM jobs WHERE subject_type='MEDIA_VERSION' AND subject_id=? AND type='MEDIA_DERIVATIVE'",
            (finalized["media_version_id"],),
        ).fetchone()
    assert derivative is not None
    assert derivative["state"] == "QUEUED"


def test_asset_image_batch_auto_chooses_text_to_image_profile_over_reference_profile(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_asset_image_text_only")
    project_id = str(project["id"])
    asset = StoryAssetService(database, workspace).create_asset(project_id, "CHARACTER", "CHAR_TEXT_ONLY", "纯文生图角色")
    reference_profile_id = _published_image_profile_by_contract(
        workspace,
        database,
        "asset-image-reference",
        capability="IMAGE_CHARACTER",
        input_slots={"PROMPT": {"min": 1, "max": 1}, "REFERENCE_IMAGE_1": {"min": 1, "max": 1}},
        updated_at="2026-02-01T00:00:00+00:00",
    )
    text_to_image_profile_id = _published_image_profile_by_contract(
        workspace,
        database,
        "asset-image-text-to-image",
        capability="IMAGE_CHARACTER",
        input_slots={"PROMPT": {"min": 1, "max": 1}},
        updated_at="2026-01-01T00:00:00+00:00",
    )

    plan = build_asset_image_batch(database, workspace).plan(
        project_id,
        asset_kind="CHARACTER",
        asset_ids=[str(asset["id"])],
    )

    assert plan["valid"] is True
    assert plan["profile_version_id"] == text_to_image_profile_id
    assert reference_profile_id != text_to_image_profile_id
    assert plan["profile_resolution"]["source"] == "TEXT_TO_IMAGE_AUTO"


def test_asset_image_batch_rejects_explicit_reference_image_profile(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "bible_asset_image_reference_rejected")
    project_id = str(project["id"])
    asset = StoryAssetService(database, workspace).create_asset(project_id, "CHARACTER", "CHAR_REF_REJECTED", "参考图角色")
    reference_profile_id = _published_image_profile_by_contract(
        workspace,
        database,
        "asset-image-reference-rejected",
        capability="IMAGE_CHARACTER",
        input_slots={"PROMPT": {"min": 1, "max": 1}, "REFERENCE_IMAGE_1": {"min": 1, "max": 1}},
    )

    plan = build_asset_image_batch(database, workspace).plan(
        project_id,
        asset_kind="CHARACTER",
        asset_ids=[str(asset["id"])],
        profile_version_id=reference_profile_id,
    )

    assert plan["valid"] is False
    assert plan["summary"]["blocked"] == 1
    assert {issue["code"] for issue in plan["issues"]} == {"ASSET_IMAGE_PROFILE_REQUIRES_REFERENCE_IMAGE"}

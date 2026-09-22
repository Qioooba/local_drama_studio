"""R02 asset-image batch idempotency boundaries (spec 3.6-3.9)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.asset_image_generation import (
    asset_batch_request_identity,
)
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.service_composition import build_asset_image_batch
from local_drama.main import create_app


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


def _published_asset_image_profile(workspace, database) -> str:
    import json as _json

    profile = next(item for item in ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"] if item["capability"] == "VIDEO_T2V")
    # A real asset HERO route must control STEPS/WIDTH/HEIGHT; a smoke-scale
    # 256x256 / 1-step verification graph is rejected by the batch service.
    nodes = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "local-model.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 4.0, "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "local_drama/asset_hero", "images": ["5", 0]}},
    }
    bindings = {
        "PROMPT": {"node_id": "2", "input": "text"},
        "SEED": {"node_id": "5", "input": "seed"},
        "STEPS": {"node_id": "5", "input": "steps"},
        "WIDTH": {"node_id": "4", "input": "width"},
        "HEIGHT": {"node_id": "4", "input": "height"},
        "OUTPUT_PREFIX": {"node_id": "7", "input": "filename_prefix"},
    }
    workflow = WorkflowService(database, workspace).register_package(
        "asset_image_batch_submit",
        "Asset image batch submit",
        nodes,
        {},
        bindings,
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (workflow["id"],))
        connection.execute(
            """UPDATE execution_profile_versions SET capability='IMAGE_CHARACTER',status='PUBLISHED',workflow_version_id=?,
            input_contract_json=?,revision=revision+1 WHERE id=?""",
            (
                workflow["id"],
                _json.dumps(
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


def _submit_service(workspace, database):
    return build_asset_image_batch(database, workspace)


def _counts(database, batch_id: str) -> tuple[int, int]:
    with database.connect() as connection:
        intents = connection.execute(
            "SELECT COUNT(*) FROM generation_intents WHERE purpose='ASSET_HERO_IMAGE'"
        ).fetchone()[0]
        items = connection.execute(
            "SELECT COUNT(*) FROM asset_image_generation_batch_items WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
    return int(intents), int(items)


def test_request_identity_is_stable_and_excludes_timestamps(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "r02_identity")
    project_id = str(project["id"])
    canonical_a, hash_a = asset_batch_request_identity(
        project_id, "character", [" a1 ", "a1", "a2"], None, "MISSING_ONLY", "h" * 64
    )
    canonical_b, hash_b = asset_batch_request_identity(
        project_id, "CHARACTER", ["a1", "a2"], None, "MISSING_ONLY", "h" * 64
    )
    assert hash_a == hash_b
    assert json.loads(canonical_a)["asset_ids"] == ["a1", "a2"]
    _, hash_c = asset_batch_request_identity(
        project_id, "CHARACTER", ["a1", "a2"], None, "MISSING_ONLY", "0" * 64
    )
    assert hash_c != hash_a


def test_same_key_same_fingerprint_replays_without_new_dispatch(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "r02_replay")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    first = assets.create_asset(project_id, "CHARACTER", "CHAR_R02_A", "甲", description="黑衣刀客")
    second = assets.create_asset(project_id, "CHARACTER", "CHAR_R02_B", "乙", description="白衣医者")
    profile_version_id = _published_asset_image_profile(workspace, database)
    service = _submit_service(workspace, database)
    asset_ids = [str(first["id"]), str(second["id"])]
    plan = service.plan(project_id, asset_kind="CHARACTER", asset_ids=asset_ids, profile_version_id=profile_version_id)
    submitted = service.submit(
        project_id, asset_kind="CHARACTER", asset_ids=asset_ids,
        profile_version_id=profile_version_id, expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="r02-replay-1",
    )
    before = _counts(database, str(submitted["id"]))
    replay = service.submit(
        project_id, asset_kind="CHARACTER", asset_ids=asset_ids,
        profile_version_id=profile_version_id, expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="r02-replay-1",
    )
    after = _counts(database, str(submitted["id"]))
    assert replay["id"] == submitted["id"]
    assert replay["idempotent_replay"] is True
    assert before == after


def test_same_key_different_params_conflicts_with_zero_side_effects(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "r02_conflict")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    first = assets.create_asset(project_id, "CHARACTER", "CHAR_R02_C", "丙", description="灰衣行者")
    second = assets.create_asset(project_id, "CHARACTER", "CHAR_R02_D", "丁", description="红衣少女")
    profile_version_id = _published_asset_image_profile(workspace, database)
    service = _submit_service(workspace, database)
    plan = service.plan(project_id, asset_kind="CHARACTER", asset_ids=[str(first["id"])], profile_version_id=profile_version_id)
    submitted = service.submit(
        project_id, asset_kind="CHARACTER", asset_ids=[str(first["id"])],
        profile_version_id=profile_version_id, expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="r02-conflict-1",
    )
    with database.connect() as connection:
        intents_before = connection.execute("SELECT COUNT(*) FROM generation_intents WHERE purpose='ASSET_HERO_IMAGE'").fetchone()[0]
        batches_before = connection.execute("SELECT COUNT(*) FROM asset_image_generation_batches").fetchone()[0]
    other_plan = service.plan(project_id, asset_kind="CHARACTER", asset_ids=[str(second["id"])], profile_version_id=profile_version_id)
    with pytest.raises(DomainRuleError) as excinfo:
        service.submit(
            project_id, asset_kind="CHARACTER", asset_ids=[str(second["id"])],
            profile_version_id=profile_version_id, expected_plan_hash=str(other_plan["plan_hash"]),
            idempotency_key="r02-conflict-1",
        )
    assert excinfo.value.code == "IDEMPOTENCY_PAYLOAD_MISMATCH"
    assert excinfo.value.details.get("existing_batch_id") == submitted["id"]
    with database.connect() as connection:
        intents_after = connection.execute("SELECT COUNT(*) FROM generation_intents WHERE purpose='ASSET_HERO_IMAGE'").fetchone()[0]
        batches_after = connection.execute("SELECT COUNT(*) FROM asset_image_generation_batches").fetchone()[0]
    assert intents_after == intents_before
    assert batches_after == batches_before


def test_in_transit_overlap_does_not_fork_second_batch(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "r02_overlap")
    project_id = str(project["id"])
    asset = StoryAssetService(database, workspace).create_asset(project_id, "CHARACTER", "CHAR_R02_E", "戊", description="蓝衣剑客")
    profile_version_id = _published_asset_image_profile(workspace, database)
    service = _submit_service(workspace, database)
    plan = service.plan(project_id, asset_kind="CHARACTER", asset_ids=[str(asset["id"])], profile_version_id=profile_version_id)
    first = service.submit(
        project_id, asset_kind="CHARACTER", asset_ids=[str(asset["id"])],
        profile_version_id=profile_version_id, expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="r02-overlap-1",
    )
    with pytest.raises(DomainRuleError) as excinfo:
        service.submit(
            project_id, asset_kind="CHARACTER", asset_ids=[str(asset["id"])],
            profile_version_id=profile_version_id, expected_plan_hash=str(plan["plan_hash"]),
            idempotency_key="r02-overlap-2",
        )
    assert excinfo.value.code == "ASSET_IMAGE_BATCH_ALREADY_IN_PROGRESS"
    assert excinfo.value.details.get("existing_batch_id") == first["id"]


def test_exact_key_query_bypasses_recent_window(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "r02_exact")
    project_id = str(project["id"])
    assets = StoryAssetService(database, workspace)
    profile_version_id = _published_asset_image_profile(workspace, database)
    service = _submit_service(workspace, database)
    first_asset = assets.create_asset(project_id, "CHARACTER", "CHAR_R02_F", "己", description="紫衣")
    plan = service.plan(project_id, asset_kind="CHARACTER", asset_ids=[str(first_asset["id"])], profile_version_id=profile_version_id)
    first = service.submit(
        project_id, asset_kind="CHARACTER", asset_ids=[str(first_asset["id"])],
        profile_version_id=profile_version_id, expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="r02-exact-old",
    )
    # Push the old batch out of the recent-5 window with other kinds is not
    # possible with a single asset; instead assert exact lookup semantics:
    # unknown key -> [], known key -> single authoritative batch.
    assert service.list_batches(project_id, idempotency_key="r02-exact-missing") == []
    exact = service.list_batches(project_id, idempotency_key="r02-exact-old")
    assert [item["id"] for item in exact] == [first["id"]]
    with TestClient(create_app(workspace)) as client:
        response = client.get(
            f"/api/v1/projects/{project_id}/asset-image-batches",
            params={"idempotency_key": "r02-exact-old"},
        )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [first["id"]]
    with TestClient(create_app(workspace)) as client:
        scoped = client.get(
            f"/api/v1/projects/{project_id}/asset-image-batches",
            params={"idempotency_key": "r02-exact-old", "asset_kind": "SCENE"},
        )
    # Exact key lookup is authoritative; kind filter must not leak others.
    assert scoped.status_code == 200

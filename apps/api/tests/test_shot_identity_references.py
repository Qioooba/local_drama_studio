from __future__ import annotations

import json
import struct
import zlib

import pytest

from local_drama.application.character_identity_packs import CharacterIdentityPackService
from local_drama.application.media import MediaService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.application.workflow_definitions import WorkflowDefinitionService
from local_drama.application.workflow_runtime import WorkflowRuntimeService
from local_drama.application.workflow_contracts import latest_bound_profile_for_capability
from local_drama.application.workflows import WorkflowService
from local_drama.infrastructure.service_composition import build_shot_keyframe_batch
from tests.test_shot_keyframe_generation import _setup


def _pack(workspace, database, project_id, shot_id, asset, number):
    packs = CharacterIdentityPackService(database)
    pack = packs.create_pack(project_id, asset["id"], f"LOOK_{number}", "基础造型")
    version_id = pack["versions"][0]["id"]
    front_id = None
    for i, slot in enumerate(("FRONT", "LEFT", "RIGHT")):
        path = workspace.work_root / f"person-{number}-{slot}.png"
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        pixels = b"".join(b"\x00" + bytes((number * 40, i * 60, 30)) * 16 for _ in range(16))
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))
        media_id = MediaService(database, workspace).import_file(project_id, path, media_kind="IMAGE")["media_version_id"]
        WorkspaceAssetService(database, workspace).authorize_media_version(project_id, media_id)
        packs.set_version_slot(version_id, slot, media_id)
        if slot == "FRONT":
            front_id = media_id
    packs.approve_pack_version(version_id, comment="测试中确认三视图")
    packs.bind_shot_identity_pack(shot_id, asset["id"], version_id)
    return front_id


@pytest.mark.parametrize("count", [1, 3])
def test_approved_identity_images_reach_frozen_generation_inputs(workspace, database, count):
    project, episode, shot, profile_id = _setup(workspace, database)
    project_id, shot_id = str(project["id"]), str(shot["id"])
    with database.connect() as connection:
        first_asset = dict(connection.execute("SELECT * FROM story_assets WHERE project_id=?", (project_id,)).fetchone())
        environment_id = connection.execute("SELECT id FROM runtime_environment_versions WHERE status='PUBLISHED'").fetchone()[0]
    expected_fronts = [_pack(workspace, database, project_id, shot_id, first_asset, 1)]
    catalogue = MediaService(database, workspace).catalogue(project_id, query=first_asset["name"], media_kind="IMAGE")
    front = next(item for item in catalogue if item["media_version_id"] == expected_fronts[0])
    assert front["identity_references"][0]["character_name"] == first_asset["name"]
    assert front["identity_references"][0]["slot_kind"] == "FRONT"
    assert len(catalogue) == 3
    front_search = MediaService(database, workspace).catalogue(project_id, query="FRONT", media_kind="IMAGE")
    assert expected_fronts[0] in {item["media_version_id"] for item in front_search}
    for i in range(2, count + 1):
        asset = StoryAssetService(database, workspace).create_asset(project_id, "CHARACTER", f"PERSON_{i}", f"人物{i}", description=f"人物{i}外观", canonical_media_version_id=expected_fronts[0])
        expected_fronts.append(_pack(workspace, database, project_id, shot_id, asset, i))
    service = build_shot_keyframe_batch(database, workspace)
    target = [{"shot_id": shot_id, "expected_revision": int(shot["revision"])}]
    blocked = service.plan(str(episode["id"]), targets=target, candidate_count=1, profile_version_id=profile_id)
    assert not blocked["valid"]
    assert "SHOT_IDENTITY_REFERENCE_ROUTE_REQUIRED" in {x["code"] for x in blocked["issues"]}

    compiled = WorkflowDefinitionService(workspace).instantiate(f"QWEN_IDENTITY_{count}", {})
    workflow = WorkflowService(database, workspace).register_package("identity-single-frame", "身份一致关键帧", compiled["workflow"], compiled["contract"], compiled["node_bindings"])
    bindings = compiled["node_bindings"]
    inputs = {role: {"type": "IMAGE" if role.startswith("REFERENCE_IMAGE") else "INTEGER" if role == "SEED" else "TEXT", "required": role in {"PROMPT", "NEGATIVE_PROMPT", "SEED"} or role.startswith("REFERENCE_IMAGE")} for role in bindings}
    runtime = WorkflowRuntimeService(database, workspace)
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (workflow["id"],))
        connection.execute("UPDATE execution_profile_versions SET workflow_version_id=?,input_contract_json=? WHERE id=?", (workflow["id"], json.dumps({"input_slots": compiled["contract"]["input_slots"]}), profile_id))
    contract = runtime.create_contract(workflow["id"], "SHOT_KEYFRAME_SINGLE_FRAME", {"output_layout": "SINGLE_FRAME", "inputs": inputs, "outputs": {"IMAGE": {"type": "IMAGE", "required": True, "layout": "SINGLE_FRAME"}}}, bindings, [])
    runtime.publish_contract(contract["id"])
    runtime.bind(workflow["id"], contract["id"], environment_id)
    persisted = WorkflowRuntimeService(database, workspace).list_workflow_contracts(workflow["id"])
    assert persisted["binding"]["contract_version_id"] == contract["id"]
    assert persisted["items"][0]["status"] == "PUBLISHED"
    assert persisted["items"][0]["bindings"][f"REFERENCE_IMAGE_{count}"]["node_id"] == str(10 + count)
    with database.connect() as connection:
        match = latest_bound_profile_for_capability(connection, route_capability="SHOT_KEYFRAME_SINGLE_FRAME", identity_reference_count=count)
        assert match is not None and match["id"] == profile_id
        assert latest_bound_profile_for_capability(connection, route_capability="SHOT_KEYFRAME_SINGLE_FRAME", identity_reference_count=2) is None
    plan = service.plan(str(episode["id"]), targets=target, candidate_count=1, profile_version_id=profile_id)
    assert plan["valid"], plan["issues"]
    assert [ref["media_version_id"] for ref in plan["items"][0]["identity_inputs"]["references"]] == expected_fronts
    assert "参考图1对应人物林舟" in plan["items"][0]["prompt"]
    batch = service.submit(str(episode["id"]), targets=target, frame_strategy="FIRST_ONLY", candidate_count=1,
        expected_plan_hash=plan["plan_hash"], idempotency_key="identity-draw", profile_version_id=profile_id)
    assert batch["summary"]["failed"] == 0, batch
    with database.connect() as connection:
        job = json.loads(connection.execute("SELECT input_snapshot_json FROM jobs WHERE id=?", (batch["items"][0]["job_id"],)).fetchone()[0])
    assert [x["media_version_id"] for x in job["media_bindings"]] == expected_fronts
    assert len(job["identity_packs"]["packs"]) == count
    for i in range(1, count + 1):
        assert compiled["workflow"]["5"]["inputs"][f"image{i}"] == [str(10 + i), 0]
        assert compiled["workflow"]["6"]["inputs"][f"image{i}"] == [str(10 + i), 0]

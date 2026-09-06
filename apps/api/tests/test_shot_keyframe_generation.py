from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.shot_keyframe_generation import ShotKeyframeGenerationBatchService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.workflow_contracts import effective_workflow_contract
from local_drama.application.workflow_runtime import WorkflowRuntimeService
from local_drama.application.workflows import WorkflowService
from local_drama.infrastructure.service_composition import build_shot_keyframe_batch, build_shot_keyframe_completion
from local_drama.main import create_app

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c020000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082")


def _setup(workspace, database):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="keyframe_batch", title="关键帧批次", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE shot_revisions SET fields_json=? WHERE id=?",
            (json.dumps({"subject_action": "林舟在雨夜抬手触碰铜灯", "creative_intent": "压抑而克制"}), shot["current_revision_id"]),
        )
    asset_service = StoryAssetService(database, workspace)
    source = workspace.work_root / "hero-keyframe-ref.png"
    source.write_bytes(PNG)
    media_id = str(MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="IMAGE")["media_version_id"])
    character = asset_service.create_asset(
        str(project["id"]), "CHARACTER", "CHAR_HERO", "林舟",
        description="黑衣青年", canonical_media_version_id=media_id,
    )
    asset_service.bind_asset_to_shot(str(shot["id"]), str(character["id"]))

    profile = next(item for item in ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"] if item["capability"] == "VIDEO_T2V")
    workflow = WorkflowService(database, workspace).register_package(
        "shot_keyframe_batch", "Shot keyframe batch",
        {
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen-clip"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen-vae"}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
            "8": {"class_type": "KSampler", "inputs": {"positive": ["5", 0], "negative": ["6", 0], "seed": 0}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "keyframe"}},
        },
        {"capability": "IMAGE_CONCEPT"},
        {"PROMPT": {"node_id": "5", "input": "text"}, "NEGATIVE_PROMPT": {"node_id": "6", "input": "text"}, "SEED": {"node_id": "8", "input": "seed"}},
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (workflow["id"],))
        connection.execute(
            """UPDATE execution_profile_versions SET capability='IMAGE_CHARACTER',status='PUBLISHED',workflow_version_id=?,
            input_contract_json=?,revision=revision+1 WHERE id=?""",
            (workflow["id"], json.dumps({"input_slots": {"PROMPT": {"min": 1, "max": 1}, "OUTPUT_PREFIX": {"min": 0, "max": 1}}}), profile["version_id"]),
        )
    runtime = WorkflowRuntimeService(database, workspace)
    environment = runtime.create_environment(
        "shot-keyframe-runtime", "Shot keyframe runtime",
        {"mode": "EXTERNAL", "endpoint": "http://127.0.0.1:8188", "custom_nodes": [], "models": []},
    )
    environment_version = runtime.publish_environment(str(environment["versions"][0]["id"]))
    contract = runtime.create_contract(
        str(workflow["id"]),
        "SHOT_KEYFRAME_SINGLE_FRAME",
        {
            "purpose": "SHOT_KEYFRAME_SINGLE_FRAME",
            "output_layout": "SINGLE_FRAME",
            "inputs": {
                "PROMPT": {"type": "TEXT", "required": True},
                "NEGATIVE_PROMPT": {"type": "TEXT", "required": True},
                "SEED": {"type": "INTEGER", "required": True},
            },
            "outputs": {"IMAGE": {"type": "IMAGE", "layout": "SINGLE_FRAME", "required": True}},
        },
        {"PROMPT": {"node_id": "5", "input": "text"}, "NEGATIVE_PROMPT": {"node_id": "6", "input": "text"}, "SEED": {"node_id": "8", "input": "seed"}},
        [{"name": "正向提示词", "node_ids": ["5"]}, {"name": "反向提示词", "node_ids": ["6"]}, {"name": "单画幅采样", "node_ids": ["8", "9"]}, {"name": "保存单帧", "node_ids": ["10"]}],
    )
    published_contract = runtime.publish_contract(str(contract["id"]))
    runtime.bind(str(workflow["id"]), str(published_contract["id"]), str(environment_version["id"]))
    return project, episode, shot, str(profile["version_id"])


def test_keyframe_plan_is_read_only_and_supports_first_and_last(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    target = [{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}]
    plan = build_shot_keyframe_batch(database, workspace).plan(
        str(episode["id"]), targets=target, frame_strategy="FIRST_AND_LAST", candidate_count=2,
        profile_version_id=profile_id,
    )
    assert plan["valid"] is True
    assert plan["summary"] == {"shots": 1, "jobs": 4, "blocked": 0}
    assert {(item["frame_role"], item["candidate_index"]) for item in plan["items"]} == {
        ("FIRST_FRAME", 1), ("FIRST_FRAME", 2), ("END_FRAME", 1), ("END_FRAME", 2),
    }
    assert {item["capability"] for item in plan["items"]} == {"IMAGE_CHARACTER"}
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM shot_keyframe_generation_batches").fetchone()[0] == 0


def test_keyframe_resolution_uses_the_published_runtime_binding_for_the_profile_workflow(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    with database.connect() as connection:
        workflow_id = str(connection.execute(
            "SELECT workflow_version_id FROM execution_profile_versions WHERE id=?", (profile_id,)
        ).fetchone()[0])
        resolved = effective_workflow_contract(connection, workflow_id)

    assert resolved["source"] == "APP_CONTRACT"
    assert resolved["contract_id"] == resolved["published_contract_id"]
    assert resolved["contract_id"]
    assert resolved["published_contract_bound"] is True
    assert resolved["runtime_environment_version_id"]
    assert set(resolved["workflow_bindings"]) == {"PROMPT", "NEGATIVE_PROMPT", "SEED"}

    plan = build_shot_keyframe_batch(database, workspace).plan(
        str(episode["id"]),
        targets=[{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}],
        frame_strategy="FIRST_ONLY",
        candidate_count=1,
        profile_version_id=profile_id,
    )
    route = plan["items"][0]["shot_keyframe_route"]
    assert route["source"] == "APP_CONTRACT"
    assert route["contract_id"] == resolved["contract_id"]
    assert route["runtime_environment_version_id"] == resolved["runtime_environment_version_id"]


def test_keyframe_auto_resolution_prefers_latest_bound_single_frame_route(workspace, database) -> None:
    """A newer page binding must win over the older AUTO image profile."""

    _, episode, shot, profile_id = _setup(workspace, database)
    workflow_service = WorkflowService(database, workspace)
    replacement_workflow = workflow_service.register_package(
        "shot-keyframe-replacement", "Shot keyframe replacement",
        {
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "sdxl-clip"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl-vae"}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
            "8": {"class_type": "KSampler", "inputs": {"positive": ["5", 0], "negative": ["6", 0], "seed": 0}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "keyframe"}},
        },
        {"capability": "IMAGE_CONCEPT"},
        {"PROMPT": {"node_id": "5", "input": "text"}, "NEGATIVE_PROMPT": {"node_id": "6", "input": "text"}, "SEED": {"node_id": "8", "input": "seed"}},
    )
    runtime = WorkflowRuntimeService(database, workspace)
    contract = runtime.create_contract(
        str(replacement_workflow["id"]),
        "SHOT_KEYFRAME_SINGLE_FRAME",
        {
            "purpose": "SHOT_KEYFRAME_SINGLE_FRAME",
            "output_layout": "SINGLE_FRAME",
            "inputs": {
                "PROMPT": {"type": "TEXT", "required": True},
                "NEGATIVE_PROMPT": {"type": "TEXT", "required": True},
                "SEED": {"type": "INTEGER", "required": True},
            },
            "outputs": {"IMAGE": {"type": "IMAGE", "layout": "SINGLE_FRAME", "required": True}},
        },
        {"PROMPT": {"node_id": "5", "input": "text"}, "NEGATIVE_PROMPT": {"node_id": "6", "input": "text"}, "SEED": {"node_id": "8", "input": "seed"}},
        [{"name": "正向提示词", "node_ids": ["5"]}, {"name": "反向提示词", "node_ids": ["6"]}, {"name": "单画幅采样", "node_ids": ["8", "9"]}, {"name": "保存单帧", "node_ids": ["10"]}],
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (replacement_workflow["id"],))
        environment_version_id = str(connection.execute(
            "SELECT id FROM runtime_environment_versions WHERE status='PUBLISHED' ORDER BY created_at DESC,id DESC LIMIT 1",
        ).fetchone()[0])
        original = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_id,)).fetchone()
        assert original is not None
        replacement_profile_id = str(uuid.uuid4())
        columns = [
            "id", "execution_profile_id", "version_no", "capability", "runtime_version_id", "workflow_version_id",
            "model_bundle_json", "input_contract_json", "parameter_schema_json", "status", "created_at", "updated_at",
            "created_by", "revision", "schema_version", "manifest_sha256", "capability_json", "worker_policy",
            "output_contract_json", "resource_policy_json",
        ]
        values = {column: original[column] for column in columns}
        values.update({
            "id": replacement_profile_id,
            "version_no": int(original["version_no"]) + 1,
            "capability": "IMAGE_CONCEPT",
            "workflow_version_id": replacement_workflow["id"],
            "status": "PUBLISHED",
        })
        connection.execute(
            f"INSERT INTO execution_profile_versions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [values[column] for column in columns],
        )
    published_contract = runtime.publish_contract(str(contract["id"]))
    runtime.bind(str(replacement_workflow["id"]), str(published_contract["id"]), environment_version_id)

    plan = build_shot_keyframe_batch(database, workspace).plan(
        str(episode["id"]),
        targets=[{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}],
        frame_strategy="FIRST_ONLY",
        candidate_count=1,
    )

    assert plan["valid"] is True
    item = plan["items"][0]
    assert item["profile_version_id"] == replacement_profile_id
    route = item["shot_keyframe_route"]
    assert route["source"] == "APP_CONTRACT"
    assert route["profile_version_id"] == replacement_profile_id
    assert route["workflow_version_id"] == str(replacement_workflow["id"])
    assert route["contract_id"] == str(published_contract["id"])
    assert route["published_contract_id"] == str(published_contract["id"])
    assert route["runtime_environment_version_id"] == environment_version_id
    assert route["published_contract_bound"] is True


def test_keyframe_submit_is_blocked_when_the_published_contract_has_no_runtime_binding(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    with database.transaction() as connection:
        workflow_id = str(connection.execute(
            "SELECT workflow_version_id FROM execution_profile_versions WHERE id=?", (profile_id,)
        ).fetchone()[0])
        connection.execute("DELETE FROM workflow_runtime_bindings WHERE workflow_version_id=?", (workflow_id,))

    target = [{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}]
    service = build_shot_keyframe_batch(database, workspace)
    plan = service.plan(
        str(episode["id"]), targets=target, frame_strategy="FIRST_ONLY", candidate_count=1, profile_version_id=profile_id,
    )
    assert plan["valid"] is False
    assert any(issue["code"] == "SHOT_KEYFRAME_RUNTIME_BINDING_REQUIRED" for issue in plan["issues"])

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v2/episodes/{episode['id']}/shot-keyframe-batches:submit",
            json={
                "targets": target,
                "frame_strategy": "FIRST_ONLY",
                "candidate_count": 1,
                "profile_version_id": profile_id,
                "expected_plan_hash": plan["plan_hash"],
                "idempotency_key": "keyframe-missing-runtime-binding",
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "SHOT_KEYFRAME_BATCH_BLOCKED"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM shot_keyframe_generation_batches").fetchone()[0] == 0


def test_keyframe_plan_blocks_when_shot_has_no_creative_prompt(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    with database.transaction() as connection:
        connection.execute("UPDATE shot_revisions SET fields_json=? WHERE id=?", ("{}", shot["current_revision_id"]))

    plan = build_shot_keyframe_batch(database, workspace).plan(
        str(episode["id"]),
        targets=[{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}],
        frame_strategy="FIRST_ONLY",
        candidate_count=1,
        profile_version_id=profile_id,
    )

    assert plan["valid"] is False
    assert plan["summary"] == {"shots": 1, "jobs": 0, "blocked": 1}
    assert {issue["code"] for issue in plan["issues"]} == {"SHOT_KEYFRAME_PROMPT_REQUIRED"}
    assert plan["items"][0]["prompt_bundle"]["final_prompt"] == ""


def test_keyframe_prompt_bundle_is_frozen_consistently_in_batch_item_and_job_snapshots(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    target = [{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}]
    bundle_request = {
        "positive_override": "single shot, full-frame, no storyboard",
        "negative_prompt": "triptych, contact sheet, split-screen, collage",
        "provenance": "PAGE_USER_EDIT",
    }
    service = build_shot_keyframe_batch(database, workspace)
    plan = service.plan(
        str(episode["id"]),
        targets=target,
        frame_strategy="FIRST_ONLY",
        candidate_count=1,
        profile_version_id=profile_id,
        prompt_bundle=bundle_request,
    )
    item = plan["items"][0]
    assert item["prompt_bundle"]["provenance"] == "PAGE_USER_EDIT"
    assert "Avoid:" not in item["prompt"]
    assert item["semantic_inputs"]["NEGATIVE_PROMPT"] == "triptych, contact sheet, split-screen, collage"

    batch = service.submit(
        str(episode["id"]),
        targets=target,
        frame_strategy="FIRST_ONLY",
        candidate_count=1,
        profile_version_id=profile_id,
        prompt_bundle=bundle_request,
        expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key="prompt-bundle-batch-1",
    )
    item_row = batch["items"][0]
    with database.connect() as connection:
        workflow_id = connection.execute(
            "SELECT workflow_version_id FROM execution_profile_versions WHERE id=?", (profile_id,)
        ).fetchone()[0]
        batch_snapshot = json.loads(str(connection.execute(
            "SELECT input_snapshot_json FROM shot_keyframe_generation_batches WHERE id=?", (batch["id"],)
        ).fetchone()[0]))
        item_snapshot = json.loads(str(connection.execute(
            "SELECT input_snapshot_json FROM shot_keyframe_generation_batch_items WHERE id=?", (item_row["id"],)
        ).fetchone()[0]))
        job_snapshot = json.loads(str(connection.execute(
            "SELECT input_snapshot_json FROM jobs WHERE id=?", (item_row["job_id"],)
        ).fetchone()[0]))
    compiled = WorkflowService(database, workspace).compile_semantic_inputs(
        str(workflow_id), item["semantic_inputs"] | {"SEED": 123456}
    )
    assert "Avoid:" not in compiled["workflow"]["5"]["inputs"]["text"]
    assert compiled["workflow"]["5"]["inputs"]["text"] == item["semantic_inputs"]["PROMPT"]
    assert compiled["workflow"]["6"]["inputs"]["text"] == item["semantic_inputs"]["NEGATIVE_PROMPT"]
    assert compiled["effect_report"]["applied_semantic_roles"] == ["NEGATIVE_PROMPT", "PROMPT", "SEED"]
    assert item_snapshot["prompt_bundle"] == plan["items"][0]["prompt_bundle"]
    assert batch_snapshot["prompt_bundles"][0] == item_snapshot["prompt_bundle"]
    assert job_snapshot["prompt_bundle"] == item_snapshot["prompt_bundle"]
    assert job_snapshot["semantic_inputs"]["PROMPT"].startswith(item["prompt"])
    assert job_snapshot["semantic_inputs"]["NEGATIVE_PROMPT"] == item["semantic_inputs"]["NEGATIVE_PROMPT"]
    assert job_snapshot["execution_snapshot"]["workflow_contract"]["source"] == "APP_CONTRACT"
    assert job_snapshot["execution_snapshot"]["workflow_contract"]["semantic_roles"] == ["NEGATIVE_PROMPT", "PROMPT", "SEED"]


def test_end_frame_prompt_leads_with_final_action_endpoint() -> None:
    prompt = ShotKeyframeGenerationBatchService._frame_prompt(
        {
            "subject_action": "镜头扫过干裂田地。切至药铺门前，沈砚把灯心草分成三束。",
            "creative_intent": "旱灾中的压价场景",
        },
        "S001",
        "END_FRAME",
    )

    assert prompt.startswith("镜头结束定格；画面只呈现动作终点：药铺门前，沈砚把灯心草分成三束")
    assert prompt.index("药铺门前") < prompt.index("镜头上下文")
    assert "不要重复镜头开场画面" in prompt


def test_single_moment_reframe_separates_first_and_last_visual_moments() -> None:
    fields = {
        "subject_action": "镜头扫过干裂田地。特写井绳提起的一桶腥泥。切至药铺门前，沈砚把灯心草分成三束。",
        "creative_intent": "旱灾中的压价场景",
    }

    first = ShotKeyframeGenerationBatchService._frame_prompt(
        fields, "S001", "FIRST_FRAME", reframe_mode="SINGLE_MOMENT"
    )
    end = ShotKeyframeGenerationBatchService._frame_prompt(
        fields, "S001", "END_FRAME", reframe_mode="SINGLE_MOMENT"
    )

    assert "首帧只呈现第一个视觉瞬间：干裂田地" in first
    assert "药铺门前" not in first
    assert "尾帧只呈现最后一个视觉瞬间：药铺门前，沈砚把灯心草分成三束" in end
    assert "干裂田地" not in end
    assert "镜头扫过干裂田地" not in first
    assert "镜头扫过干裂田地" not in end


def test_single_moment_reframe_preserves_source_base_and_compiles_distinct_roles(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    fields = {
        "subject_action": "镜头扫过干裂田地。切至药铺门前，沈砚把灯心草分成三束。",
        "creative_intent": "旱灾中的压价场景",
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE shot_revisions SET fields_json=? WHERE id=?",
            (json.dumps(fields), shot["current_revision_id"]),
        )
    service = build_shot_keyframe_batch(database, workspace)
    plan = service.plan(
        str(episode["id"]),
        targets=[{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}],
        frame_strategy="FIRST_AND_LAST",
        candidate_count=1,
        profile_version_id=profile_id,
        prompt_bundle={
            "positive_override": "单一满画幅",
            "negative_prompt": "triptych, contact sheet",
            "provenance": "PAGE_USER_EDIT",
            "frame_reframe_mode": "SINGLE_MOMENT",
        },
    )

    assert plan["valid"] is True
    first = next(item for item in plan["items"] if item["frame_role"] == "FIRST_FRAME")
    end = next(item for item in plan["items"] if item["frame_role"] == "END_FRAME")
    source = first["prompt_bundle"]["base_prompt"]
    assert "镜头扫过干裂田地" in source
    assert first["prompt_bundle"]["frame_reframe_mode"] == "SINGLE_MOMENT"
    assert first["prompt_bundle"]["effective_base_prompt"] != end["prompt_bundle"]["effective_base_prompt"]
    assert first["prompt_bundle"]["provenance"] == "PAGE_USER_EDIT"
    assert end["prompt_bundle"]["provenance"] == "PAGE_USER_EDIT"
    for effective in (first["prompt_bundle"]["effective_base_prompt"], end["prompt_bundle"]["effective_base_prompt"]):
        assert effective
        assert all(marker not in effective for marker in ("切至", "转至", "随后", "镜头扫过"))
    assert "药铺门前" not in first["prompt"]
    assert "干裂田地" not in end["prompt"]
    assert source not in first["prompt"]


def test_keyframe_redraw_adds_a_new_candidate_window() -> None:
    assert list(ShotKeyframeGenerationBatchService._next_candidate_indices(0, 4)) == [1, 2, 3, 4]
    assert list(ShotKeyframeGenerationBatchService._next_candidate_indices(4, 4)) == [1, 2, 3, 4]


def test_keyframe_redraw_seed_changes_but_replay_seed_is_stable() -> None:
    first = ShotKeyframeGenerationBatchService._seed("shot-1", "FIRST_FRAME", 1, "draw-1")
    replay = ShotKeyframeGenerationBatchService._seed("shot-1", "FIRST_FRAME", 1, "draw-1")
    redraw = ShotKeyframeGenerationBatchService._seed("shot-1", "FIRST_FRAME", 1, "draw-2")

    assert first == replay
    assert first != redraw


def test_keyframe_plan_deduplicates_shared_blockers_across_candidates_and_frame_roles(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET status='DRAFT', revision=revision+1 WHERE id=?",
            (profile_id,),
        )

    plan = build_shot_keyframe_batch(database, workspace).plan(
        str(episode["id"]),
        targets=[{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}],
        frame_strategy="FIRST_AND_LAST",
        candidate_count=2,
        profile_version_id=profile_id,
    )

    assert plan["valid"] is False
    assert plan["summary"] == {"shots": 1, "jobs": 0, "blocked": 4}
    assert len([issue for issue in plan["issues"] if issue["code"] == "SHOT_KEYFRAME_PROFILE_REQUIRED"]) == 1


def test_keyframe_plan_crosswalks_character_to_published_concept_profile(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET capability='IMAGE_CONCEPT', revision=revision+1 WHERE id=?",
            (profile_id,),
        )

    plan = build_shot_keyframe_batch(database, workspace).plan(
        str(episode["id"]),
        targets=[{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}],
        frame_strategy="FIRST_AND_LAST",
        candidate_count=1,
    )

    assert plan["valid"] is True
    assert plan["summary"] == {"shots": 1, "jobs": 2, "blocked": 0}
    assert {item["capability"] for item in plan["items"]} == {"IMAGE_CHARACTER"}
    assert {item["profile_version_id"] for item in plan["items"]} == {profile_id}


def test_keyframe_plan_crosswalks_when_exact_profile_uses_smoke_only_workflow(workspace, database) -> None:
    _, episode, shot, concept_profile_id = _setup(workspace, database)
    profiles = ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"]
    exact_profile_id = str(next(item for item in profiles if item["version_id"] != concept_profile_id)["version_id"])
    smoke = WorkflowService(database, workspace).register_package(
        "shot_keyframe_smoke_only", "Shot keyframe smoke only",
        {"1": {"class_type": "SaveImage", "inputs": {"prompt": "", "seed": 0}}}, {},
        {"PROMPT": {"node_id": "1", "input": "prompt"}},
    )
    with database.transaction() as connection:
        concept_workflow_id = connection.execute(
            "SELECT workflow_version_id FROM execution_profile_versions WHERE id=?", (concept_profile_id,)
        ).fetchone()[0]
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (smoke["id"],))
        connection.execute(
            "UPDATE execution_profile_versions SET capability='IMAGE_CONCEPT',status='PUBLISHED',workflow_version_id=? WHERE id=?",
            (concept_workflow_id, concept_profile_id),
        )
        connection.execute(
            "UPDATE execution_profile_versions SET capability='IMAGE_CHARACTER',status='PUBLISHED',workflow_version_id=? WHERE id=?",
            (smoke["id"], exact_profile_id),
        )

    plan = build_shot_keyframe_batch(database, workspace).plan(
        str(episode["id"]),
        targets=[{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}],
        frame_strategy="FIRST_AND_LAST",
        candidate_count=1,
    )

    assert plan["valid"] is True
    assert {item["profile_version_id"] for item in plan["items"]} == {concept_profile_id}


def test_keyframe_submit_replays_and_completion_promotes_end_frame(workspace, database) -> None:
    project, episode, shot, profile_id = _setup(workspace, database)
    target = [{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}]
    service = build_shot_keyframe_batch(database, workspace)
    plan = service.plan(str(episode["id"]), targets=target, frame_strategy="FIRST_AND_LAST", candidate_count=1, profile_version_id=profile_id)
    batch = service.submit(
        str(episode["id"]), targets=target, frame_strategy="FIRST_AND_LAST", candidate_count=1,
        profile_version_id=profile_id, expected_plan_hash=str(plan["plan_hash"]), idempotency_key="keyframe-batch-1",
    )
    replay = service.submit(
        str(episode["id"]), targets=target, frame_strategy="FIRST_AND_LAST", candidate_count=1,
        profile_version_id=profile_id, expected_plan_hash=str(plan["plan_hash"]), idempotency_key="keyframe-batch-1",
    )
    assert batch["summary"]["total"] == 2
    assert replay["id"] == batch["id"] and replay["idempotent_replay"] is True
    end_item = next(item for item in batch["items"] if item["frame_role"] == "END_FRAME")
    jobs = JobService(database, workspace)
    while True:
        claim = jobs.claim("keyframe-test-worker", ["GPU_H3"])
        assert claim is not None
        if str(claim["job"]["id"]) == end_item["job_id"]:
            break
        jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "keyframe-test-worker", success=False, error_code="TEST_SKIP", error_detail_redacted="skip")
    output = workspace.work_root / "jobs" / end_item["job_id"] / "end.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(PNG)
    artifact = jobs.register_artifact(str(claim["attempt"]["id"]), "COMFY_OUTPUT", output.relative_to(workspace.work_root).as_posix())
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "keyframe-test-worker", success=True)
    result = build_shot_keyframe_completion(database, workspace).finalize_job(str(end_item["job_id"]), [artifact])
    assert result is not None and result["status"] == "SUCCEEDED"
    media = MediaService(database, workspace).get_version(str(result["media_version_id"]))
    assert {key: media[key] for key in ("purpose", "stage", "integrity_status")} == {
        "purpose": "KEYFRAME_END", "stage": "KEYFRAME", "integrity_status": "VERIFIED",
    }
    with TestClient(create_app(workspace)) as client:
        studio_response = client.get(f"/api/v2/episodes/{episode['id']}/shots/{shot['id']}/studio")
    assert studio_response.status_code == 200, studio_response.text
    candidate = next(
        item for item in studio_response.json()["current_shot"]["candidates"]
        if item["media_version_id"] == result["media_version_id"]
    )
    assert candidate["frame_role"] == "END_FRAME"


def test_keyframe_batch_routes_expose_explicit_plan(workspace, database) -> None:
    _, episode, shot, profile_id = _setup(workspace, database)
    target = [{"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}]
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v2/episodes/{episode['id']}/shot-keyframe-batches:plan",
            json={"targets": target, "frame_strategy": "FIRST_ONLY", "candidate_count": 2, "profile_version_id": profile_id},
        )
    assert response.status_code == 200, response.text
    assert response.json()["plan"]["summary"] == {"shots": 1, "jobs": 2, "blocked": 0}

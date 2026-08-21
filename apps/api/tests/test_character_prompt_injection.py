from __future__ import annotations

import hashlib
import json
import uuid

from fastapi.testclient import TestClient

from local_drama.application.generation import GenerationService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.workflows import WorkflowService
from local_drama.domain.generation import VariantPlan
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
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    return projects, project, episode, shot


def _bind_character(
    database,
    project_id: str,
    shot_id: str,
    *,
    code: str,
    name: str,
    description: str = "",
    canonical_media_version_id: str | None = None,
    role_in_shot: str = "main",
) -> str:
    """SQL-direct ACTIVE CHARACTER asset + shot binding (per G11 P0-1 spec)."""
    asset_id = str(uuid.uuid4())
    now = "2025-01-01T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO story_assets
            (id, project_id, kind, code, name, description, canonical_media_version_id, extra_json, status,
             created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'CHARACTER', ?, ?, ?, ?, '{}', 'ACTIVE', ?, ?, 'local-user', 1, 'v2')""",
            (asset_id, project_id, code, name, description, canonical_media_version_id, now, now),
        )
        connection.execute(
            """INSERT INTO shot_asset_bindings
            (id, shot_id, asset_id, role_in_shot, created_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, ?, ?, 'local-user', 1, 'v2')""",
            (str(uuid.uuid4()), shot_id, asset_id, role_in_shot, now),
        )
    return asset_id


def _published_profile(workspace, database):
    """Reuse the test_variant_submission.py profile/workflow scaffold (T2V)."""
    profile = next(
        item
        for item in ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"]
        if item["capability"] == "VIDEO_T2V"
    )
    workflow = {"1": {"class_type": "LoadImage", "inputs": {"prompt": "", "seed": 0}}}
    version = WorkflowService(database, workspace).register_package(
        "anchor_submit_workflow", "Anchor submit", workflow, {},
        {"PROMPT": {"node_id": "1", "input": "prompt"}, "SEED": {"node_id": "1", "input": "seed"}},
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (version["id"],))
        connection.execute(
            """UPDATE execution_profile_versions SET status='PUBLISHED', workflow_version_id=?,
            input_contract_json=?, revision=revision+1 WHERE id=?""",
            (version["id"], json.dumps({"input_slots": {}}), profile["version_id"]),
        )
    return profile


def _plan(profile_version_id: str, prompt: str = "candle") -> VariantPlan:
    return VariantPlan(
        variant_type="BASE", parent_variant_id=None, branch_reason="anchor take", prompt_revision_id=None,
        profile_version_id=profile_version_id,
        parameter_set={"PROMPT": prompt, "SEED": 101}, seed_policy="EXPLICIT", explicit_seed=101, bindings=(),
    )


def _submit(workspace, database, project_id: str, shot_id: str, prompt: str = "candle"):
    profile = _published_profile(workspace, database)
    service = GenerationService(database, workspace)
    intent = service.create_intent(project_id, "SHOT", shot_id, "T2V", "character anchor injection")
    plan = _plan(str(profile["version_id"]), prompt)
    preflight = service.preflight_variant(str(intent["id"]), plan)
    submitted = service.submit_confirmed_variant(
        str(intent["id"]), plan, str(preflight["plan_hash"]), f"anchor-{uuid.uuid4()}"
    )
    return intent, submitted


def test_single_character_anchor_injected_into_job_snapshot(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "anchor_single")
    _bind_character(database, str(project["id"]), str(shot["id"]), code="CHAR_MOTHER", name="母亲", description="短发、蓝色连衣裙")
    intent, submitted = _submit(workspace, database, str(project["id"]), str(shot["id"]))

    snapshot = submitted["job"]["input_snapshot"]
    assert snapshot["semantic_inputs"]["PROMPT"] == "candle\n\n角色锚点 · 母亲：短发、蓝色连衣裙"
    anchor = snapshot["story_assets"]["anchor"]
    assert anchor == "角色锚点 · 母亲：短发、蓝色连衣裙"
    expected_sha = hashlib.sha256(anchor.encode("utf-8")).hexdigest()
    assert snapshot["story_assets"]["anchor_sha256"] == expected_sha

    with database.connect() as connection:
        audit = connection.execute(
            "SELECT metadata_redacted_json FROM audit_events WHERE action='GENERATION_CHARACTER_ANCHOR_INJECTED' AND subject_id=?",
            (submitted["variant"]["id"],),
        ).fetchone()
    assert audit is not None
    metadata = json.loads(audit["metadata_redacted_json"])
    assert metadata["character_anchor_sha256"] == expected_sha
    assert metadata["character_anchor_lines"] == 1
    # The frozen variant parameter_set must stay anchor-free (EXACT_REPLAY base).
    assert json.loads(submitted["variant"]["parameter_set_json"])["PROMPT"] == "candle"


def test_two_characters_anchors_ordered_by_role_then_name(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "anchor_two")
    _bind_character(database, str(project["id"]), str(shot["id"]), code="CHAR_SUPPORT", name="配角", description="戴眼镜", role_in_shot="support")
    _bind_character(database, str(project["id"]), str(shot["id"]), code="CHAR_LEAD", name="主角", description="银发", role_in_shot="main")
    intent, submitted = _submit(workspace, database, str(project["id"]), str(shot["id"]))

    snapshot = submitted["job"]["input_snapshot"]
    anchor = snapshot["story_assets"]["anchor"]
    assert anchor.split("\n") == ["角色锚点 · 主角：银发", "角色锚点 · 配角：戴眼镜"]
    prompt = snapshot["semantic_inputs"]["PROMPT"]
    assert prompt.index("角色锚点 · 主角") < prompt.index("角色锚点 · 配角")
    assert snapshot["story_assets"]["anchor_sha256"] == hashlib.sha256(anchor.encode("utf-8")).hexdigest()


def test_no_bindings_leaves_prompt_and_snapshot_untouched(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "anchor_none")
    intent, submitted = _submit(workspace, database, str(project["id"]), str(shot["id"]))

    snapshot = submitted["job"]["input_snapshot"]
    assert snapshot["semantic_inputs"]["PROMPT"] == "candle"
    assert "story_assets" not in snapshot
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='GENERATION_CHARACTER_ANCHOR_INJECTED' AND subject_id=?",
            (submitted["variant"]["id"],),
        ).fetchone()[0]
    assert count == 0


def test_non_shot_owner_skips_injection(workspace, database) -> None:
    _, project, _, _ = _project(workspace, database, "anchor_project_owner")
    profile = _published_profile(workspace, database)
    service = GenerationService(database, workspace)
    intent = service.create_intent(str(project["id"]), "PROJECT", str(project["id"]), "T2V", "project owned")
    plan = _plan(str(profile["version_id"]))
    preflight = service.preflight_variant(str(intent["id"]), plan)
    submitted = service.submit_confirmed_variant(
        str(intent["id"]), plan, str(preflight["plan_hash"]), "anchor-project-owner"
    )

    snapshot = submitted["job"]["input_snapshot"]
    assert snapshot["semantic_inputs"]["PROMPT"] == "candle"
    assert "story_assets" not in snapshot


def test_prompt_anchor_preview_endpoint(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "anchor_preview")
    with TestClient(create_app(workspace)) as client:
        empty = client.get(f"/api/v1/shots/{shot['id']}/prompt-anchor")
        missing = client.get("/api/v1/shots/00000000-0000-0000-0000-000000000000/prompt-anchor")
    assert empty.status_code == 200
    assert empty.json()["shot_id"] == shot["id"]
    assert empty.json()["anchor"] == ""
    assert empty.json()["characters"] == []
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SHOT_NOT_FOUND"

    _bind_character(database, str(project["id"]), str(shot["id"]), code="CHAR_A", name="角色甲", description="红发")
    with TestClient(create_app(workspace)) as client:
        bound = client.get(f"/api/v1/shots/{shot['id']}/prompt-anchor")
    assert bound.status_code == 200
    payload = bound.json()
    assert payload["anchor"] == "角色锚点 · 角色甲：红发"
    assert payload["characters"][0]["asset_id"]
    assert payload["characters"][0]["name"] == "角色甲"
    assert payload["characters"][0]["canonical_media_version_id"] is None


def test_anchor_line_shape_empty_description_and_canonical_media(workspace, database) -> None:
    _, project, _, shot = _project(workspace, database, "anchor_shape")
    canonical_id = "22222222-2222-2222-2222-222222222222"
    _bind_character(database, str(project["id"]), str(shot["id"]), code="CHAR_NO_DESC", name="无描述")
    _bind_character(database, str(project["id"]), str(shot["id"]), code="CHAR_WITH_REF", name="有参考图", role_in_shot="main2", canonical_media_version_id=canonical_id)
    intent, submitted = _submit(workspace, database, str(project["id"]), str(shot["id"]))

    snapshot = submitted["job"]["input_snapshot"]
    lines = snapshot["story_assets"]["anchor"].split("\n")
    assert lines == ["角色锚点 · 无描述", f"角色锚点 · 有参考图（参考图 {canonical_id}）"]
    assert "角色锚点 · 无描述" in snapshot["semantic_inputs"]["PROMPT"]

    with TestClient(create_app(workspace)) as client:
        preview = client.get(f"/api/v1/shots/{shot['id']}/prompt-anchor")
    assert preview.status_code == 200
    characters = {item["name"]: item for item in preview.json()["characters"]}
    assert characters["无描述"]["description"] == ""
    assert characters["无描述"]["canonical_media_version_id"] is None
    assert characters["有参考图"]["canonical_media_version_id"] == canonical_id

"""Integration and invariant tests for Character Identity Packs (PR-CUR-007)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from local_drama.application.character_identity_packs import CharacterIdentityPackService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _setup_character_and_media(workspace, database):
    project = ProjectService(database, workspace.projects_root).create_project(
        code="id_pack_prj",
        title="ID Pack Project",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    p_id = str(project["id"])

    # Create Character Asset
    asset_svc = StoryAssetService(database, workspace)
    char_asset = asset_svc.create_asset(p_id, "CHARACTER", "HERO_01", "主角林远", "年轻侦探")
    state_id = str(uuid.uuid4())
    now = "2026-08-21T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO story_asset_states
            (id, project_id, story_asset_id, code, label, state_kind, description, state_json, status, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, 'OUTFIT_RAIN', '雨衣造型', 'OUTFIT', '穿黑色雨衣', '{}', 'ACTIVE', ?, ?, 'test', 1, 'v1')""",
            (state_id, p_id, char_asset["id"], now, now),
        )
    state = {"id": state_id, "code": "OUTFIT_RAIN"}

    # Create Media Versions for Slots
    media_svc = MediaService(database, workspace)
    front_file = workspace.work_root / "hero_front.png"
    front_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4FRONT")
    front_mv = media_svc.import_file(p_id, front_file)

    left_file = workspace.work_root / "hero_left.png"
    left_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4LEFT")
    left_mv = media_svc.import_file(p_id, left_file)

    right_file = workspace.work_root / "hero_right.png"
    right_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4RIGHT")
    right_mv = media_svc.import_file(p_id, right_file)

    authorization_svc = WorkspaceAssetService(database, workspace)
    for media_version_id in (
        front_mv["media_version_id"],
        left_mv["media_version_id"],
        right_mv["media_version_id"],
    ):
        authorization_svc.authorize_media_version(p_id, media_version_id)

    return {
        "project_id": p_id,
        "character": char_asset,
        "state": state,
        "front_mv": front_mv["media_version_id"],
        "left_mv": left_mv["media_version_id"],
        "right_mv": right_mv["media_version_id"],
    }


def _create_shot(database, project_id: str, code: str = "S01") -> str:
    with database.connect() as connection:
        episode_id = connection.execute(
            """SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id
            WHERE s.project_id=? ORDER BY e.display_order,e.id LIMIT 1""",
            (project_id,),
        ).fetchone()["id"]
    shot_id = str(uuid.uuid4())
    now = "2026-08-21T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO shots
            (id,episode_id,code,order_key,shot_type,status,target_duration_ms,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,'0001','WIDE','DRAFT',5000,?,?,'test',1,'v1')""",
            (shot_id, episode_id, code, now, now),
        )
    return shot_id


def _import_media(
    workspace,
    database,
    project_id: str,
    name: str,
    payload: bytes,
    *,
    media_kind: str = "IMAGE",
    authorize: bool = True,
) -> str:
    source = workspace.work_root / name
    source.write_bytes(payload)
    media_version_id = str(
        MediaService(database, workspace).import_file(
            project_id,
            source,
            media_kind=media_kind,
        )["media_version_id"]
    )
    if authorize:
        WorkspaceAssetService(database, workspace).authorize_media_version(project_id, media_version_id)
    return media_version_id


def _create_approved_pack(ctx, database, *, code: str = "APPROVED_PACK"):
    service = CharacterIdentityPackService(database)
    pack = service.create_pack(
        project_id=ctx["project_id"],
        story_asset_id=ctx["character"]["id"],
        code=code,
        name=code,
        asset_state_id=ctx["state"]["id"],
    )
    version = pack["versions"][0]
    for slot_kind, key in (("FRONT", "front_mv"), ("LEFT", "left_mv"), ("RIGHT", "right_mv")):
        service.set_version_slot(version["id"], slot_kind, ctx[key])
    return pack, service.approve_pack_version(version["id"], comment="人工核对三视图、状态与授权通过")


def test_character_identity_pack_lifecycle_and_immutability(workspace, database) -> None:
    ctx = _setup_character_and_media(workspace, database)
    pack_svc = CharacterIdentityPackService(database)

    # 1. Create Identity Pack
    pack = pack_svc.create_pack(
        project_id=ctx["project_id"],
        story_asset_id=ctx["character"]["id"],
        code="DEFAULT_PACK",
        name="林远基础三视图包",
        description="标准西装外观",
        asset_state_id=ctx["state"]["id"],
    )
    assert pack["code"] == "DEFAULT_PACK"
    assert len(pack["versions"]) == 1
    v1 = pack["versions"][0]
    assert v1["version_no"] == 1
    assert v1["status"] == "DRAFT"

    # 2. Add Three-View Slots to v1
    pack_svc.set_version_slot(v1["id"], "FRONT", ctx["front_mv"])
    pack_svc.set_version_slot(v1["id"], "LEFT", ctx["left_mv"])
    pack_svc.set_version_slot(v1["id"], "RIGHT", ctx["right_mv"])

    v1_updated = pack_svc.get_version(v1["id"])
    assert len(v1_updated["slots"]) == 3
    assert v1_updated["slots_map"]["FRONT"] == ctx["front_mv"]

    # 3. Approve v1
    v1_approved = pack_svc.approve_pack_version(v1["id"], comment="人设初审通过")
    assert v1_approved["status"] == "APPROVED"
    assert v1_approved["approval_metadata"]["comment"] == "人设初审通过"

    pack_after_approve = pack_svc.get_pack(pack["id"])
    assert pack_after_approve["current_version_id"] == v1["id"]

    # 4. Approved version is immutable
    with pytest.raises(DomainRuleError) as caught:
        pack_svc.set_version_slot(v1["id"], "BACK", ctx["front_mv"])
    assert caught.value.code == "APPROVED_PACK_IMMUTABLE"

    # 5. Create v2 draft derived from v1
    v2 = pack_svc.create_version_draft(pack["id"], from_version_id=v1["id"])
    assert v2["version_no"] == 2
    assert v2["status"] == "DRAFT"
    assert len(v2["slots"]) == 3

    # Add extra FACE slot to v2
    pack_svc.set_version_slot(v2["id"], "FACE", ctx["front_mv"])
    v2_approved = pack_svc.approve_pack_version(v2["id"], comment="补充面部特写槽位")
    assert v2_approved["status"] == "APPROVED"

    # v1 is now SUPERSEDED
    v1_recheck = pack_svc.get_version(v1["id"])
    assert v1_recheck["status"] == "SUPERSEDED"

    pack_after_v2 = pack_svc.get_pack(pack["id"])
    assert pack_after_v2["current_version_id"] == v2["id"]


def test_shot_binding_and_stale_detection(workspace, database) -> None:
    ctx = _setup_character_and_media(workspace, database)
    pack_svc = CharacterIdentityPackService(database)

    pack = pack_svc.create_pack(
        project_id=ctx["project_id"],
        story_asset_id=ctx["character"]["id"],
        code="STALE_CHECK",
        name="时效性测试包",
    )
    v1 = pack["versions"][0]
    pack_svc.set_version_slot(v1["id"], "FRONT", ctx["front_mv"])
    pack_svc.set_version_slot(v1["id"], "LEFT", ctx["left_mv"])
    pack_svc.set_version_slot(v1["id"], "RIGHT", ctx["right_mv"])
    pack_svc.approve_pack_version(v1["id"], comment="人工确认初版三视图")

    # Create a shot in this project
    with database.connect() as connection:
        ep_row = connection.execute("SELECT e.id FROM episodes e JOIN seasons s ON s.id = e.season_id WHERE s.project_id = ?", (ctx["project_id"],)).fetchone()
    ep_id = ep_row["id"]
    shot_id = str(uuid.uuid4())
    now = "2026-08-21T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO shots
            (id, episode_id, code, order_key, shot_type, status, target_duration_ms, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'S01', '0001', 'WIDE', 'DRAFT', 5000, ?, ?, 'test', 1, 'v1')""",
            (shot_id, ep_id, now, now),
        )

    # Bind shot to v1
    pack_svc.bind_shot_identity_pack(shot_id, ctx["character"]["id"], v1["id"])

    packs_on_shot = pack_svc.get_shot_character_packs(shot_id)
    assert len(packs_on_shot) == 1
    assert packs_on_shot[0]["identity_pack_version_id"] == v1["id"]
    assert packs_on_shot[0]["is_stale"] is False

    # Create v2 and approve it
    v2 = pack_svc.create_version_draft(pack["id"], from_version_id=v1["id"])
    pack_svc.approve_pack_version(v2["id"], comment="人工确认新版三视图")

    # Shot should now report stale because v2 is approved
    packs_on_shot_after = pack_svc.get_shot_character_packs(shot_id)
    assert len(packs_on_shot_after) == 1
    assert packs_on_shot_after[0]["identity_pack_version_id"] == v1["id"]
    assert packs_on_shot_after[0]["latest_approved_version_id"] == v2["id"]
    assert packs_on_shot_after[0]["is_stale"] is True


def test_unbound_shot_character_exposes_approved_pack_choices(workspace, database) -> None:
    ctx = _setup_character_and_media(workspace, database)
    pack_svc = CharacterIdentityPackService(database)
    pack, approved = _create_approved_pack(ctx, database, code="INITIAL_BINDING")
    shot_id = _create_shot(database, ctx["project_id"], "S_INITIAL_BINDING")
    StoryAssetService(database, workspace).bind_asset_to_shot(
        shot_id, str(ctx["character"]["id"]), "main",
    )

    item = pack_svc.get_shot_character_packs(shot_id)[0]

    assert item["identity_pack_version_id"] is None
    assert item["approved_versions"] == [
        {
            "pack_id": str(pack["id"]),
            "pack_name": "INITIAL_BINDING",
            "pack_code": "INITIAL_BINDING",
            "version_id": str(approved["id"]),
            "version_no": 1,
            "status": "APPROVED",
        }
    ]


def test_character_identity_pack_http_endpoints(workspace, database) -> None:
    ctx = _setup_character_and_media(workspace, database)
    with TestClient(create_app(workspace)) as client:
        # Create pack via HTTP
        resp = client.post(
            f"/api/v1/story-assets/{ctx['character']['id']}/identity-packs",
            json={
                "project_id": ctx["project_id"],
                "code": "HTTP_PACK",
                "name": "接口创建的身份包",
            },
        )
        assert resp.status_code == 201
        pack_data = resp.json()["pack"]
        pack_id = pack_data["id"]
        v1_id = pack_data["versions"][0]["id"]

        # Set slot
        slot_resp = client.put(
            f"/api/v1/character-identity-pack-versions/{v1_id}/slots",
            json={"slot_kind": "FRONT", "media_version_id": ctx["front_mv"]},
        )
        assert slot_resp.status_code == 200
        assert slot_resp.json()["version"]["slots_map"]["FRONT"] == ctx["front_mv"]

        # Approval fails closed while LEFT/RIGHT are absent.
        blocked_approve = client.post(
            f"/api/v1/character-identity-pack-versions/{v1_id}:approve",
            json={"comment": "不能绕过缺槽门禁"},
        )
        assert blocked_approve.status_code == 422
        assert blocked_approve.json()["error"]["code"] == "PACK_REQUIRED_SLOTS_MISSING"

        for slot_kind, media_version_id in (
            ("LEFT", ctx["left_mv"]),
            ("RIGHT", ctx["right_mv"]),
        ):
            slot_resp = client.put(
                f"/api/v1/character-identity-pack-versions/{v1_id}/slots",
                json={"slot_kind": slot_kind, "media_version_id": media_version_id},
            )
            assert slot_resp.status_code == 200

        # Approval is a separate explicit human action.
        approve_resp = client.post(
            f"/api/v1/character-identity-pack-versions/{v1_id}:approve",
            json={"comment": "HTTP 批准通过"},
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["version"]["status"] == "APPROVED"

        # Suffix actions must be declared before the generic version GET.
        # Otherwise Starlette consumes ``<uuid>:compare`` as a version id.
        v2_resp = client.post(
            f"/api/v1/character-identity-packs/{pack_id}/versions",
            params={"from_version_id": v1_id},
        )
        assert v2_resp.status_code == 201
        v2_id = v2_resp.json()["version"]["id"]
        compare_resp = client.get(
            f"/api/v1/character-identity-pack-versions/{v1_id}:compare",
            params={"target_version_id": v2_id},
        )
        assert compare_resp.status_code == 200
        assert compare_resp.json()["comparison"]["base"]["id"] == v1_id
        assert compare_resp.json()["comparison"]["target"]["id"] == v2_id


def test_approval_requires_human_comment_complete_unique_views_and_authorizations(workspace, database) -> None:
    ctx = _setup_character_and_media(workspace, database)
    service = CharacterIdentityPackService(database)
    pack = service.create_pack(
        ctx["project_id"],
        ctx["character"]["id"],
        "FAIL_CLOSED",
        "门禁测试",
    )
    version_id = pack["versions"][0]["id"]

    with pytest.raises(DomainRuleError) as missing_comment:
        service.approve_pack_version(version_id)
    assert missing_comment.value.code == "PACK_APPROVAL_COMMENT_REQUIRED"

    with pytest.raises(DomainRuleError) as missing_slots:
        service.approve_pack_version(version_id, comment="人工审核，但素材不齐")
    assert missing_slots.value.code == "PACK_REQUIRED_SLOTS_MISSING"
    assert missing_slots.value.details["missing_slots"] == ["FRONT", "LEFT", "RIGHT"]

    service.set_version_slot(version_id, "FRONT", ctx["front_mv"])
    service.set_version_slot(version_id, "LEFT", ctx["front_mv"])
    service.set_version_slot(version_id, "RIGHT", ctx["right_mv"])
    draft = service.get_version(version_id)
    assert draft["approval_ready"] is False
    assert {item["code"] for item in draft["approval_blockers"]} == {"PACK_REQUIRED_MEDIA_DUPLICATED"}
    with pytest.raises(DomainRuleError) as duplicated:
        service.approve_pack_version(version_id, comment="不能把同一张图当两个视角")
    assert duplicated.value.code == "PACK_REQUIRED_MEDIA_DUPLICATED"

    unauthorized_id = _import_media(
        workspace,
        database,
        ctx["project_id"],
        "identity-unapproved.png",
        b"identity-pack-unapproved",
        authorize=False,
    )
    with pytest.raises(DomainRuleError) as unauthorized:
        service.set_version_slot(version_id, "LEFT", unauthorized_id)
    assert unauthorized.value.code == "IDENTITY_PACK_MEDIA_AUTHORIZATION_REQUIRED"


def test_slot_media_must_be_same_project_image_verified_and_currently_authorized(workspace, database) -> None:
    ctx = _setup_character_and_media(workspace, database)
    service = CharacterIdentityPackService(database)
    pack = service.create_pack(ctx["project_id"], ctx["character"]["id"], "MEDIA_GATES", "媒体门禁")
    version_id = pack["versions"][0]["id"]

    document_id = _import_media(
        workspace,
        database,
        ctx["project_id"],
        "identity-not-image.txt",
        b"not-an-image",
        media_kind="DOCUMENT",
    )
    with pytest.raises(DomainRuleError) as wrong_kind:
        service.set_version_slot(version_id, "FRONT", document_id)
    assert wrong_kind.value.code == "IDENTITY_PACK_MEDIA_KIND_INVALID"

    corrupt_id = _import_media(
        workspace,
        database,
        ctx["project_id"],
        "identity-corrupt.png",
        b"identity-pack-corrupt",
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE media_versions SET integrity_status='CORRUPT' WHERE id=?",
            (corrupt_id,),
        )
    with pytest.raises(DomainRuleError) as corrupt:
        service.set_version_slot(version_id, "FRONT", corrupt_id)
    assert corrupt.value.code == "IDENTITY_PACK_MEDIA_NOT_VERIFIED"

    revoked_id = _import_media(
        workspace,
        database,
        ctx["project_id"],
        "identity-revoked.png",
        b"identity-pack-revoked",
    )
    WorkspaceAssetService(database, workspace).revoke_authorization(
        ctx["project_id"],
        revoked_id,
        "素材授权被撤回",
    )
    with pytest.raises(DomainRuleError) as revoked:
        service.set_version_slot(version_id, "FRONT", revoked_id)
    assert revoked.value.code == "IDENTITY_PACK_MEDIA_AUTHORIZATION_INVALID"

    other_project = ProjectService(database, workspace.projects_root).create_project(
        code="id_pack_other",
        title="Other Project",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    cross_project_id = _import_media(
        workspace,
        database,
        str(other_project["id"]),
        "identity-other-project.png",
        b"identity-pack-other-project",
    )
    with pytest.raises(DomainRuleError) as project_mismatch:
        service.set_version_slot(version_id, "FRONT", cross_project_id)
    assert project_mismatch.value.code == "MEDIA_PROJECT_MISMATCH"


def test_only_current_approved_pack_binds_and_superseded_or_retired_versions_stay_immutable(workspace, database) -> None:
    ctx = _setup_character_and_media(workspace, database)
    service = CharacterIdentityPackService(database)
    shot_id = _create_shot(database, ctx["project_id"])
    pack = service.create_pack(ctx["project_id"], ctx["character"]["id"], "BINDING_GATES", "绑定门禁")
    draft = pack["versions"][0]
    with pytest.raises(DomainRuleError) as draft_binding:
        service.bind_shot_identity_pack(shot_id, ctx["character"]["id"], draft["id"])
    assert draft_binding.value.code == "APPROVED_IDENTITY_PACK_REQUIRED"

    for slot_kind, key in (("FRONT", "front_mv"), ("LEFT", "left_mv"), ("RIGHT", "right_mv")):
        service.set_version_slot(draft["id"], slot_kind, ctx[key])
    v1 = service.approve_pack_version(draft["id"], comment="人工批准 v1")
    service.bind_shot_identity_pack(shot_id, ctx["character"]["id"], v1["id"])

    v2 = service.create_version_draft(pack["id"], from_version_id=v1["id"])
    service.approve_pack_version(v2["id"], comment="人工批准 v2")
    assert service.get_version(v1["id"])["status"] == "SUPERSEDED"
    with pytest.raises(DomainRuleError) as stale_binding:
        service.bind_shot_identity_pack(shot_id, ctx["character"]["id"], v1["id"])
    assert stale_binding.value.code == "APPROVED_IDENTITY_PACK_REQUIRED"
    with pytest.raises(DomainRuleError) as superseded_mutation:
        service.remove_version_slot(v1["id"], "FRONT")
    assert superseded_mutation.value.code == "APPROVED_PACK_IMMUTABLE"

    retired = service.retire_pack_version(v2["id"], "新版定妆替代此版本")
    assert retired["status"] == "RETIRED"
    assert service.get_pack(pack["id"])["current_version_id"] is None
    with pytest.raises(DomainRuleError) as retired_mutation:
        service.set_version_slot(v2["id"], "FACE", ctx["front_mv"])
    assert retired_mutation.value.code == "APPROVED_PACK_IMMUTABLE"


def test_compare_impact_and_retire_preserve_traceable_history(workspace, database) -> None:
    ctx = _setup_character_and_media(workspace, database)
    service = CharacterIdentityPackService(database)
    pack, v1 = _create_approved_pack(ctx, database, code="IMPACT_PACK")
    shot_id = _create_shot(database, ctx["project_id"], "S_IMPACT")
    service.bind_shot_identity_pack(shot_id, ctx["character"]["id"], v1["id"])

    new_front = _import_media(
        workspace,
        database,
        ctx["project_id"],
        "identity-new-front.png",
        b"identity-pack-new-front",
    )
    v2 = service.create_version_draft(pack["id"], from_version_id=v1["id"])
    service.set_version_slot(v2["id"], "FRONT", new_front)
    comparison = service.compare_versions(v1["id"], v2["id"])
    assert comparison["has_changes"] is True
    assert comparison["slots"]["changed"] == [
        {
            "slot_kind": "FRONT",
            "before_media_version_id": ctx["front_mv"],
            "after_media_version_id": new_front,
        }
    ]

    impact = service.version_impact(v1["id"])
    assert impact["summary"]["shot_binding_count"] == 1
    assert impact["shots"][0]["shot_id"] == shot_id
    retired = service.retire_pack_version(v1["id"], "历史版本停止用于新生成")
    assert retired["status"] == "RETIRED"
    post_retire = service.version_impact(v1["id"])
    assert post_retire["summary"]["shot_binding_count"] == 1
    assert service.get_shot_character_packs(shot_id)[0]["is_stale"] is True

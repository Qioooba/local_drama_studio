from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from local_drama.application.dialogue import DialogueService
from local_drama.application.projects import ProjectService
from local_drama.infrastructure.database.shot_studio_command_repository import shot_studio_command_service
from local_drama.main import create_app
from tests.test_generation_variants import _project


def _context(workspace, database):
    project = _project(workspace, database, "semantic_shot_edit")
    service = ProjectService(database, workspace.projects_root)
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    first = service.create_shot(str(episode["id"]), "SHOT_001", 4000, "WIDE")
    second = service.create_shot(str(episode["id"]), "SHOT_002", 2000, "CLOSE")
    revision = shot_studio_command_service(database).save_draft_revision(
        str(first["id"]),
        {
            "source_ranges": [{"start": 10, "end": 30}],
            "director_intent": {"goal": "建立冲突"},
            "action": "角色冲向门口",
            "dialogue": "快走！",
        },
    )
    first = service.get_shot(str(first["id"]))
    now = "2026-08-20T00:00:00Z"
    asset_id = str(uuid.uuid4())
    character_id, state_id, pack_id, pack_version_id = (str(uuid.uuid4()) for _ in range(4))
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO story_assets
            (id,project_id,kind,code,name,description,extra_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'PROP','PROP_001','证物','','{}','ACTIVE',?,?,?,1,'v2')""",
            (asset_id, project["id"], now, now, "test"),
        )
        connection.execute(
            """INSERT INTO shot_asset_bindings
            (id,shot_id,asset_id,role_in_shot,created_at,created_by,revision,schema_version)
            VALUES (?,?,?,'evidence',?,'test',1,'v2')""",
            (str(uuid.uuid4()), first["id"], asset_id, now),
        )
        connection.execute(
            """INSERT INTO story_assets
            (id,project_id,kind,code,name,description,extra_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'CHARACTER','CHAR_SPLIT','拆镜角色','','{}','ACTIVE',?,?,?,1,'v2')""",
            (character_id, project["id"], now, now, "test"),
        )
        connection.execute(
            """INSERT INTO story_asset_states
            (id,project_id,story_asset_id,code,label,state_kind,description,state_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,'STATE_SPLIT','拆镜状态','OUTFIT','','{}','ACTIVE',?,?,'test',1,'v1')""",
            (state_id, project["id"], character_id, now, now),
        )
        connection.execute(
            """INSERT INTO character_identity_packs
            (id,project_id,story_asset_id,asset_state_id,code,name,description,status,current_version_id,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?, 'PACK_SPLIT','拆镜身份包','','ACTIVE',?, ?,?,'test',1,'v1')""",
            (pack_id, project["id"], character_id, state_id, pack_version_id, now, now),
        )
        connection.execute(
            """INSERT INTO character_identity_pack_versions
            (id,pack_id,project_id,story_asset_id,asset_state_id,version_no,status,slots_json,approval_metadata_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,1,'APPROVED','{}','{}',?,?,'test',1,'v1')""",
            (pack_version_id, pack_id, project["id"], character_id, state_id, now, now),
        )
        connection.execute(
            """INSERT INTO shot_asset_bindings
            (id,shot_id,asset_id,role_in_shot,asset_state_id,identity_pack_version_id,created_at,created_by,revision,schema_version)
            VALUES (?,?,?,'main',?,?,?,'test',1,'v2')""",
            (str(uuid.uuid4()), first["id"], character_id, state_id, pack_version_id, now),
        )
    dialogue = DialogueService(database, workspace).create_line(
        str(episode["id"]), code="DL-SPLIT", speaker="拆镜角色", text="快走！",
        pronunciation={}, shot_id=str(first["id"]),
    )
    return episode, first, second, revision, {
        "prop_id": asset_id,
        "character_id": character_id,
        "state_id": state_id,
        "pack_version_id": pack_version_id,
        "dialogue_id": str(dialogue["id"]),
    }


def test_semantic_reorder_and_split_preserve_lineage(workspace, database) -> None:
    episode, first, second, source_revision, refs = _context(workspace, database)
    episode_id = str(episode["id"])
    with TestClient(create_app(workspace)) as client:
        context = client.get(f"/api/v1/episodes/{episode_id}/shot-edit").json()["context"]
        reorder_payload = {
            "ordering_token": context["ordering_token"],
            "reorder": {"shot_id": second["id"], "before_shot_id": first["id"], "expected_revision": second["revision"]},
            "splits": [],
        }
        reorder_plan = client.post(f"/api/v1/episodes/{episode_id}/shot-edit:plan", json=reorder_payload).json()["plan"]
        assert reorder_plan["valid"] is True
        reordered = client.post(
            f"/api/v1/episodes/{episode_id}/shot-edit:commit",
            json={
                **reorder_payload,
                "expected_plan_hash": reorder_plan["plan_hash"],
            },
        )
        assert reordered.status_code == 200
        assert reordered.json()["result"]["ordered_shot_ids"] == [second["id"], first["id"]]

        context = client.get(f"/api/v1/episodes/{episode_id}/shot-edit").json()["context"]
        first_snapshot = next(item for item in context["items"] if item["id"] == first["id"])
        split_payload = {
            "ordering_token": context["ordering_token"],
            "reorder": None,
            "splits": [
                {
                    "shot_id": first["id"],
                    "expected_revision": first_snapshot["revision"],
                    "first_code": "SHOT_001-A",
                    "second_code": "SHOT_001-B",
                    "first_duration_ms": 1500,
                    "dialogue_destination": "FIRST",
                    "action_destination": "BOTH",
                }
            ],
        }
        split_plan = client.post(f"/api/v1/episodes/{episode_id}/shot-edit:plan", json=split_payload).json()["plan"]
        assert split_plan["valid"] is True
        assert split_plan["effects"] == {
            "timeline": "MARK_STALE",
            "selected_results": "NOT_COPIED",
            "asset_bindings": "COPY_DECLARATIVE_IDENTITY_AND_STATE_ONLY",
            "dialogue": "EXPLICIT_SINGLE_DESTINATION",
        }
        split = client.post(
            f"/api/v1/episodes/{episode_id}/shot-edit:commit",
            json={
                **split_payload,
                "expected_plan_hash": split_plan["plan_hash"],
            },
        )
        assert split.status_code == 200
        children = split.json()["result"]["created_shots"]
        assert [child["segment"] for child in children] == ["A", "B"]

    with database.connect() as connection:
        source = connection.execute("SELECT archived_at,revision,current_revision_id FROM shots WHERE id=?", (first["id"],)).fetchone()
        assert source["archived_at"] is not None
        assert source["revision"] == first_snapshot["revision"]
        assert source["current_revision_id"] == source_revision["id"]
        child_rows = connection.execute(
            "SELECT * FROM shots WHERE source_shot_id=? ORDER BY code",
            (first["id"],),
        ).fetchall()
        assert [row["target_duration_ms"] for row in child_rows] == [1500, 2500]
        for row in child_rows:
            fields = json.loads(
                connection.execute(
                    "SELECT fields_json FROM shot_revisions WHERE id=?",
                    (row["current_revision_id"],),
                ).fetchone()[0]
            )
            assert fields["source_ranges"] == [{"start": 10, "end": 30}]
            assert fields["director_intent"] == {"goal": "建立冲突"}
            assert fields["split_lineage"]["source_shot_id"] == first["id"]
            assert fields["action"] == "角色冲向门口"
            assert fields["dialogue"] == ("快走！" if str(row["code"]).endswith("-A") else "")
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM shot_asset_bindings WHERE shot_id=? AND asset_id=?",
                    (row["id"], refs["prop_id"]),
                ).fetchone()[0]
                == 1
            )
        child_bindings = connection.execute(
            """SELECT shot_id,asset_state_id,identity_pack_version_id FROM shot_asset_bindings
            WHERE shot_id IN (?,?) AND asset_id=? ORDER BY shot_id""",
            (child_rows[0]["id"], child_rows[1]["id"], refs["character_id"]),
        ).fetchall()
        assert len(child_bindings) == 2
        assert {str(row["asset_state_id"]) for row in child_bindings} == {refs["state_id"]}
        assert {str(row["identity_pack_version_id"]) for row in child_bindings} == {refs["pack_version_id"]}
        dialogue_shot = connection.execute(
            "SELECT shot_id FROM dialogue_lines WHERE id=?", (refs["dialogue_id"],),
        ).fetchone()[0]
        assert dialogue_shot == child_rows[0]["id"]
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM generation_intents WHERE owner_type='SHOT' AND owner_id IN (?,?)",
                (child_rows[0]["id"], child_rows[1]["id"]),
            ).fetchone()[0]
            == 0
        )
        actions = {
            row[0]
            for row in connection.execute(
                "SELECT action FROM audit_events WHERE subject_id IN (?,?)",
                (episode_id, first["id"]),
            )
        }
        assert {"SHOT_REORDERED", "SHOT_SPLIT", "SHOT_EDIT_PLAN_COMMITTED"} <= actions


def test_shot_edit_rejects_stale_ordering_token(workspace, database) -> None:
    episode, first, second, _, _ = _context(workspace, database)
    with TestClient(create_app(workspace)) as client:
        plan = client.post(
            f"/api/v1/episodes/{episode['id']}/shot-edit:plan",
            json={
                "ordering_token": "0" * 64,
                "reorder": {"shot_id": first["id"], "after_shot_id": second["id"], "expected_revision": first["revision"]},
                "splits": [],
            },
        ).json()["plan"]
        assert plan["valid"] is False
        assert plan["issues"][0]["code"] == "SHOT_ORDERING_STALE"


def test_split_cannot_copy_revoked_state_or_unapproved_identity_pack(workspace, database) -> None:
    episode, first, _second, _, refs = _context(workspace, database)
    with database.transaction() as connection:
        connection.execute("UPDATE story_asset_states SET status='RETIRED' WHERE id=?", (refs["state_id"],))
        connection.execute(
            "UPDATE character_identity_pack_versions SET status='DRAFT' WHERE id=?",
            (refs["pack_version_id"],),
        )
    with TestClient(create_app(workspace)) as client:
        context = client.get(f"/api/v1/episodes/{episode['id']}/shot-edit").json()["context"]
        first_snapshot = next(item for item in context["items"] if item["id"] == first["id"])
        plan = client.post(
            f"/api/v1/episodes/{episode['id']}/shot-edit:plan",
            json={
                "ordering_token": context["ordering_token"],
                "reorder": None,
                "splits": [{
                    "shot_id": first["id"],
                    "expected_revision": first_snapshot["revision"],
                    "first_code": "SHOT_001-A",
                    "second_code": "SHOT_001-B",
                    "first_duration_ms": 1500,
                    "dialogue_destination": "SOURCE_ONLY",
                    "action_destination": "SOURCE_ONLY",
                }],
            },
        ).json()["plan"]

    assert plan["valid"] is False
    assert {issue["code"] for issue in plan["issues"]} >= {
        "SHOT_SPLIT_ASSET_STATE_INVALID",
        "SHOT_SPLIT_IDENTITY_PACK_INVALID",
    }

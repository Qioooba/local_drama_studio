from __future__ import annotations

import sqlite3

from scripts.refactor_invariants import audit


def test_release_invariants_cover_0044_through_0048_and_pass_at_head(database) -> None:
    result = audit(database.path)
    checks = {item["code"]: item for item in result["checks"]}
    expected = {
        "SHOT_SCENE_SCOPE",
        "SHOT_SPLIT_LINEAGE",
        "SHOT_GROUP_SCOPE",
        "QC_POLICY_SCOPE",
        "QC_POLICY_VERSION_LINEAGE",
        "VARIANT_QC_LINEAGE",
        "DIRECTOR_RECIPE_SCOPE",
        "VARIANT_DIRECTOR_RECIPE",
        "ASSET_PROPOSAL_SCOPE",
        "CHARACTER_IDENTITY_PACK_SCOPE",
    }

    assert result["alembic_revision"] == "0057_provider_connections"
    assert expected <= checks.keys()
    assert {checks[code]["status"] for code in expected} == {"PASS"}
    assert result["mode"] == "READ_ONLY"
    assert result["status"] == "PASS"


def test_release_invariants_report_cross_episode_shot_group_member(database) -> None:
    now = "2026-08-20T00:00:00Z"
    with sqlite3.connect(database.path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """INSERT INTO shot_groups
            (id,episode_id,kind,code,title,order_key,metadata_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('bad-group','missing-episode','BEAT','BAD','Bad','1','{}','ACTIVE',?,?, 'test',1,'v1')""",
            (now, now),
        )
    result = audit(database.path)
    check = next(item for item in result["checks"] if item["code"] == "SHOT_GROUP_SCOPE")
    assert check["status"] == "FAIL"
    assert check["violation_count"] == 1
    assert check["samples"][0]["group_id"] == "bad-group"

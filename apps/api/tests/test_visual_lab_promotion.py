from __future__ import annotations

from pathlib import Path

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.visual_labs import VisualLabService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.shot_studio_command_repository import SqliteShotStudioCommandRepository
from local_drama.infrastructure.database.shot_studio_repository import SqliteShotStudioReadRepository

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082")


def _project_with_shot(workspace, database, code: str):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=10_000, allow_unconfigured_capabilities=True,
    )
    with database.connect() as connection:
        episode_id = str(
            connection.execute(
                """SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? LIMIT 1""",
                (project["id"],),
            ).fetchone()[0]
        )
    shot = projects.create_shot(episode_id, "SH-001", 4_000)
    return projects, project, episode_id, shot


def _verified_image(workspace, database, project_id: str) -> str:
    source_path = Path(workspace.work_root) / "lab-output.png"
    source_path.write_bytes(PNG)
    imported = MediaService(database, workspace).import_file(project_id, source_path, media_kind="IMAGE")
    return str(imported["media_version_id"])


def _lab_with_output_node(workspace, database, project_id: str, episode_id: str):
    lab = VisualLabService(database, workspace)
    document = lab.create(project_id, "look-dev", "Look Development", episode_id)
    node = lab.add_node(
        str(document["id"]),
        {
            "node_kind": "TEXT_REF", "position_x": 10, "position_y": 20, "width": 260, "height": 180,
            "content": {"title": "情绪", "body": "雨夜"}, "expected_topology_revision": 1,
        },
    )
    return lab, document, node


def test_visual_lab_promotion_records_shot_lineage_and_not_selection(workspace, database) -> None:
    projects, project, episode_id, shot = _project_with_shot(workspace, database, "lab_promote")
    media_version_id = _verified_image(workspace, database, str(project["id"]))
    lab, document, node = _lab_with_output_node(workspace, database, str(project["id"]), episode_id)

    plan = lab.promotion_preflight(str(node["id"]), media_version_id, "SHOT_CANDIDATE", str(shot["id"]))
    assert plan["status"] == "READY", plan["blockers"]
    assert plan["snapshot"]["target_type"] == "SHOT_CANDIDATE"

    result = lab.promote(str(node["id"]), media_version_id, "SHOT_CANDIDATE", str(shot["id"]), plan["plan_hash"])

    assert result["target_shot_id"] == str(shot["id"])
    assert result["approved"] is False
    assert result["mutated"] is True

    with database.connect() as connection:
        promoted_rows = connection.execute(
            "SELECT * FROM visual_lab_promotions WHERE node_id=?",
            (str(node["id"]),),
        ).fetchall()
        selection_rows = connection.execute(
            "SELECT COUNT(*) AS n FROM selections WHERE selection_type='LAB_ADOPTED_CANDIDATE'",
        ).fetchone()

    assert len(promoted_rows) == 1
    row = dict(promoted_rows[0])
    assert row["document_id"] == str(document["id"])
    assert row["source_media_version_id"] == media_version_id
    assert row["target_type"] == "SHOT_CANDIDATE"
    assert row["target_id"] == str(shot["id"])
    assert row["result_subject_type"] == "SHOT"
    assert row["result_subject_id"] == str(shot["id"])
    # The retired per-asset selections table must not receive a stray LAB adoption.
    assert selection_rows["n"] == 0


def test_visual_lab_promotion_rejects_stale_plan_and_cross_project_target(workspace, database) -> None:
    projects, project, episode_id, shot = _project_with_shot(workspace, database, "lab_promote_reject")
    media_version_id = _verified_image(workspace, database, str(project["id"]))
    lab, document, node = _lab_with_output_node(workspace, database, str(project["id"]), episode_id)

    plan = lab.promotion_preflight(str(node["id"]), media_version_id, "SHOT_CANDIDATE", str(shot["id"]))
    assert plan["status"] == "READY"

    with pytest.raises(DomainRuleError) as error:
        lab.promote(str(node["id"]), media_version_id, "SHOT_CANDIDATE", str(shot["id"]), "0" * 64)
    assert error.value.code == "PLAN_HASH_MISMATCH"

    # A shot inside a different project must be rejected before any write.
    other_project = ProjectService(database, workspace.projects_root).create_project(
        code="other", title="Other", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=10_000, allow_unconfigured_capabilities=True,
    )
    with database.connect() as connection:
        other_episode_id = str(
            connection.execute(
                """SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? LIMIT 1""",
                (other_project["id"],),
            ).fetchone()[0]
        )
    other_shot = ProjectService(database, workspace.projects_root).create_shot(other_episode_id, "SH-002", 4_000)

    cross_plan = lab.promotion_preflight(
        str(node["id"]), media_version_id, "SHOT_CANDIDATE", str(other_shot["id"])
    )
    assert cross_plan["status"] == "BLOCKED"
    assert any(blocker["code"] == "PROJECT_SCOPE_MISMATCH" for blocker in cross_plan["blockers"])

    with pytest.raises(DomainRuleError) as blocked:
        lab.promote(str(node["id"]), media_version_id, "SHOT_CANDIDATE", str(other_shot["id"]), cross_plan["plan_hash"])
    assert blocked.value.code == "VISUAL_LAB_PROMOTION_BLOCKED"

    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM visual_lab_promotions WHERE node_id=?",
            (str(node["id"]),),
        ).fetchone()
    assert count["n"] == 0


def test_visual_lab_output_surfaces_as_shot_candidate_not_selected_or_approved(workspace, database) -> None:
    projects, project, episode_id, shot = _project_with_shot(workspace, database, "lab_candidate")
    media_version_id = _verified_image(workspace, database, str(project["id"]))
    shot_candidate = MediaService(database, workspace).create_keyframe_candidate(media_version_id, str(shot["id"]))
    lab, document, node = _lab_with_output_node(workspace, database, str(project["id"]), episode_id)
    plan = lab.promotion_preflight(
        str(node["id"]), str(shot_candidate["media_version_id"]), "SHOT_CANDIDATE", str(shot["id"])
    )
    assert plan["status"] == "READY", plan["blockers"]
    lab.promote(str(node["id"]), str(shot_candidate["media_version_id"]), "SHOT_CANDIDATE", str(shot["id"]), plan["plan_hash"])

    facts = SqliteShotStudioReadRepository(database).studio_facts(episode_id, str(shot["id"]))
    lab_candidates = [
        candidate for candidate in facts["current_shot"]["candidates"]
        if candidate.get("variant_type") == "VISUAL_LAB"
    ]
    assert lab_candidates, "Lab output should surface as a shot candidate"
    lab_candidate = lab_candidates[0]
    assert lab_candidate["media_version_id"] == str(shot_candidate["media_version_id"])
    assert lab_candidate["id"].startswith("lab-")
    assert lab_candidate["selected"] is False
    assert lab_candidate["approved"] is False
    assert lab_candidate["candidate_source"] == "VISUAL_LAB"


def test_visual_lab_candidate_is_adoptable_via_promotion_fallback(workspace, database) -> None:
    projects, project, episode_id, shot = _project_with_shot(workspace, database, "lab_adopt")
    # A project-owned KEYFRAME image is NOT owned by the shot, so the standard
    # shot-resolution path fails and the promotion fallback must resolve it.
    source_path = Path(workspace.work_root) / "lab-adopt.png"
    source_path.write_bytes(PNG)
    imported = MediaService(database, workspace).import_file(
        str(project["id"]), source_path, media_kind="IMAGE", stage="KEYFRAME"
    )

    lab, document, node = _lab_with_output_node(workspace, database, str(project["id"]), episode_id)
    plan = lab.promotion_preflight(
        str(node["id"]), str(imported["media_version_id"]), "SHOT_CANDIDATE", str(shot["id"])
    )
    assert plan["status"] == "READY", plan["blockers"]
    lab.promote(str(node["id"]), str(imported["media_version_id"]), "SHOT_CANDIDATE", str(shot["id"]), plan["plan_hash"])

    adoption = SqliteShotStudioCommandRepository(database).adopt_working_version(str(imported["media_version_id"]), actor="local-user")
    assert adoption["shot_id"] == str(shot["id"])
    assert adoption["slot_type"] == "KEYFRAME"
    assert adoption["selection_type"] == "KEYFRAME"

    with database.connect() as connection:
        slot_count = connection.execute(
            "SELECT COUNT(*) AS n FROM shot_working_media_slots WHERE shot_id=? AND slot_type='KEYFRAME'",
            (str(shot["id"]),),
        ).fetchone()
    assert slot_count["n"] == 1

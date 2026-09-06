from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.episode_replan import EpisodeReplanService
from local_drama.application.projects import ProjectService
from local_drama.domain.duration import TARGET_DURATION_TECHNICAL_TOLERANCE_MS
from local_drama.infrastructure.database.episode_production_repository import (
    SqliteEpisodeProductionReadRepository,
)
from local_drama.main import create_app
from tests.test_generation_variants import _project


def _draft_row(
    workspace,
    database,
    project,
    episode,
    imported,
    *,
    target_seconds: int,
    start: int = 1,
    end: int = 1,
    shot_count: int = 2,
    stamp: int = 0,
    with_dialogue: bool = False,
) -> str:
    draft_id = str(uuid.uuid4())
    now = (datetime.now(UTC) + timedelta(seconds=stamp)).isoformat()
    shots = [
        {
            "shot_no": index,
            "visual": f"重规划镜头 {index}",
            "action": "角色继续前进",
            "dialogue": f"旁白：重规划对白 {index}" if with_dialogue else "",
            "duration_seconds": target_seconds / shot_count,
        }
        for index in range(1, shot_count + 1)
    ]
    payload = {"scenes": [{"scene_no": 1, "title": "本集场景", "summary": "本集重规划", "shots": shots}]}
    confidence = {
        "target_episode_id": str(episode["id"]),
        "target_duration_seconds": target_seconds,
        "source_paragraph_start": start,
        "source_paragraph_end": end,
        "source_passages": [],
        "questions": [],
    }
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts
            (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,status,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,'DRAFT_READY',?,?, 'test',1,'v2')""",
            (
                draft_id,
                str(project["id"]),
                str(imported["source_document_version_id"]),
                str(imported["import_session_id"]),
                json.dumps(payload, ensure_ascii=False),
                json.dumps(confidence, ensure_ascii=False),
                now,
                now,
            ),
        )
    return draft_id


def _context(workspace, database):
    project = _project(workspace, database, f"episode_replan_{uuid.uuid4().hex[:8]}")
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    source = workspace.work_root / f"episode-replan-{uuid.uuid4().hex[:8]}.md"
    source.write_text("# 本集原文\n\n角色进入房间。", encoding="utf-8")
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE episodes SET target_duration_ms=120000,source_range_json=? WHERE id=?",
            (json.dumps({"start_paragraph": 1, "end_paragraph": 1}), str(episode["id"])),
        )
    return project, episode, imported


def test_replan_ignores_historical_target_or_source_drafts(workspace, database) -> None:
    project, episode, imported = _context(workspace, database)
    # A 60-second result and a 120-second result from another source range are
    # both historical DRAFT_READY rows. Neither can be presented for this 120s
    # episode until a matching candidate exists.
    _draft_row(workspace, database, project, episode, imported, target_seconds=60, stamp=1)
    _draft_row(workspace, database, project, episode, imported, target_seconds=120, start=2, end=2, stamp=2)
    service = EpisodeReplanService(database, workspace)
    assert service.plan(str(episode["id"])) == {"status": "NOT_READY", "episode_id": str(episode["id"]), "job": None}

    current_id = _draft_row(workspace, database, project, episode, imported, target_seconds=120, stamp=3)
    plan = service.plan(str(episode["id"]))
    assert plan["status"] == "DRAFT_READY"
    assert plan["draft_id"] == current_id
    assert plan["target_duration_ms"] == 120000


@pytest.mark.parametrize(
    ("planned_delta_ms", "replan_required"),
    [
        (6_000, True),
        (TARGET_DURATION_TECHNICAL_TOLERANCE_MS, False),
        (TARGET_DURATION_TECHNICAL_TOLERANCE_MS + 1, True),
    ],
)
def test_replan_duration_mismatch_uses_shared_technical_tolerance(
    workspace,
    database,
    planned_delta_ms: int,
    replan_required: bool,
) -> None:
    _project, episode, _imported = _context(workspace, database)
    target_ms = 120_000
    ProjectService(database, workspace.projects_root).create_shot(
        str(episode["id"]),
        "S001",
        target_ms - planned_delta_ms,
        "STANDARD",
    )

    service = EpisodeReplanService(database, workspace)
    replan_overview = service.overview(str(episode["id"]))
    production_overview = SqliteEpisodeProductionReadRepository(database).overview_facts(
        str(episode["id"])
    )

    assert replan_overview["target_duration_ms"] == target_ms
    assert replan_overview["planned_duration_ms"] == target_ms - planned_delta_ms
    assert production_overview["target_duration_ms"] == target_ms
    assert production_overview["planned_duration_ms"] == target_ms - planned_delta_ms
    assert replan_overview["replan_required"] is replan_required
    assert production_overview["replan_required"] is replan_required
    mismatch_codes = {
        item["code"]
        for item in replan_overview["replan_reasons"]
        if item["code"] == "TARGET_DURATION_MISMATCH"
    }
    assert ("TARGET_DURATION_MISMATCH" in mismatch_codes) is replan_required
    assert (
        "TARGET_DURATION_MISMATCH"
        in {item["code"] for item in production_overview["replan_reasons"]}
    ) is replan_required


def test_replan_replaces_frozen_history_and_generates_unique_codes_on_repeat(workspace, database) -> None:
    project, episode, imported = _context(workspace, database)
    projects = ProjectService(database, workspace.projects_root)
    frozen = projects.create_shot(str(episode["id"]), "S001", 60000, "STANDARD")
    with database.transaction() as connection:
        connection.execute("UPDATE shot_revisions SET is_frozen=1 WHERE id=?", (str(frozen["current_revision_id"]),))

    service = EpisodeReplanService(database, workspace)
    first_draft = _draft_row(workspace, database, project, episode, imported, target_seconds=120, shot_count=2, stamp=1)
    first_plan = service.plan(str(episode["id"]))
    assert first_plan["draft_id"] == first_draft
    assert first_plan["valid"] is True
    assert any(item["action"] == "REPLACE_PROTECTED" for item in first_plan["diff"])
    first = service.apply(
        str(episode["id"]),
        expected_episode_revision=int(first_plan["expected_episode_revision"]),
        expected_plan_hash=str(first_plan["plan_hash"]),
        idempotency_key="episode-replan-first",
    )
    assert first["status"] == "APPLIED"
    assert first["historical_media_preserved"] is True
    assert str(frozen["id"]) in first["archived_shot_ids"] or str(frozen["id"]) in first["protected_shot_ids"]
    repeated = service.apply(
        str(episode["id"]),
        expected_episode_revision=int(first_plan["expected_episode_revision"]),
        expected_plan_hash=str(first_plan["plan_hash"]),
        idempotency_key="episode-replan-first",
    )
    assert repeated == first

    # Draft revisions are local to their immutable draft rows. A later draft
    # may therefore reuse revision 1; generated shot codes must still be
    # unique within the episode.
    second_draft = _draft_row(workspace, database, project, episode, imported, target_seconds=120, shot_count=3, stamp=4)
    second_plan = service.plan(str(episode["id"]))
    assert second_plan["draft_id"] == second_draft
    second = service.apply(
        str(episode["id"]),
        expected_episode_revision=int(second_plan["expected_episode_revision"]),
        expected_plan_hash=str(second_plan["plan_hash"]),
        idempotency_key="episode-replan-second",
    )
    assert second["status"] == "APPLIED"
    with database.connect() as connection:
        rows = connection.execute("SELECT code,archived_at FROM shots WHERE episode_id=?", (str(episode["id"]),)).fetchall()
        codes = [str(row["code"]) for row in rows]
        assert len(codes) == len(set(codes))
        assert connection.execute("SELECT is_frozen FROM shot_revisions WHERE id=?", (str(frozen["current_revision_id"]),)).fetchone()[0] == 1
        assert connection.execute("SELECT archived_at FROM shots WHERE id=?", (str(frozen["id"]),)).fetchone()[0] is not None


def test_replan_apply_eight_modifications_and_three_additions(workspace, database) -> None:
    """Regression for the production-sized apply path seen in the UI."""
    project, episode, imported = _context(workspace, database)
    projects = ProjectService(database, workspace.projects_root)
    for index in range(1, 9):
        projects.create_shot(str(episode["id"]), f"S{index:03d}", 7500, "STANDARD")
    draft_id = _draft_row(workspace, database, project, episode, imported, target_seconds=120, shot_count=11, stamp=1)
    service = EpisodeReplanService(database, workspace)
    plan = service.plan(str(episode["id"]))
    assert plan["draft_id"] == draft_id
    assert plan["summary"]["MODIFY"] == 8
    assert plan["summary"]["ADD"] == 3
    assert plan["valid"] is True

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v2/episodes/{episode['id']}/production:replan:apply",
            json={
                "expected_episode_revision": int(plan["expected_episode_revision"]),
                "expected_plan_hash": str(plan["plan_hash"]),
                "idempotency_key": "episode-replan-eight-three",
            },
        )
    assert response.status_code == 200, response.text
    result = response.json()["apply"]
    assert result["status"] == "APPLIED"
    assert len(result["modified_shot_ids"]) == 8
    assert len(result["created_shot_ids"]) == 3
    assert len(result["modified_shot_ids"]) + len(result["created_shot_ids"]) == 11


def test_replan_apply_eight_modifications_and_three_additions_with_dialogue(workspace, database) -> None:
    """Exercise the dialogue-line insert path used by real AI breakdowns."""
    project, episode, imported = _context(workspace, database)
    projects = ProjectService(database, workspace.projects_root)
    current_shots = []
    for index in range(1, 9):
        current_shots.append(projects.create_shot(str(episode["id"]), f"S{index:03d}", 7500, "STANDARD"))
    # Existing dialogue lines represent a prior approved breakdown.  The
    # replan must append text revisions for these MODIFY shots while creating
    # fresh lines for the three ADD shots.
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        for index, shot in enumerate(current_shots, start=1):
            line_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO dialogue_lines
                (id,episode_id,shot_id,code,speaker,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?, ?,1,'v2')""",
                (line_id, str(episode["id"]), str(shot["id"]), f"S{index:03d}-DL-001", "旁白", now, now, "test"),
            )
            connection.execute(
                """INSERT INTO dialogue_text_revisions
                (id,dialogue_line_id,revision_no,text,pronunciation_json,text_hash,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,?,'{}',?,?,?, ?,1,'v2')""",
                (str(uuid.uuid4()), line_id, f"既有对白 {index}", "seed", now, now, "test"),
            )
    draft_id = _draft_row(
        workspace,
        database,
        project,
        episode,
        imported,
        target_seconds=120,
        shot_count=11,
        stamp=1,
        with_dialogue=True,
    )
    service = EpisodeReplanService(database, workspace)
    plan = service.plan(str(episode["id"]))
    assert plan["draft_id"] == draft_id

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v2/episodes/{episode['id']}/production:replan:apply",
            json={
                "expected_episode_revision": int(plan["expected_episode_revision"]),
                "expected_plan_hash": str(plan["plan_hash"]),
                "idempotency_key": "episode-replan-eight-three-dialogue",
            },
        )
    assert response.status_code == 200, response.text
    result = response.json()["apply"]
    assert result["status"] == "APPLIED"
    assert len(result["modified_shot_ids"]) == 8
    assert len(result["created_shot_ids"]) == 3
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM dialogue_lines WHERE episode_id=?",
            (str(episode["id"]),),
        ).fetchone()[0] == 11
        assert connection.execute(
            "SELECT COUNT(*) FROM dialogue_text_revisions WHERE dialogue_line_id IN "
            "(SELECT id FROM dialogue_lines WHERE episode_id=?)",
            (str(episode["id"]),),
        ).fetchone()[0] == 19

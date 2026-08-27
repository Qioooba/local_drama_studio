from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.search import SearchService
from local_drama.infrastructure.database.search_repository import SqliteSearchRepository
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app


def _project(workspace, database, code: str) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code, title=f"{code} title", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def test_search_is_read_only_bounded_scoped_and_returns_navigable_semantics(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database, "needle_project")
    other = _project(workspace, database, "other_project")
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "NEEDLE_SHOT", 1_000)
    scene = projects.create_scene(str(project["id"]), "NEEDLE_SCENE", "Needle scene", "Studio", "DAY")
    now = "2026-08-20T00:00:00+00:00"

    # Lifespan seeds built-in profile/review metadata before the write-count baseline.
    with TestClient(create_app(workspace)) as client:
        with database.connect() as connection:
            profile_id = str(connection.execute("SELECT id FROM execution_profile_versions ORDER BY id LIMIT 1").fetchone()["id"])
        intent = GenerationService(database, workspace).create_intent(
            str(project["id"]), "SHOT", str(shot["id"]), "I2V", "Needle intent",
        )
        variant_id = str(uuid.uuid4())
        asset_id = str(uuid.uuid4())
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO story_assets
                (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,'CHARACTER','NEEDLE_HERO','Needle Hero','semantic search asset',NULL,'{}','ACTIVE',?,?, 'test',1,'v2')""",
                (asset_id, project["id"], now, now),
            )
            connection.execute(
                """INSERT INTO generation_variants
                (id,intent_id,variant_no,variant_type,parent_variant_id,branch_reason,prompt_revision_id,
                 capability_profile_version_id,parameter_set_json,seed_policy,explicit_seed,input_fingerprint,
                 recipe_hash,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,'BASE',NULL,'Needle candidate',NULL,?,'{}','EXPLICIT',7,?,?,'DRAFT',?,?,'test',1,'v2')""",
                (variant_id, intent["id"], profile_id, "a" * 64, "b" * 64, now, now),
            )
        source = workspace.work_root / "needle-search.txt"
        source.write_text("metadata fixture", encoding="utf-8")
        media = MediaService(database, workspace).import_file(
            str(project["id"]), source, purpose="SEARCH_FIXTURE", owner_type="GENERATION_VARIANT",
            owner_id=variant_id, media_kind="DOCUMENT", stage="IMPORTED",
        )
        review_id = str(uuid.uuid4())
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO review_decisions
                (id,subject_type,subject_id,review_template_version_id,decision,comment,supersedes_decision_id,
                 subject_revision,is_stale,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,'MEDIA_VERSION',?,'search-fixture','NEEDS_CHANGES','Needle review',NULL,1,0,?,?, 'test',1,'v2')""",
                (review_id, media["media_version_id"], now, now),
            )
        job = JobService(database, workspace).create_job(
            str(project["id"]), "NEEDLE_JOB", "SHOT", str(shot["id"]), "CPU",
            {"search_fixture": True}, "needle-search-job",
        )
        JobService(database, workspace).create_job(
            str(other["id"]), "NEEDLE_OTHER_JOB", "PROJECT", str(other["id"]), "CPU",
            {"search_fixture": True}, "needle-other-job",
        )

        with database.connect() as connection:
            connection.execute("DELETE FROM fts_search")
            fts_before = int(connection.execute("SELECT COUNT(*) FROM fts_search").fetchone()[0])
        monkeypatch.setattr(
            database, "transaction",
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError("search GET must not open a write transaction")),
        )
        response = client.get(f"/api/v1/search?q=needle&project_id={project['id']}&limit=500")
        assert response.status_code == 200
        items = response.json()["items"]
        with database.connect() as connection:
            assert int(connection.execute("SELECT COUNT(*) FROM fts_search").fetchone()[0]) == fts_before == 0

    assert len(items) <= 200
    by_type = {item["subject_type"]: item for item in items}
    assert {"SCENE", "SHOT", "STORY_ASSET", "GENERATION_VARIANT", "REVIEW", "JOB"} <= set(by_type)
    assert by_type["SCENE"]["subject_id"] == scene["id"]
    assert by_type["GENERATION_VARIANT"]["subject_id"] == variant_id
    assert by_type["REVIEW"]["subject_id"] == review_id
    assert by_type["JOB"]["subject_id"] == job["id"]
    assert all(item["project_id"] == project["id"] for item in items)
    assert all(item["route"].startswith(("/projects/", "/jobs?")) for item in items)
    assert all(item["label"] and item["context"] and "rel_path" not in item for item in items)


def test_search_tolerates_pre_extension_schema_without_rebuilding_index(tmp_path: Path) -> None:
    database = Database(tmp_path / "old-search.sqlite3")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE projects (id TEXT PRIMARY KEY,code TEXT NOT NULL,title TEXT NOT NULL)")
        connection.execute("CREATE VIRTUAL TABLE fts_search USING fts5(project_id,subject_type,subject_id,content)")
        connection.execute("INSERT INTO projects VALUES ('p-old','OLD_NEEDLE','Old Needle Project')")
    results = SearchService(SqliteSearchRepository(database)).search(
        "needle", project_id="p-old", limit=999
    )
    assert results == [{
        "project_id": "p-old", "subject_type": "PROJECT", "subject_id": "p-old",
        "label": "OLD_NEEDLE · Old Needle Project", "context": "项目",
        "route": "/projects/p-old", "snippet": "OLD_NEEDLE · Old Needle Project · 项目",
    }]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM fts_search").fetchone()[0] == 0

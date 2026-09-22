"""BKT-08 regression: required built-in initialization is decoupled from the optional model manifest.

The old lifespan put the optional local model-manifest sync and the REQUIRED
built-in review-template seeding inside one ``try``.  A missing or corrupt
``model_manifest.json`` raised first, the single ``except`` swallowed it, and
``ReviewService.ensure_templates`` never ran, so a fresh database ended up with
zero review templates and imported media became unreviewable.

These tests drive a real FastAPI ``lifespan`` against a genuinely fresh
migrated SQLite database whose built-in review templates have not been seeded
yet, under three manifest conditions: absent, corrupt, and valid.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scripts.migrate import migrate

from local_drama.application.reviews import TEMPLATES
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import (
    INIT_STEP_DATABASE_SCHEMA,
    INIT_STEP_MODEL_MANIFEST,
    INIT_STEP_REVIEW_TEMPLATES,
    create_app,
)

EXPECTED_TEMPLATE_CODES = {str(template["code"]) for template in TEMPLATES}


def _workspace(tmp_path: Path, manifest_path: Path) -> Settings:
    workspace = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    return workspace.model_copy(update={"model_manifest_override": manifest_path})


def _valid_manifest(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "manifest_type": "canonical_model_inventory",
                "manifest_version": "2026-09-22",
                "read_only_inventory": True,
                "canonical_model_root": {"path": str(path.parent / "models")},
                "runtime": {
                    "comfyui_api": {
                        "base_url": "http://127.0.0.1:8188",
                        "port_8188_listening": False,
                    }
                },
                "authoritative_current_state": {
                    "worker_policy": "LOCAL_ONLY",
                    "route_status": {},
                },
                "h3_capabilities": {},
                "models": {"partitions": {}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _fresh_workspace(tmp_path: Path, manifest_path: Path) -> Settings:
    """Return a workspace with a genuinely fresh migrated database and zero templates.

    The database is freshly migrated by the release migration chain and then
    emptied of review templates, which is exactly the state a first real
    startup observes: a migrated schema plus no built-in seed rows yet (the
    local ``database`` fixture in ``conftest.py`` also stops before startup
    seeding, so tests never inherit another startup's seed).
    """
    workspace = _workspace(tmp_path, manifest_path)
    workspace.ensure_roots()
    workspace.database_path.unlink(missing_ok=True)
    assert not workspace.database_path.exists()
    migrate(workspace.database_path)
    with sqlite3.connect(workspace.database_path) as connection:
        connection.execute("DELETE FROM review_templates")
        remaining = connection.execute("SELECT COUNT(*) FROM review_templates").fetchone()[0]
    assert int(remaining) == 0
    return workspace


def _template_codes(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT code FROM review_templates").fetchall()
    return {str(row[0]) for row in rows}


def _template_rows(database_path: Path) -> list[tuple[str, int, str, str]]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT code, version_no, items_json, created_by FROM review_templates ORDER BY code, version_no"
        ).fetchall()
    return [(str(row[0]), int(row[1]), str(row[2]), str(row[3])) for row in rows]


@pytest.mark.parametrize("manifest_case", ["missing", "corrupt", "valid"])
def test_fresh_database_lists_builtin_review_templates_for_every_manifest_case(tmp_path: Path, manifest_case: str) -> None:
    manifest_path = tmp_path / "model_manifest.json"
    if manifest_case == "corrupt":
        manifest_path.write_text("{ this is not valid json", encoding="utf-8")
    elif manifest_case == "valid":
        _valid_manifest(manifest_path)
    workspace = _fresh_workspace(tmp_path, manifest_path)

    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/review-templates")

    assert response.status_code == 200
    codes = {str(item["code"]) for item in response.json()["items"]}
    assert codes == EXPECTED_TEMPLATE_CODES
    assert len(EXPECTED_TEMPLATE_CODES) == 6


def test_missing_model_manifest_does_not_skip_required_review_template_seeding(tmp_path: Path) -> None:
    workspace = _fresh_workspace(tmp_path, tmp_path / "absent_model_manifest.json")

    with TestClient(create_app(workspace)) as client:
        report = client.app.state.readiness
        rows = _template_rows(workspace.database_path)

    assert _template_codes(workspace.database_path) == EXPECTED_TEMPLATE_CODES
    # The optional manifest step is what failed, and it is recorded on its own.
    assert report.step(INIT_STEP_MODEL_MANIFEST) is not None
    manifest_step = report.step(INIT_STEP_MODEL_MANIFEST)
    assert manifest_step is not None and manifest_step.status == "failed"
    assert manifest_step.required is False
    # Required initialization succeeded, so a model being unconfigured cannot
    # make the application not-servable.
    assert report.step(INIT_STEP_REVIEW_TEMPLATES) is not None
    template_step = report.step(INIT_STEP_REVIEW_TEMPLATES)
    assert template_step is not None and template_step.status == "completed"
    assert report.ready is True
    assert len(rows) == len(EXPECTED_TEMPLATE_CODES)


def test_corrupt_model_manifest_still_seeds_templates(tmp_path: Path) -> None:
    manifest_path = tmp_path / "model_manifest.json"
    manifest_path.write_text("not-json-at-all", encoding="utf-8")
    workspace = _fresh_workspace(tmp_path, manifest_path)

    with TestClient(create_app(workspace)) as client:
        report = client.app.state.readiness
        templates = client.get("/api/v1/review-templates").json()["items"]

    assert len(templates) == 6
    manifest_step = report.step(INIT_STEP_MODEL_MANIFEST)
    template_step = report.step(INIT_STEP_REVIEW_TEMPLATES)
    assert manifest_step is not None and manifest_step.status == "failed"
    assert manifest_step.error_type == "ManifestValidationError"
    assert template_step is not None and template_step.status == "completed"


def test_valid_model_manifest_records_both_optional_and_required_steps(tmp_path: Path) -> None:
    manifest_path = _valid_manifest(tmp_path / "model_manifest.json")
    workspace = _fresh_workspace(tmp_path, manifest_path)

    with TestClient(create_app(workspace)) as client:
        report = client.app.state.readiness
        synced = client.app.state.manifest_sync

    manifest_step = report.step(INIT_STEP_MODEL_MANIFEST)
    schema_step = report.step(INIT_STEP_DATABASE_SCHEMA)
    assert manifest_step is not None and manifest_step.status == "completed"
    assert schema_step is not None and schema_step.required is True
    assert synced is not None
    with sqlite3.connect(workspace.database_path) as connection:
        runtimes = connection.execute("SELECT COUNT(*) FROM local_runtimes WHERE code='comfyui-h3-local'").fetchone()[0]
    assert int(runtimes) == 1
    assert _template_codes(workspace.database_path) == EXPECTED_TEMPLATE_CODES


def test_repeated_startup_is_idempotent_and_preserves_published_template_versions(tmp_path: Path) -> None:
    manifest_path = _valid_manifest(tmp_path / "model_manifest.json")
    workspace = _fresh_workspace(tmp_path, manifest_path)

    with TestClient(create_app(workspace)):
        pass
    first = _template_rows(workspace.database_path)
    assert {code for code, _version, _items, _actor in first} == EXPECTED_TEMPLATE_CODES

    # A user-appended version must survive the next startup untouched.
    with TestClient(create_app(workspace)) as client:
        created = client.post(
            "/api/v1/review-templates",
            json={
                "code": "image_asset",
                "subject_type": "MEDIA_VERSION",
                "items": [{"id": "identity", "label": "人物身份", "required": True}],
            },
        )
    assert created.status_code == 201
    appended = created.json()["template"]
    assert appended["version_no"] == 2

    with TestClient(create_app(workspace)):
        pass
    after = _template_rows(workspace.database_path)

    assert len(after) == len(EXPECTED_TEMPLATE_CODES) + 1
    published = [row for row in after if row[0] == "image_asset"]
    assert [row[1] for row in published] == [1, 2]
    version_one = next(row for row in published if row[1] == 1)
    version_one_before = next(row for row in first if row[0] == "image_asset" and row[1] == 1)
    assert version_one[2] == version_one_before[2]
    assert version_one[3] == "startup"


def test_ensure_templates_is_repeatable_without_duplicating_rows(tmp_path: Path) -> None:
    from local_drama.application.reviews import ReviewService

    workspace = _fresh_workspace(tmp_path, tmp_path / "model_manifest.json")
    database = Database(workspace.database_path)
    with TestClient(create_app(workspace)):
        pass

    before = _template_rows(workspace.database_path)
    ReviewService(database, workspace).ensure_templates(actor="startup")
    ReviewService(database, workspace).ensure_templates(actor="restart")
    after = _template_rows(workspace.database_path)

    assert before == after

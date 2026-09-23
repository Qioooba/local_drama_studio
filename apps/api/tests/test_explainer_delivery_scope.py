"""Delivery scope, export confirmation and review-subject contracts.

Reproduced defects on the audit snapshot:

* ``EXP-05`` / ``LDS-07`` — ``POST .../exports`` and ``GET .../qc`` fetched an
  explicit ``render_id`` with a plain ``find``, so a render belonging to another
  edition/project could be bound into *this* edition's publication package.  The
  per-column foreign keys prove each row exists, never that they share a workspace.
* ``LDS-13`` — ``ExplainerExportRequest.confirm`` defaulted to ``False`` and was
  never read, so a "plan only" call still inserted a ``BUILDING`` package, and the
  ``Idempotency-Key`` was only echoed back: the same key produced a *second*
  package on every click.
* ``LDS-08`` — the QC read used subject ``EDITION`` while the human decision wrote
  ``COMPOSITION_RENDER`` + the current render hash, so a recorded approval never
  came back after a refresh; and the video projection carried no ``revision``, so
  the browser fell back to ``?? 1`` and manufactured a stale-revision conflict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from local_drama.api.routes.explainers import (
    _qc_view,
    _start_export,
)
from local_drama.api.schemas.explainers import ExplainerExportRequest
from local_drama.domain.explainers.contracts import ExplainerContractError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database


def _seed(database: Database, *, key: str) -> dict[str, str]:
    """One explainer project + video + edition + frozen composition + render."""

    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO projects (id, code, title, status, template_version, root_rel, product_kind)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                f"proj-{key}", f"code_{key}", f"项目{key}", "ACTIVE", "v2",
                f"projects/{key}", "EXPLAINER",
            ),
        )
        connection.execute(
            "INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale,"
            " input_kind, duration_mode, target_seconds, tolerance_percent, automation_mode,"
            " inference_mode, research_mode, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"video-{key}", f"proj-{key}", f"作品{key}", "主题", "FACTUAL_EXPLAINER", "zh-CN",
                "TOPIC", "TARGET", 300, 5.0, "AUTO_WITH_EXCEPTIONS", "LOCAL_ONLY", "OFFLINE_IMPORT", "DRAFT",
            ),
        )
        connection.execute(
            "INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status)"
            " VALUES (?,?,?,?,?)",
            (f"ed-{key}", f"video-{key}", f"main_{key}", "zh-CN", "READY"),
        )
        connection.execute(
            "INSERT INTO composition_revisions (id, edition_id, video_id, project_id, revision_no,"
            " status, manifest_hash, fps_num, fps_den, total_frames, audio_sample_rate_hz)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"comp-{key}", f"ed-{key}", f"video-{key}", f"proj-{key}", 1, "FROZEN",
                key * 64, 25, 1, 250, 48_000,
            ),
        )
        connection.execute(
            "INSERT INTO composition_renders (id, edition_id, composition_revision_id, video_id,"
            " project_id, revision_no, manifest_hash, status, sha256, frame_count, integrity_status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"render-{key}", f"ed-{key}", f"comp-{key}", f"video-{key}", f"proj-{key}", 1,
                key * 64, "SUCCEEDED", key * 64, 250, "VERIFIED",
            ),
        )
    return {
        "project_id": f"proj-{key}",
        "video_id": f"video-{key}",
        "edition_id": f"ed-{key}",
        "composition_id": f"comp-{key}",
        "render_id": f"render-{key}",
    }


def _repo(database: Database) -> ExplainerRepository:
    connection = database.connect()
    return ExplainerRepository(connection)


def _packages(database: Database, edition_id: str) -> list[dict[str, Any]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM publication_packages WHERE edition_id = ?", (edition_id,)
        ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# EXP-05 / LDS-07: a render must belong to the edition it is used from
# --------------------------------------------------------------------------- #
def test_export_refuses_a_render_from_another_project_with_zero_writes(
    database: Database, workspace: object
) -> None:
    a = _seed(database, key="a")
    b = _seed(database, key="b")
    repo = _repo(database)
    try:
        with pytest.raises(ExplainerContractError) as error:
            _start_export(
                repo,
                a["edition_id"],
                ExplainerExportRequest(render_id=b["render_id"], confirm=True),
                "cross-1",
            )
        assert error.value.code == "INVALID_REQUEST"
        assert _packages(database, a["edition_id"]) == []
        assert _packages(database, b["edition_id"]) == []
    finally:
        repo.connection.close()


def test_export_refuses_a_render_from_another_edition_of_the_same_video(
    database: Database, workspace: object
) -> None:
    a = _seed(database, key="c")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status)"
            " VALUES (?,?,?,?,?)",
            ("ed-c2", a["video_id"], "alt_c", "en-US", "READY"),
        )
        connection.execute(
            "INSERT INTO composition_revisions (id, edition_id, video_id, project_id, revision_no,"
            " status, manifest_hash, fps_num, fps_den, total_frames, audio_sample_rate_hz)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("comp-c2", "ed-c2", a["video_id"], a["project_id"], 1, "FROZEN", "c" * 64, 25, 1, 250, 48_000),
        )
        connection.execute(
            "INSERT INTO composition_renders (id, edition_id, composition_revision_id, video_id,"
            " project_id, revision_no, manifest_hash, status, sha256, frame_count, integrity_status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("render-c2", "ed-c2", "comp-c2", a["video_id"], a["project_id"], 1, "c" * 64,
             "SUCCEEDED", "d" * 64, 250, "VERIFIED"),
        )
    repo = _repo(database)
    try:
        with pytest.raises(ExplainerContractError) as error:
            _start_export(
                repo, a["edition_id"], ExplainerExportRequest(render_id="render-c2", confirm=True), "cross-2"
            )
        assert error.value.code == "INVALID_REQUEST"
        assert _packages(database, a["edition_id"]) == []
    finally:
        repo.connection.close()


def test_export_accepts_a_render_that_really_belongs_to_the_edition(
    database: Database, workspace: object
) -> None:
    a = _seed(database, key="e")
    repo = _repo(database)
    try:
        result = _start_export(
            repo, a["edition_id"], ExplainerExportRequest(render_id=a["render_id"], confirm=True), "ok-1"
        )
        assert result["status"] == "BUILDING"
        assert result["package"]["render_id"] == a["render_id"]
        assert result["package"]["project_id"] == a["project_id"]
        assert result["package"]["video_id"] == a["video_id"]
    finally:
        repo.connection.close()


def test_qc_view_refuses_a_foreign_render(database: Database, workspace: object) -> None:
    a = _seed(database, key="f")
    b = _seed(database, key="g")
    repo = _repo(database)
    try:
        with pytest.raises(ExplainerContractError):
            _qc_view(repo, a["edition_id"], b["render_id"])
    finally:
        repo.connection.close()


# --------------------------------------------------------------------------- #
# LDS-13: confirm means confirm, and the key means idempotency
# --------------------------------------------------------------------------- #
def test_export_without_confirmation_writes_no_package(database: Database, workspace: object) -> None:
    a = _seed(database, key="h")
    repo = _repo(database)
    try:
        result = _start_export(
            repo, a["edition_id"], ExplainerExportRequest(render_id=a["render_id"], confirm=False), "plan-1"
        )
        assert result["status"] == "PREVIEW"
        assert result["requires_confirmation"] is True
        assert result["would_build_package"] is False
        assert result["package"] is None
        assert result["operation_id"] is None
        assert result["plan_hash"]
        assert _packages(database, a["edition_id"]) == []
    finally:
        repo.connection.close()


def test_export_same_key_twenty_times_creates_one_package(database: Database, workspace: object) -> None:
    a = _seed(database, key="i")
    repo = _repo(database)
    try:
        payload = ExplainerExportRequest(render_id=a["render_id"], confirm=True)
        first = _start_export(repo, a["edition_id"], payload, "same-key")
        for _ in range(19):
            replay = _start_export(repo, a["edition_id"], payload, "same-key")
            assert replay["package_id"] == first["package_id"]
            assert replay["idempotent_replay"] is True
        packages = _packages(database, a["edition_id"])
        assert len(packages) == 1
    finally:
        repo.connection.close()


def test_export_same_key_with_a_different_plan_conflicts(database: Database, workspace: object) -> None:
    a = _seed(database, key="j")
    repo = _repo(database)
    try:
        _start_export(repo, a["edition_id"], ExplainerExportRequest(render_id=a["render_id"], confirm=True), "k")
        with pytest.raises(ExplainerContractError) as error:
            _start_export(
                repo,
                a["edition_id"],
                ExplainerExportRequest(
                    render_id=a["render_id"], confirm=True, intended_territories=["CN"]
                ),
                "k",
            )
        assert error.value.code == "IDEMPOTENCY_KEY_CONFLICT"
        assert len(_packages(database, a["edition_id"])) == 1
    finally:
        repo.connection.close()


def test_export_confirm_requires_a_deliverable_render(database: Database, workspace: object) -> None:
    a = _seed(database, key="l")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE composition_renders SET integrity_status='CORRUPT', status='FAILED' WHERE id=?",
            (a["render_id"],),
        )
    repo = _repo(database)
    try:
        # A corrupt render cannot be *planned* as a deliverable either, once the
        # caller names it explicitly.
        with pytest.raises(ExplainerContractError) as error:
            _start_export(
                repo, a["edition_id"], ExplainerExportRequest(render_id=a["render_id"], confirm=True), "bad"
            )
        assert error.value.code == "RENDER_NOT_DELIVERABLE"
        assert _packages(database, a["edition_id"]) == []
    finally:
        repo.connection.close()


def test_export_body_edition_mismatch_is_refused(database: Database, workspace: object) -> None:
    a = _seed(database, key="m")
    b = _seed(database, key="n")
    repo = _repo(database)
    try:
        with pytest.raises(ExplainerContractError):
            _start_export(
                repo,
                a["edition_id"],
                ExplainerExportRequest(edition_id=b["edition_id"], confirm=True),
                "mismatch",
            )
    finally:
        repo.connection.close()


# --------------------------------------------------------------------------- #
# LDS-08: one review subject, and a real revision
# --------------------------------------------------------------------------- #
def test_qc_subject_is_the_current_render_when_one_exists(
    database: Database, workspace: object
) -> None:
    a = _seed(database, key="o")
    repo = _repo(database)
    try:
        view = _qc_view(repo, a["edition_id"], None)
        assert view["subject"]["kind"] == "COMPOSITION_RENDER"
        assert view["subject"]["revision_id"] == a["render_id"]
        assert view["subject"]["hash"] == "o" * 64
        assert view["subject_is_the_film"] is True
        assert view["empty_state"] is None
        assert view["film_review_target"]["render_sha256"] == view["subject"]["hash"]
        assert view["film_review_target"]["manifest_hash"]
    finally:
        repo.connection.close()


def test_qc_subject_says_so_when_there_is_no_render(database: Database, workspace: object) -> None:
    a = _seed(database, key="p")
    with database.transaction() as connection:
        connection.execute("DELETE FROM composition_renders WHERE edition_id=?", (a["edition_id"],))
    repo = _repo(database)
    try:
        view = _qc_view(repo, a["edition_id"], None)
        assert view["subject"]["kind"] == "EDITION"
        assert view["subject_is_the_film"] is False
        assert view["empty_state"] == "NO_VERIFIED_RENDER"
        assert view["film_review_target"]["has_render"] is False
    finally:
        repo.connection.close()


def test_review_target_carries_the_real_video_revision(database: Database, workspace: object) -> None:
    """The browser's ``?? 1`` fallback could not work when the row said 3."""

    a = _seed(database, key="q")
    with database.transaction() as connection:
        connection.execute("UPDATE explainer_videos SET revision=3 WHERE id=?", (a["video_id"],))
    repo = _repo(database)
    try:
        target = repo.current_review_target(a["edition_id"])
        assert target["video_revision"] == 3
        assert target["edition_revision"] == 1
    finally:
        repo.connection.close()


def test_overview_exposes_the_video_revision(database: Database, workspace: object) -> None:
    from local_drama.application.explainers.production import ExplainerProductionService

    a = _seed(database, key="r")
    with database.transaction() as connection:
        connection.execute("UPDATE explainer_videos SET revision=4 WHERE id=?", (a["video_id"],))
    overview = ExplainerProductionService(database).overview(project_id=a["project_id"])
    assert overview["video"]["revision"] == 4


def test_human_confirmation_is_readable_against_the_same_subject(
    database: Database, workspace: object
) -> None:
    """A confirmation written for the film must be visible when the page reloads."""

    a = _seed(database, key="s")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO explainer_qc_reports (id, project_id, video_id, edition_id, subject_kind,"
            " subject_revision_id, subject_hash, status, coverage_json, unverified_checks_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "report-s", a["project_id"], a["video_id"], a["edition_id"], "COMPOSITION_RENDER",
                a["render_id"], "s" * 64, "PASS", json.dumps({"total_frames": 250}), "[]",
            ),
        )
        connection.execute(
            "INSERT INTO explainer_decisions (id, project_id, video_id, edition_id, subject_kind,"
            " subject_revision_id, subject_hash, decision_kind, actor, actor_type,"
            " reviewed_intervals_json, content_hash_at_decision, decided_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "dec-s", a["project_id"], a["video_id"], a["edition_id"], "COMPOSITION_RENDER",
                a["render_id"], "s" * 64, "HUMAN_APPROVED", "operator", "HUMAN",
                "[[0,250]]", "s" * 64, "2026-01-01T00:00:00Z",
            ),
        )
    repo = _repo(database)
    try:
        view = _qc_view(repo, a["edition_id"], None)
        assert view["report"] is not None
        assert view["human_decision"] is not None
        assert view["human_decision"]["id"] == "dec-s"
        assert view["status"] == "PASS"
    finally:
        repo.connection.close()


def test_render_scope_helper_is_the_single_shared_implementation() -> None:
    """Export, QC and decision subject resolution must share one validator."""

    source = Path("apps/api/local_drama/api/routes/explainers.py").read_text(encoding="utf-8")
    assert "require_render_for_edition(" in source
    # The old "fetch whatever id you were given" pattern must be gone from the
    # export and QC paths.
    assert 'repo.find("composition_renders", payload.render_id)' not in source
    assert 'repo.find("composition_renders", render_id)' not in source


"""The replacement-collect flow and the migration that makes it possible (§D5.1, §D3).

Two things are pinned here.

**The recovery command.**  When a collection stage is blocked because some candidate
jobs failed, "用现有结果继续" must replace the collect job *for the same workflow
task* while keeping every failure auditable.  The tests below check the refusals (an
unresolved required owner, a paused/cancelled run, a non-collection step, a stale task
pointer) and the migration's conflict handling.

**The migration.**  ``0106`` adds the candidate owner columns, the selection
``purpose`` and the ACTIVE-only partial unique indexes.  A migration that only works
on an empty database is useless, so what matters is realistic pre-existing rows —
including two conflicting human locks, which the migration must leave untouched and
report instead of guessing.

Requirement mapping: §D3 (schema increments), §D5.1 steps 1–6 and §F2.4.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from local_drama.application.explainers.visual_generation import (
    ExplainerVisualGenerationError,
    ExplainerVisualGenerationService,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "recover-project"
VIDEO_ID = "recover-video"
BEAT_ID = "recover-beat"
RUN_ID = "recover-run"
STEP_ID = "recover-step"
WORKFLOW_RUN_ID = "recover-workflow-run"
TASK_ID = "recover-task"
SUBJECT_ID = "recover-subject"
OLD_JOB_ID = "recover-old-job"
NOW = "2026-01-01T00:00:00Z"


class _Settings:
    """The recovery path reads no settings; an empty object keeps the dependency explicit."""


def _seed(database: Database, *, run_status: str = "RUNNING", with_candidate: bool = True) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'rec_proj', '恢复', 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '恢复', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        repo = ExplainerRepository(connection)
        repo.insert(
            "explainer_visual_beats",
            {
                "id": BEAT_ID,
                "video_id": VIDEO_ID,
                "code": "B001",
                "ordinal": 0,
                "render_type": "I2V",
                "visual_intent": "画面",
            },
        )
        # Dependency order matters: the workflow run and the job rows must exist before
        # the rows that reference them, or SQLite's foreign keys reject the seed.
        connection.execute(
            """INSERT INTO automation_workflows (id, project_id, code, title, mode, definition_json, plan_hash,
            status, created_at, updated_at, created_by, revision, schema_version, version_no, template_code)
            VALUES ('recover-workflow', ?, 'EXPLAINER_RECOVER', '恢复', 'BATCH_AUTOMATED', '{}', ?, 'ACTIVE',
            ?, ?, 'test', 1, 'v2', 1, 'EXPLAINER_FULL_PRODUCTION')""",
            (PROJECT_ID, "f" * 64, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO automation_workflow_runs (id, workflow_id, project_id, status, plan_hash,
            iteration_count, task_count, disk_bytes, max_iterations, max_tasks, max_disk_bytes, created_at,
            updated_at, created_by, revision, schema_version)
            VALUES (?, 'recover-workflow', ?, 'RUNNING', ?, 0, 14, 0, 56, 14, 0, ?, ?, 'test', 1, 'v2')""",
            (WORKFLOW_RUN_ID, PROJECT_ID, "f" * 64, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO jobs (id, project_id, type, subject_type, subject_id, subject_kind, scope_kind,
            scope_project_id, stage_code, state, channel, idempotency_key, input_snapshot_json,
            created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'AUTOMATION_WORKFLOW_TASK', 'AUTOMATION_WORKFLOW_TASK', ?, 'AUTOMATION_WORKFLOW_TASK',
            'PROJECT', ?, 'AUTOMATION', 'NEEDS_ATTENTION', 'CPU', ?, '{}',
            ?, ?, 'test', 1, 'v2')""",
            (OLD_JOB_ID, PROJECT_ID, SUBJECT_ID, PROJECT_ID, "recover-old-key", NOW, NOW),
        )
        repo.insert(
            "explainer_runs",
            {
                "id": RUN_ID,
                "project_id": PROJECT_ID,
                "video_id": VIDEO_ID,
                "status": run_status,
                "plan_hash": "e" * 64,
                "automation_workflow_run_id": WORKFLOW_RUN_ID,
            },
        )
        repo.insert(
            "explainer_step_bindings",
            {
                "id": STEP_ID,
                "run_id": RUN_ID,
                "video_id": VIDEO_ID,
                "planned_step_code": "IMAGE_COLLECT",
                "task_key": "explainer:IMAGE_COLLECT",
                "status": "BLOCKED",
                "job_id": OLD_JOB_ID,
                "blocker_code": "JOB_DEPENDENCY_FAILED",
            },
        )
        connection.execute(
            """INSERT INTO automation_workflow_run_tasks (id, run_id, ordinal, item_key, item_json, status,
            machine_context_json, created_at, updated_at, revision, job_id)
            VALUES (?, ?, 11, 'explainer:IMAGE_COLLECT', ?, 'QUEUED', '{}', ?, ?, 4, ?)""",
            (TASK_ID, WORKFLOW_RUN_ID, json.dumps({"step_binding_id": STEP_ID}), NOW, NOW, OLD_JOB_ID),
        )
        if with_candidate:
            connection.execute(
                """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
                version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
                VALUES ('rec-asset', ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_BEAT_CLIP', 'IMAGE', 1, '{}',
                ?, ?, 'test', 1, 'v2')""",
                (PROJECT_ID, VIDEO_ID, NOW, NOW),
            )
            connection.execute(
                """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
                byte_size, sha256, integrity_status, created_at, updated_at, created_by, revision, schema_version)
                VALUES ('rec-media', 'rec-asset', 1, 1, 'VISUAL_GENERATION', 'media/ok.png', 'image/png', 10, ?,
                'VERIFIED', ?, ?, 'test', 1, 'v2')""",
                ("d" * 64, NOW, NOW),
            )
            repo.insert(
                "explainer_media_candidates",
                {
                    "id": "rec-candidate",
                    "video_id": VIDEO_ID,
                    "beat_id": BEAT_ID,
                    "variant_no": 1,
                    "candidate_kind": "CREATIVE",
                    "purpose": "KEYFRAME",
                    "media_asset_id": "rec-asset",
                    "media_version_id": "rec-media",
                    "media_sha256": "d" * 64,
                    "status": "READY",
                    # A KEYFRAME candidate is a picture, so it declares the beat's
                    # planned type but no actual render type of its own.
                    "render_type_planned": "I2V",
                    "render_type_actual": None,
                },
            )


@pytest.fixture()
def seeded(database: Database) -> Database:
    _seed(database)
    return database


@pytest.fixture()
def service(seeded: Database):
    connection = seeded.connect()
    built = ExplainerVisualGenerationService(
        ExplainerRepository(connection), database=seeded, settings=_Settings()
    )
    yield built
    connection.close()


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #
def test_a_required_owner_without_a_candidate_blocks_the_continue(database: Database) -> None:
    _seed(database, with_candidate=False)
    connection = database.connect()
    service = ExplainerVisualGenerationService(
        ExplainerRepository(connection), database=database, settings=_Settings()
    )
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.continue_collection_with_available(
            run_id=RUN_ID,
            step_binding_id=STEP_ID,
            selected_candidate_ids=[],
            idempotency_key="continue-1",
        )
    assert error.value.code == "EXPLAINER_REQUIRED_OWNER_MISSING"
    assert error.value.detail["missing_owner_ids"] == [BEAT_ID]
    connection.close()


def test_a_cancelled_run_cannot_be_continued(database: Database) -> None:
    _seed(database, run_status="CANCELLED")
    connection = database.connect()
    service = ExplainerVisualGenerationService(
        ExplainerRepository(connection), database=database, settings=_Settings()
    )
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.continue_collection_with_available(
            run_id=RUN_ID,
            step_binding_id=STEP_ID,
            selected_candidate_ids=["rec-candidate"],
            idempotency_key="continue-2",
        )
    assert error.value.code == "EXPLAINER_RUN_NOT_CONTINUABLE"
    connection.close()


def test_a_paused_run_cannot_be_continued(database: Database) -> None:
    _seed(database, run_status="PAUSED")
    connection = database.connect()
    service = ExplainerVisualGenerationService(
        ExplainerRepository(connection), database=database, settings=_Settings()
    )
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.continue_collection_with_available(
            run_id=RUN_ID,
            step_binding_id=STEP_ID,
            selected_candidate_ids=["rec-candidate"],
            idempotency_key="continue-2b",
        )
    assert error.value.code == "EXPLAINER_RUN_NOT_CONTINUABLE"
    connection.close()


def test_a_non_collection_step_is_refused(seeded: Database, service) -> None:
    with seeded.transaction() as connection:
        ExplainerRepository(connection).update(
            "explainer_step_bindings", STEP_ID, {"planned_step_code": "NARRATION_TTS"}
        )
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.continue_collection_with_available(
            run_id=RUN_ID,
            step_binding_id=STEP_ID,
            selected_candidate_ids=["rec-candidate"],
            idempotency_key="continue-3",
        )
    assert error.value.code == "EXPLAINER_NOT_A_COLLECTION"


def test_a_stale_task_revision_is_refused(seeded: Database, service) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.continue_collection_with_available(
            run_id=RUN_ID,
            step_binding_id=STEP_ID,
            selected_candidate_ids=["rec-candidate"],
            expected_task_revision=999,
            idempotency_key="continue-4",
        )
    assert error.value.code == "EXPLAINER_STALE_TASK"


def test_a_stale_old_job_id_is_refused(seeded: Database, service) -> None:
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.continue_collection_with_available(
            run_id=RUN_ID,
            step_binding_id=STEP_ID,
            selected_candidate_ids=["rec-candidate"],
            expected_old_job_id="a-different-tab-already-replaced-it",
            idempotency_key="continue-5",
        )
    assert error.value.code == "EXPLAINER_STALE_TASK"


# --------------------------------------------------------------------------- #
# the real replacement
# --------------------------------------------------------------------------- #
def test_a_valid_continue_replaces_the_collect_job_for_the_same_task(seeded: Database, service) -> None:
    receipt = service.continue_collection_with_available(
        run_id=RUN_ID,
        step_binding_id=STEP_ID,
        selected_candidate_ids=["rec-candidate"],
        failed_candidate_ids=["rec-failed"],
        expected_task_revision=4,
        expected_old_job_id=OLD_JOB_ID,
        actor="machine-policy",
        idempotency_key="continue-ok",
    )
    assert receipt["status"] == "REPLACED"
    assert receipt["previous_job_id"] == OLD_JOB_ID
    assert receipt["replacement_job_id"]
    assert receipt["machine_policy_applied"] is True

    with seeded.connect() as connection:
        task = connection.execute(
            "SELECT job_id, revision, ordinal FROM automation_workflow_run_tasks WHERE id=?", (TASK_ID,)
        ).fetchone()
        step = connection.execute(
            "SELECT job_id, status, blocker_code FROM explainer_step_bindings WHERE id=?", (STEP_ID,)
        ).fetchone()
        old_job = connection.execute("SELECT state FROM jobs WHERE id=?", (OLD_JOB_ID,)).fetchone()
        new_job = connection.execute("SELECT state FROM jobs WHERE id=?", (str(task["job_id"]),)).fetchone()
    # The task pointer moved to one real replacement job, the ordinal did not change
    # and the old collect keeps its real (not rewritten) state.
    assert str(task["job_id"]) == receipt["replacement_job_id"]
    assert int(task["ordinal"]) == 11
    assert int(task["revision"]) == 5
    assert str(step["job_id"]) == receipt["replacement_job_id"]
    assert str(step["status"]) == "PENDING"
    assert step["blocker_code"] is None
    assert str(old_job["state"]) != "SUCCEEDED"
    assert str(new_job["state"]) in {"QUEUED", "PENDING"}


def test_replaying_the_same_continue_returns_the_same_receipt(seeded: Database, service) -> None:
    first = service.continue_collection_with_available(
        run_id=RUN_ID,
        step_binding_id=STEP_ID,
        selected_candidate_ids=["rec-candidate"],
        expected_task_revision=4,
        expected_old_job_id=OLD_JOB_ID,
        idempotency_key="continue-replay",
    )
    replay = service.continue_collection_with_available(
        run_id=RUN_ID,
        step_binding_id=STEP_ID,
        selected_candidate_ids=["rec-candidate"],
        expected_task_revision=4,
        expected_old_job_id=OLD_JOB_ID,
        idempotency_key="continue-replay",
    )
    assert replay["idempotent_replay"] is True
    assert replay["replacement_job_id"] == first["replacement_job_id"]
    with seeded.connect() as connection:
        jobs = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE input_snapshot_json LIKE '%collection_hash%'"
        ).fetchone()[0]
    assert jobs == 1


def test_a_second_tab_with_the_same_stale_pointer_cannot_replace_twice(seeded: Database, service) -> None:
    service.continue_collection_with_available(
        run_id=RUN_ID,
        step_binding_id=STEP_ID,
        selected_candidate_ids=["rec-candidate"],
        expected_task_revision=4,
        expected_old_job_id=OLD_JOB_ID,
        idempotency_key="continue-tab-1",
    )
    with pytest.raises(ExplainerVisualGenerationError) as error:
        service.continue_collection_with_available(
            run_id=RUN_ID,
            step_binding_id=STEP_ID,
            selected_candidate_ids=["rec-candidate"],
            expected_task_revision=4,
            expected_old_job_id=OLD_JOB_ID,
            idempotency_key="continue-tab-2",
        )
    assert error.value.code == "EXPLAINER_STALE_TASK"


# --------------------------------------------------------------------------- #
# migration 0106 helper
# --------------------------------------------------------------------------- #
def _migration_module():
    path = Path("apps/api/alembic/versions/0106_explainer_visual_candidate_owners.py")
    spec = importlib.util.spec_from_file_location("migration_0106", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_reports_two_human_locks_without_resolving_them(seeded: Database) -> None:
    """The conflict resolver is exercised on pre-migration data.

    The migrated database already enforces one ACTIVE row per scope, so the scenario
    the resolver exists for — a legacy database that still holds duplicates — is
    recreated by dropping those two indexes first.  That is exactly the state
    ``upgrade()`` meets before it tries to create them.
    """

    with seeded.transaction() as connection:
        connection.execute("DROP INDEX uq_explainer_beat_selections_active_global")
        connection.execute("DROP INDEX uq_explainer_beat_selections_active_scope")
        repo = ExplainerRepository(connection)
        for index in range(2):
            repo.insert(
                "explainer_beat_selections",
                {
                    "id": f"sel-{index}",
                    "video_id": VIDEO_ID,
                    "beat_id": BEAT_ID,
                    "purpose": "VISUAL",
                    "candidate_id": "rec-candidate",
                    "media_asset_id": "rec-asset",
                    "media_version_id": "rec-media",
                    "media_sha256": "a" * 64,
                    "adoption_authority": "HUMAN",
                    "locked_by_human": True,
                    "actor": "tester",
                    "decided_at": f"2026-01-0{index + 1}T00:00:00Z",
                    "status": "ACTIVE",
                },
            )
    connection = seeded.connect()
    module = _migration_module()
    try:
        conflicts = module._resolve_active_duplicates(connection)
        assert conflicts["resolved"] == []
        assert len(conflicts["unresolved"]) == 1
        assert conflicts["unresolved"][0]["beat_id"] == BEAT_ID

        # With a single lock the other ACTIVE row is superseded and the lock survives.
        with seeded.transaction() as inner:
            inner.execute("UPDATE explainer_beat_selections SET locked_by_human=0 WHERE id='sel-1'")
        conflicts = module._resolve_active_duplicates(connection)
        assert len(conflicts["resolved"]) == 1
        assert conflicts["resolved"][0]["kept_selection_id"] == "sel-0"
        rows = {
            str(row["id"]): str(row["status"])
            for row in connection.execute("SELECT id, status FROM explainer_beat_selections")
        }
    finally:
        connection.close()
    assert rows["sel-0"] == "ACTIVE"
    assert rows["sel-1"] == "SUPERSEDED"

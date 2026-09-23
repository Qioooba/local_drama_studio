"""Commands must persist a real execution intent, or say why not (LDS-01 / EXP-02).

Reproduced defects on the audit snapshot:

* ``narration:resynthesize`` returned ``requested_stage=NARRATION_TTS`` and a prose
  description with **zero** writes — no job, no take, no receipt;
* ``renders{confirm:true}`` returned ``202 SUBMITTED`` after setting
  ``edition.status=RENDERING`` while ``jobs``, ``composition_renders`` and
  ``outbox_events`` were all unchanged, so the workbench ran forever;
* ``decisions{rerun_policy:true}`` answered "已请求内部处理器按冻结政策重跑" without
  persisting anything a worker could claim;
* the loose ``video + canonical_segment_id`` lookup could resolve a segment from a
  *different language* or an older script revision than the request meant.
"""

from __future__ import annotations

from typing import Any

import pytest

from local_drama.application.explainers.stage_commands import (
    EXPLAINER_STAGE_JOB_TYPES,
    STAGE_CAPABILITY_UNAVAILABLE,
    build_explainers_command_service,
)
from local_drama.domain.explainers.contracts import ExplainerContractError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "project-cmd-1"
VIDEO_ID = "video-cmd-1"
EDITION_ID = "edition-cmd-1"
SCRIPT_REVISION_ID = "script-cmd-1"


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel,
            target_duration_ms, product_kind, created_at, updated_at, created_by)
            VALUES (?, ?, ?, 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, "EXP-CMD", "命令测试", PROJECT_ID),
        )
        connection.execute(
            "INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale,"
            " input_kind, duration_mode, target_seconds, tolerance_percent, automation_mode,"
            " inference_mode, research_mode, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                VIDEO_ID, PROJECT_ID, "作品", "主题", "FACTUAL_EXPLAINER", "zh-CN", "TOPIC",
                "TARGET", 300, 5.0, "AUTO_WITH_EXCEPTIONS", "LOCAL_ONLY", "OFFLINE_IMPORT", "DRAFT",
            ),
        )
        # Three script revisions so the loose ``video + canonical`` lookup has a
        # wrong answer to give: the en-US revision and an older zh-CN revision.
        for revision_id, locale, revision_no in (
            (SCRIPT_REVISION_ID, "zh-CN", 1),
            ("script-en", "en-US", 1),
            ("script-old", "zh-CN", 2),
        ):
            connection.execute(
                "INSERT INTO explainer_script_revisions (id, video_id, revision_no, locale, status,"
                " content_hash) VALUES (?,?,?,?,?,?)",
                (revision_id, VIDEO_ID, revision_no, locale, "FROZEN", revision_id.ljust(64, "0")[:64]),
            )
        connection.execute(
            "INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status,"
            " frozen_script_revision_id) VALUES (?,?,?,?,?,?)",
            (EDITION_ID, VIDEO_ID, "main", "zh-CN", "READY", SCRIPT_REVISION_ID),
        )
        connection.execute(
            "INSERT INTO composition_revisions (id, edition_id, video_id, project_id, revision_no,"
            " status, manifest_hash, fps_num, fps_den, total_frames, audio_sample_rate_hz)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("comp-cmd-1", EDITION_ID, VIDEO_ID, PROJECT_ID, 1, "FROZEN", "m" * 64, 25, 1, 250, 48_000),
        )
        repo = ExplainerRepository(connection)
        for ordinal, (canonical, locale, text, revision) in enumerate(
            [
                ("seg-1", "zh-CN", "第一段中文旁白。", SCRIPT_REVISION_ID),
                ("seg-1", "en-US", "First English line.", "script-en"),
                ("seg-1", "zh-CN", "第一段旧修订。", "script-old"),
            ]
        ):
            repo.insert(
                "narration_segments",
                {
                    "video_id": VIDEO_ID,
                    "script_revision_id": revision,
                    "chapter_id": None,
                    "ordinal": ordinal,
                    "canonical_segment_id": canonical,
                    "locale": locale,
                    "display_text": text,
                    "spoken_text": text,
                    "segment_hash": f"seg{ordinal + 1}" + "0" * 60,
                    "content_locked_by_human": 0,
                },
            )


def _counts(database: Database) -> dict[str, int]:
    with database.connect() as connection:
        return {
            "jobs": int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]),
            "renders": int(connection.execute("SELECT COUNT(*) FROM composition_renders").fetchone()[0]),
            "takes": int(connection.execute("SELECT COUNT(*) FROM narration_takes").fetchone()[0]),
        }


def _edition_status(database: Database) -> str:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM explainer_editions WHERE id = ?", (EDITION_ID,)
        ).fetchone()
    return str(row["status"])


# --------------------------------------------------------------------------- #
# narration re-read
# --------------------------------------------------------------------------- #
def test_resynthesis_writes_a_real_job_and_receipt(database: Database) -> None:
    _seed(database)
    service = build_explainers_command_service(database)
    result = service.submit_narration_resynthesis(
        edition_id=EDITION_ID, canonical_segment_id="seg-1", reason="LOCAL_RE_READ",
        idempotency_key="reread-1",
    )
    assert result["status"] == "ACCEPTED"
    assert result["durable_intent_persisted"] is True
    assert result["job_id"]
    assert result["job_state"] == "QUEUED"
    assert _counts(database)["jobs"] == 1

    with database.connect() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (result["job_id"],)).fetchone()
    assert job is not None
    assert str(job["type"]) == EXPLAINER_STAGE_JOB_TYPES["NARRATION_TTS"]
    assert str(job["stage_code"]) == "NARRATION_TTS"
    assert str(job["state"]) == "QUEUED"
    import json

    snapshot = json.loads(str(job["input_snapshot_json"]))
    # The frozen scope is pinned, not "whatever is latest".
    assert snapshot["semantic_inputs"]["locale"] == "zh-CN"
    assert snapshot["semantic_inputs"]["frozen_script_revision_id"] == SCRIPT_REVISION_ID
    assert snapshot["semantic_inputs"]["segment_hash"] == "seg1" + "0" * 60
    assert snapshot["frozen_plan_hash"]


def test_resynthesis_replays_the_same_job_for_the_same_key(database: Database) -> None:
    _seed(database)
    service = build_explainers_command_service(database)
    first = service.submit_narration_resynthesis(
        edition_id=EDITION_ID, canonical_segment_id="seg-1", reason="LOCAL_RE_READ",
        idempotency_key="reread-2",
    )
    second = service.submit_narration_resynthesis(
        edition_id=EDITION_ID, canonical_segment_id="seg-1", reason="LOCAL_RE_READ",
        idempotency_key="reread-2",
    )
    assert second["job_id"] == first["job_id"]
    assert second["idempotent_replay"] is True
    assert _counts(database)["jobs"] == 1


def test_resynthesis_uses_the_frozen_revision_and_locale_not_the_loose_lookup(
    database: Database,
) -> None:
    """The old ``segment_by_canonical`` would have picked the en-US or old row."""

    _seed(database)
    service = build_explainers_command_service(database)
    result = service.submit_narration_resynthesis(
        edition_id=EDITION_ID, canonical_segment_id="seg-1", reason="X", idempotency_key="scope-1"
    )
    import json

    with database.connect() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (result["job_id"],)).fetchone()
        segment = connection.execute(
            "SELECT * FROM narration_segments WHERE id = ?",
            (json.loads(str(job["input_snapshot_json"]))["semantic_inputs"]["narration_segment_id"],),
        ).fetchone()
    assert str(segment["script_revision_id"]) == SCRIPT_REVISION_ID
    assert str(segment["locale"]) == "zh-CN"
    assert str(segment["spoken_text"]) == "第一段中文旁白。"


def test_resynthesis_without_a_frozen_script_is_blocked_with_no_writes(database: Database) -> None:
    _seed(database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE explainer_editions SET frozen_script_revision_id = NULL WHERE id = ?", (EDITION_ID,)
        )
    service = build_explainers_command_service(database)
    result = service.submit_narration_resynthesis(
        edition_id=EDITION_ID, canonical_segment_id="seg-1", reason="X", idempotency_key="blocked-1"
    )
    assert result["status"] == "BLOCKED"
    assert result["job_id"] is None
    assert result["would_create_jobs"] is False
    assert _counts(database)["jobs"] == 0


def test_resynthesis_rejects_an_unknown_segment(database: Database) -> None:
    _seed(database)
    service = build_explainers_command_service(database)
    with pytest.raises(ExplainerContractError) as error:
        service.submit_narration_resynthesis(
            edition_id=EDITION_ID, canonical_segment_id="nope", reason="X", idempotency_key="missing-1"
        )
    assert error.value.code == "NOT_FOUND"
    assert _counts(database)["jobs"] == 0


def test_resynthesis_marks_the_downstream_closure_stale(database: Database) -> None:
    _seed(database)
    with database.connect() as connection:
        segment_id = str(
            connection.execute(
                "SELECT id FROM narration_segments WHERE script_revision_id = ? AND locale = 'zh-CN'",
                (SCRIPT_REVISION_ID,),
            ).fetchone()["id"]
        )
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        repo.add_dependency(
            video_id=VIDEO_ID,
            project_id=PROJECT_ID,
            upstream_kind="NARRATION_SEGMENT",
            upstream_id=segment_id,
            upstream_hash="1" * 64,
            downstream_kind="COMPOSITION_REVISION",
            downstream_id="comp-cmd-1",
        )
    service = build_explainers_command_service(database)
    result = service.submit_narration_resynthesis(
        edition_id=EDITION_ID, canonical_segment_id="seg-1", reason="X", idempotency_key="stale-1"
    )
    assert result["status"] == "ACCEPTED"
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT stale, stale_reason FROM artifact_dependencies WHERE upstream_id=?", (segment_id,)
        ).fetchall()
    assert rows and int(rows[0]["stale"]) == 1
    assert str(rows[0]["stale_reason"]) == "NARRATION_RESYNTHESIS_REQUESTED"


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def test_render_creates_a_claimable_job_and_never_fakes_a_rendering_status(database: Database) -> None:
    """A frozen composition yields a real, claimable render job.

    This stage used to report ``CAPABILITY_UNAVAILABLE`` because no worker handler
    existed for it.  ``COMPOSITION_RENDER`` now has a first-party handler, so the
    command must hand back a real job — and it still must not write a
    ``RENDERING`` edition status or a render row of its own, because only the
    worker's finished render is evidence that anything was rendered.
    """

    _seed(database)
    service = build_explainers_command_service(database)
    result = service.submit_composition_render(
        edition_id=EDITION_ID,
        composition={"id": "comp-cmd-1", "status": "FROZEN", "manifest_hash": "m" * 64},
        idempotency_key="render-1",
        confirm=True,
    )
    assert result["status"] == "ACCEPTED"
    assert result["job_id"]
    assert result["durable_intent_persisted"] is True
    assert result["would_create_jobs"] is True
    with database.connect() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (result["job_id"],)).fetchone()
    assert str(job["type"]) == "EXPLAINER_TASK"
    assert str(job["stage_code"]) == "COMPOSITION_RENDER"
    # No render row may be fabricated by the command, and the edition must NOT be
    # left in RENDERING with nothing running.
    assert _counts(database)["renders"] == 0
    assert _edition_status(database) == "READY"


def test_render_refuses_an_unfrozen_composition(database: Database) -> None:
    _seed(database)
    service = build_explainers_command_service(database)
    result = service.submit_composition_render(
        edition_id=EDITION_ID,
        composition={"id": "comp-cmd-1", "status": "DRAFT", "manifest_hash": "m" * 64},
        idempotency_key="render-2",
        confirm=True,
    )
    assert result["status"] == "BLOCKED"
    assert _counts(database)["renders"] == 0


# --------------------------------------------------------------------------- #
# policy re-run
# --------------------------------------------------------------------------- #
def test_policy_rerun_creates_a_claimable_explainer_task(database: Database) -> None:
    _seed(database)
    service = build_explainers_command_service(database)
    result = service.submit_policy_rerun(edition_id=EDITION_ID, idempotency_key="policy-1")
    assert result["status"] == "ACCEPTED"
    assert result["job_id"]
    with database.connect() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (result["job_id"],)).fetchone()
    assert str(job["type"]) == "EXPLAINER_TASK"
    assert str(job["stage_code"]) == "EXPLAINER_POLICY_EVALUATE"


def test_stage_job_type_table_names_a_registered_worker_for_every_entry() -> None:
    """Every enqueued stage must have a job type the worker really registers."""

    from local_drama.application.worker import _EXTRACTED_HANDLER_PROVIDERS

    for stage, job_type in EXPLAINER_STAGE_JOB_TYPES.items():
        assert job_type in _EXTRACTED_HANDLER_PROVIDERS, (stage, job_type)


def test_unknown_stage_is_reported_as_unavailable_not_accepted(database: Database) -> None:
    _seed(database)
    service = build_explainers_command_service(database)
    result = service._submit_stage(  # noqa: SLF001 - exercising the guard directly
        type(
            "R",
            (),
            {
                "stage_code": "NOT_A_STAGE",
                "project_id": PROJECT_ID,
                "video_id": VIDEO_ID,
                "subject_type": "X",
                "subject_id": "y",
                "subject_kind": "X",
                "snapshot": {},
                "idempotency_key": "z",
            },
        )()
    )
    assert result["status"] == STAGE_CAPABILITY_UNAVAILABLE
    assert _counts(database)["jobs"] == 0


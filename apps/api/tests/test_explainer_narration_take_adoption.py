"""A re-read must produce a take that actually takes effect.

The standalone ``NARRATION_TTS`` job family inserted ``selected: False`` and nothing
ever adopted it, while the production pipeline *did* adopt the takes it made.  So a
re-read created an inert take: an older selected take still won the lookup, and
alignment — which refuses an unselected take — failed the segment with
``NARRATION_TAKE_NOT_SELECTED`` (audit A03, design §5.2).

Requirement mapping: §5.2 (measure, then the automatic policy adopts the take) and
§4.3 (TTS COLLECT adopts a measured take; alignment reads the adopted set).
"""

from __future__ import annotations

import json
from pathlib import Path

from local_drama.application.explainers.narration import ExplainerNarrationService
from local_drama.domain.explainers.contracts import ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "take-adopt-project"
VIDEO_ID = "take-adopt-video"
SEGMENT_ID = "take-adopt-segment"
ASSET_ID = "take-adopt-asset"
NOW = "2026-01-01T00:00:00Z"


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'take_proj', '重读采用', 'DRAFT', 'v2', ?, 300000, ?,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '重读采用', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
            version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_NARRATION', 'AUDIO', 1, '{}',
            ?, ?, 'test', 1, 'v2')""",
            (ASSET_ID, PROJECT_ID, VIDEO_ID, NOW, NOW),
        )
        repo = ExplainerRepository(connection)
        connection.execute(
            """INSERT INTO explainer_script_revisions (id, video_id, revision_no, locale, status, content_hash)
            VALUES ('script-1', ?, 1, 'zh-CN', 'FROZEN', ?)""",
            (VIDEO_ID, "s" * 64),
        )
        repo.insert(
            "narration_segments",
            {
                "id": SEGMENT_ID,
                "video_id": VIDEO_ID,
                "script_revision_id": "script-1",
                "canonical_segment_id": "seg_001",
                "ordinal": 0,
                "locale": "zh-CN",
                "display_text": "第一句。",
                "spoken_text": "第一句。",
                "statement_type": "QUESTION",
                "segment_hash": "h" * 64,
            },
        )


def _take(database: Database, *, take_no: int, selected: bool = False, media: str | None = None) -> str:
    media_version_id = media or f"mv-{take_no}"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
            byte_size, sha256, integrity_status, duration_ms, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, ?, 'NARRATION_TTS', ?, 'audio/wav', 4096, ?, 'VERIFIED', 1300,
            ?, ?, 'test', 1, 'v2')""",
            (media_version_id, ASSET_ID, take_no, take_no, f"media/{media_version_id}.wav", "a" * 64, NOW, NOW),
        )
        row = ExplainerRepository(connection).insert(
            "narration_takes",
            {
                "video_id": VIDEO_ID,
                "segment_id": SEGMENT_ID,
                "canonical_segment_id": "seg_001",
                "locale": "zh-CN",
                "take_no": take_no,
                "media_asset_id": ASSET_ID,
                "media_version_id": media_version_id,
                "media_sha256": "a" * 64,
                "segment_hash": "h" * 64,
                "voice_profile_version_id": "voice-1",
                "model_ref": "voxcpm2",
                "measured_duration_ms": 1200 + take_no,
                "measured_sample_count": 57_600,
                "sample_rate_hz": 48_000,
                "status": "GENERATED",
                "selected": selected,
                "generation_json": {"schema_version": "localdrama.explainer.narration-take.v1"},
            },
        )
    return str(row["id"])


def _generation(database: Database, take_id: str) -> dict[str, object]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT generation_json FROM narration_takes WHERE id=?", (take_id,)
        ).fetchone()
    raw = row["generation_json"]
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


# --------------------------------------------------------------------------- #
# the adoption command
# --------------------------------------------------------------------------- #
def test_adopting_a_take_selects_it_and_demotes_its_siblings(database: Database) -> None:
    _seed(database)
    _take(database, take_no=1, selected=True)
    newer = _take(database, take_no=2)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        adopted = ExplainerNarrationService(repo).adopt_take(segment_id=SEGMENT_ID, actor="worker")
        rows = repo.list_where("narration_takes", {"segment_id": SEGMENT_ID}, order_by="take_no")
    assert adopted is not None
    assert str(adopted["id"]) == newer
    assert bool(adopted["selected"]) is True
    assert str(adopted["status"]) == "VERIFIED"
    # Only one take of a segment may be selected.
    by_take_no = {int(item["take_no"]): bool(item["selected"]) for item in rows}
    assert by_take_no == {1: False, 2: True}


def test_the_newer_adopted_take_wins_the_segment_lookup(database: Database) -> None:
    """The A03 precedence bug: an older selected take must not survive a re-read."""

    _seed(database)
    older = _take(database, take_no=1, selected=True)
    newer = _take(database, take_no=2)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        before = repo.selected_takes(VIDEO_ID)
        assert [str(item["id"]) for item in before] == [older]
        assert int(before[0]["take_no"]) == 1
        ExplainerNarrationService(repo).adopt_take(segment_id=SEGMENT_ID, actor="worker")
        after = repo.selected_takes(VIDEO_ID)
    assert [str(item["id"]) for item in after] == [newer]
    assert int(after[0]["take_no"]) == 2


def test_adoption_records_the_stage_that_made_it(database: Database) -> None:
    _seed(database)
    take_id = _take(database, take_no=1)
    with database.transaction() as connection:
        ExplainerNarrationService(ExplainerRepository(connection)).adopt_take(
            segment_id=SEGMENT_ID,
            take_id=take_id,
            actor="narration-tts-worker",
            record={"adoption_reason": "MEASURED_AND_DECODED", "probe_status": "PASS"},
        )
    generation = _generation(database, take_id)
    assert generation["adoption_authority"] == "MACHINE_STAGE"
    assert generation["adopted_by"] == "narration-tts-worker"
    assert generation["adoption_reason"] == "MEASURED_AND_DECODED"
    assert generation["probe_status"] == "PASS"
    # The measurement record the take already carried is preserved.
    assert generation["schema_version"] == "localdrama.explainer.narration-take.v1"


def test_adoption_can_target_one_explicit_take(database: Database) -> None:
    _seed(database)
    older = _take(database, take_no=1)
    _take(database, take_no=2)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        adopted = ExplainerNarrationService(repo).adopt_take(
            segment_id=SEGMENT_ID, take_id=older, actor="worker"
        )
    assert adopted is not None
    assert str(adopted["id"]) == older
    assert int(adopted["take_no"]) == 1


def test_adopting_a_segment_with_no_take_is_a_no_op(database: Database) -> None:
    _seed(database)
    with database.transaction() as connection:
        assert (
            ExplainerNarrationService(ExplainerRepository(connection)).adopt_take(
                segment_id=SEGMENT_ID, actor="worker"
            )
            is None
        )


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #
def test_the_standalone_tts_job_adopts_the_take_it_measured() -> None:
    """A wiring guard: the job family that produced an inert take now adopts."""

    source = (
        Path(__file__).resolve().parents[1]
        / "local_drama"
        / "application"
        / "worker_handlers"
        / "narration_tts.py"
    ).read_text(encoding="utf-8")
    assert "adopt_take(" in source
    assert '"adopted": adoption is not None' in source
    # The adoption must not happen before the measurement is known to be real.
    assert "instrumentally_verified" in source


def test_the_pipeline_and_the_job_family_share_one_adoption_implementation() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "local_drama"
        / "application"
        / "explainers"
        / "production_pipeline.py"
    ).read_text(encoding="utf-8")
    assert "adopt_take(segment_id=segment_id, actor=actor)" in source
    # A second inline UPDATE of ``selected`` is what let the two paths drift.
    assert "UPDATE narration_takes SET selected=0" not in source

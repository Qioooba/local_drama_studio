"""Picture adoption happens in the QC stage, after the checks — not in generation.

``VISUAL_GENERATION`` used to insert the active selection itself, so a candidate
became the beat's picture before anything had examined it and the vision/QC stages
were decorative (audit A06). The design puts it the other way round: generation
registers candidates, ``EXPLAINER_VISUAL_QC`` adopts them ("通过后统一采用"), and a
beat with no adopted candidate is unfinished work rather than a pass (design §4.2).

Requirement mapping: §4.2 (VISUAL_GENERATION registers; VISUAL_QC adopts) and §6.3
(one adoption entry).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_drama.application.explainers.production_pipeline import adopt_generated_candidates
from local_drama.application.explainers.quality import ExplainerQualityService
from local_drama.application.worker_handlers.explainer_qc import ADOPTION_STAGE, run_qc_layers
from local_drama.domain.explainers.contracts import ExplainerContractError, ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "visual-adopt-project"
VIDEO_ID = "visual-adopt-video"
BEAT_ID = "visual-adopt-beat"
EDITIONS = ("visual-adopt-ed-169", "visual-adopt-ed-916")
ASSET_ID = "visual-adopt-asset"
MEDIA_ID = "visual-adopt-media"
NOW = "2026-01-01T00:00:00Z"

#: Everything the required checks need, all measured.
FULLY_CHECKED = {
    "file_valid": True,
    "decoded": True,
    "content_relevant": True,
    "identity_ok": True,
    "constraints_ok": True,
    "text_readable": True,
}

#: What generation can honestly record: the file exists, nothing else was checked.
GENERATION_FACTS = {"file_valid": True, "content_checked": False, "measured_by": "VISUAL_GENERATION"}


def _seed(database: Database, *, editions: tuple[str, ...] = EDITIONS) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'va_proj', '画面采用', 'DRAFT', 'v2', ?, 300000, ?,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '画面采用', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
            version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_BEAT_CLIP', 'VIDEO', 1, '{}',
            ?, ?, 'test', 1, 'v2')""",
            (ASSET_ID, PROJECT_ID, VIDEO_ID, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
            byte_size, sha256, integrity_status, duration_ms, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 1, 'VISUAL_GENERATION', 'media/beat.mp4', 'video/mp4', 1024, ?, 'VERIFIED', 5000,
            ?, ?, 'test', 1, 'v2')""",
            (MEDIA_ID, ASSET_ID, "a" * 64, NOW, NOW),
        )
        repo = ExplainerRepository(connection)
        for index, edition_id in enumerate(editions):
            repo.insert(
                "explainer_editions",
                {
                    "id": edition_id,
                    "video_id": VIDEO_ID,
                    "edition_key": "zh-captioned-169" if index == 0 else "zh-captioned-916",
                    "voice_locale": "zh-CN",
                    "subtitle_mode": "BURNED",
                    "aspect_ratio": "16:9" if index == 0 else "9:16",
                },
            )
        repo.insert(
            "explainer_visual_beats",
            {
                "id": BEAT_ID,
                "video_id": VIDEO_ID,
                "code": "B001",
                "ordinal": 0,
                "render_type": "STILL_MOTION",
                "visual_intent": "画面",
                "entity_refs_json": ["ZHOU"],
            },
        )


def _candidate(database: Database, *, checks: dict[str, object] | None = None, **columns: object) -> str:
    with database.transaction() as connection:
        row = ExplainerRepository(connection).insert(
            "explainer_media_candidates",
            {
                "video_id": VIDEO_ID,
                "beat_id": BEAT_ID,
                "variant_no": 1,
                "candidate_kind": "CREATIVE",
                "purpose": "VISUAL",
                "media_asset_id": ASSET_ID,
                "media_version_id": MEDIA_ID,
                "media_sha256": "a" * 64,
                "status": "READY",
                "render_type_planned": "STILL_MOTION",
                "render_type_actual": "STILL_MOTION",
                "qc_summary_json": dict(checks if checks is not None else GENERATION_FACTS),
                **columns,
            },
        )
    return str(row["id"])


def _context() -> dict[str, object]:
    return {"project_id": PROJECT_ID, "video_id": VIDEO_ID, "edition_scope": "VIDEO"}


# --------------------------------------------------------------------------- #
# the adoption command
# --------------------------------------------------------------------------- #
def test_a_checked_candidate_is_adopted_for_every_edition(database: Database) -> None:
    _seed(database)
    _candidate(database, checks=FULLY_CHECKED)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = adopt_generated_candidates(repo, _context())
        selections = repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
    assert result["adopted_count"] == len(EDITIONS)
    assert result["needs_review_count"] == 0
    assert result["beats_without_candidate_count"] == 0
    assert result["every_beat_has_a_selection"] is True
    assert len(selections) == len(EDITIONS)
    assert {str(item["status"]) for item in selections} == {"ACTIVE"}
    assert {str(item["adoption_authority"]) for item in selections} == {"MACHINE_POLICY"}


def test_a_candidate_generation_only_registered_is_not_adopted(database: Database) -> None:
    """The A06 defect: generation's own bookkeeping must not become the picture."""

    _seed(database)
    _candidate(database, checks=GENERATION_FACTS)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = adopt_generated_candidates(repo, _context())
        selections = repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
    assert result["adopted_count"] == 0
    assert result["needs_review_count"] == 1
    entry = result["needs_review"][0]
    assert entry["reason"] == "NO_MACHINE_ADOPTABLE_CANDIDATE"
    # The report names exactly which checks were never measured.
    unknown = set(entry["candidates"][0]["unknown_checks"])
    assert "CONTENT_RELEVANCE" in unknown
    assert result["every_beat_has_a_selection"] is False
    assert selections == []


def test_a_beat_without_any_candidate_is_reported(database: Database) -> None:
    _seed(database)
    with database.transaction() as connection:
        result = adopt_generated_candidates(ExplainerRepository(connection), _context())
    assert result["beats_without_candidate_count"] == 1
    assert result["beats_without_candidate"][0]["reason"] == "NO_CANDIDATE"
    assert result["every_beat_has_a_selection"] is False


def test_a_candidate_with_a_broken_file_is_refused_even_so(database: Database) -> None:
    _seed(database)
    _candidate(database, checks={**FULLY_CHECKED, "decoded": False})
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = adopt_generated_candidates(repo, _context())
        selections = repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
    assert result["adopted_count"] == 0
    assert result["needs_review_count"] == 1
    assert "FILE_NOT_DECODED" in result["needs_review"][0]["candidates"][0]["blockers"]
    assert selections == []


def test_adoption_can_be_scoped_to_one_edition(database: Database) -> None:
    _seed(database)
    _candidate(database, checks=FULLY_CHECKED)
    context = {"project_id": PROJECT_ID, "video_id": VIDEO_ID, "edition_id": EDITIONS[0]}
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = adopt_generated_candidates(repo, context)
        selections = repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
    assert result["adopted_count"] == 1
    assert str(selections[0]["edition_id"]) == EDITIONS[0]


# --------------------------------------------------------------------------- #
# the QC stage that owns it
# --------------------------------------------------------------------------- #
def _qc_payload(*, stage: str = ADOPTION_STAGE) -> dict[str, object]:
    del stage
    return {
        "project_id": PROJECT_ID,
        "video_id": VIDEO_ID,
        "edition_id": EDITIONS[0],
        "edition_scope": "VIDEO",
        "subject_kind": "EDITION",
        "layers": ["FACT"],
    }


def test_the_visual_qc_stage_reports_adoption_and_does_not_pass_without_it(database: Database) -> None:
    _seed(database)
    _candidate(database, checks=GENERATION_FACTS)
    with database.transaction() as connection:
        report = run_qc_layers(
            _qc_payload(),
            quality_factory=lambda repo: ExplainerQualityService(repo),
            repo_factory=lambda: _ctx(connection),
            candidate_adopter=adopt_generated_candidates,
        )
    assert "candidate_adoption" in report
    assert report["candidate_adoption"]["needs_review_count"] == 1
    assert report["required_selections_present"] is False
    # Unfinished work must not be summarised as a pass.
    assert report["status"] == "NEEDS_HITL"
    assert report["machine_check"]["ok"] is False
    assert "等待人工处理" in report["summary"]


def test_the_visual_qc_stage_is_not_held_back_when_every_beat_was_adopted(database: Database) -> None:
    _seed(database)
    _candidate(database, checks=FULLY_CHECKED)
    with database.transaction() as connection:
        report = run_qc_layers(
            _qc_payload(),
            quality_factory=lambda repo: ExplainerQualityService(repo),
            repo_factory=lambda: _ctx(connection),
            candidate_adopter=adopt_generated_candidates,
        )
    assert report["candidate_adoption"]["adopted_count"] == len(EDITIONS)
    assert report["required_selections_present"] is True
    # Adoption completed, so it must not be what holds the stage back.  The status
    # here still reflects the layers: with no fact reader injected the FACT layer is
    # NOT_RUN, and a layer that did not run is never summarised as a pass.
    assert report["status"] != "NEEDS_HITL"
    assert report["status"] == "NOT_RUN"


def test_the_composition_qc_stage_does_not_adopt(database: Database) -> None:
    _seed(database)
    _candidate(database, checks=GENERATION_FACTS)
    with database.transaction() as connection:
        report = run_qc_layers(
            {**_qc_payload(), "subject_kind": "EDITION"},
            stage="COMPOSITION_QC",
            quality_factory=lambda repo: ExplainerQualityService(repo),
            repo_factory=lambda: _ctx(connection),
            candidate_adopter=adopt_generated_candidates,
        )
    assert report["candidate_adoption"] is None


# --------------------------------------------------------------------------- #
# the operator's batch decision
# --------------------------------------------------------------------------- #
def test_a_preview_reports_the_scope_and_writes_nothing(database: Database) -> None:
    _seed(database)
    _candidate(database, checks=GENERATION_FACTS)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        preview = adopt_generated_candidates(repo, _context(), authority="HUMAN", dry_run=True)
        selections = repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
    assert preview["dry_run"] is True
    assert preview["planned_count"] == len(EDITIONS)
    assert preview["adopted_count"] == 0
    assert selections == []
    # The preview names the check that was never measured.
    assert "CONTENT_RELEVANCE" in preview["planned"][0]["unknown_checks"]
    assert preview["planned"][0]["would_use_machine_policy"] is False


def test_human_authority_adopts_past_an_unmeasured_content_check(database: Database) -> None:
    """The operator is the authority for content; the adoption records that."""

    _seed(database)
    _candidate(database, checks=GENERATION_FACTS)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = adopt_generated_candidates(
            repo, _context(), authority="HUMAN", actor="reviewer"
        )
        selections = repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
    assert result["adopted_count"] == len(EDITIONS)
    assert result["every_beat_has_a_selection"] is True
    assert {str(item["adoption_authority"]) for item in selections} == {"HUMAN"}
    assert {bool(item["locked_by_human"]) for item in selections} == {True}
    assert {str(item["actor"]) for item in selections} == {"reviewer"}


def test_human_authority_still_refuses_a_hard_technical_failure(database: Database) -> None:
    """A file that does not decode cannot be reviewed away (design §6.2)."""

    _seed(database)
    _candidate(database, checks={**GENERATION_FACTS, "file_valid": False})
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = adopt_generated_candidates(
            repo, _context(), authority="HUMAN", actor="reviewer"
        )
        selections = repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
    assert result["adopted_count"] == 0
    assert result["needs_review_count"] == 1
    assert selections == []


def test_a_human_actor_is_required_by_the_adoption_it_records(database: Database) -> None:
    _seed(database)
    _candidate(database, checks=GENERATION_FACTS)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = adopt_generated_candidates(repo, _context(), authority="HUMAN", actor="")
    # ``adopt_selection`` refuses HUMAN authority without a real actor, so every
    # beat lands in the review list instead of writing an anonymous adoption.
    assert result["adopted_count"] == 0
    assert result["needs_review_count"] == len(EDITIONS)


def test_the_batch_can_be_scoped_to_selected_beats(database: Database) -> None:
    _seed(database)
    _candidate(database, checks=FULLY_CHECKED)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = adopt_generated_candidates(
            repo, _context(), beat_ids=["some-other-beat"]
        )
    assert result["adopted_count"] == 0
    assert result["planned_count"] == 0


# --------------------------------------------------------------------------- #
# the HTTP command
# --------------------------------------------------------------------------- #
def test_the_route_previews_then_requires_a_key_and_records_human_adoption(database: Database) -> None:
    from local_drama.api.routes.explainers import _adopt_generated_beats
    from local_drama.api.schemas.explainers import ExplainerBatchAdoptionRequest

    _seed(database)
    _candidate(database, checks=GENERATION_FACTS)
    revision = 1
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        preview = _adopt_generated_beats(
            repo,
            PROJECT_ID,
            ExplainerBatchAdoptionRequest(expected_revision=revision, actor="reviewer", confirm=False),
            None,
        )
        assert preview["requires_confirmation"] is True
        assert preview["submitted"] is False
        assert preview["planned_count"] == len(EDITIONS)
        assert repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID}) == []

        # Confirming without a key must not silently write.
        with pytest.raises(ExplainerContractError) as error:
            _adopt_generated_beats(
                repo,
                PROJECT_ID,
                ExplainerBatchAdoptionRequest(expected_revision=revision, actor="reviewer", confirm=True),
                None,
            )
        assert error.value.code == "IDEMPOTENCY_KEY_REQUIRED"

        confirmed = _adopt_generated_beats(
            repo,
            PROJECT_ID,
            ExplainerBatchAdoptionRequest(expected_revision=revision, actor="reviewer", confirm=True),
            "batch-adopt-1",
        )
        selections = repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
    assert confirmed["requires_confirmation"] is False
    assert confirmed["submitted"] is True
    assert confirmed["adopted_count"] == len(EDITIONS)
    assert {str(item["adoption_authority"]) for item in selections} == {"HUMAN"}


def test_the_route_refuses_a_stale_revision(database: Database) -> None:
    from local_drama.api.routes.explainers import _adopt_generated_beats
    from local_drama.api.schemas.explainers import ExplainerBatchAdoptionRequest

    _seed(database)
    with database.transaction() as connection:
        with pytest.raises(ExplainerContractError) as error:
            _adopt_generated_beats(
                ExplainerRepository(connection),
                PROJECT_ID,
                ExplainerBatchAdoptionRequest(expected_revision=99, actor="reviewer", confirm=False),
                None,
            )
    assert error.value.code == "STALE_REVISION"


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #
def test_generation_no_longer_writes_a_selection_itself() -> None:
    """A wiring guard: the bypass is gone from the generation stage's module."""

    source = (
        Path(__file__).resolve().parents[1]
        / "local_drama"
        / "application"
        / "explainers"
        / "production_pipeline.py"
    ).read_text(encoding="utf-8")
    assert "explainer_beat_selections" not in source, (
        "picture adoption must go through ExplainerStoryboardService.adopt_selection"
    )


class _ctx:
    """Re-enter the open transaction so the handler shares the test's connection."""

    def __init__(self, connection: object) -> None:
        self._connection = connection

    def __enter__(self) -> ExplainerRepository:
        return ExplainerRepository(self._connection)  # type: ignore[arg-type]

    def __exit__(self, *args: object) -> bool:
        return False

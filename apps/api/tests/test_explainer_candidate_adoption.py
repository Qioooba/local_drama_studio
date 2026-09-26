"""Candidate adoption must be tri-state, and there must be exactly one entry.

Audit finding A06 had two halves:

* ``evaluate_candidates`` only ever tested ``field is False``, so a candidate whose
  checks were *never measured* produced no blocker and was reported
  ``PASSED_ALL_STAGES`` — a high aesthetic score was then enough to have it
  recommended (matrix case V06);
* the HTTP selection route inserted ``explainer_beat_selections`` itself and never
  called ``ExplainerStoryboardService.adopt_selection``, so it skipped the
  must-be-motion refusal, the frozen source window and the machine-check gate.

Requirement mapping: design §6.2 (check states) and §6.3 (one adoption entry).
"""

from __future__ import annotations

import pytest

from local_drama.application.explainers.storyboard import (
    CANDIDATE_CHECK_FIELDS,
    CHECK_FAIL,
    CHECK_NOT_RUN,
    CHECK_PASS,
    CHECK_UNKNOWN,
    build_storyboard_service,
)
from local_drama.domain.explainers.contracts import ExplainerContractError, ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "adopt-project"
VIDEO_ID = "adopt-video"
BEAT_ID = "adopt-beat"
EDITION_ID = "adopt-edition"
ASSET_ID = "adopt-asset"
MEDIA_ID = "adopt-media"
NOW = "2026-01-01T00:00:00Z"

#: Every applicable required check declared as passing.
FULLY_CHECKED = {
    "file_valid": True,
    "decoded": True,
    "content_relevant": True,
    "identity_ok": True,
    "constraints_ok": True,
    "text_readable": True,
    "aesthetic_score": 0.4,
}

#: The audit's promoted candidate: a great aesthetic score and no checks at all.
UNCHECKED = {"aesthetic_score": 0.99}


def _seed(database: Database, *, with_identity: bool = False) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'adopt_proj', '采用测试', 'DRAFT', 'v2', ?, 300000, ?,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '采用测试', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status)
            VALUES (?, ?, 'zh-captioned-169', 'zh-CN', 'READY')""",
            (EDITION_ID, VIDEO_ID),
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
        repo.insert(
            "explainer_visual_beats",
            {
                "id": BEAT_ID,
                "video_id": VIDEO_ID,
                "code": "B001",
                "ordinal": 0,
                "render_type": "I2V",
                "visual_intent": "采用测试画面",
                # An identity binding makes the identity/state check applicable.
                "entity_refs_json": ["ZHOU"] if with_identity else [],
            },
        )


def _candidate(database: Database, *, candidate_id: str = "cand-1", **columns: object) -> dict[str, object]:
    """Insert a candidate, routing any check verdicts into ``qc_summary_json``.

    ``explainer_media_candidates`` has no column per check — the QC layers write
    them into the candidate's ``qc_summary``, which is what the evaluator reads.
    """

    check_columns = {
        key: columns.pop(key) for key in list(columns) if key in CANDIDATE_CHECK_FIELDS
    }
    with database.transaction() as connection:
        return ExplainerRepository(connection).insert(
            "explainer_media_candidates",
            {
                "id": candidate_id,
                "video_id": VIDEO_ID,
                "beat_id": BEAT_ID,
                "variant_no": 1,
                "candidate_kind": "CREATIVE",
                "purpose": "VISUAL",
                "media_asset_id": ASSET_ID,
                "media_version_id": MEDIA_ID,
                "media_sha256": "a" * 64,
                "status": "READY",
                "render_type_planned": "I2V",
                "render_type_actual": "I2V",
                "qc_summary_json": check_columns,
                **columns,
            },
        )


# --------------------------------------------------------------------------- #
# check states (design §6.2 / matrix V06)
# --------------------------------------------------------------------------- #
def test_missing_checks_are_unknown_and_never_recommended(database: Database) -> None:
    """V06: a high aesthetic score cannot promote a candidate with no checks."""

    _seed(database)
    with database.connect() as connection:
        report = build_storyboard_service(ExplainerRepository(connection)).evaluate_candidates(
            beat_id=BEAT_ID,
            candidates=[
                {"candidate_id": "unverified", **UNCHECKED},
                {"candidate_id": "verified", **FULLY_CHECKED},
            ],
        )
    assert report["unknown_is_not_a_pass"] is True
    by_id = {item["candidate_id"]: item for item in report["ranked"]}
    unchecked = by_id["unverified"]
    verified = by_id["verified"]
    # The unchecked candidate is not adoptable and not recommended, despite 0.99.
    assert unchecked["machine_adoption_allowed"] is False
    assert unchecked["human_review_required"] is True
    assert unchecked["adoption_tier_name"] != "PASSED_ALL_STAGES"
    assert verified["machine_adoption_allowed"] is True
    assert report["recommended_candidate_id"] == "verified"
    assert report["eligible_count"] == 1
    assert report["needs_review_count"] == 1
    # Verified work is ranked ahead of merely well-scoring work.
    assert verified["rank"] < unchecked["rank"]


def test_absent_check_reports_unknown_not_pass(database: Database) -> None:
    _seed(database)
    with database.connect() as connection:
        report = build_storyboard_service(ExplainerRepository(connection)).evaluate_candidates(
            beat_id=BEAT_ID, candidates=[{"candidate_id": "only", **UNCHECKED}]
        )
    entry = report["ranked"][0]
    assert entry["required_checks_state"]["FILE_DECODE"] == CHECK_UNKNOWN
    assert entry["required_checks_state"]["CONTENT_RELEVANCE"] == CHECK_UNKNOWN
    assert entry["required_checks_state"]["READABILITY"] == CHECK_NOT_RUN
    assert report["recommended_candidate_id"] is None


def test_a_fully_checked_candidate_passes_every_required_check(database: Database) -> None:
    _seed(database, with_identity=True)
    with database.connect() as connection:
        report = build_storyboard_service(ExplainerRepository(connection)).evaluate_candidates(
            beat_id=BEAT_ID, candidates=[{"candidate_id": "good", **FULLY_CHECKED}]
        )
    entry = report["ranked"][0]
    assert entry["machine_adoption_allowed"] is True
    assert entry["adoption_tier_name"] == "PASSED_ALL_STAGES"
    assert entry["unknown_checks"] == []
    assert set(entry["required_checks_state"].values()) == {CHECK_PASS}
    assert report["recommended_candidate_id"] == "good"


def test_identity_check_is_not_run_when_the_beat_binds_no_reference(database: Database) -> None:
    """An inapplicable check is NOT_RUN with a reason, not a silent pass."""

    _seed(database, with_identity=False)
    with database.connect() as connection:
        report = build_storyboard_service(ExplainerRepository(connection)).evaluate_candidates(
            beat_id=BEAT_ID, candidates=[{"candidate_id": "good", **FULLY_CHECKED}]
        )
    entry = report["ranked"][0]
    assert entry["required_checks_state"]["IDENTITY_CONSTRAINTS"] == CHECK_NOT_RUN
    assert "BEAT_BINDS_NO_IDENTITY_REFERENCE" in entry["check_reasons"]
    # Not applicable, so it does not block a machine adoption.
    assert entry["machine_adoption_allowed"] is True


def test_a_needed_identity_check_that_was_not_measured_blocks_machine_adoption(database: Database) -> None:
    _seed(database, with_identity=True)
    partial = {key: value for key, value in FULLY_CHECKED.items() if key not in {"identity_ok", "constraints_ok"}}
    with database.connect() as connection:
        report = build_storyboard_service(ExplainerRepository(connection)).evaluate_candidates(
            beat_id=BEAT_ID, candidates=[{"candidate_id": "partial", **partial}]
        )
    entry = report["ranked"][0]
    assert entry["required_checks_state"]["IDENTITY_CONSTRAINTS"] == CHECK_UNKNOWN
    assert entry["machine_adoption_allowed"] is False


def test_explicit_failure_still_reports_its_blocker(database: Database) -> None:
    _seed(database)
    broken = {**FULLY_CHECKED, "file_valid": False}
    with database.connect() as connection:
        report = build_storyboard_service(ExplainerRepository(connection)).evaluate_candidates(
            beat_id=BEAT_ID, candidates=[{"candidate_id": "broken", **broken}]
        )
    entry = report["ranked"][0]
    assert entry["required_checks_state"]["FILE_DECODE"] == CHECK_FAIL
    assert entry["adoption_blockers"] == ["FILE_NOT_DECODED"]
    assert entry["machine_adoption_allowed"] is False


# --------------------------------------------------------------------------- #
# one adoption entry (design §6.3)
# --------------------------------------------------------------------------- #
def test_machine_adoption_refuses_a_candidate_with_unmeasured_checks(database: Database) -> None:
    _seed(database)
    _candidate(database)
    with database.transaction() as connection:
        service = build_storyboard_service(ExplainerRepository(connection))
        with pytest.raises(ExplainerContractError) as error:
            service.adopt_selection(
                project_id=PROJECT_ID,
                video_id=VIDEO_ID,
                beat_id=BEAT_ID,
                candidate_id="cand-1",
                edition_id=EDITION_ID,
                authority="MACHINE_POLICY",
            )
    assert "必需检查" in error.value.message


def test_human_adoption_records_the_gap_but_refuses_a_hard_technical_failure(database: Database) -> None:
    _seed(database)
    _candidate(database, candidate_id="cand-1", file_valid=False)
    with database.transaction() as connection:
        service = build_storyboard_service(ExplainerRepository(connection))
        with pytest.raises(ExplainerContractError) as error:
            service.adopt_selection(
                project_id=PROJECT_ID,
                video_id=VIDEO_ID,
                beat_id=BEAT_ID,
                candidate_id="cand-1",
                edition_id=EDITION_ID,
                authority="HUMAN",
                actor="reviewer",
            )
    assert "技术硬错误" in error.value.message


def test_readopting_the_same_candidate_is_not_a_change(database: Database) -> None:
    """Design §6.3: the same candidate and hash must not be re-rendered."""

    _seed(database)
    candidate = _candidate(database, candidate_id="cand-1")
    with database.transaction() as connection:
        service = build_storyboard_service(ExplainerRepository(connection))
        first = service.adopt_selection(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beat_id=BEAT_ID,
            candidate_id="cand-1",
            edition_id=EDITION_ID,
            authority="HUMAN",
            actor="reviewer",
        )
        second = service.adopt_selection(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beat_id=BEAT_ID,
            candidate_id="cand-1",
            edition_id=EDITION_ID,
            authority="HUMAN",
            actor="reviewer",
        )
    assert first["reused_existing_selection"] is False
    assert second["reused_existing_selection"] is True
    assert second["selection_id"] == first["selection_id"]
    assert second["superseded_selection_id"] is None
    assert second["invalidated"] == []
    # Exactly one selection row exists: nothing was superseded and re-created.
    with database.connect() as connection:
        rows = ExplainerRepository(connection).list_where(
            "explainer_beat_selections", {"beat_id": BEAT_ID}
        )
    assert len(rows) == 1
    assert str(rows[0]["candidate_id"]) == str(candidate["id"])


def test_adoption_freezes_the_source_window_from_the_execution_snapshot(database: Database) -> None:
    """The frame window comes from the snapshot, not from a column that never existed."""

    _seed(database)
    _candidate(
        database,
        candidate_id="cand-1",
        file_valid=True,
        decoded=True,
        content_relevant=True,
        identity_ok=True,
        constraints_ok=True,
        text_readable=True,
        execution_snapshot_json={"source_in_us": 1_500_000, "source_out_us": 4_000_000},
    )
    with database.transaction() as connection:
        result = build_storyboard_service(ExplainerRepository(connection)).adopt_selection(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beat_id=BEAT_ID,
            candidate_id="cand-1",
            edition_id=EDITION_ID,
            authority="MACHINE_POLICY",
        )
    assert result["source_window"] == {"source_in_us": 1_500_000, "source_out_us": 4_000_000}
    assert int(result["selection"]["source_in_us"]) == 1_500_000


# --------------------------------------------------------------------------- #
# the HTTP entry point must not bypass the service
# --------------------------------------------------------------------------- #
def test_the_selection_route_uses_the_service_gate(database: Database) -> None:
    """The old route inserted the row itself and skipped every service check."""

    from local_drama.api.routes.explainers import _select_candidate
    from local_drama.api.schemas.explainers import ExplainerSelectionRequest

    _seed(database)
    _candidate(database, candidate_id="unverified")
    payload = ExplainerSelectionRequest(
        expected_revision=1, candidate_id="unverified", edition_id=EDITION_ID
    )
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        # A machine adoption of unmeasured material is refused by the service the
        # route now delegates to, instead of being written straight to the table.
        with pytest.raises(ExplainerContractError):
            _select_candidate(repo, PROJECT_ID, BEAT_ID, payload)
        assert repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID}) == []


def test_the_selection_route_records_a_human_adoption_and_its_lock(database: Database) -> None:
    from local_drama.api.routes.explainers import _select_candidate
    from local_drama.api.schemas.explainers import ExplainerSelectionRequest

    _seed(database)
    _candidate(
        database,
        candidate_id="cand-1",
        file_valid=True,
        decoded=True,
        execution_snapshot_json={"source_in_us": 0, "source_out_us": 4_000_000},
    )
    payload = ExplainerSelectionRequest(
        expected_revision=1, candidate_id="cand-1", edition_id=EDITION_ID, lock=True, actor="reviewer"
    )
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        result = _select_candidate(repo, PROJECT_ID, BEAT_ID, payload)
        assert repo.has_human_lock(BEAT_ID) is True
    assert result["adoption_authority"] == "HUMAN"
    assert result["human_approval_written"] is False
    # A human adoption may record an UNKNOWN content check, and says so.
    assert result["reused_existing_selection"] is False
    assert result["supersedes_previous"] is True
    assert result["source_window"]["source_out_us"] == 4_000_000

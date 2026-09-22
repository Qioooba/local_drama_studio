"""Explainer factory foundation contracts (W01/W08).

These tests assert the structural decisions the design says must not drift.  They
cover real SQLite through the repository, real migration output, and the real
worker registry; no model call and no GPU is involved, so nothing here is
evidence of generated-media quality.

Requirement mapping (《测试与验收矩阵》§4):
* REQ-01 / REQ-21 -> project kind, migration, "no fake episode"
* REQ-17         -> machine policy vs human approval vs publication authorization
* REQ-18         -> preflight freeze, plan hash, idempotent run submission
* REQ-19         -> schedule trigger key without config_revision
* REQ-20         -> staleness propagation graph
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from local_drama.application.errors import api_error_from_explainer
from local_drama.application.explainers.production import (
    REQUIRED_CAPABILITIES,
    TASK_SKELETON,
    ExplainerProductionService,
)
from local_drama.domain.explainers.contracts import (
    Budget,
    DurationMode,
    ExplainerContractError,
    ExplainerErrorCode,
    ProductKind,
)
from local_drama.domain.explainers.policies import (
    AssetLicense,
    BudgetLedger,
    CoverageFact,
    IssueFact,
    evaluate_license_scope,
    evaluate_machine_acceptance,
    resolve_visual_fallback,
    staleness_plan,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "project-explainer-1"


def _seed_explainer_project(database: Database, *, kind: str = ProductKind.EXPLAINER.value) -> str:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, ?, ?, 'DRAFT', 'v2', ?, 300000, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, f"EXP-{kind}", "灯塔最后一页值班记录", PROJECT_ID, kind),
        )
    return PROJECT_ID


def _probe_all_available(capability: str, *, project_id: str) -> dict[str, object]:
    del project_id
    return {"available": True, "profile_version_id": f"profile-{capability}", "execution_class": "LOCAL"}


def _outputs() -> list[dict[str, object]]:
    return [
        {
            "edition_key": "zh-clean-169",
            "voice_locale": "zh-CN",
            "subtitle_locales": [],
            "subtitle_mode": "NONE",
            "aspect_ratio": "16:9",
            "fps": {"num": 25, "den": 1},
            "duration_policy": "USE_SOURCE_TARGET",
            "allow_soft_subtitle_fallback": False,
        },
        {
            "edition_key": "zh-bilingual-169",
            "voice_locale": "zh-CN",
            "subtitle_locales": ["zh-CN", "en-US"],
            "subtitle_mode": "BILINGUAL_BURNED",
            "aspect_ratio": "16:9",
            "fps": {"num": 25, "den": 1},
            "duration_policy": "USE_SOURCE_TARGET",
            "allow_soft_subtitle_fallback": False,
        },
    ]


def _add_source(database: Database, video_id: str) -> None:
    with database.transaction() as connection:
        repository = ExplainerRepository(connection)
        packet = repository.insert(
            "explainer_research_packets",
            {
                "video_id": video_id,
                "revision_no": 1,
                "status": "READY",
                "mode": "OFFLINE_IMPORT",
                "topic": "灯塔",
                "max_external_requests": 0,
                "content_hash": "a" * 64,
            },
        )
        repository.insert(
            "explainer_sources",
            {
                "packet_id": packet["id"],
                "video_id": video_id,
                "project_id": PROJECT_ID,
                "source_kind": "AUTHORED_FICTION_PACK",
                "title": "原创虚构事实包",
                "event_date_precision": "SECOND",
                "fetched_at": "2026-01-01T00:00:00Z",
                "body_sha256": "b" * 64,
                "credibility_kind": "AUTHORED_FICTION",
                "retrieved_via": "USER_SUPPLIED",
            },
        )


# --------------------------------------------------------------------------- #
# migration and product kind
# --------------------------------------------------------------------------- #
def test_migration_adds_product_kind_and_backfills_drama(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, created_at, updated_at, created_by)
            VALUES ('legacy-drama', 'LEGACY', '旧短剧', 'DRAFT', 'v2', 'LEGACY', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')"""
        )
        row = connection.execute("SELECT product_kind FROM projects WHERE id='legacy-drama'").fetchone()
    assert row is not None
    assert row["product_kind"] == ProductKind.DRAMA.value


def test_migration_creates_explainer_tables_and_active_stages(database: Database) -> None:
    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        stages = {
            row[0]
            for row in connection.execute("SELECT code FROM job_stage_definitions WHERE active=1")
        }
    required_tables = {
        "channel_profiles",
        "channel_profile_versions",
        "explainer_videos",
        "explainer_research_packets",
        "explainer_sources",
        "explainer_source_spans",
        "explainer_claims",
        "claim_evidence",
        "explainer_events",
        "explainer_entities",
        "entity_state_revisions",
        "explainer_script_revisions",
        "explainer_chapters",
        "narration_segments",
        "explainer_visual_beats",
        "beat_narration_links",
        "explainer_editions",
        "narration_takes",
        "narration_alignment_revisions",
        "explainer_subtitle_revisions",
        "composition_revisions",
        "composition_items",
        "composition_renders",
        "composition_render_chunks",
        "composition_deliveries",
        "explainer_runs",
        "explainer_step_bindings",
        "artifact_dependencies",
        "explainer_media_candidates",
        "explainer_beat_selections",
        "run_identity_inputs",
        "entity_identity_bindings",
        "explainer_qc_reports",
        "explainer_qc_issues",
        "explainer_decisions",
        "explainer_schedules",
        "schedule_occurrences",
        "publication_packages",
        "publication_receipts",
    }
    assert required_tables <= names
    assert {
        "RESEARCH_ACQUIRE",
        "FACT_EXTRACT",
        "NARRATION_WRITE",
        "NARRATION_TTS",
        "NARRATION_ALIGN",
        "EXPLAINER_STORYBOARD",
        "EXPLAINER_VISUAL_QC",
        "COMPOSITION_RENDER",
        "COMPOSITION_QC",
        "EXPLAINER_POLICY_EVALUATE",
        "EXPLAINER_EXPORT",
    } <= stages


def test_explainer_tables_do_not_reuse_legacy_episode_tables(database: Database) -> None:
    """The explainer domain must not write into the drama subtitle/audio tables."""

    with database.connect() as connection:
        subtitle_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(subtitle_revisions)")
        }
        audio_columns = {row[1] for row in connection.execute("PRAGMA table_info(audio_bindings)")}
        explainer_subtitle = {
            row[1] for row in connection.execute("PRAGMA table_info(explainer_subtitle_revisions)")
        }
    assert "edition_id" in explainer_subtitle
    assert "narration_take_id" not in subtitle_columns
    assert "narration_take_id" not in audio_columns


def test_schedule_trigger_key_excludes_config_revision(database: Database) -> None:
    with database.connect() as connection:
        indexes = connection.execute("PRAGMA index_list('schedule_occurrences')").fetchall()
        unique_columns: list[tuple[str, ...]] = []
        for index in indexes:
            if not index["unique"]:
                continue
            columns = tuple(
                row[2] for row in connection.execute(f"PRAGMA index_info('{index['name']}')")
            )
            unique_columns.append(columns)
    assert ("schedule_id", "scheduled_for") in unique_columns
    assert all("config_revision" not in columns for columns in unique_columns)


# --------------------------------------------------------------------------- #
# project kind enforcement
# --------------------------------------------------------------------------- #
def test_create_video_requires_explainer_project(database: Database) -> None:
    _seed_explainer_project(database, kind=ProductKind.DRAMA.value)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    with pytest.raises(ExplainerContractError) as error:
        service.create_video(
            project_id=PROJECT_ID,
            title="标题",
            topic="主题",
            content_kind="ORIGINAL_FICTION",
            input_kind="TOPIC",
            input_payload={},
            duration_mode="TARGET",
            target_seconds=300,
            tolerance_percent=5.0,
            source_locale="zh-CN",
            automation_mode="AUTO_WITH_EXCEPTIONS",
            inference_mode="LOCAL_ONLY",
            research_mode="OFFLINE_IMPORT",
        )
    assert error.value.code == "INVALID_REQUEST"


def test_one_video_per_project_and_no_episode_creation(database: Database) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔最后一页值班记录",
        topic="观察窗为什么短暂无光",
        content_kind="ORIGINAL_FICTION",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "fixture:sample_episode.json", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="FIXED",
        target_seconds=300,
        tolerance_percent=0.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    assert video["duration_mode"] == "FIXED"
    with database.connect() as connection:
        episode_count = connection.execute(
            "SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?",
            (PROJECT_ID,),
        ).fetchone()[0]
    assert episode_count == 0
    with pytest.raises(ExplainerContractError) as error:
        service.create_video(
            project_id=PROJECT_ID,
            title="第二个作品",
            topic="重复",
            content_kind="ORIGINAL_FICTION",
            input_kind="TOPIC",
            input_payload={},
            duration_mode="TARGET",
            target_seconds=300,
            tolerance_percent=5.0,
            source_locale="zh-CN",
            automation_mode="AUTO_WITH_EXCEPTIONS",
            inference_mode="LOCAL_ONLY",
            research_mode="OFFLINE_IMPORT",
        )
    assert "一个解说项目" in error.value.message


def test_explainer_project_rejects_seasons_and_episodes(database: Database) -> None:
    from local_drama.application.projects import ProjectService
    from local_drama.domain.errors import DomainRuleError

    service = ProjectService(database, Path("."))
    with pytest.raises(DomainRuleError) as error:
        service.create_project(
            code="exp_bad",
            title="错误项目",
            episode_count=1,
            season_count=1,
            aspect_ratio="16:9",
            fps_num=25,
            fps_den=1,
            allow_unconfigured_capabilities=True,
            product_kind=ProductKind.EXPLAINER.value,
        )
    assert error.value.code == "INVALID_EPISODE_COUNT"


def test_explainer_project_creation_needs_no_episode(database: Database, tmp_path: Path) -> None:
    from local_drama.application.projects import ProjectService

    service = ProjectService(database, tmp_path / "projects")
    project = service.create_project(
        code="exp_lighthouse",
        title="灯塔最后一页值班记录",
        episode_count=0,
        season_count=0,
        aspect_ratio="16:9",
        fps_num=25,
        fps_den=1,
        allow_unconfigured_capabilities=True,
        product_kind=ProductKind.EXPLAINER.value,
    )
    assert project["product_kind"] == ProductKind.EXPLAINER.value
    with database.connect() as connection:
        seasons = connection.execute(
            "SELECT COUNT(*) FROM seasons WHERE project_id=?", (project["id"],)
        ).fetchone()[0]
        episodes = connection.execute(
            "SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=?",
            (project["id"],),
        ).fetchone()[0]
    assert seasons == 0
    assert episodes == 0
    # The drama filter must not return an explainer project and vice versa.
    page = service.list_projects_page(limit=50, product_kind=ProductKind.DRAMA.value)
    assert all(item["product_kind"] == ProductKind.DRAMA.value for item in page["items"])
    explainer_page = service.list_projects_page(limit=50, product_kind=ProductKind.EXPLAINER.value)
    assert [item["id"] for item in explainer_page["items"]] == [project["id"]]


# --------------------------------------------------------------------------- #
# preflight freeze
# --------------------------------------------------------------------------- #
def test_preflight_is_blocked_without_capability_probe(database: Database) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="ORIGINAL_FICTION",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "x", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="TARGET",
        target_seconds=300,
        tolerance_percent=5.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    del video
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    assert report["executable"] is False
    codes = {blocker["code"] for blocker in report["blockers"]}
    assert ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value in codes
    assert report["capability_snapshot"]["probed"] is False
    assert all(
        entry["status"] == "UNKNOWN" for entry in report["capability_snapshot"]["capabilities"]
    )
    for entry in report["capability_snapshot"]["capabilities"]:
        assert entry["available"] is False


def test_preflight_blocks_missing_sources_then_executes(database: Database) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="ORIGINAL_FICTION",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "x", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="FIXED",
        target_seconds=300,
        tolerance_percent=0.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    blocked = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    assert blocked["executable"] is False
    assert ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value in {
        blocker["code"] for blocker in blocked["blockers"]
    }
    assert blocked["categories"]["NEEDS_SOURCE_MATERIAL"]

    _add_source(database, str(video["id"]))
    ready = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    assert ready["executable"] is True
    assert ready["plan_scope"]["internal_expansion_can_self_stale"] is False
    assert set(ready["plan_scope"]["does_not_cover"]) == {
        "script_revision",
        "narration_takes",
        "final_shot_plan",
    }
    assert ready["estimate"]["timing_basis"]["timing_status"].startswith("TOPIC_ROUGH_ESTIMATE")
    assert ready["estimate"]["shot_arithmetic"]["estimated_shot_count"] > 0
    assert len(ready["task_skeleton"]) == len(TASK_SKELETON)
    assert ready["categories"]["EXECUTABLE"] == []


def test_preflight_blocks_unresolved_core_conflict(database: Database) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="FACTUAL_EXPLAINER",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "x", "role": "RESEARCH_SOURCE"}]},
        duration_mode="TARGET",
        target_seconds=300,
        tolerance_percent=5.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    _add_source(database, str(video["id"]))
    with database.transaction() as connection:
        ExplainerRepository(connection).insert(
            "explainer_claims",
            {
                "video_id": video["id"],
                "code": "C001",
                "statement": "三名失踪者的结局存在冲突",
                "statement_kind": "FACT",
                "status": "DISPUTED",
                "importance": "CORE",
                "verified_as_history": False,
            },
        )
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    assert report["executable"] is False
    assert ExplainerErrorCode.CLAIM_CONFLICT.value in {blocker["code"] for blocker in report["blockers"]}
    assert report["categories"]["NEEDS_SOURCE_MATERIAL"]


def test_plan_hash_is_stable_across_repeated_preflights(database: Database) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="ORIGINAL_FICTION",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "x", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="FIXED",
        target_seconds=300,
        tolerance_percent=0.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    _add_source(database, str(video["id"]))
    first = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    second = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    assert first["plan_hash"] == second["plan_hash"]
    # An authorized internal expansion (a new script revision) must not make the
    # frozen plan look stale.
    with database.transaction() as connection:
        ExplainerRepository(connection).insert(
            "explainer_script_revisions",
            {
                "video_id": video["id"],
                "revision_no": 1,
                "locale": "zh-CN",
                "title": "副本文稿",
                "content_hash": "c" * 64,
            },
        )
    third = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    assert third["plan_hash"] == first["plan_hash"]


# --------------------------------------------------------------------------- #
# run projection
# --------------------------------------------------------------------------- #
def test_run_submission_rejects_stale_plan_and_is_idempotent(database: Database) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="ORIGINAL_FICTION",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "x", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="FIXED",
        target_seconds=300,
        tolerance_percent=0.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    _add_source(database, str(video["id"]))
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs())

    with pytest.raises(ExplainerContractError) as stale:
        service.submit_run(project_id=PROJECT_ID, plan_hash="0" * 64, idempotency_key="run-1", outputs=_outputs())
    assert stale.value.code == ExplainerErrorCode.STALE_PLAN.value

    run = service.submit_run(
        project_id=PROJECT_ID, plan_hash=report["plan_hash"], idempotency_key="run-1", outputs=_outputs()
    )
    replay = service.submit_run(
        project_id=PROJECT_ID, plan_hash=report["plan_hash"], idempotency_key="run-1", outputs=_outputs()
    )
    assert replay["idempotent_replay"] is True
    assert replay["id"] == run["id"]

    projected = service.get_run(run_id=str(run["id"]))
    assert projected["execution_authority"] == {
        "source_of_truth": "automation_workflow_runs+jobs+job_attempts",
        "explainer_runs_is_projection": True,
        "second_claim_queue": False,
    }
    assert len(projected["steps"]) == len(TASK_SKELETON)
    assert projected["step_statuses"]["RESEARCH_ACQUIRE"] == "PENDING"
    assert projected["step_statuses"]["FACT_EXTRACT"] == "BLOCKED"


def test_step_completion_requires_reason_when_skipped_and_unblocks_dependents(
    database: Database,
) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="ORIGINAL_FICTION",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "x", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="TARGET",
        target_seconds=300,
        tolerance_percent=5.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    _add_source(database, str(video["id"]))
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    run = service.submit_run(
        project_id=PROJECT_ID, plan_hash=report["plan_hash"], idempotency_key="run-2", outputs=_outputs()
    )
    with pytest.raises(ExplainerContractError) as missing_reason:
        service.set_step_status(
            run_id=str(run["id"]), step_code="RESEARCH_ACQUIRE", status="SKIPPED_WITH_REASON"
        )
    assert missing_reason.value.code == "SCHEMA_INVALID"

    service.set_step_status(run_id=str(run["id"]), step_code="RESEARCH_ACQUIRE", status="SUCCEEDED")
    after = service.get_run(run_id=str(run["id"]))
    assert after["step_statuses"]["FACT_EXTRACT"] == "PENDING"
    assert after["step_statuses"]["NARRATION_WRITE"] == "BLOCKED"


def test_repairs_skip_human_locked_beats(database: Database) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="ORIGINAL_FICTION",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "x", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="TARGET",
        target_seconds=300,
        tolerance_percent=5.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    with database.transaction() as connection:
        repository = ExplainerRepository(connection)
        locked = repository.insert(
            "explainer_visual_beats",
            {"video_id": video["id"], "code": "B01", "ordinal": 0, "render_type": "STILL_MOTION", "locked_by_human": True},
        )
        free = repository.insert(
            "explainer_visual_beats",
            {"video_id": video["id"], "code": "B02", "ordinal": 1, "render_type": "I2V"},
        )
        report = repository.insert(
            "explainer_qc_reports",
            {
                "video_id": video["id"],
                "project_id": PROJECT_ID,
                "subject_kind": "EDITION",
                "subject_revision_id": "edition-x",
                "status": "PASS_WITH_ISSUES",
            },
        )
        locked_issue = repository.insert(
            "explainer_qc_issues",
            {
                "report_id": report["id"],
                "video_id": video["id"],
                "issue_kind": "IDENTITY_WRONG_CHARACTER",
                "severity": "BLOCKER",
                "detector": "test",
                "beat_id": locked["id"],
                "responsible_step_code": "EXPLAINER_STORYBOARD",
                "status": "OPEN",
            },
        )
        free_issue = repository.insert(
            "explainer_qc_issues",
            {
                "report_id": report["id"],
                "video_id": video["id"],
                "issue_kind": "COMPOSITION_AESTHETIC",
                "severity": "MINOR",
                "detector": "test",
                "beat_id": free["id"],
                "responsible_step_code": "EXPLAINER_STORYBOARD",
                "status": "OPEN",
            },
        )
    plan = service.plan_repairs(
        project_id=PROJECT_ID,
        issue_ids=[str(locked_issue["id"]), str(free_issue["id"])],
        expected_revision=1,
    )
    assert str(free["id"]) in plan["beats"]
    assert str(locked["id"]) not in plan["beats"]
    assert plan["locked_beats_skipped"] == [str(locked["id"])]
    assert plan["would_create_jobs"] is False


# --------------------------------------------------------------------------- #
# domain policies
# --------------------------------------------------------------------------- #
def test_machine_acceptance_never_passes_without_semantic_coverage() -> None:
    coverage = CoverageFact(
        total_frames=7500,
        decoded_frames=7500,
        technical_checked_frames=7500,
        semantic_checked_frames=0,
        semantic_detector_available=False,
    )
    decision = evaluate_machine_acceptance(
        subject_kind="COMPOSITION_RENDER",
        subject_revision_id="render-1",
        subject_hash="d" * 64,
        issues=(),
        coverage=coverage,
    )
    assert decision.accepted is False
    assert decision.workflow_effect == "REQUEST_HUMAN"
    assert any(blocker["issue_kind"] == "SEMANTIC_QC_UNCHECKED" for blocker in decision.blockers)
    payload = decision.as_dict()
    assert payload["human_approval_written"] is False
    assert payload["publication_authorized"] is False
    assert payload["actor_type"] == "MACHINE"


def test_machine_acceptance_passes_only_with_full_technical_coverage() -> None:
    coverage = CoverageFact(
        total_frames=7500,
        decoded_frames=7500,
        technical_checked_frames=7500,
        semantic_checked_frames=750,
    )
    decision = evaluate_machine_acceptance(
        subject_kind="COMPOSITION_RENDER",
        subject_revision_id="render-1",
        subject_hash="d" * 64,
        issues=(IssueFact("COMPOSITION_AESTHETIC", "MINOR", detector="visual"),),
        coverage=coverage,
    )
    assert decision.accepted is True
    assert decision.workflow_effect == "CONTINUE"
    assert decision.warnings and decision.warnings[0]["treatment"] == "HINT_ONLY_NO_AUTOMATIC_REDRAW"
    assert "AUTO_APPROVE" not in {decision.workflow_effect}


def test_hard_blocker_stops_acceptance_even_with_full_coverage() -> None:
    coverage = CoverageFact(7500, 7500, 7500, 7500)
    decision = evaluate_machine_acceptance(
        subject_kind="COMPOSITION_RENDER",
        subject_revision_id="render-1",
        subject_hash="d" * 64,
        issues=(IssueFact("FACT_KEY_CONFLICT", "BLOCKER", detector="fact"),),
        coverage=coverage,
    )
    assert decision.accepted is False
    assert any(blocker["issue_kind"] == "FACT_KEY_CONFLICT" for blocker in decision.blockers)


def test_budget_ledger_stops_instead_of_upsizing() -> None:
    budget = Budget(max_gpu_seconds=100, max_wall_seconds=100, max_creative_repairs_per_beat=1)
    # A check inside the limit passes; the caller records the consumption.
    BudgetLedger(budget).check_gpu(additional_seconds=99)
    exhausted = BudgetLedger(budget, gpu_seconds_used=99.0, wall_seconds_used=99.0)
    with pytest.raises(ExplainerContractError) as gpu:
        exhausted.check_gpu(additional_seconds=2)
    assert gpu.value.code == ExplainerErrorCode.BUDGET_EXCEEDED.value
    with pytest.raises(ExplainerContractError) as wall:
        exhausted.check_wall(additional_seconds=2)
    assert wall.value.code == ExplainerErrorCode.BUDGET_EXCEEDED.value

    repairs = BudgetLedger(budget, creative_repairs_by_beat={"beat-1": 1})
    with pytest.raises(ExplainerContractError) as repair:
        repairs.check_creative_repair("beat-1")
    assert repair.value.code == ExplainerErrorCode.BUDGET_EXCEEDED.value
    # A different beat still has its own allowance.
    repairs.check_creative_repair("beat-2")

    retries = BudgetLedger(
        Budget(max_gpu_seconds=100, max_wall_seconds=100, max_technical_retries_per_step=1),
        technical_retries_by_step={"EXPLAINER_STORYBOARD": 1},
    )
    with pytest.raises(ExplainerContractError) as retry:
        retries.check_technical_retry("EXPLAINER_STORYBOARD")
    assert retry.value.code == ExplainerErrorCode.BUDGET_EXCEEDED.value

    scripts = BudgetLedger(Budget(max_gpu_seconds=100, max_wall_seconds=100, max_script_revisions=1), script_revisions_used=1)
    with pytest.raises(ExplainerContractError) as script:
        scripts.check_script_revision()
    assert script.value.code == ExplainerErrorCode.BUDGET_EXCEEDED.value


def test_must_be_motion_never_degrades_to_a_still_image() -> None:
    decision = resolve_visual_fallback(
        beat_must_be_motion=True,
        beat_locked_by_human=False,
        planned_render_type="I2V",
        allowed_fallbacks=("I2V_TO_MOTION_STILL",),
        repair_budget_exhausted=True,
    )
    assert decision.allowed is False
    assert decision.preserves_must_be_motion is True
    assert "MUST_BE_MOTION" in decision.reason

    locked = resolve_visual_fallback(
        beat_must_be_motion=False,
        beat_locked_by_human=True,
        planned_render_type="I2V",
        allowed_fallbacks=("I2V_TO_MOTION_STILL",),
        repair_budget_exhausted=True,
    )
    assert locked.allowed is False
    assert locked.preserves_human_lock is True


def test_license_scope_blocks_unverified_and_out_of_scope() -> None:
    unknown = evaluate_license_scope(
        assets=[AssetLicense("MODEL", "h3", "H3", "UNKNOWN")],
        intended_territories=["GLOBAL"],
    )
    assert unknown.publishable is False
    assert unknown.blockers[0]["reason"] == "LICENSE_SCOPE_NOT_VERIFIED"

    regional = evaluate_license_scope(
        assets=[AssetLicense("MUSIC", "bgm", "本地音乐", "REGION_RESTRICTED", territories=("CN",))],
        intended_territories=["GLOBAL"],
    )
    assert regional.publishable is False
    assert regional.blockers[0]["reason"] == "DISTRIBUTION_TERRITORY_OUTSIDE_VERIFIED_SCOPE"

    fine = evaluate_license_scope(
        assets=[AssetLicense("FONT", "font", "中文字体", "GLOBAL")],
        intended_territories=["GLOBAL"],
    )
    assert fine.publishable is True
    assert fine.as_dict()["scope_is_legal_conclusion"] is False


def test_staleness_plan_matches_the_documented_invalidation() -> None:
    text = staleness_plan("SEGMENT_TEXT")
    assert "NARRATION_TAKE" in text["invalidates"]
    assert "SUBTITLE_REVISION" in text["invalidates"]
    assert "COMPOSITION_REVISION" in text["invalidates"]
    assert "OTHER_CHAPTER_ASSET" in text["preserves"]

    bgm = staleness_plan("BGM_SELECTION")
    assert "COMPOSITION_REVISION" in bgm["invalidates"]
    assert "NARRATION_AUDIO" in bgm["preserves"]
    assert "IMAGE_VIDEO_SOURCE" in bgm["preserves"]

    font = staleness_plan("SUBTITLE_FONT")
    assert "SUBTITLE_REVISION" in font["invalidates"]
    assert "NARRATION_AUDIO" in font["preserves"]


def test_artifact_dependency_edges_round_trip(database: Database) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="ORIGINAL_FICTION",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "x", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="TARGET",
        target_seconds=300,
        tolerance_percent=5.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    with database.transaction() as connection:
        repository = ExplainerRepository(connection)
        repository.add_dependency(
            video_id=str(video["id"]),
            project_id=PROJECT_ID,
            upstream_kind="SCRIPT_SEGMENT",
            upstream_id="seg_001",
            upstream_hash="e" * 64,
            downstream_kind="NARRATION_TAKE",
            downstream_id="take-1",
        )
        stale = repository.mark_dependents_stale(
            upstream_kind="SCRIPT_SEGMENT",
            upstream_id="seg_001",
            downstream_kinds=("NARRATION_TAKE", "SUBTITLE_REVISION"),
            reason="SEGMENT_TEXT_CHANGED",
            invalidated_by="test",
        )
        assert [item["downstream_id"] for item in stale] == ["take-1"]
        assert repository.stale_dependents(str(video["id"]))[0]["stale_reason"] == "SEGMENT_TEXT_CHANGED"


def test_decision_constraints_reject_machine_human_misuse(database: Database) -> None:
    _seed_explainer_project(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="ORIGINAL_FICTION",
        input_kind="DOCUMENT_IMPORT",
        input_payload={"source_refs": [{"reference": "x", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="TARGET",
        target_seconds=300,
        tolerance_percent=5.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    with database.connect() as connection:
        repository = ExplainerRepository(connection)
        with pytest.raises(sqlite3.IntegrityError):
            repository.insert(
                "explainer_decisions",
                {
                    "video_id": video["id"],
                    "project_id": PROJECT_ID,
                    "decision_kind": "POLICY_ACCEPTED",
                    "subject_kind": "EDITION",
                    "subject_revision_id": "e1",
                    "actor_type": "HUMAN",
                    "policy_processor": "EXPLAINER_POLICY_EVALUATE",
                    "decided_at": "2026-01-01T00:00:00Z",
                },
            )
        with pytest.raises(sqlite3.IntegrityError):
            repository.insert(
                "explainer_decisions",
                {
                    "video_id": video["id"],
                    "project_id": PROJECT_ID,
                    "decision_kind": "HUMAN_APPROVED",
                    "subject_kind": "EDITION",
                    "subject_revision_id": "e1",
                    "actor_type": "MACHINE",
                    "decided_at": "2026-01-01T00:00:00Z",
                },
            )


def test_media_reference_must_belong_to_the_same_project(database: Database) -> None:
    _seed_explainer_project(database)
    with database.connect() as connection:
        repository = ExplainerRepository(connection)
        with pytest.raises(ExplainerContractError) as error:
            repository.require_same_project_media(project_id="other-project", media_version_id=str(uuid.uuid4()))
    assert error.value.code == "NOT_FOUND"


# --------------------------------------------------------------------------- #
# worker and API surface
# --------------------------------------------------------------------------- #
def test_worker_registers_the_explainer_and_narration_handlers() -> None:
    from local_drama.application.worker import _EXTRACTED_HANDLER_PROVIDERS

    assert {"EXPLAINER_TASK", "NARRATION_TTS", "NARRATION_ALIGN"} <= set(_EXTRACTED_HANDLER_PROVIDERS)


def test_worker_registers_every_explainer_stage_job_type() -> None:
    """A stage job carries the stage code as its job type.

    The migration creates one ``job_stage_definitions`` row per explainer stage, so
    each of those codes must resolve to a registered provider.  Otherwise the job is
    enqueued, never claimed, and the run silently stalls in the queue.
    """

    from local_drama.application.worker import _EXTRACTED_HANDLER_PROVIDERS

    for stage_code in (
        "RESEARCH_ACQUIRE",
        "FACT_EXTRACT",
        "NARRATION_WRITE",
        "EXPLAINER_STORYBOARD",
        "EXPLAINER_VISUAL_QC",
        "COMPOSITION_QC",
        "EXPLAINER_POLICY_EVALUATE",
    ):
        assert _EXTRACTED_HANDLER_PROVIDERS[stage_code] is _EXTRACTED_HANDLER_PROVIDERS["EXPLAINER_TASK"]


def test_production_dispatcher_really_claims_every_explainer_stage(workspace: object, database: object) -> None:
    """The whole explainer wiring must build on a real worker, not only in a unit test.

    This constructs the production worker (real database, real settings, real
    ffmpeg paths, real planner factory, real visual provider factory) and inspects
    the dispatcher it hands to the queue.  It is the check that catches an injected
    dependency that only exists in the test's own construction path.
    """

    from local_drama.application.worker import LocalMediaWorker

    worker = LocalMediaWorker(database, workspace)
    dispatcher = worker._dispatcher(attempt_id="attempt-1", token="token-1", worker_id="test-worker")

    for stage_code in (
        "RESEARCH_ACQUIRE",
        "FACT_EXTRACT",
        "NARRATION_WRITE",
        "EXPLAINER_STORYBOARD",
        "EXPLAINER_VISUAL_QC",
        "COMPOSITION_QC",
        "EXPLAINER_POLICY_EVALUATE",
        "EXPLAINER_TASK",
        "NARRATION_TTS",
        "NARRATION_ALIGN",
    ):
        assert stage_code in dispatcher.supported_types, stage_code


def test_stage_availability_is_reported_honestly() -> None:
    from local_drama.application.explainers.runtime_adapters import stage_handler_report

    report = stage_handler_report()
    assert report["EXPLAINER_POLICY_EVALUATE"] == "WIRED"
    # Every stage the production graph plans must declare exactly how it runs.
    for stage, status in report.items():
        assert status in {
            "WIRED",
            "WIRED_LOCAL_TEXT_PLANNER",
            "REQUIRES_LOCAL_TEXT_PLANNER",
            "WIRED_LAYERED_QC",
        }, stage
    assert report["NARRATION_WRITE"] == "WIRED_LOCAL_TEXT_PLANNER"
    # The QC stages run, but each layer only runs with its real measurement port.
    assert report["EXPLAINER_VISUAL_QC"] == "WIRED_LAYERED_QC"
    assert report["COMPOSITION_QC"] == "WIRED_LAYERED_QC"


def test_stage_handlers_are_absent_without_injected_factories() -> None:
    """A missing planner must fail loudly, never silently produce an empty script."""

    from local_drama.application.explainers.runtime_adapters import build_explainer_task_handlers

    assert build_explainer_task_handlers() == {}
    handlers = build_explainer_task_handlers(planner_factory=lambda: None, repo_factory=lambda: None)  # type: ignore[arg-type]
    # The QC handlers need no planner: their inputs are the frozen revision
    # artefacts, so they are bound whenever a repository factory exists.
    assert set(handlers) == {
        "RESEARCH_ACQUIRE",
        "FACT_EXTRACT",
        "NARRATION_WRITE",
        "EXPLAINER_STORYBOARD",
        "EXPLAINER_VISUAL_QC",
        "COMPOSITION_QC",
    }


def test_api_exposes_the_explainer_surface() -> None:
    from local_drama.main import app

    paths = app.openapi()["paths"]
    expected = {
        ("/api/v2/explainers", "get"),
        ("/api/v2/explainers", "post"),
        ("/api/v2/explainers/{project_id}", "get"),
        ("/api/v2/explainers/{project_id}/sources:import", "post"),
        ("/api/v2/explainers/{project_id}/research-runs", "post"),
        ("/api/v2/explainers/{project_id}/claims/{claim_id}", "patch"),
        ("/api/v2/explainers/{project_id}/script-revisions", "post"),
        ("/api/v2/explainers/{project_id}/script:freeze", "post"),
        ("/api/v2/explainers/{project_id}/segments", "get"),
        ("/api/v2/explainers/{project_id}/segments/{segment_id}", "patch"),
        ("/api/v2/explainers/{project_id}/plans:preflight", "post"),
        ("/api/v2/explainers/{project_id}/runs", "post"),
        ("/api/v2/explainer-runs/{run_id}", "get"),
        ("/api/v2/explainer-runs/{run_id}:pause", "post"),
        ("/api/v2/explainer-runs/{run_id}:resume", "post"),
        ("/api/v2/explainer-runs/{run_id}:cancel", "post"),
        ("/api/v2/explainers/{project_id}/repairs", "post"),
        ("/api/v2/explainers/{project_id}/beats", "get"),
        ("/api/v2/explainers/{project_id}/beats/{beat_id}/candidates", "get"),
        ("/api/v2/explainers/{project_id}/beats/{beat_id}/selections", "post"),
        ("/api/v2/explainers/{project_id}/beats/{beat_id}/impact", "get"),
        ("/api/v2/explainers/{project_id}/assets", "get"),
        ("/api/v2/explainers/{project_id}/editions", "get"),
        ("/api/v2/explainers/{project_id}/editions", "post"),
        ("/api/v2/explainer-editions/{edition_id}/narration", "get"),
        ("/api/v2/explainer-editions/{edition_id}/narration:resynthesize", "post"),
        ("/api/v2/explainer-editions/{edition_id}/subtitles", "get"),
        ("/api/v2/explainer-editions/{edition_id}/renders", "post"),
        ("/api/v2/explainer-editions/{edition_id}/qc", "get"),
        ("/api/v2/explainer-editions/{edition_id}/decisions", "post"),
        ("/api/v2/explainer-editions/{edition_id}/exports", "post"),
        ("/api/v2/explainer-schedules", "get"),
        ("/api/v2/explainer-schedules", "post"),
        ("/api/v2/explainer-schedules/{schedule_id}", "get"),
        ("/api/v2/explainer-schedules/{schedule_id}", "patch"),
        ("/api/v2/publication-packages/{package_id}/attempts", "post"),
    }
    missing = {(path, method) for path, method in expected if method not in paths.get(path, {})}
    assert missing == set()


def test_decision_endpoint_cannot_mint_a_machine_acceptance() -> None:
    from pydantic import ValidationError

    from local_drama.api.schemas.explainers import ExplainerDecisionRequest

    # ``POLICY_ACCEPTED`` is deliberately absent from the Literal, so the contract
    # itself rejects an attempt to mint machine acceptance over HTTP.
    with pytest.raises(ValidationError):
        ExplainerDecisionRequest(
            decision_kind="POLICY_ACCEPTED",
            subject_revision_id="e1",
            subject_hash="f" * 64,
            actor="local-user",
        )


def test_explainer_error_mapping_surfaces_retryability_and_next_step() -> None:
    error = ExplainerContractError(ExplainerErrorCode.GPU_CAPACITY_UNAVAILABLE.value, "GPU 排队中", {})
    mapped = api_error_from_explainer(error)
    assert mapped.status_code == 503
    assert mapped.retryable is True
    assert mapped.suggested_action

    stale = api_error_from_explainer(ExplainerContractError(ExplainerErrorCode.STALE_PLAN.value, "计划过期", {}))
    assert stale.status_code == 409
    assert stale.retryable is True


def test_required_capabilities_cover_the_documented_execution_classes() -> None:
    assert set(REQUIRED_CAPABILITIES) == {
        "text.generation",
        "audio.tts",
        "audio.forced_alignment",
        "image.text_to_image",
        "image.reference_edit",
        "video.image_to_video",
        "vision.qa",
        "video.render",
    }


def test_duration_mode_fixed_requires_zero_tolerance() -> None:
    from local_drama.domain.explainers.contracts import DurationSpec

    with pytest.raises(ExplainerContractError):
        DurationSpec(DurationMode.FIXED, 300, 5.0)
    assert DurationSpec(DurationMode.TARGET, 300, 5.0).accepts_seconds(300)
    assert not DurationSpec(DurationMode.TARGET, 300, 5.0).accepts_seconds(320)

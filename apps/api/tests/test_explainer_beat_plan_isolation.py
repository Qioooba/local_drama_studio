"""A second storyboard plan must not collide with, or read, the previous plan.

Audit finding A11: ``explainer_visual_beats`` was unique on ``(video_id, code)``
and read by ``video_id`` alone, so re-planning one video either hit the unique
constraint or silently produced a plan that was the union of both attempts.
Migration ``0105_explainer_beat_plan_scope`` added the attribution column; this
module proves the behaviour that depends on it.

Requirement mapping: design §4.4 (plan-version isolation) and matrix case V16.
"""

from __future__ import annotations

import sqlite3

import pytest

from local_drama.application.explainers.narration import ExplainerNarrationService
from local_drama.application.explainers.storyboard import ExplainerStoryboardService
from local_drama.domain.explainers.contracts import ProductKind
from local_drama.infrastructure.database.explainer_repository import (
    LEGACY_UNATTRIBUTED_BEATS,
    ExplainerRepository,
)
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "plan-scope-project"
VIDEO_ID = "plan-scope-video"


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'plan_scope_proj', '分镜隔离', 'DRAFT', 'v2', ?, 300000, ?,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '分镜隔离', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        repo = ExplainerRepository(connection)
        service = ExplainerNarrationService(repo)
        created = service.create_script_revision(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            locale="zh-CN",
            title="分镜隔离",
            outline=["隔离"],
            segments=[
                {
                    "canonical_segment_id": "seg_001",
                    "display_text": "第一段。",
                    "spoken_text": "第一段。",
                    "statement_type": "QUESTION",
                },
                {
                    "canonical_segment_id": "seg_002",
                    "display_text": "第二段。",
                    "spoken_text": "第二段。",
                    "statement_type": "ORIGINAL_EXPLANATION",
                },
            ],
        )
        revision = created["script_revision"]
        service.freeze_script(script_revision_id=str(revision["id"]), actor="test")
        repo.update("explainer_videos", VIDEO_ID, {"current_script_revision_id": str(revision["id"])})


def _beats(codes: tuple[str, ...], canonical: str) -> list[dict[str, object]]:
    return [
        {
            "code": code,
            "render_type": "I2V",
            "visual_intent": f"{code} 的画面",
            "visual_factuality": "SYMBOLIC",
            "segment_canonical_ids": [canonical],
            "must_be_motion": False,
        }
        for code in codes
    ]


def test_two_plans_for_one_video_keep_their_own_codes_and_beats(database: Database) -> None:
    """V16: the second plan neither collides with nor mixes into the first."""

    _seed(database)
    with database.transaction() as connection:
        service = ExplainerStoryboardService(ExplainerRepository(connection))
        first = service.create_plan(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beats=_beats(("B001", "B002"), "seg_001"),
            plan_step_binding_id="plan-A",
        )
        # The same codes again: before 0105 this raised a unique violation.
        second = service.create_plan(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beats=_beats(("B001", "B002"), "seg_002"),
            plan_step_binding_id="plan-B",
        )
    assert first["mapping"]["beat_count"] == 2
    assert second["mapping"]["beat_count"] == 2

    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        plan_a = repo.beats(VIDEO_ID, plan_step_binding_id="plan-A")
        plan_b = repo.beats(VIDEO_ID, plan_step_binding_id="plan-B")
        everything = repo.beats(VIDEO_ID)
        links_a = repo.beat_links(VIDEO_ID, plan_step_binding_id="plan-A")
        links_b = repo.beat_links(VIDEO_ID, plan_step_binding_id="plan-B")

    assert [item["code"] for item in plan_a] == ["B001", "B002"]
    assert [item["code"] for item in plan_b] == ["B001", "B002"]
    assert {str(item["plan_step_binding_id"]) for item in plan_a} == {"plan-A"}
    assert {str(item["plan_step_binding_id"]) for item in plan_b} == {"plan-B"}
    # An unscoped read still sees both plans, which is why scoping exists.
    assert len(everything) == 4
    # Links follow their own plan only: plan-A covers seg_001, plan-B covers seg_002.
    assert {str(item["canonical_segment_id"]) for item in links_a} == {"seg_001"}
    assert {str(item["canonical_segment_id"]) for item in links_b} == {"seg_002"}
    assert len(links_a) == 2
    assert len(links_b) == 2


def test_duplicate_code_inside_one_plan_is_still_refused(database: Database) -> None:
    """Attribution scopes uniqueness; it does not remove it."""

    _seed(database)
    with database.transaction() as connection:
        service = ExplainerStoryboardService(ExplainerRepository(connection))
        service.create_plan(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beats=_beats(("B001",), "seg_001"),
            plan_step_binding_id="plan-A",
        )
        with pytest.raises(sqlite3.IntegrityError):
            service.create_plan(
                project_id=PROJECT_ID,
                video_id=VIDEO_ID,
                beats=_beats(("B001",), "seg_002"),
                plan_step_binding_id="plan-A",
            )


def test_legacy_unattributed_beats_are_read_only_explicitly(database: Database) -> None:
    """Rows written before 0105 stay readable, and are not confused with a plan."""

    _seed(database)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        repo.insert(
            "explainer_visual_beats",
            {
                "video_id": VIDEO_ID,
                "code": "B900",
                "ordinal": 0,
                "render_type": "I2V",
                "visual_intent": "旧分镜",
            },
        )
        service = ExplainerStoryboardService(repo)
        service.create_plan(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beats=_beats(("B001",), "seg_001"),
            plan_step_binding_id="plan-A",
        )
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        legacy = repo.beats(VIDEO_ID, plan_step_binding_id=LEGACY_UNATTRIBUTED_BEATS)
        plan_a = repo.beats(VIDEO_ID, plan_step_binding_id="plan-A")
    assert [item["code"] for item in legacy] == ["B900"]
    assert [item["code"] for item in plan_a] == ["B001"]
    # A plan-scoped query never returns the unattributed row.
    assert all(item["plan_step_binding_id"] is None for item in legacy)

"""Storyboard duration planning must account for the whole narration clock.

Audit finding A07 had two halves.  The first — the 160-segment catalogue cap — is
covered by ``test_explainer_text_planner.py``.  This module covers the second:
``plan_durations_from_real_audio`` derived its total from the segments some beat
happened to link, so measured narration that no picture covered contributed
nothing and a short film could be reported as complete.

Requirement mapping: design §5.2 (measure audio before locking picture frames),
§5.3 (full coverage) and matrix case V09.
"""

from __future__ import annotations

import pytest

from local_drama.application.explainers.narration import ExplainerNarrationService
from local_drama.application.explainers.storyboard import ExplainerStoryboardService
from local_drama.domain.explainers.contracts import ExplainerContractError, ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "duration-project"
VIDEO_ID = "duration-video"


def _seed(database: Database, *, link_second_segment: bool = False) -> dict[str, str]:
    """One edition, two measured segments, and one beat.

    ``link_second_segment`` decides whether the single beat also covers
    ``seg_002``; the unlinked case is the audit's "8 s film for 12 s of
    narration" shape.
    """

    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'duration_proj', '时长计划', 'DRAFT', 'v2', ?, 300000, ?,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '时长计划', '观察窗为什么无光', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
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
            title="时长计划",
            outline=["时长"],
            segments=[
                {
                    "canonical_segment_id": "seg_001",
                    "display_text": "第一段解说。",
                    "spoken_text": "第一段解说。",
                    "statement_type": "QUESTION",
                    "pause_after_ms": 600,
                },
                {
                    "canonical_segment_id": "seg_002",
                    "display_text": "第二段解说。",
                    "spoken_text": "第二段解说。",
                    "statement_type": "ORIGINAL_EXPLANATION",
                },
            ],
        )
        revision = created["script_revision"]
        service.freeze_script(script_revision_id=str(revision["id"]), actor="test")
        repo.update("explainer_videos", VIDEO_ID, {"current_script_revision_id": str(revision["id"])})
        segment_ids = {
            str(row["canonical_segment_id"]): str(row["id"]) for row in repo.segments(str(revision["id"]))
        }
        edition = repo.insert(
            "explainer_editions",
            {
                "video_id": VIDEO_ID,
                "edition_key": "zh-captioned-169",
                "voice_locale": "zh-CN",
                "subtitle_mode": "BURNED",
                "subtitle_locales_json": ["zh-CN"],
                "aspect_ratio": "16:9",
                "fps_num": 25,
                "fps_den": 1,
                "frozen_script_revision_id": str(revision["id"]),
            },
        )
        beat = repo.insert(
            "explainer_visual_beats",
            {
                "video_id": VIDEO_ID,
                "code": "B001",
                "ordinal": 0,
                "render_type": "I2V",
                "visual_intent": "第一段画面",
            },
        )
        linked = ["seg_001", "seg_002"] if link_second_segment else ["seg_001"]
        for ordinal, canonical in enumerate(linked):
            repo.insert(
                "beat_narration_links",
                {
                    "beat_id": str(beat["id"]),
                    "narration_segment_id": segment_ids[canonical],
                    "video_id": VIDEO_ID,
                    "ordinal": ordinal,
                },
            )
        return {
            "edition_id": str(edition["id"]),
            "beat_id": str(beat["id"]),
            "script_revision_id": str(revision["id"]),
        }


# 8 s + 4 s, mirroring matrix case V09.
MEASURED = {"seg_001": 8_000, "seg_002": 4_000}


def test_duration_plan_refuses_measured_narration_no_picture_covers(database: Database) -> None:
    """V09: only the first segment mapped must not yield an 8 s "complete" plan."""

    seeded = _seed(database)
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            ExplainerStoryboardService(ExplainerRepository(connection)).plan_durations_from_real_audio(
                video_id=VIDEO_ID,
                edition_id=seeded["edition_id"],
                segment_durations_ms=MEASURED,
            )
    assert "没有被任何画面段覆盖" in error.value.message
    assert error.value.details["unlinked_measured_segment_ids"] == ["seg_002"]


def test_duration_plan_totals_every_measured_segment_once(database: Database) -> None:
    """With full coverage the total is the whole narration clock, not a beat sum."""

    seeded = _seed(database, link_second_segment=True)
    with database.connect() as connection:
        plan = ExplainerStoryboardService(ExplainerRepository(connection)).plan_durations_from_real_audio(
            video_id=VIDEO_ID,
            edition_id=seeded["edition_id"],
            segment_durations_ms=MEASURED,
        )
    # 12 s of narration plus the declared 600 ms pause on seg_001.
    assert plan["total_measured_ms"] == 12_000
    assert plan["total_pause_ms"] == 600
    assert plan["expected_segment_source"] == "EDITION_FROZEN_SCRIPT_REVISION"
    assert plan["expected_segment_count"] == 2
    assert plan["measured_segment_count"] == 2
    assert plan["measured_take_covers_every_segment"] is True
    assert plan["unlinked_measured_segment_ids"] == []
    # 12.6 s at 25/1, and the last beat still ends exactly at the total.
    assert plan["total_frames"] == round(12_600 * 25 / 1000)
    assert plan["last_end_equals_total"] is True


def test_duration_plan_refuses_a_segment_without_measured_audio(database: Database) -> None:
    """A linked but unmeasured segment cannot be timed from a plan hint."""

    seeded = _seed(database, link_second_segment=True)
    with database.connect() as connection:
        with pytest.raises(ExplainerContractError) as error:
            ExplainerStoryboardService(ExplainerRepository(connection)).plan_durations_from_real_audio(
                video_id=VIDEO_ID,
                edition_id=seeded["edition_id"],
                segment_durations_ms={"seg_001": 8_000},
            )
    # seg_002 is linked but has no take: the pre-existing check still owns this.
    assert "缺少真实 TTS 实测时长" in error.value.message


def test_duration_plan_scoped_to_the_editions_language(database: Database) -> None:
    """Another language's takes must not leak into this edition's clock (A10)."""

    seeded = _seed(database, link_second_segment=True)
    with database.connect() as connection:
        plan = ExplainerStoryboardService(ExplainerRepository(connection)).plan_durations_from_real_audio(
            video_id=VIDEO_ID,
            edition_id=seeded["edition_id"],
            # A stale take from another revision/language must be ignored when the
            # canonical id is not part of this edition's frozen script.
            segment_durations_ms={**MEASURED, "seg_legacy_099": 9_999},
        )
    assert plan["total_measured_ms"] == 12_000
    assert plan["expected_segment_count"] == 2

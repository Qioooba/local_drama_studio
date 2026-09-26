"""The channel mix is a guideline over the surviving picture families.

The retired ``still_motion`` family is gone from ``MIX_FAMILIES``: an explainer's moving
pictures are real AI image-to-video clips, so the mix no longer offers a still-image
route.  The two families that carry the whole mix — ``i2v`` and ``infographic`` — must
therefore still sum to exactly 1, and a preset that does not is refused by name instead
of being normalised into a plan that draws a different mix than the caller asked for.

Requirement mapping: design §B3.2 (channel mix guideline) and §B6.2 (no still route).
"""

from __future__ import annotations

import pytest

from local_drama.application.explainers.storyboard import (
    DEFAULT_CHANNEL_MIX,
    MIX_FAMILIES,
    build_storyboard_service,
)
from local_drama.domain.explainers.contracts import ExplainerContractError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "mix-project"
VIDEO_ID = "mix-video"


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel,
            target_duration_ms, product_kind, created_at, updated_at, created_by)
            VALUES (?, 'mix_proj', '比例', 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale,
            input_kind, duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode,
            research_mode, status, created_at, updated_at, created_by)
            VALUES (?, ?, '比例', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC', 'FIXED', 300, 5,
            'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        repo = ExplainerRepository(connection)
        for ordinal, (code, render_type) in enumerate((("B001", "I2V"), ("B002", "INFOGRAPHIC"))):
            repo.insert(
                "explainer_visual_beats",
                {
                    "id": f"mix-beat-{code}",
                    "video_id": VIDEO_ID,
                    "code": code,
                    "ordinal": ordinal,
                    "render_type": render_type,
                    "visual_intent": "画面",
                },
            )


def _mix(database: Database, preset: dict[str, float] | None = None) -> dict:
    with database.connect() as connection:
        return build_storyboard_service(ExplainerRepository(connection)).channel_mix(
            video_id=VIDEO_ID, preset=preset
        )


def test_the_default_mix_is_i2v_led_and_has_no_still_motion_family() -> None:
    assert DEFAULT_CHANNEL_MIX == {"i2v": 0.9, "infographic": 0.1}
    assert "still_motion" not in MIX_FAMILIES
    assert set(MIX_FAMILIES) == {"i2v", "infographic", "licensed_media"}


def test_a_preset_that_does_not_sum_to_one_is_refused(database: Database) -> None:
    _seed(database)
    with pytest.raises(ExplainerContractError) as error:
        _mix(database, {"i2v": 0.5, "infographic": 0.4})
    assert error.value.code == "SCHEMA_INVALID"
    assert error.value.message == "i2v / infographic 两个比例之和必须为 1"


def test_a_preset_that_sums_to_one_is_used_and_reported(database: Database) -> None:
    _seed(database)
    report = _mix(database, {"i2v": 0.5, "infographic": 0.5})
    assert report["preset_source"] == "CALLER_PRESET"
    assert report["preset"]["i2v"] == 0.5
    assert report["planned"]["counts"]["i2v"] == 1
    assert report["planned"]["counts"]["infographic"] == 1
    assert report["is_template_lock"] is False


def test_the_default_preset_is_used_when_no_preset_is_given(database: Database) -> None:
    _seed(database)
    report = _mix(database)
    assert report["preset_source"] == "DEFAULT_GUIDELINE"
    assert report["preset"] == DEFAULT_CHANNEL_MIX

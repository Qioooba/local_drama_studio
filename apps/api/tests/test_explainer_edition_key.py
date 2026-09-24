"""An edition key must describe the edition it names.

The one-click page defaulted to four editions (bilingual, vertical, English, clean)
and labelled them with fixed keys, so a vertical or English edition could be stored
under ``zh-clean-169`` — a name that describes neither its language, its subtitle
treatment nor its aspect (design §1.3/§2.3, cases V03).

Requirement mapping: V03 (default output is one captioned edition) and §2.3
(``edition_key`` derives from the real language, subtitle mode and aspect).
"""

from __future__ import annotations

import pytest

from local_drama.application.explainers.production_pipeline import (
    canonical_edition_key,
    ensure_editions_for_outputs,
)
from local_drama.domain.explainers.contracts import ExplainerContractError, ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "edition-key-project"
VIDEO_ID = "edition-key-video"


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'ed_key_proj', '版本键', 'DRAFT', 'v2', ?, 300000, ?,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '版本键', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )


def _output(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "voice_locale": "zh-CN",
        "subtitle_mode": "BURNED",
        "subtitle_locales": ["zh-CN"],
        "aspect_ratio": "16:9",
        "fps": {"num": 25, "den": 1},
        "duration_policy": "NATURAL_NARRATION",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# derivation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("voice_locale", "subtitle_mode", "aspect_ratio", "expected"),
    [
        ("zh-CN", "NONE", "16:9", "zh-clean-169"),
        ("zh-CN", "BURNED", "16:9", "zh-captioned-169"),
        ("zh-CN", "BILINGUAL_BURNED", "16:9", "zh-bilingual-169"),
        ("zh-CN", "BURNED", "9:16", "zh-captioned-916"),
        ("en-US", "BURNED", "16:9", "en-captioned-169"),
        ("zh-CN", "BURNED", "3:4", "zh-captioned-34"),
        ("zh-CN", "SOFT", "1:1", "zh-captioned-11"),
    ],
)
def test_the_key_names_the_language_subtitles_and_aspect(
    voice_locale: str, subtitle_mode: str, aspect_ratio: str, expected: str
) -> None:
    assert (
        canonical_edition_key(
            voice_locale=voice_locale, subtitle_mode=subtitle_mode, aspect_ratio=aspect_ratio
        )
        == expected
    )


def test_different_shapes_never_share_a_key() -> None:
    keys = {
        canonical_edition_key(voice_locale=locale, subtitle_mode=mode, aspect_ratio=aspect)
        for locale in ("zh-CN", "en-US")
        for mode in ("NONE", "BURNED", "BILINGUAL_BURNED")
        for aspect in ("16:9", "9:16", "3:4", "1:1")
    }
    assert len(keys) == 2 * 3 * 4


# --------------------------------------------------------------------------- #
# edition creation
# --------------------------------------------------------------------------- #
def test_the_default_single_output_creates_exactly_one_edition(database: Database) -> None:
    """V03: one captioned edition, no English narration and no second render."""

    _seed(database)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        video = repo.get("explainer_videos", VIDEO_ID)
        editions = ensure_editions_for_outputs(
            repo,
            video=video,
            outputs=[_output(edition_key="zh-captioned-169")],
        )
        stored = repo.editions(VIDEO_ID)
    assert len(editions) == 1
    assert len(stored) == 1
    assert str(stored[0]["edition_key"]) == "zh-captioned-169"
    assert str(stored[0]["subtitle_mode"]) == "BURNED"
    assert str(stored[0]["aspect_ratio"]) == "16:9"


def test_replaying_the_same_shape_reuses_the_edition(database: Database) -> None:
    _seed(database)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        video = repo.get("explainer_videos", VIDEO_ID)
        first = ensure_editions_for_outputs(repo, video=video, outputs=[_output()])
        second = ensure_editions_for_outputs(repo, video=video, outputs=[_output()])
        stored = repo.editions(VIDEO_ID)
    assert [str(item["id"]) for item in first] == [str(item["id"]) for item in second]
    assert len(stored) == 1


def test_a_mislabelled_key_is_refused(database: Database) -> None:
    """The fixed-key bug: a vertical edition must not be called ``zh-clean-169``."""

    _seed(database)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        video = repo.get("explainer_videos", VIDEO_ID)
        with pytest.raises(ExplainerContractError) as error:
            ensure_editions_for_outputs(
                repo,
                video=video,
                outputs=[
                    _output(
                        edition_key="zh-clean-169",
                        subtitle_mode="BURNED",
                        aspect_ratio="9:16",
                    )
                ],
            )
    assert "edition_key" in error.value.message
    assert error.value.details["declared"] == "zh-clean-169"
    assert error.value.details["derived"] == "zh-captioned-916"


def test_an_english_edition_is_created_under_its_own_key(database: Database) -> None:
    _seed(database)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        video = repo.get("explainer_videos", VIDEO_ID)
        editions = ensure_editions_for_outputs(
            repo,
            video=video,
            outputs=[
                _output(),
                _output(voice_locale="en-US", subtitle_locales=["en-US"]),
            ],
        )
    keys = sorted(str(item["edition_key"]) for item in editions)
    assert keys == ["en-captioned-169", "zh-captioned-169"]

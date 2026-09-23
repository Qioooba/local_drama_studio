"""Narration read model: current take, real alignment state, honest total (LDS-10).

Reproduced defects on the audit snapshot:

* ``measured_total_ms`` summed *every* take of ``video_id + locale``, so saving a
  historical re-read made the film appear longer (selected 2000 ms + an old
  unselected 1000 ms reported 3000 ms);
* the page labelled a segment "已对齐" purely because a selected take existed: the
  alignment output, its revision and its hash were never read;
* the ``locale`` query parameter steered the *narration* query (the backend fell
  back to ``edition.voice_locale`` only when it was missing), so switching the
  subtitle preview language could silently change the narration clock while the
  page still labelled the edition's voice language;
* the page tested a disabled child query's ``isPending`` before "no editions", so a
  successfully-empty edition list showed "loading" forever.
"""

from __future__ import annotations

from typing import Any

import pytest

from local_drama.api.routes.explainers import _narration_view
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "project-narr-1"
VIDEO_ID = "video-narr-1"
EDITION_ID = "edition-narr-1"
SCRIPT_ID = "script-narr-1"


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel,
            target_duration_ms, product_kind, created_at, updated_at, created_by)
            VALUES (?, ?, ?, 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, "EXP-NARR", "旁白读模型", PROJECT_ID),
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
        connection.execute(
            "INSERT INTO explainer_script_revisions (id, video_id, revision_no, locale, status,"
            " content_hash) VALUES (?,?,?,?,?,?)",
            (SCRIPT_ID, VIDEO_ID, 1, "zh-CN", "FROZEN", "s" * 64),
        )
        connection.execute(
            "INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status,"
            " subtitle_locales_json, frozen_script_revision_id) VALUES (?,?,?,?,?,?,?)",
            (EDITION_ID, VIDEO_ID, "main", "zh-CN", "READY", '["zh-CN","en-US"]', SCRIPT_ID),
        )
        repo = ExplainerRepository(connection)
        for ordinal, canonical in enumerate(("seg-1", "seg-2")):
            repo.insert(
                "narration_segments",
                {
                    "video_id": VIDEO_ID,
                    "script_revision_id": SCRIPT_ID,
                    "ordinal": ordinal,
                    "canonical_segment_id": canonical,
                    "locale": "zh-CN",
                    "display_text": f"第{ordinal + 1}段。",
                    "spoken_text": f"第{ordinal + 1}段。",
                    "segment_hash": f"{canonical}-hash".ljust(64, "0"),
                    "pause_after_ms": 120,
                    "content_locked_by_human": 0,
                },
            )


def _segment_id(database: Database, canonical: str) -> str:
    with database.connect() as connection:
        return str(
            connection.execute(
                "SELECT id FROM narration_segments WHERE canonical_segment_id = ?", (canonical,)
            ).fetchone()["id"]
        )


def _add_take(
    database: Database,
    *,
    canonical: str,
    take_no: int,
    duration_ms: int,
    selected: bool,
    locale: str = "zh-CN",
    segment_hash: str | None = None,
) -> str:
    segment_id = _segment_id(database, canonical)
    asset_id = f"asset-{canonical}-{locale}-{take_no}"
    version_id = f"media-{canonical}-{locale}-{take_no}"
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        # media_* is a shared table, not an explainer table, so it is seeded with
        # plain SQL; the take itself goes through the repository.
        connection.execute(
            "INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind)"
            " VALUES (?,?,?,?,?,?)",
            (asset_id, PROJECT_ID, "EXPLAINER_SEGMENT", segment_id, "NARRATION", "AUDIO"),
        )
        connection.execute(
            "INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path,"
            " mime_type, byte_size, sha256, duration_ms, integrity_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                version_id, asset_id, 1, take_no, "GENERATED", f"narration/{version_id}.wav",
                "audio/wav", 1024, "t" * 64, duration_ms, "VERIFIED",
            ),
        )
        take = repo.insert(
            "narration_takes",
            {
                "video_id": VIDEO_ID,
                "segment_id": segment_id,
                "canonical_segment_id": canonical,
                "locale": locale,
                "take_no": take_no,
                "selected": 1 if selected else 0,
                "status": "VERIFIED",
                "measured_duration_ms": duration_ms,
                "segment_hash": segment_hash or f"{canonical}-hash".ljust(64, "0"),
                "media_asset_id": asset_id,
                "media_version_id": version_id,
                "media_sha256": "t" * 64,
            },
        )
    return str(take["id"])


def _add_alignment(
    database: Database,
    *,
    take_id: str,
    canonical: str,
    status: str,
    segment_hash: str,
    unaligned: str | None = None,
) -> str:
    segment_id = _segment_id(database, canonical)
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        row = repo.insert(
            "narration_alignment_revisions",
            {
                "take_id": take_id,
                "segment_id": segment_id,
                "video_id": VIDEO_ID,
                "revision_no": 1,
                "locale": "zh-CN",
                "media_sha256": "t" * 64,
                "sample_rate_hz": 48_000,
                "alignment_status": status,
                "script_hash": segment_hash,
                "unaligned_tokens_json": [] if unaligned is None else [unaligned],
            },
        )
    return str(row["id"])


def _align(
    database: Database, *, canonical: str, take_no: int, duration_ms: int, locale: str = "zh-CN"
) -> str:
    """Add a selected take and a matching ``ALIGNED`` revision for it."""

    take_id = _add_take(
        database, canonical=canonical, take_no=take_no, duration_ms=duration_ms, selected=True, locale=locale
    )
    _add_alignment(
        database,
        take_id=take_id,
        canonical=canonical,
        status="ALIGNED",
        segment_hash=f"{canonical}-hash".ljust(64, "0"),
    )
    return take_id


def _view(database: Database, locale: str | None = None) -> dict[str, Any]:
    connection = database.connect()
    try:
        return _narration_view(ExplainerRepository(connection), EDITION_ID, locale)
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# the total is the current edit, not the take history
# --------------------------------------------------------------------------- #
def test_historical_takes_do_not_inflate_the_measured_total(database: Database) -> None:
    _seed(database)
    _add_take(database, canonical="seg-1", take_no=1, duration_ms=1000, selected=False)
    _align(database, canonical="seg-1", take_no=2, duration_ms=2000)
    _align(database, canonical="seg-2", take_no=1, duration_ms=500)

    view = _view(database)
    # selected and aligned only: 2000 + 500.  The old code reported 3500.
    assert view["measured_total_ms"] == 2500
    assert view["segment_duration_sum_ms"] == 2500
    # Timeline adds the declared pauses of the aligned segments only.
    assert view["declared_pause_total_ms"] == 240
    assert view["timeline_total_ms"] == 2740
    # The unselected historical take of the *same* revision does not contribute to
    # the clock, but it is still listed as an alternative take.
    assert view["measured_total_ms"] == 2500
    assert view["selected_take_count"] == 2
    assert len(view["takes"]) == 3
    unselected = [take for take in view["takes"] if not take["selected"]]
    assert len(unselected) == 1 and int(unselected[0]["measured_duration_ms"]) == 1000


def test_saving_a_third_re_read_does_not_change_the_total(database: Database) -> None:
    _seed(database)
    _align(database, canonical="seg-1", take_no=1, duration_ms=2000)
    before = _view(database)["measured_total_ms"]
    _add_take(database, canonical="seg-1", take_no=2, duration_ms=900, selected=False)
    _add_take(database, canonical="seg-1", take_no=3, duration_ms=1100, selected=False)
    after = _view(database)["measured_total_ms"]
    assert before == after == 2000


def test_segments_without_a_take_report_null_not_zero(database: Database) -> None:
    _seed(database)
    view = _view(database)
    assert view["measured_total_ms"] is None
    assert view["state_counts"]["NOT_GENERATED"] == 2
    assert view["null_means_not_generated"] is True


def test_pauses_are_added_to_the_timeline_only_for_aligned_segments(database: Database) -> None:
    _seed(database)
    take_id = _add_take(database, canonical="seg-1", take_no=1, duration_ms=2000, selected=True)
    _add_alignment(
        database,
        take_id=take_id,
        canonical="seg-1",
        status="ALIGNED",
        segment_hash="seg-1-hash".ljust(64, "0"),
    )
    view = _view(database)
    assert view["segment_duration_sum_ms"] == 2000
    assert view["timeline_total_ms"] == 2120
    assert view["declared_pause_total_ms"] == 120


# --------------------------------------------------------------------------- #
# alignment is read, not assumed
# --------------------------------------------------------------------------- #
def test_a_take_without_alignment_is_audio_ready_not_aligned(database: Database) -> None:
    _seed(database)
    _add_take(database, canonical="seg-1", take_no=1, duration_ms=2000, selected=True)
    view = _view(database)
    state = next(item for item in view["segment_states"] if item["canonical_segment_id"] == "seg-1")
    assert state["state"] == "AUDIO_READY"
    assert view["state_counts"]["AUDIO_READY"] == 1
    assert view["measured_total_ms"] is None
    assert view["audio_ready_total_ms"] == 2000


@pytest.mark.parametrize(
    ("alignment_status", "expected"),
    [
        ("ALIGNED", "ALIGNED"),
        ("PARTIAL", "AUDIO_READY"),
        ("FAILED", "FAILED"),
    ],
)
def test_alignment_status_drives_the_segment_state(
    database: Database, alignment_status: str, expected: str
) -> None:
    _seed(database)
    take_id = _add_take(database, canonical="seg-1", take_no=1, duration_ms=2000, selected=True)
    _add_alignment(
        database,
        take_id=take_id,
        canonical="seg-1",
        status=alignment_status,
        segment_hash="seg-1-hash".ljust(64, "0"),
    )
    view = _view(database)
    state = next(item for item in view["segment_states"] if item["canonical_segment_id"] == "seg-1")
    assert state["state"] == expected
    if expected == "ALIGNED":
        assert view["measured_total_ms"] == 2000
    else:
        assert view["measured_total_ms"] is None


def test_an_alignment_for_a_different_segment_hash_is_stale(database: Database) -> None:
    _seed(database)
    take_id = _add_take(database, canonical="seg-1", take_no=1, duration_ms=2000, selected=True)
    _add_alignment(database, take_id=take_id, canonical="seg-1", status="ALIGNED", segment_hash="other" + "0" * 59)
    view = _view(database)
    state = next(item for item in view["segment_states"] if item["canonical_segment_id"] == "seg-1")
    assert state["state"] == "STALE"
    assert view["measured_total_ms"] is None


def test_a_failed_alignment_exposes_its_error(database: Database) -> None:
    _seed(database)
    take_id = _add_take(database, canonical="seg-1", take_no=1, duration_ms=2000, selected=True)
    _add_alignment(
        database, take_id=take_id, canonical="seg-1", status="FAILED",
        segment_hash="seg-1-hash".ljust(64, "0"), unaligned="0-3",
    )
    view = _view(database)
    state = next(item for item in view["segment_states"] if item["canonical_segment_id"] == "seg-1")
    assert state["alignment_error"]


def test_selected_takes_expose_a_controlled_audio_url(database: Database) -> None:
    """A take existing is only useful if the page can build an ``<audio>``."""

    _seed(database)
    _add_take(database, canonical="seg-1", take_no=1, duration_ms=2000, selected=True)
    view = _view(database)
    state = next(item for item in view["segment_states"] if item["canonical_segment_id"] == "seg-1")
    assert state["audio_url"] == f"/api/v1/media-versions/{state['media_version_id']}/content"
    assert str(state["waveform_url"]).endswith("/waveform")
    assert ".wav" not in str(view)


def test_a_segment_without_a_take_has_no_audio_url(database: Database) -> None:
    _seed(database)
    state = _view(database)["segment_states"][0]
    assert state["audio_url"] is None
    assert state["waveform_url"] is None


# --------------------------------------------------------------------------- #
# the voice clock is the edition's, never the subtitle language
# --------------------------------------------------------------------------- #
def test_subtitle_language_never_moves_the_narration_clock(database: Database) -> None:
    _seed(database)
    _add_take(database, canonical="seg-1", take_no=1, duration_ms=2000, selected=True)
    _add_take(database, canonical="seg-2", take_no=1, duration_ms=7000, selected=True, locale="en-US")

    chinese = _view(database, None)
    english_subtitles = _view(database, "en-US")

    assert chinese["voice_locale"] == "zh-CN"
    assert english_subtitles["voice_locale"] == "zh-CN"
    assert english_subtitles["subtitle_locale"] == "en-US"
    # The clock is identical; only the subtitle hint differs.
    assert english_subtitles["takes"] == chinese["takes"]
    assert english_subtitles["segment_states"] == chinese["segment_states"]
    assert english_subtitles["requested_locale_ignored_for_the_clock"] is True
    assert english_subtitles["subtitle_locale_does_not_change_the_voice_clock"] is True


def test_takes_of_another_language_are_not_in_the_clock(database: Database) -> None:
    _seed(database)
    _add_take(database, canonical="seg-1", take_no=1, duration_ms=7000, selected=True, locale="en-US")
    view = _view(database, None)
    assert view["takes"] == []
    assert view["measured_total_ms"] is None


def test_an_independent_english_edition_uses_its_own_takes(database: Database) -> None:
    _seed(database)
    _add_take(database, canonical="seg-1", take_no=1, duration_ms=2000, selected=True)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status,"
            " subtitle_locales_json, frozen_script_revision_id) VALUES (?,?,?,?,?,?,?)",
            ("edition-en", VIDEO_ID, "en", "en-US", "READY", '["en-US"]', SCRIPT_ID),
        )
    _align(database, canonical="seg-2", take_no=1, duration_ms=7000, locale="en-US")

    connection = database.connect()
    try:
        english = _narration_view(ExplainerRepository(connection), "edition-en", None)
    finally:
        connection.close()
    assert english["voice_locale"] == "en-US"
    assert english["measured_total_ms"] == 7000


def test_takes_of_an_older_script_revision_are_excluded(database: Database) -> None:
    _seed(database)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO explainer_script_revisions (id, video_id, revision_no, locale, status,"
            " content_hash) VALUES (?,?,?,?,?,?)",
            ("script-old", VIDEO_ID, 2, "zh-CN", "SUPERSEDED", "o" * 64),
        )
        connection.execute(
            "INSERT INTO narration_segments (id, video_id, script_revision_id, ordinal,"
            " canonical_segment_id, locale, display_text, spoken_text, segment_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "seg-old", VIDEO_ID, "script-old", 0, "seg-1", "zh-CN", "旧修订。", "旧修订。",
                "old" + "0" * 61,
            ),
        )
    _align(database, canonical="seg-1", take_no=1, duration_ms=2000)
    with database.transaction() as connection:
        asset_id, version_id = "asset-old", "media-old"
        connection.execute(
            "INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind)"
            " VALUES (?,?,?,?,?,?)",
            (asset_id, PROJECT_ID, "EXPLAINER_SEGMENT", "seg-old", "NARRATION", "AUDIO"),
        )
        connection.execute(
            "INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path,"
            " mime_type, byte_size, sha256, duration_ms, integrity_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (version_id, asset_id, 1, 2, "GENERATED", "narration/old.wav", "audio/wav", 1024,
             "o" * 64, 5000, "VERIFIED"),
        )
        connection.execute(
            "INSERT INTO narration_takes (id, video_id, segment_id, canonical_segment_id, locale,"
            " take_no, selected, status, measured_duration_ms, segment_hash, media_asset_id,"
            " media_version_id, media_sha256)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "take-old", VIDEO_ID, "seg-old", "seg-1", "zh-CN", 2, 1, "VERIFIED", 5000,
                "old" + "0" * 61, asset_id, version_id, "o" * 64,
            ),
        )
    view = _view(database)
    # The old revision's 5000 ms take is not part of this edition's clock.
    assert view["measured_total_ms"] == 2000
    assert {take["id"] for take in view["historical_takes"]} == {"take-old"}
    assert all(take["id"] != "take-old" for take in view["takes"])





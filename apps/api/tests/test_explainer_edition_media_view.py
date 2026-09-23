"""Edition media read model: playable facts per edition (LDS-09 / FE-A04).

Reproduced defects on the audit snapshot:

* ``_editions_view`` exposed only ``current_render = {id, integrity_status, sha256}``
  and no composition frames or frame rate, so the review page could not build a
  ``<video>`` even for a verified render and rendered a placeholder that always said
  "尚未生成媒体";
* ``subtitle_revision_count`` was computed by listing **every** subtitle revision of
  the whole video inside the per-edition loop, so each edition reported the video's
  total rather than its own count.

Nothing here proves media *quality*; it proves that the read model carries the facts
a player needs, states availability honestly, and never leaks a local path.
"""

from __future__ import annotations

from typing import Any

from local_drama.api.routes.explainers import _editions_view
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "project-media-1"
VIDEO_ID = "video-media-1"


def _seed(database: Database, *, editions: int = 1) -> list[str]:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel,
            target_duration_ms, product_kind, created_at, updated_at, created_by)
            VALUES (?, ?, ?, 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, "EXP-MEDIA", "媒体读模型", PROJECT_ID),
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
        edition_ids: list[str] = []
        for index in range(editions):
            edition_id = f"ed-{index}"
            edition_ids.append(edition_id)
            connection.execute(
                "INSERT INTO explainer_editions (id, video_id, edition_key, voice_locale, status,"
                " subtitle_locales_json) VALUES (?,?,?,?,?,?)",
                (edition_id, VIDEO_ID, f"main{index}", "zh-CN", "READY", '["zh-CN","en-US"]'),
            )
    return edition_ids


def _add_composition(database: Database, edition_id: str, *, total_frames: int = 250) -> str:
    composition_id = f"comp-{edition_id}"
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO composition_revisions (id, edition_id, video_id, project_id, revision_no,"
            " status, manifest_hash, fps_num, fps_den, total_frames, audio_sample_rate_hz)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (composition_id, edition_id, VIDEO_ID, PROJECT_ID, 1, "FROZEN", "m" * 64, 30000, 1001,
             total_frames, 48_000),
        )
    return composition_id


def _add_render(
    database: Database,
    edition_id: str,
    *,
    composition_id: str,
    media: bool = True,
    integrity: str = "VERIFIED",
    status: str = "SUCCEEDED",
    frame_count: int | None = 250,
) -> str:
    render_id = f"render-{edition_id}"
    media_version_id = f"media-{edition_id}"
    with database.transaction() as connection:
        if media:
            connection.execute(
                "INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind)"
                " VALUES (?,?,?,?,?,?)",
                (f"asset-{edition_id}", PROJECT_ID, "EXPLAINER_EDITION", edition_id, "RENDER", "VIDEO"),
            )
            connection.execute(
                "INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path,"
                " mime_type, byte_size, sha256, duration_ms, integrity_status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    media_version_id, f"asset-{edition_id}", 1, 1, "GENERATED",
                    f"renders/{edition_id}.mp4", "video/mp4", 4096, "r" * 64, 8342, "VERIFIED",
                ),
            )
        connection.execute(
            "INSERT INTO composition_renders (id, edition_id, composition_revision_id, video_id,"
            " project_id, revision_no, manifest_hash, status, sha256, frame_count, duration_ms,"
            " media_asset_id, media_version_id, integrity_status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                render_id, edition_id, composition_id, VIDEO_ID, PROJECT_ID, 1, "m" * 64, status,
                "r" * 64, frame_count, 8342,
                None if not media else f"asset-{edition_id}",
                None if not media else media_version_id,
                integrity,
            ),
        )
    return render_id


def _view(database: Database) -> dict[str, Any]:
    connection = database.connect()
    try:
        return _editions_view(ExplainerRepository(connection), PROJECT_ID)
    finally:
        connection.close()


def _edition(database: Database, edition_id: str) -> dict[str, Any]:
    return next(item for item in _view(database)["editions"] if str(item["id"]) == edition_id)


# --------------------------------------------------------------------------- #
# the player has enough facts
# --------------------------------------------------------------------------- #
def test_a_verified_render_exposes_playable_media_facts(database: Database) -> None:
    (edition_id,) = _seed(database)
    composition_id = _add_composition(database, edition_id)
    _add_render(database, edition_id, composition_id=composition_id)

    render = _edition(database, edition_id)["current_render"]
    assert render is not None
    assert render["availability"] == "PLAYABLE"
    assert render["playable"] is True
    assert render["media_version_id"] == f"media-{edition_id}"
    assert render["mime_type"] == "video/mp4"
    assert render["byte_size"] == 4096
    assert render["duration_ms"] == 8342
    assert render["frame_count"] == 250
    # The rational frame rate survives as a rational: 30000/1001 is not 29.97.
    assert (render["fps_num"], render["fps_den"]) == (30000, 1001)
    assert render["playback_url"].endswith(f"/media-versions/media-{edition_id}/content")
    assert render["thumbnail_url"].endswith("/thumbnail")
    assert render["waveform_url"].endswith("/waveform")


def test_no_path_leaks_into_the_media_view(database: Database) -> None:
    (edition_id,) = _seed(database)
    composition_id = _add_composition(database, edition_id)
    _add_render(database, edition_id, composition_id=composition_id)
    payload = _view(database)
    serialized = str(payload)
    assert "renders/" not in serialized
    assert "rel_path" not in serialized
    assert payload["media_paths_are_never_exposed"] is True


def test_frame_count_falls_back_to_the_composition(database: Database) -> None:
    (edition_id,) = _seed(database)
    composition_id = _add_composition(database, edition_id, total_frames=480)
    _add_render(database, edition_id, composition_id=composition_id, frame_count=None)
    render = _edition(database, edition_id)["current_render"]
    assert render["frame_count"] == 480


def test_missing_media_reference_is_reported_not_faked(database: Database) -> None:
    (edition_id,) = _seed(database)
    composition_id = _add_composition(database, edition_id)
    _add_render(database, edition_id, composition_id=composition_id, media=False)
    render = _edition(database, edition_id)["current_render"]
    assert render["availability"] == "MEDIA_REFERENCE_MISSING"
    assert render["playable"] is False
    assert render["playback_url"] is None
    assert render["mime_type"] is None


def test_unverified_or_unfinished_renders_are_not_offered_as_current(database: Database) -> None:
    (edition_id,) = _seed(database)
    composition_id = _add_composition(database, edition_id)
    _add_render(database, edition_id, composition_id=composition_id, integrity="HASH_MISMATCH")
    # ``current_root_render`` requires VERIFIED, so a corrupt render is never
    # offered as "the current film" — the page must show "no render", not a player
    # bound to a hash mismatch.
    assert _edition(database, edition_id)["current_render"] is None

    with database.transaction() as connection:
        connection.execute(
            "UPDATE composition_renders SET integrity_status='VERIFIED', status='RUNNING'"
            " WHERE edition_id=?",
            (edition_id,),
        )
    render = _edition(database, edition_id)["current_render"]
    assert render is not None
    assert render["availability"] == "NOT_READY"
    assert render["playable"] is False


def test_an_edition_without_a_render_says_so(database: Database) -> None:
    (edition_id,) = _seed(database)
    item = _edition(database, edition_id)
    assert item["current_render"] is None
    assert item["composition"] is None


def test_subtitle_counts_are_per_edition(database: Database) -> None:
    first, second = _seed(database, editions=2)
    with database.transaction() as connection:
        for index in range(3):
            connection.execute(
                "INSERT INTO explainer_subtitle_revisions (id, video_id, edition_id, locale,"
                " revision_no, content_hash, cues_json) VALUES (?,?,?,?,?,?,?)",
                (f"sub-a-{index}", VIDEO_ID, first, "zh-CN", index + 1, "h" * 64, "[]"),
            )
        connection.execute(
            "INSERT INTO explainer_subtitle_revisions (id, video_id, edition_id, locale,"
            " revision_no, content_hash, cues_json) VALUES (?,?,?,?,?,?,?)",
            ("sub-b-0", VIDEO_ID, second, "en-US", 1, "i" * 64, "[]"),
        )
    assert _edition(database, first)["subtitle_revision_count"] == 3
    assert _edition(database, second)["subtitle_revision_count"] == 1


def test_composition_projection_carries_frames_and_rate(database: Database) -> None:
    (edition_id,) = _seed(database)
    _add_composition(database, edition_id, total_frames=1234)
    composition = _edition(database, edition_id)["composition"]
    assert composition["total_frames"] == 1234
    assert (composition["fps_num"], composition["fps_den"]) == (30000, 1001)
    assert composition["manifest_hash"] == "m" * 64


def test_editions_keep_their_independent_clocks(database: Database) -> None:
    first, second = _seed(database, editions=2)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE explainer_editions SET voice_locale='en-US', duration_policy='FIXED_FRAMES' WHERE id=?",
            (second,),
        )
    view = _view(database)
    assert view["independent_clocks"]["zh-CN"] == "NATURAL_NARRATION"
    assert view["independent_clocks"]["en-US"] == "FIXED_FRAMES"
    assert view["english_timing_copied_from_source_locale"] is False
    assert {str(item["id"]) for item in view["editions"]} == {first, second}


# --------------------------------------------------------------------------- #
# the timeline needs real manifest ranges
# --------------------------------------------------------------------------- #
def test_composition_items_expose_real_ranges_without_paths(database: Database) -> None:
    (edition_id,) = _seed(database)
    composition_id = _add_composition(database, edition_id)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO composition_items (id, composition_revision_id, edition_id, video_id, track, item_kind, ordinal, item_hash,"
            " start_frame, end_frame_exclusive, source_in_us, source_out_us)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("item-v1", composition_id, edition_id, VIDEO_ID, "VIDEO", "VIDEO_CLIP", 0, "h1" + "0" * 62, 0, 50, 0, 2_000_000),
        )
        connection.execute(
            "INSERT INTO composition_items (id, composition_revision_id, edition_id, video_id, track, item_kind, ordinal, item_hash,"
            " start_frame, end_frame_exclusive, source_in_us, source_out_us)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("item-v2", composition_id, edition_id, VIDEO_ID, "VIDEO", "VIDEO_CLIP", 1, "h2" + "0" * 62, 50, 100, 2_000_000, 4_000_000),
        )
        connection.execute(
            "INSERT INTO composition_items (id, composition_revision_id, edition_id, video_id, track, item_kind, ordinal, item_hash,"
            " start_frame, end_frame_exclusive)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("item-n1", composition_id, edition_id, VIDEO_ID, "NARRATION", "AUDIO_CLIP", 0, "h3" + "0" * 62, 0, 100),
        )
    item = _edition(database, edition_id)
    items = item["composition_items"]
    # ``composition_items`` is ordered by (track, ordinal); the timeline groups by
    # track itself, so the assertion is per track rather than global.
    assert [str(entry["track"]) for entry in items] == sorted(str(entry["track"]) for entry in items)
    assert len(items) == 3
    video_items = [entry for entry in items if str(entry["track"]) == "VIDEO"]
    assert video_items[1]["start_frame"] == 50
    assert video_items[1]["end_frame_exclusive"] == 100
    assert video_items[1]["source_in_us"] == 2_000_000
    # No filesystem path may appear anywhere in the payload.
    serialized = str(item)
    assert "rel_path" not in serialized
    assert ".mp4" not in serialized



"""Candidate ownership, selection purpose scope and the human lock (design §D2/§D3).

The design splits one overloaded "adopt a picture" action into three layers —
``REFERENCE`` (定妆参考), ``KEYFRAME`` (the adopted first frame) and ``VISUAL`` (the
final composable clip) — and separates "采用" from "采用并锁定".  Before this change a
single ``(beat, edition)`` scope held one ACTIVE row, so adopting a nicer still
silently replaced an already adopted clip, and a lock on the still blocked the
clip.

These tests pin the new contract:

* each purpose owns its own ACTIVE row, and adopting one never supersedes another;
* the ``must_be_motion`` gate applies to the final ``VISUAL`` selection only, so a
  still candidate is still adoptable as the first frame an I2V job consumes;
* a human lock is per purpose and can be removed by an explicit, actor-recorded
  unlock that keeps the current media;
* an unlocked (ordinary) adoption is written by ``MACHINE_POLICY`` and never
  produces a human lock;
* entity reference candidates live in the same table with an entity owner and can
  never be adopted as a beat's picture.

Requirement mapping: §D2.2 items 1–3, §D3, §B9, §B12 item 7 and §F3.
"""

from __future__ import annotations

import pytest

from local_drama.application.explainers.storyboard import build_storyboard_service
from local_drama.domain.explainers.contracts import ExplainerContractError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "owner-project"
VIDEO_ID = "owner-video"
BEAT_ID = "owner-beat"
MOVING_BEAT_ID = "owner-beat-moving"
ENTITY_ID = "owner-entity"
EDITION_ID = "owner-edition"
ASSET_ID = "owner-asset"
NOW = "2026-01-01T00:00:00Z"
FULLY_CHECKED = {
    "file_valid": True,
    "decoded": True,
    "content_relevant": True,
    "identity_ok": True,
    "constraints_ok": True,
    "text_readable": True,
    # A still frame that is explicitly *not* reported as a motion violation is a
    # perfectly valid first frame for a moving beat (design §D4.2).
    "must_be_motion_violated": False,
}


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'owner_proj', '归属', 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '归属', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        repo = ExplainerRepository(connection)
        repo.insert(
            "explainer_editions",
            {"id": EDITION_ID, "video_id": VIDEO_ID, "edition_key": "zh-captioned-169", "voice_locale": "zh-CN"},
        )
        repo.insert(
            "explainer_visual_beats",
            {
                "id": BEAT_ID,
                "video_id": VIDEO_ID,
                "code": "B001",
                "ordinal": 0,
                "render_type": "I2V",
                "visual_intent": "静帧画面",
            },
        )
        repo.insert(
            "explainer_visual_beats",
            {
                "id": MOVING_BEAT_ID,
                "video_id": VIDEO_ID,
                "code": "B002",
                "ordinal": 1,
                "render_type": "I2V",
                "visual_intent": "人物走来",
                "must_be_motion": True,
            },
        )
        repo.insert(
            "explainer_entities",
            {
                "id": ENTITY_ID,
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "E001",
                "entity_type": "FICTIONAL_CHARACTER",
                "name": "周工",
            },
        )


def _media(database: Database, media_id: str, *, sha: str, asset_id: str, kind: str = "VIDEO") -> None:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
            version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_BEAT_CLIP', ?, 1, '{}', ?, ?, 'test', 1, 'v2')""",
            (asset_id, PROJECT_ID, VIDEO_ID, kind, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
            byte_size, sha256, integrity_status, duration_ms, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 1, 'VISUAL_GENERATION', ?, ?, 1024, ?, 'VERIFIED', ?, ?, ?, 'test', 1, 'v2')""",
            (
                media_id,
                asset_id,
                f"media/{media_id}.bin",
                "video/mp4" if kind == "VIDEO" else "image/png",
                sha,
                5000 if kind == "VIDEO" else None,
                NOW,
                NOW,
            ),
        )


def _candidate(
    database: Database,
    *,
    candidate_id: str,
    purpose: str,
    media_id: str,
    beat_id: str | None = BEAT_ID,
    entity_id: str | None = None,
    render_type: str = "I2V",
    variant_no: int = 1,
) -> str:
    with database.transaction() as connection:
        ExplainerRepository(connection).insert(
            "explainer_media_candidates",
            {
                "id": candidate_id,
                "video_id": VIDEO_ID,
                "beat_id": beat_id,
                "entity_id": entity_id,
                "variant_no": variant_no,
                "candidate_kind": "CREATIVE",
                "purpose": purpose,
                "media_version_id": media_id,
                "media_sha256": "a" * 64,
                "status": "READY",
                "render_type_planned": render_type,
                # A VISUAL candidate is a real composable clip and therefore carries its
                # actual render type; a KEYFRAME / REFERENCE candidate is a picture, which
                # declares no rendered type at all.
                "render_type_actual": render_type if purpose == "VISUAL" else None,
                "qc_summary_json": dict(FULLY_CHECKED),
            },
        )
    return candidate_id


def _adopt(database: Database, **kwargs: object) -> dict[str, object]:
    with database.transaction() as connection:
        return build_storyboard_service(ExplainerRepository(connection)).adopt_selection(
            project_id=PROJECT_ID, video_id=VIDEO_ID, **kwargs  # type: ignore[arg-type]
        )


def _unlock(database: Database, **kwargs: object) -> dict[str, object]:
    with database.transaction() as connection:
        return build_storyboard_service(ExplainerRepository(connection)).unlock_selection(
            project_id=PROJECT_ID, video_id=VIDEO_ID, **kwargs  # type: ignore[arg-type]
        )


def _active(database: Database, beat_id: str, purpose: str) -> dict[str, object] | None:
    with database.connect() as connection:
        return ExplainerRepository(connection).active_beat_selection(beat_id, purpose=purpose)


# --------------------------------------------------------------------------- #
# purpose scope
# --------------------------------------------------------------------------- #
def test_each_purpose_owns_its_own_active_selection(database: Database) -> None:
    _seed(database)
    _media(database, "mv-image", sha="1" * 64, asset_id="asset-image", kind="IMAGE")
    _media(database, "mv-clip", sha="2" * 64, asset_id="asset-clip")
    _candidate(database, candidate_id="cand-key", purpose="KEYFRAME", media_id="mv-image")
    _candidate(database, candidate_id="cand-visual", purpose="VISUAL", media_id="mv-clip", render_type="I2V")

    keyframe = _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key", purpose="KEYFRAME")
    visual = _adopt(database, beat_id=BEAT_ID, candidate_id="cand-visual", purpose="VISUAL")

    assert keyframe["purpose"] == "KEYFRAME"
    assert visual["purpose"] == "VISUAL"
    # The clip adoption must not supersede the first-frame adoption.
    assert visual["superseded_selection_id"] is None
    assert _active(database, BEAT_ID, "KEYFRAME")["candidate_id"] == "cand-key"
    assert _active(database, BEAT_ID, "VISUAL")["candidate_id"] == "cand-visual"
    with database.connect() as connection:
        rows = ExplainerRepository(connection).list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
    assert {str(row["status"]) for row in rows} == {"ACTIVE"}


def test_readopting_a_new_candidate_supersedes_only_the_same_purpose(database: Database) -> None:
    _seed(database)
    _media(database, "mv-image-1", sha="1" * 64, asset_id="asset-image-1", kind="IMAGE")
    _media(database, "mv-image-2", sha="2" * 64, asset_id="asset-image-2", kind="IMAGE")
    _candidate(database, candidate_id="cand-key-1", purpose="KEYFRAME", media_id="mv-image-1", variant_no=1)
    _candidate(database, candidate_id="cand-key-2", purpose="KEYFRAME", media_id="mv-image-2", variant_no=2)
    _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key-1", purpose="KEYFRAME")
    second = _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key-2", purpose="KEYFRAME")

    assert second["superseded_selection_id"]
    assert _active(database, BEAT_ID, "KEYFRAME")["candidate_id"] == "cand-key-2"
    with database.connect() as connection:
        superseded = ExplainerRepository(connection).list_where(
            "explainer_beat_selections", {"beat_id": BEAT_ID, "status": "SUPERSEDED"}
        )
    assert len(superseded) == 1


def test_an_edition_scoped_adoption_does_not_change_the_video_wide_one(database: Database) -> None:
    """A deliberate per-edition choice is neither overwritten by, nor leaked into, the global row."""

    _seed(database)
    _media(database, "mv-image-1", sha="1" * 64, asset_id="asset-image-1", kind="IMAGE")
    _media(database, "mv-image-2", sha="2" * 64, asset_id="asset-image-2", kind="IMAGE")
    _candidate(database, candidate_id="cand-key-1", purpose="KEYFRAME", media_id="mv-image-1", variant_no=1)
    _candidate(database, candidate_id="cand-key-2", purpose="KEYFRAME", media_id="mv-image-2", variant_no=2)
    first = _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key-1", purpose="KEYFRAME")
    _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key-2", purpose="KEYFRAME", edition_id=EDITION_ID)

    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        rows = repo.list_where("explainer_beat_selections", {"beat_id": BEAT_ID})
        global_row = repo.active_beat_selection(BEAT_ID, purpose="KEYFRAME")
        edition_row = repo.active_beat_selection(BEAT_ID, EDITION_ID, purpose="KEYFRAME")
        resolved = repo.resolved_beat_selection(BEAT_ID, EDITION_ID, purpose="KEYFRAME")
    assert all(str(row["status"]) == "ACTIVE" for row in rows)
    assert global_row is not None, [(row["id"], row["edition_id"], row["status"]) for row in rows]
    assert str(global_row["id"]) == str(first["selection_id"])
    assert edition_row is not None and str(edition_row["candidate_id"]) == "cand-key-2"
    # The documented fallback is an explicit read, not a silent override.
    assert str(resolved["candidate_id"]) == "cand-key-2"


def test_a_still_candidate_is_adoptable_as_the_first_frame_of_a_moving_beat(database: Database) -> None:
    """The must_be_motion gate belongs to the final clip, not to the input still."""

    _seed(database)
    _media(database, "mv-still", sha="1" * 64, asset_id="asset-still", kind="IMAGE")
    _candidate(
        database,
        candidate_id="cand-still",
        purpose="KEYFRAME",
        media_id="mv-still",
        beat_id=MOVING_BEAT_ID,
    )
    adopted = _adopt(database, beat_id=MOVING_BEAT_ID, candidate_id="cand-still", purpose="KEYFRAME")
    assert adopted["purpose"] == "KEYFRAME"


def test_a_still_graphic_candidate_is_refused_as_the_final_clip_of_a_moving_beat(database: Database) -> None:
    _seed(database)
    _media(database, "mv-still", sha="1" * 64, asset_id="asset-still", kind="IMAGE")
    _candidate(
        database,
        candidate_id="cand-still",
        purpose="VISUAL",
        media_id="mv-still",
        beat_id=MOVING_BEAT_ID,
        # INFOGRAPHIC is the surviving still render type: it is a real picture, never a
        # moving clip, so it can never satisfy a beat that must really move.
        render_type="INFOGRAPHIC",
    )
    with pytest.raises(ExplainerContractError) as raised:
        _adopt(database, beat_id=MOVING_BEAT_ID, candidate_id="cand-still", purpose="VISUAL")
    assert raised.value.code == "QC_BLOCKED"


def test_a_candidate_cannot_be_adopted_for_a_different_purpose(database: Database) -> None:
    _seed(database)
    _media(database, "mv-image", sha="1" * 64, asset_id="asset-image", kind="IMAGE")
    _candidate(database, candidate_id="cand-key", purpose="KEYFRAME", media_id="mv-image")
    with pytest.raises(ExplainerContractError) as raised:
        _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key", purpose="VISUAL")
    assert raised.value.code == "INVALID_REQUEST"


def test_a_pending_candidate_cannot_be_adopted(database: Database) -> None:
    _seed(database)
    _media(database, "mv-image", sha="1" * 64, asset_id="asset-image", kind="IMAGE")
    _candidate(database, candidate_id="cand-key", purpose="KEYFRAME", media_id="mv-image")
    with database.transaction() as connection:
        ExplainerRepository(connection).update("explainer_media_candidates", "cand-key", {"status": "PENDING"})
    with pytest.raises(ExplainerContractError) as raised:
        _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key", purpose="KEYFRAME")
    assert raised.value.code == "QC_BLOCKED"


def test_an_entity_reference_candidate_cannot_be_adopted_as_a_picture(database: Database) -> None:
    _seed(database)
    _media(database, "mv-ref", sha="1" * 64, asset_id="asset-ref", kind="IMAGE")
    _candidate(
        database,
        candidate_id="cand-ref",
        purpose="REFERENCE",
        media_id="mv-ref",
        beat_id=None,
        entity_id=ENTITY_ID,
    )
    with pytest.raises(ExplainerContractError) as raised:
        _adopt(database, beat_id=BEAT_ID, candidate_id="cand-ref", purpose="KEYFRAME")
    assert raised.value.code == "INVALID_REQUEST"


# --------------------------------------------------------------------------- #
# expected_selection_id concurrency
# --------------------------------------------------------------------------- #
def test_a_stale_expected_selection_id_is_refused(database: Database) -> None:
    _seed(database)
    _media(database, "mv-image-1", sha="1" * 64, asset_id="asset-image-1", kind="IMAGE")
    _media(database, "mv-image-2", sha="2" * 64, asset_id="asset-image-2", kind="IMAGE")
    _candidate(database, candidate_id="cand-1", purpose="KEYFRAME", media_id="mv-image-1", variant_no=1)
    _candidate(database, candidate_id="cand-2", purpose="KEYFRAME", media_id="mv-image-2", variant_no=2)
    first = _adopt(database, beat_id=BEAT_ID, candidate_id="cand-1", purpose="KEYFRAME")
    with pytest.raises(ExplainerContractError) as raised:
        _adopt(
            database,
            beat_id=BEAT_ID,
            candidate_id="cand-2",
            purpose="KEYFRAME",
            expected_selection_id="some-other-tab-selection",
        )
    assert raised.value.code == "STALE_REVISION"
    # The conflicting request kept the first selection intact.
    assert _active(database, BEAT_ID, "KEYFRAME")["id"] == first["selection_id"]


# --------------------------------------------------------------------------- #
# lock / unlock
# --------------------------------------------------------------------------- #
def test_an_ordinary_adoption_is_machine_authority_and_never_locks(database: Database) -> None:
    _seed(database)
    _media(database, "mv-image", sha="1" * 64, asset_id="asset-image", kind="IMAGE")
    _candidate(database, candidate_id="cand-key", purpose="KEYFRAME", media_id="mv-image")
    result = _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key", purpose="KEYFRAME")
    assert result["adoption_authority"] == "MACHINE_POLICY"
    assert result["locked_by_human"] is False
    with database.connect() as connection:
        assert ExplainerRepository(connection).has_human_lock(BEAT_ID, purpose="KEYFRAME") is False


def test_a_human_lock_on_one_purpose_does_not_block_the_other(database: Database) -> None:
    _seed(database)
    _media(database, "mv-image-1", sha="1" * 64, asset_id="asset-image-1", kind="IMAGE")
    _media(database, "mv-image-2", sha="2" * 64, asset_id="asset-image-2", kind="IMAGE")
    _media(database, "mv-image-3", sha="3" * 64, asset_id="asset-image-3", kind="IMAGE")
    _candidate(database, candidate_id="cand-key-1", purpose="KEYFRAME", media_id="mv-image-1", variant_no=1)
    _candidate(database, candidate_id="cand-key-2", purpose="KEYFRAME", media_id="mv-image-2", variant_no=2)
    _candidate(database, candidate_id="cand-visual", purpose="VISUAL", media_id="mv-image-3", variant_no=3)
    _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key-1", purpose="KEYFRAME", authority="HUMAN", actor="tester")

    with pytest.raises(ExplainerContractError) as raised:
        _adopt(database, beat_id=BEAT_ID, candidate_id="cand-key-2", purpose="KEYFRAME")
    assert raised.value.code == "QC_BLOCKED"

    # A different purpose is a different lock scope.
    adopted = _adopt(database, beat_id=BEAT_ID, candidate_id="cand-visual", purpose="VISUAL")
    assert adopted["purpose"] == "VISUAL"


def test_unlock_keeps_the_current_media_and_requires_an_actor(database: Database) -> None:
    _seed(database)
    _media(database, "mv-image", sha="1" * 64, asset_id="asset-image", kind="IMAGE")
    _candidate(database, candidate_id="cand-key", purpose="KEYFRAME", media_id="mv-image")
    adopted = _adopt(
        database, beat_id=BEAT_ID, candidate_id="cand-key", purpose="KEYFRAME", authority="HUMAN", actor="tester"
    )
    with pytest.raises(ExplainerContractError) as raised:
        _unlock(database, beat_id=BEAT_ID, selection_id=adopted["selection_id"], purpose="KEYFRAME", actor="")
    assert raised.value.code == "SCHEMA_INVALID"

    unlocked = _unlock(
        database, beat_id=BEAT_ID, selection_id=adopted["selection_id"], purpose="KEYFRAME", actor="tester"
    )
    assert unlocked["was_locked"] is True
    assert unlocked["locked_by_human"] is False
    assert unlocked["current_media_kept"] is True
    assert _active(database, BEAT_ID, "KEYFRAME")["candidate_id"] == "cand-key"


# --------------------------------------------------------------------------- #
# repository reads
# --------------------------------------------------------------------------- #
def test_media_candidates_requires_exactly_one_owner(database: Database) -> None:
    _seed(database)
    with database.connect() as connection:
        repo = ExplainerRepository(connection)
        with pytest.raises(ValueError):
            repo.media_candidates()
        with pytest.raises(ValueError):
            repo.media_candidates(beat_id=BEAT_ID, entity_id=ENTITY_ID)


def test_media_candidates_are_newest_first_and_never_hide_history(database: Database) -> None:
    _seed(database)
    _media(database, "mv-image-1", sha="1" * 64, asset_id="asset-image-1", kind="IMAGE")
    _candidate(database, candidate_id="cand-1", purpose="KEYFRAME", media_id="mv-image-1", variant_no=1)
    _candidate(database, candidate_id="cand-2", purpose="KEYFRAME", media_id="mv-image-1", variant_no=2)
    with database.transaction() as connection:
        ExplainerRepository(connection).update("explainer_media_candidates", "cand-1", {"status": "SUPERSEDED"})
    with database.connect() as connection:
        rows = ExplainerRepository(connection).media_candidates(beat_id=BEAT_ID, purpose="KEYFRAME")
    assert {str(row["id"]) for row in rows} == {"cand-1", "cand-2"}
    with database.connect() as connection:
        live = ExplainerRepository(connection).media_candidates(
            beat_id=BEAT_ID, purpose="KEYFRAME", include_superseded=False
        )
    assert {str(row["id"]) for row in live} == {"cand-2"}

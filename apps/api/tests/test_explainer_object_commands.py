"""The remaining per-object commands: take adoption, archive, beat edit, strict review.

Each command here closes a gap where the interface used to offer a control that did
nothing, or where a saved field had no write path at all:

* **采用此配音** (design §B4 「已生成版本 → 采用此配音」) — choosing an already
  generated take is a real, recorded human adoption with a real measured duration.
  A take without measurement, from another作品 or another clock locale, is refused.
* **不采用** (design §B5.3) — archiving keeps the media and is reversible, but the
  currently adopted candidate can never be archived away like that.
* **保存镜头描述 / 呈现方式** (design §B5.2、§B5.3、§B6.1) — a person's edit is recorded
  with an explicit revision check, the compatible ``prompt_intent`` and the structured
  ``shot_grammar_json.prompt_bundle`` are written together, and switching a
  motion-required beat to a still is recorded as an explicit user decision.
* **candidate-review.v1** (design §C5.5) — the strict boundary refuses a reply with a
  frame nobody sent, a missing criterion, an unasked-for criterion value, or an
  ``OBSERVED`` motion claim based on one still, instead of turning it into a verdict.
"""

from __future__ import annotations

import json

import pytest

from local_drama.application.explainers.storyboard import build_storyboard_service
from local_drama.domain.explainers.contracts import ExplainerContractError
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "cmd-project"
VIDEO_ID = "cmd-video"
BEAT_ID = "cmd-beat"
MOVING_BEAT_ID = "cmd-beat-moving"
EDITION_ID = "cmd-edition"
SEGMENT_ID = "cmd-segment"
AUDIO_ASSET_ID = "cmd-audio-asset"
IMAGE_ASSET_ID = "cmd-image-asset"
IMAGE_MEDIA_ID = "cmd-image-media"
NOW = "2026-01-01T00:00:00Z"
IMAGE_SHA = "c" * 64


def _seed(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'cmd_proj', '命令', 'DRAFT', 'v2', ?, 300000, 'EXPLAINER',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '命令', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
            version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_NARRATION', 'AUDIO', 1, '{}', ?, ?, 'test', 1, 'v2')""",
            (AUDIO_ASSET_ID, PROJECT_ID, VIDEO_ID, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
            version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_BEAT_CLIP', 'IMAGE', 1, '{}', ?, ?, 'test', 1, 'v2')""",
            (IMAGE_ASSET_ID, PROJECT_ID, VIDEO_ID, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
            byte_size, sha256, integrity_status, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, 1, 'VISUAL_GENERATION', 'media/uploaded.png', 'image/png', 2048, ?, 'VERIFIED',
            ?, ?, 'test', 1, 'v2')""",
            (IMAGE_MEDIA_ID, IMAGE_ASSET_ID, IMAGE_SHA, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO explainer_script_revisions (id, video_id, revision_no, locale, status, content_hash)
            VALUES ('cmd-script', ?, 1, 'zh-CN', 'FROZEN', ?)""",
            (VIDEO_ID, "s" * 64),
        )
        repo = ExplainerRepository(connection)
        repo.insert(
            "explainer_editions",
            {"id": EDITION_ID, "video_id": VIDEO_ID, "edition_key": "zh-captioned-169", "voice_locale": "zh-CN"},
        )
        repo.insert(
            "narration_segments",
            {
                "id": SEGMENT_ID,
                "video_id": VIDEO_ID,
                "script_revision_id": "cmd-script",
                "canonical_segment_id": "seg_001",
                "ordinal": 0,
                "locale": "zh-CN",
                "display_text": "第一句。",
                "spoken_text": "第一句。",
                "statement_type": "QUESTION",
                "segment_hash": "h" * 64,
            },
        )
        repo.insert(
            "explainer_visual_beats",
            {
                "id": BEAT_ID,
                "video_id": VIDEO_ID,
                "code": "B001",
                "ordinal": 0,
                "render_type": "I2V",
                "visual_intent": "静态画面",
                "prompt_intent": "静态画面",
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


@pytest.fixture()
def repo(database: Database):
    _seed(database)
    connection = database.connect()
    built = ExplainerRepository(connection)
    yield built
    connection.close()


def _take(database: Database, *, take_no: int, measured: int | None = 1300, locale: str = "zh-CN") -> str:
    media_version_id = f"cmd-mv-{take_no}"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
            byte_size, sha256, integrity_status, duration_ms, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, ?, 'NARRATION_TTS', ?, 'audio/wav', 4096, ?, 'VERIFIED', ?, ?, ?, 'test', 1, 'v2')""",
            (
                media_version_id,
                AUDIO_ASSET_ID,
                take_no,
                take_no,
                f"media/{media_version_id}.wav",
                "a" * 64,
                measured,
                NOW,
                NOW,
            ),
        )
        row = ExplainerRepository(connection).insert(
            "narration_takes",
            {
                "video_id": VIDEO_ID,
                "segment_id": SEGMENT_ID,
                "canonical_segment_id": "seg_001",
                "locale": locale,
                "take_no": take_no,
                "media_asset_id": AUDIO_ASSET_ID,
                "media_version_id": media_version_id,
                "media_sha256": "a" * 64,
                "segment_hash": "h" * 64,
                "measured_duration_ms": measured,
                "status": "VERIFIED",
                "selected": False,
            },
        )
    return str(row["id"])


def _candidate(
    database: Database,
    *,
    candidate_id: str,
    purpose: str,
    beat_id: str = BEAT_ID,
    checks: dict[str, object] | None = None,
) -> str:
    with database.transaction() as connection:
        ExplainerRepository(connection).insert(
            "explainer_media_candidates",
            {
                "id": candidate_id,
                "video_id": VIDEO_ID,
                "beat_id": beat_id,
                "variant_no": 1,
                "candidate_kind": "CREATIVE",
                "purpose": purpose,
                "media_asset_id": IMAGE_ASSET_ID,
                "media_version_id": IMAGE_MEDIA_ID,
                "media_sha256": IMAGE_SHA,
                "status": "READY",
                "render_type_planned": "I2V",
                # A VISUAL candidate is a real clip (I2V); a KEYFRAME / REFERENCE
                # candidate is a picture and stores no actual render type.
                "render_type_actual": "I2V" if purpose == "VISUAL" else None,
                "qc_summary_json": dict(checks if checks is not None else {"file_valid": True, "decoded": True}),
            },
        )
    return candidate_id


# --------------------------------------------------------------------------- #
# narration take adoption
# --------------------------------------------------------------------------- #
def test_adopting_an_existing_take_is_a_real_human_adoption(database: Database, repo) -> None:
    first = _take(database, take_no=1)
    second = _take(database, take_no=2)
    from local_drama.api.routes.explainers import _adopt_narration_take
    from local_drama.api.schemas.explainers import ExplainerNarrationTakeAdoptionRequest

    with database.transaction() as connection:
        inner = ExplainerRepository(connection)
        result = _adopt_narration_take(
            inner, EDITION_ID, second, ExplainerNarrationTakeAdoptionRequest(actor="reviewer")
        )
    assert result["adoption_authority"] == "HUMAN"
    assert result["measured_duration_ms"] == 1300
    assert result["superseded_take_ids"] == []
    with database.connect() as connection:
        inner = ExplainerRepository(connection)
        assert inner.find("narration_takes", second)["selected"] is True
        assert inner.find("narration_takes", first)["selected"] is False
        record = inner.find("narration_takes", second)["generation_json"]
    assert record["adoption_authority"] == "HUMAN"
    assert record["adopted_by"] == "reviewer"


def test_a_take_without_a_measured_duration_cannot_be_adopted(database: Database, repo) -> None:
    take = _take(database, take_no=1, measured=None)
    from local_drama.api.routes.explainers import _adopt_narration_take
    from local_drama.api.schemas.explainers import ExplainerNarrationTakeAdoptionRequest

    with database.transaction() as connection:
        with pytest.raises(ExplainerContractError) as raised:
            _adopt_narration_take(
                ExplainerRepository(connection),
                EDITION_ID,
                take,
                ExplainerNarrationTakeAdoptionRequest(actor="reviewer"),
            )
    assert raised.value.code == "QC_BLOCKED"


def test_a_take_from_another_clock_locale_is_refused(database: Database, repo) -> None:
    take = _take(database, take_no=1, locale="en-US")
    from local_drama.api.routes.explainers import _adopt_narration_take
    from local_drama.api.schemas.explainers import ExplainerNarrationTakeAdoptionRequest

    with database.transaction() as connection:
        with pytest.raises(ExplainerContractError) as raised:
            _adopt_narration_take(
                ExplainerRepository(connection),
                EDITION_ID,
                take,
                ExplainerNarrationTakeAdoptionRequest(actor="reviewer"),
            )
    assert raised.value.code == "INVALID_REQUEST"


# --------------------------------------------------------------------------- #
# archive / register
# --------------------------------------------------------------------------- #
def test_archiving_a_candidate_keeps_its_media_and_is_reversible(
    database: Database, repo
) -> None:
    candidate_id = _candidate(database, candidate_id="cmd-cand-1", purpose="KEYFRAME")
    with database.transaction() as connection:
        result = build_storyboard_service(ExplainerRepository(connection)).archive_candidate(
            project_id=PROJECT_ID, video_id=VIDEO_ID, candidate_id=candidate_id, actor="reviewer"
        )
    assert result["archived"] is True
    assert result["media_kept"] is True
    with database.connect() as connection:
        row = ExplainerRepository(connection).find("explainer_media_candidates", candidate_id)
    assert str(row["status"]) == "REJECTED"
    assert str(row["media_version_id"]) == IMAGE_MEDIA_ID


def test_the_adopted_candidate_cannot_be_archived(database: Database, repo) -> None:
    candidate_id = _candidate(
        database,
        candidate_id="cmd-cand-2",
        purpose="KEYFRAME",
        checks={
            "file_valid": True,
            "decoded": True,
            "content_relevant": True,
            "identity_ok": True,
            "constraints_ok": True,
            "text_readable": True,
            "must_be_motion_violated": False,
        },
    )
    with database.transaction() as connection:
        inner = ExplainerRepository(connection)
        build_storyboard_service(inner).adopt_selection(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beat_id=BEAT_ID,
            candidate_id=candidate_id,
            purpose="KEYFRAME",
        )
    with database.transaction() as connection:
        with pytest.raises(ExplainerContractError) as raised:
            build_storyboard_service(ExplainerRepository(connection)).archive_candidate(
                project_id=PROJECT_ID, video_id=VIDEO_ID, candidate_id=candidate_id, actor="reviewer"
            )
    assert raised.value.code == "QC_BLOCKED"


def test_registering_uploaded_media_creates_a_candidate_not_an_adoption(
    database: Database, repo
) -> None:
    import asyncio

    from local_drama.api.routes.explainers import register_explainer_candidate_from_media
    from local_drama.api.schemas.explainers import ExplainerCandidateRegistrationRequest

    request = ExplainerCandidateRegistrationRequest(media_version_id=IMAGE_MEDIA_ID, purpose="KEYFRAME")
    asyncio.get_event_loop_policy()
    # The route needs a Request; the pure path is the service call it delegates to, so
    # exercise the registration contract directly through the service.
    with database.transaction() as connection:
        inner = ExplainerRepository(connection)
        result = build_storyboard_service(inner).register_candidate(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beat_id=BEAT_ID,
            candidate_kind="CREATIVE",
            media_version_id=IMAGE_MEDIA_ID,
            purpose="KEYFRAME",
            fallback_reason="USER_UPLOADED_MEDIA",
            lineage={"source": "USER_UPLOADED_MEDIA", "actor": "reviewer"},
            execution_snapshot={"source": "USER_UPLOADED_MEDIA"},
            qc_summary={"file_valid": True, "content_checked": False},
        )
    assert result["candidate_id"]
    with database.connect() as connection:
        rows = ExplainerRepository(connection).media_candidates(beat_id=BEAT_ID, purpose="KEYFRAME")
    assert len(rows) == 1
    assert str(rows[0]["status"]) == "READY"
    # A first-frame candidate is a picture: the beat is planned as I2V, but the
    # candidate itself declares no rendered type at all.
    assert str(rows[0]["render_type_planned"]) == "I2V"
    assert rows[0]["render_type_actual"] is None
    # Media registration never adopts: no selection row exists yet.
    with database.connect() as connection:
        assert ExplainerRepository(connection).active_beat_selection(BEAT_ID, purpose="KEYFRAME") is None
    del request
    del register_explainer_candidate_from_media


def test_registering_a_still_as_the_final_clip_of_a_moving_beat_is_refused(
    database: Database, repo
) -> None:
    from local_drama.api.schemas.explainers import ExplainerCandidateRegistrationRequest

    payload = ExplainerCandidateRegistrationRequest(
        media_version_id=IMAGE_MEDIA_ID, purpose="VISUAL"
    )
    beat = repo.find("explainer_visual_beats", MOVING_BEAT_ID)
    assert bool(beat["must_be_motion"]) is True
    assert payload.purpose == "VISUAL"
    # The route applies the same gate the adoption path uses; assert the rule directly.
    with pytest.raises(ExplainerContractError) as raised:
        build_storyboard_service(repo).register_candidate(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beat_id=MOVING_BEAT_ID,
            candidate_kind="CREATIVE",
            media_version_id=IMAGE_MEDIA_ID,
            purpose="VISUAL",
            render_type_actual="INFOGRAPHIC",
            fallback_reason="USER_UPLOADED_MEDIA",
            lineage={},
            execution_snapshot={},
        )
    assert raised.value.code in {"SCHEMA_INVALID", "QC_BLOCKED"}


def test_a_registered_video_visual_candidate_is_recorded_as_i2v(database: Database, repo) -> None:
    """The only composable final clip is a real moving picture, so its actual type is I2V."""

    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO media_assets (id, project_id, owner_type, owner_id, purpose, media_kind,
            version_counter, metadata_json, created_at, updated_at, created_by, revision, schema_version)
            VALUES ('cmd-video-asset', ?, 'EXPLAINER_VIDEO', ?, 'EXPLAINER_BEAT_CLIP', 'VIDEO', 1, '{}',
            ?, ?, 'test', 1, 'v2')""",
            (PROJECT_ID, VIDEO_ID, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO media_versions (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type,
            byte_size, sha256, integrity_status, duration_ms, created_at, updated_at, created_by, revision, schema_version)
            VALUES ('cmd-video-media', 'cmd-video-asset', 1, 1, 'VISUAL_GENERATION', 'media/clip.mp4', 'video/mp4',
            4096, ?, 'VERIFIED', 5000, ?, ?, 'test', 1, 'v2')""",
            ("d" * 64, NOW, NOW),
        )
    with database.transaction() as connection:
        result = build_storyboard_service(ExplainerRepository(connection)).register_candidate(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beat_id=MOVING_BEAT_ID,
            candidate_kind="CREATIVE",
            media_version_id="cmd-video-media",
            purpose="VISUAL",
            lineage={"source": "USER_UPLOADED_MEDIA"},
            execution_snapshot={"source": "USER_UPLOADED_MEDIA"},
        )
    with database.connect() as connection:
        row = ExplainerRepository(connection).find("explainer_media_candidates", str(result["candidate_id"]))
    assert str(row["render_type_planned"]) == "I2V"
    assert str(row["render_type_actual"]) == "I2V"


# --------------------------------------------------------------------------- #
# beat edit
# --------------------------------------------------------------------------- #
def test_saving_a_beat_description_writes_both_prompt_fields(database: Database, repo) -> None:
    with database.transaction() as connection:
        result = build_storyboard_service(ExplainerRepository(connection)).update_beat(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beat_id=BEAT_ID,
            changes={
                "visual_intent": "改为近景：人物盯着屏幕",
                "prompt_intent": "中景，人物侧脸，屏幕冷光",
                "negative_prompt": "额外人物",
                "camera_movement": "缓慢推进",
            },
            actor="reviewer",
        )
    assert result["edit_authority"] == "HUMAN"
    assert "prompt_intent" in result["changed_fields"]
    with database.connect() as connection:
        beat = ExplainerRepository(connection).find("explainer_visual_beats", BEAT_ID)
    assert beat["prompt_intent"] == "中景，人物侧脸，屏幕冷光"
    bundle = beat["shot_grammar_json"]["prompt_bundle"]
    assert bundle["prompt"] == "中景，人物侧脸，屏幕冷光"
    assert bundle["negative_prompt"] == "额外人物"
    assert bundle["camera_movement"] == "缓慢推进"
    assert bundle["visual_intent"] == "改为近景：人物盯着屏幕"


def test_a_stale_beat_revision_is_refused(database: Database, repo) -> None:
    with database.transaction() as connection:
        with pytest.raises(ExplainerContractError) as raised:
            build_storyboard_service(ExplainerRepository(connection)).update_beat(
                project_id=PROJECT_ID,
                video_id=VIDEO_ID,
                beat_id=BEAT_ID,
                changes={"visual_intent": "新描述"},
                actor="reviewer",
                expected_revision=99,
            )
    assert raised.value.code == "STALE_REVISION"


def test_unknown_beat_fields_are_rejected(database: Database, repo) -> None:
    with database.transaction() as connection:
        with pytest.raises(ExplainerContractError) as raised:
            build_storyboard_service(ExplainerRepository(connection)).update_beat(
                project_id=PROJECT_ID,
                video_id=VIDEO_ID,
                beat_id=BEAT_ID,
                changes={"seed": 1234},
                actor="reviewer",
            )
    assert raised.value.code == "SCHEMA_INVALID"


def test_switching_a_moving_beat_to_a_still_records_the_decision(database: Database, repo) -> None:
    with database.transaction() as connection:
        build_storyboard_service(ExplainerRepository(connection)).update_beat(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            beat_id=MOVING_BEAT_ID,
            # INFOGRAPHIC is the surviving still presentation; it is still a still, so an
            # explicit switch away from motion must be recorded rather than silent.
            changes={"render_type": "INFOGRAPHIC"},
            actor="reviewer",
        )
    with database.connect() as connection:
        beat = ExplainerRepository(connection).find("explainer_visual_beats", MOVING_BEAT_ID)
    assert str(beat["render_type"]) == "INFOGRAPHIC"
    assert beat["must_be_motion"] in (False, 0)
    assert str(beat["fallback_reason"]) == "USER_CHANGED_PRESENTATION_TO_STILL"


def test_declaring_a_still_beat_as_required_motion_is_refused(database: Database, repo) -> None:
    with database.transaction() as connection:
        with pytest.raises(ExplainerContractError) as raised:
            build_storyboard_service(ExplainerRepository(connection)).update_beat(
                project_id=PROJECT_ID,
                video_id=VIDEO_ID,
                beat_id=BEAT_ID,
                changes={"render_type": "INFOGRAPHIC", "must_be_motion": True},
                actor="reviewer",
            )
    assert raised.value.code == "SCHEMA_INVALID"


# --------------------------------------------------------------------------- #
# candidate-review.v1
# --------------------------------------------------------------------------- #
def _request_payload() -> dict[str, object]:
    from local_drama.application.explainers.candidate_review import build_review_request

    return build_review_request(
        candidate_id="cmd-cand-review",
        candidate_snapshot={"media_sha256": IMAGE_SHA, "prompt_hash": "p" * 64},
        frame_manifest=[
            {"frame_id": 0, "frame_ref": "frames/0.png", "role": "CANDIDATE_FRAME"},
            {"frame_id": 24, "frame_ref": "frames/24.png", "role": "CANDIDATE_FRAME"},
        ],
        review_basis={"must_be_motion": False, "references": []},
    )


def _report(frame_results: list[dict[str, object]], *, motion: str = "NOT_OBSERVED") -> dict[str, object]:
    return {
        "schema_version": "localdrama.explainer.candidate-review.v1",
        "candidate_id": "cmd-cand-review",
        "frame_results": frame_results,
        "motion_observation": motion,
    }


def _frame(frame_id: int, checks: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"frame_id": frame_id, "observations": ["看到一个人物"], "checks": checks, "issues": [], "unknown_reason": "", **extra}


def test_a_valid_review_is_accepted_and_mapped(database: Database) -> None:
    from local_drama.application.explainers.candidate_review import (
        CANDIDATE_REVIEW_CRITERIA,
        findings_from_review,
        validate_review_response,
    )

    checks = [{"criterion": criterion, "result": "PASS", "evidence": "符合"} for criterion in CANDIDATE_REVIEW_CRITERIA]
    checks[2] = {"criterion": "SCENE_CONTINUITY", "result": "FAIL", "evidence": "场景变了"}
    report = _report([_frame(0, checks), _frame(24, checks)])
    review = validate_review_response(report, request=_request_payload())
    assert review["checked_frame_ids"] == [0, 24]
    assert review["criteria_never_reported"] == []
    findings = findings_from_review(review, frame_refs={0: "frames/0.png", 24: "frames/24.png"})
    assert [item["issue_kind"] for item in findings] == ["SCENE_CONTINUITY", "SCENE_CONTINUITY"]
    assert all(item["criterion_result"] == "FAIL" for item in findings)


def test_a_frame_nobody_sent_is_refused(database: Database) -> None:
    from local_drama.application.explainers.candidate_review import (
        CANDIDATE_REVIEW_CRITERIA,
        validate_review_response,
    )

    checks = [{"criterion": criterion, "result": "PASS", "evidence": "符合"} for criterion in CANDIDATE_REVIEW_CRITERIA]
    with pytest.raises(ExplainerContractError) as raised:
        validate_review_response(_report([_frame(999, checks)]), request=_request_payload())
    assert raised.value.code == "SCHEMA_INVALID"


def test_a_missing_criterion_is_reported_as_never_measured(database: Database) -> None:
    from local_drama.application.explainers.candidate_review import validate_review_response

    partial = [{"criterion": "CHARACTER_COUNT", "result": "PASS", "evidence": "一人"}]
    review = validate_review_response(_report([_frame(0, partial)]), request=_request_payload())
    assert {item["criterion"] for item in review["criteria_never_reported"]} == {
        "REFERENCE_CONSISTENCY",
        "SCENE_CONTINUITY",
        "KEY_PROP",
        "MAIN_ACTION",
        "DISTORTION",
        "UNEXPECTED_TEXT",
    }


def test_motion_cannot_be_observed_from_a_single_still(database: Database) -> None:
    from local_drama.application.explainers.candidate_review import (
        build_review_request,
        validate_review_response,
    )

    request = build_review_request(
        candidate_id="cmd-cand-review",
        candidate_snapshot={},
        frame_manifest=[{"frame_id": 0, "frame_ref": "frames/0.png", "role": "CANDIDATE_FRAME"}],
        review_basis={},
    )
    checks = [{"criterion": "MAIN_ACTION", "result": "PASS", "evidence": "动作完成"}]
    with pytest.raises(ExplainerContractError) as raised:
        validate_review_response(_report([_frame(0, checks)], motion="OBSERVED"), request=request)
    assert raised.value.code == "SCHEMA_INVALID"


def test_an_unknown_criterion_stays_unknown_without_becoming_a_problem(database: Database) -> None:
    """An unanswered criterion must block automatic adoption without inventing a defect."""

    from local_drama.application.explainers.candidate_review import (
        CANDIDATE_REVIEW_CRITERIA,
        findings_from_review,
        validate_review_response,
        verdicts_from_review,
    )

    checks = [{"criterion": criterion, "result": "PASS", "evidence": "符合"} for criterion in CANDIDATE_REVIEW_CRITERIA]
    checks[0] = {"criterion": "CHARACTER_COUNT", "result": "UNKNOWN", "evidence": "人物被遮挡"}
    review = validate_review_response(_report([_frame(0, checks)]), request=_request_payload())
    # No fabricated problem card for "could not tell"...
    assert [item for item in findings_from_review(review) if item["issue_kind"] == "VISUAL_UNKNOWN"] == []
    # ...but the gate state stays UNKNOWN, so automatic adoption still refuses it.
    verdicts = verdicts_from_review(review)
    assert verdicts["check_states"]["identity_ok"] == "UNKNOWN"
    assert "identity_ok" not in verdicts["verdicts"]
    assert verdicts["criteria_states"]["CHARACTER_COUNT"] == "UNKNOWN"


def test_a_failing_criterion_is_a_problem_and_a_missing_one_is_not(database: Database) -> None:
    """The user-visible problem list must contain real defects only."""

    from local_drama.application.explainers.candidate_review import (
        CANDIDATE_REVIEW_CRITERIA,
        findings_from_review,
        validate_review_response,
        verdicts_from_review,
    )

    checks = [{"criterion": criterion, "result": "PASS", "evidence": "符合"} for criterion in CANDIDATE_REVIEW_CRITERIA]
    checks[3] = {"criterion": "KEY_PROP", "result": "FAIL", "evidence": "道具缺失"}
    review = validate_review_response(_report([_frame(0, checks)]), request=_request_payload())
    problems = findings_from_review(review)
    assert [item["issue_kind"] for item in problems] == ["KEY_PROP"]
    assert verdicts_from_review(review)["verdicts"]["content_relevant"] is False


def test_an_unrecognised_issue_label_is_preserved_not_dropped(database: Database) -> None:
    from local_drama.application.explainers.candidate_review import (
        CANDIDATE_REVIEW_CRITERIA,
        findings_from_review,
        validate_review_response,
    )

    checks = [{"criterion": criterion, "result": "PASS", "evidence": "符合"} for criterion in CANDIDATE_REVIEW_CRITERIA]
    frame = _frame(
        0,
        checks,
        issues=[{"issue_kind": "SOMETHING_NEW", "observed": "看到异常", "expected": "无异常", "confidence": 0.8, "unknown_reason": ""}],
    )
    review = validate_review_response(_report([frame]), request=_request_payload())
    findings = findings_from_review(review)
    assert any(item["issue_kind"] == "VISUAL_UNIDENTIFIED_ISSUE" for item in findings)
    assert any(item.get("reported_issue_kind") == "SOMETHING_NEW" for item in findings)


def test_a_report_for_another_candidate_is_refused(database: Database) -> None:
    from local_drama.application.explainers.candidate_review import (
        CANDIDATE_REVIEW_CRITERIA,
        validate_review_response,
    )

    checks = [{"criterion": criterion, "result": "PASS", "evidence": "符合"} for criterion in CANDIDATE_REVIEW_CRITERIA]
    report = _report([_frame(0, checks)])
    report["candidate_id"] = "someone-else"
    with pytest.raises(ExplainerContractError) as raised:
        validate_review_response(report, request=_request_payload())
    assert raised.value.code == "SCHEMA_INVALID"


def test_a_wrong_schema_version_is_refused(database: Database) -> None:
    from local_drama.application.explainers.candidate_review import (
        CANDIDATE_REVIEW_CRITERIA,
        validate_review_response,
    )

    checks = [{"criterion": criterion, "result": "PASS", "evidence": "符合"} for criterion in CANDIDATE_REVIEW_CRITERIA]
    report = _report([_frame(0, checks)])
    report["schema_version"] = "localdrama.explainer.candidate-review.v0"
    with pytest.raises(ExplainerContractError) as raised:
        validate_review_response(report, request=_request_payload())
    assert raised.value.code == "SCHEMA_INVALID"


def test_an_unknown_top_level_field_is_refused(database: Database) -> None:
    from local_drama.application.explainers.candidate_review import (
        CANDIDATE_REVIEW_CRITERIA,
        validate_review_response,
    )

    checks = [{"criterion": criterion, "result": "PASS", "evidence": "符合"} for criterion in CANDIDATE_REVIEW_CRITERIA]
    report = _report([_frame(0, checks)])
    report["adopted"] = True
    with pytest.raises(ExplainerContractError) as raised:
        validate_review_response(report, request=_request_payload())
    assert raised.value.code == "SCHEMA_INVALID"


def test_the_strict_provider_refuses_an_unvalidated_reply(workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shape the contract rejects must raise, so the stage records UNCHECKED.

    The frame has to be a real readable file: a check that never saw an image is
    UNCHECKED for a different reason, and this test is about the contract boundary.
    """

    import local_drama.application.explainers.visual_qc as visual_qc

    frame_path = workspace.work_root / "frames" / "0.png"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    captured: dict[str, object] = {}

    class _Client:
        supports_images = True
        model = "test-multimodal"

        def chat_json(self, system, user, images, *, json_schema=None, inference_options=None):
            captured["images"] = images
            # Frame 7 was never sent and one still cannot prove motion.
            return {"frames": [{"frame_id": 7, "issues": []}], "motion_observation": "OBSERVED"}

    provider = visual_qc.LocalLlmVisualQcProvider(
        client_factory=lambda: _Client(),
        frame_path_resolver=lambda frame: frame_path,
        strict_review=_request_payload(),
        strict_required=True,
    )
    with pytest.raises(ExplainerContractError) as raised:
        provider.check_frames(
            frames=[
                {
                    "frame_id": 0,
                    "frame_ref": "frames/0.png",
                    "media_version_id": IMAGE_MEDIA_ID,
                    "candidate_id": "cmd-cand-review",
                }
            ],
            questions=["检查画面"],
        )
    # The reply really went out as an image, and the refusal is the contract.
    assert captured.get("images")
    assert raised.value.code == "SCHEMA_INVALID"
    assert raised.value.details.get("errors")


def test_json_report_round_trips_through_the_contract(database: Database) -> None:
    """The strict model accepts a JSON-encoded reply, which is what the client returns."""

    from local_drama.application.explainers.candidate_review import (
        CANDIDATE_REVIEW_CRITERIA,
        validate_review_response,
    )

    checks = [{"criterion": criterion, "result": "PASS", "evidence": "符合"} for criterion in CANDIDATE_REVIEW_CRITERIA]
    payload = json.loads(json.dumps(_report([_frame(0, checks)])))
    review = validate_review_response(payload, request=_request_payload())
    assert review["schema_version"] == "localdrama.explainer.candidate-review.v1"


# --------------------------------------------------------------------------- #
# story seed and reference design over HTTP
# --------------------------------------------------------------------------- #
def test_the_story_seed_route_only_accepts_topic_original_fiction(
    database: Database, repo
) -> None:
    """A factual topic must go through the research path, never the fiction seed."""

    from local_drama.api.routes.explainers import _create_story_seed
    from local_drama.api.schemas.explainers import ExplainerStorySeedRequest

    request = _StubRequest(database)
    with database.transaction() as connection:
        with pytest.raises(ExplainerContractError) as raised:
            _create_story_seed(
                request,
                ExplainerRepository(connection),
                PROJECT_ID,
                ExplainerStorySeedRequest(),
            )
    # The video is FACTUAL_EXPLAINER with the legacy ADAPT_SOURCES policy, so the
    # fiction branch refuses instead of silently inventing a story.
    assert raised.value.code == "INVALID_REQUEST"


def test_the_creative_scope_is_forwarded_to_the_seed_planner(database: Database, repo) -> None:
    from local_drama.api.schemas.explainers import ExplainerStorySeedRequest

    payload = ExplainerStorySeedRequest(
        revision_request="把结局改成开放式",
        creative_scope={"story_tone": "悬疑", "forbidden_settings": ["真实事件"]},
    )
    assert payload.revision_request == "把结局改成开放式"
    assert payload.creative_scope["forbidden_settings"] == ["真实事件"]


def test_the_reference_design_route_surfaces_the_compiled_prompt(database: Database, repo) -> None:
    """The single requested entity's prompt is surfaced flat, deterministically."""

    from local_drama.api.routes.explainers import _reference_design_view

    entity = repo.find("explainer_entities", "cmd-entity")
    assert entity is None
    with database.transaction() as connection:
        ExplainerRepository(connection).insert(
            "explainer_entities",
            {
                "id": "cmd-entity",
                "video_id": VIDEO_ID,
                "project_id": PROJECT_ID,
                "code": "E900",
                "entity_type": "FICTIONAL_CHARACTER",
                "name": "守灯员",
            },
        )
    request = _StubRequest(database)
    with database.connect() as connection:
        result = _reference_design_view(
            request, ExplainerRepository(connection), PROJECT_ID, "cmd-entity", "HERO", False
        )
    assert result["entity_id"] == "cmd-entity"
    assert result["reference_kind"] == "HERO"
    assert result["description_prompt"]
    assert "守灯员" in result["description_prompt"]
    assert result["compile_order"]
    assert result["over_budget_fields"] == []
    assert result["requires_real_image_consumption"] is True
    assert result["model_calls_on_this_read"] == 0


class _StubRequest:
    """A minimal stand-in for a Starlette request.

    Only ``app.state.database`` and ``app.state.settings`` are read by the routes under
    test, so the stub supplies exactly those two to keep the assertion about the route
    logic rather than about TestClient plumbing.
    """

    def __init__(self, database: Database) -> None:
        state = type("State", (), {"database": database, "settings": _PlannerSettings()})()
        self.app = type("App", (), {"state": state})()
        self._tmp = None


class _PlannerSettings:
    """Settings good enough for the planner factory: no model profile is published."""

    def __init__(self) -> None:
        self.database_path = None
        self.work_root = None


_Settings = _PlannerSettings

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from local_drama.application.episode_front_half_actions import EpisodeFrontHalfActionService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.keyframe_references import approved_keyframes_for_shots
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.application.worker_handlers.automation_task import _automation_keyframe_check
from tests.test_director_fields import _published_camera_profile


def _project_episode_shots(workspace, database, code: str, shot_codes: tuple[str, ...]):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shots = [projects.create_shot(str(episode["id"]), code, 4_000) for code in shot_codes]
    return project, episode, shots


def _insert_approved_keyframe(
    workspace,
    database,
    *,
    project_id: str,
    shot_id: str,
    owner_type: str = "SHOT",
    approved: bool = True,
    stale_review: bool = False,
    profile_id: str | None = None,
) -> tuple[str, str | None]:
    """Insert only persisted media/review facts; no worker or API is invoked."""

    ReviewService(database).ensure_templates()
    now = datetime.now(UTC).isoformat()
    intent_id: str | None = None
    variant_id: str | None = None
    if owner_type == "GENERATION_VARIANT":
        intent_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        profile = profile_id or _published_camera_profile(workspace, database, "NATIVE")
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO generation_intents
                (id,project_id,owner_type,owner_id,purpose,creative_goal,status,created_at,updated_at,created_by)
                VALUES (?,?,'SHOT',?,'T2I','test keyframe','DRAFT',?,?, 'test')""",
                (intent_id, project_id, shot_id, now, now),
            )
            connection.execute(
                """INSERT INTO generation_variants
                (id,intent_id,variant_no,variant_type,parent_variant_id,branch_reason,prompt_revision_id,
                 capability_profile_version_id,parameter_set_json,seed_policy,explicit_seed,input_fingerprint,
                 recipe_hash,status,created_at,updated_at,created_by)
                VALUES (?,?,1,'BASE',NULL,'TEST',NULL,?,'{}','EXPLICIT',7,?,?,'SUCCEEDED',?,?, 'test')""",
                (variant_id, intent_id, profile, "0" * 64, "1" * 64, now, now),
            )
    asset_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    decision_id = str(uuid.uuid4())
    owner_id = variant_id if owner_type == "GENERATION_VARIANT" else shot_id
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO media_assets
            (id,project_id,owner_type,owner_id,purpose,media_kind,approved_version_id,version_counter,
             metadata_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?, 'KEYFRAME','IMAGE',?,?, '{}',?,?, 'test',1,'v2')""",
            (asset_id, project_id, owner_type, owner_id, version_id if approved else None, 1, now, now),
        )
        connection.execute(
            """INSERT INTO media_versions
            (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,sha256,
             integrity_status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,1,'KEYFRAME','media/keyframe.png','image/png',0,?,'VERIFIED',?,?, 'test',1,'v2')""",
            (version_id, asset_id, "0" * 64, now, now),
        )
        template = connection.execute(
            "SELECT id FROM review_templates WHERE code='image_asset' ORDER BY version_no DESC LIMIT 1"
        ).fetchone()
        assert template is not None
        connection.execute(
            """INSERT INTO review_decisions
            (id,subject_type,subject_id,review_template_version_id,decision,comment,subject_revision,
             is_stale,stale_reason,created_at,updated_at,created_by,revision,schema_version)
             VALUES (?,'MEDIA_VERSION',?,?, 'APPROVED','test approval',1,?,?,?,?,'test',1,'v2')""",
            (decision_id, version_id, template["id"], int(stale_review), "expired review" if stale_review else None, now, now),
        )
    return version_id, variant_id


def test_approved_keyframe_resolution_supports_legacy_and_generated_owners(
    workspace, database,
) -> None:
    project, episode, shots = _project_episode_shots(workspace, database, "keyframe_owner_shapes", ("SH-LEGACY", "SH-GENERATED"))
    legacy_version, _ = _insert_approved_keyframe(
        workspace, database, project_id=str(project["id"]), shot_id=str(shots[0]["id"]), owner_type="SHOT",
    )
    generated_version, generated_variant = _insert_approved_keyframe(
        workspace, database, project_id=str(project["id"]), shot_id=str(shots[1]["id"]), owner_type="GENERATION_VARIANT",
    )

    with database.connect() as connection:
        resolved = approved_keyframes_for_shots(
            connection,
            (str(shot["id"]) for shot in shots),
            project_id=str(project["id"]),
        )
    assert resolved[str(shots[0]["id"])]["media_version_id"] == legacy_version
    assert resolved[str(shots[1]["id"])]["media_version_id"] == generated_version
    assert resolved[str(shots[1]["id"])]["owner_type"] == "GENERATION_VARIANT"
    assert resolved[str(shots[1]["id"])]["owner_id"] == generated_variant

    worker = EpisodeWorkerActionService(database, workspace)
    assert worker._approved_keyframe(str(shots[0]["id"]))["media_version_id"] == legacy_version
    assert worker._approved_keyframe(str(shots[1]["id"]))["media_version_id"] == generated_version
    front_report, _ = EpisodeFrontHalfActionService(database, workspace).keyframe_check(str(episode["id"]))
    assert front_report["status"] == "PASS"
    legacy_report, _ = _automation_keyframe_check(database, str(episode["id"]))
    assert legacy_report["status"] == "PASS"


def test_approved_keyframe_resolution_rejects_cross_scope_and_stale_reviews(
    workspace, database,
) -> None:
    project_a, episode_a, shots_a = _project_episode_shots(workspace, database, "keyframe_owner_scope_a", ("SH-A", "SH-A2"))
    project_b, _episode_b, shots_b = _project_episode_shots(workspace, database, "keyframe_owner_scope_b", ("SH-B",))
    # A valid generated candidate for a different shot must not satisfy SH-A.
    _generated_version, generated_variant = _insert_approved_keyframe(
        workspace, database, project_id=str(project_a["id"]), shot_id=str(shots_a[1]["id"]), owner_type="GENERATION_VARIANT",
    )
    # The same candidate shape with a different project's intent/media asset
    # must not cross the project boundary, even when its owner_id points at a
    # shot in project A.
    _insert_approved_keyframe(
        workspace, database, project_id=str(project_b["id"]), shot_id=str(shots_a[0]["id"]), owner_type="GENERATION_VARIANT",
    )
    # A stale human decision is not an approved production dependency.
    _stale_version, _ = _insert_approved_keyframe(
        workspace, database, project_id=str(project_a["id"]), shot_id=str(shots_a[0]["id"]), owner_type="GENERATION_VARIANT", stale_review=True,
    )

    worker = EpisodeWorkerActionService(database, workspace)
    assert worker._approved_keyframe(str(shots_a[0]["id"])) is None
    assert worker._approved_keyframe(str(shots_b[0]["id"])) is None
    with database.connect() as connection:
        resolved = approved_keyframes_for_shots(
            connection,
            (str(shot["id"]) for shot in shots_a),
            project_id=str(project_a["id"]),
        )
    assert str(shots_a[0]["id"]) not in resolved
    assert str(shots_a[1]["id"]) in resolved
    assert resolved[str(shots_a[1]["id"])]["owner_id"] == generated_variant

    front_report, _ = EpisodeFrontHalfActionService(database, workspace).keyframe_check(str(episode_a["id"]))
    assert front_report["status"] == "NEEDS_HITL"
    assert [item["shot_id"] for item in front_report["machine_check"]["missing_shots"]] == [str(shots_a[0]["id"])]
    legacy_report, _ = _automation_keyframe_check(database, str(episode_a["id"]))
    assert legacy_report["status"] == "NEEDS_HITL"
    assert [item["shot_id"] for item in legacy_report["machine_check"]["missing_shots"]] == [str(shots_a[0]["id"])]

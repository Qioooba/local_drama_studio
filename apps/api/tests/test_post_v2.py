from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app
from tests.test_shot_studio_commands_v2 import _keyframe
from tests.test_video_review_annotations import _project_video


def _episode(workspace, database):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="post_v2", title="Post v2", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "SHOT-001", 4_000)
    return project, episode, shot


def test_post_v2_overview_and_review_targets_are_typed_bounded_and_path_safe(workspace, database) -> None:
    project, episode, shot = _episode(workspace, database)
    media_id = _keyframe(workspace, database, str(project["id"]), str(shot["id"]), "post-review")
    with TestClient(create_app(workspace)) as client:
        overview = client.get(f"/api/v2/episodes/{episode['id']}/post/overview")
        assert overview.status_code == 200, overview.text
        body = overview.json()
        assert set(body) == {"overview", "read_only", "request_shape"}
        assert body["overview"]["review"]["pending_count"] == 1
        assert body["overview"]["next_action"] == "OPEN_REVIEW"
        assert body["overview"]["edit"]["state"] == "EMPTY"

        targets = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "MEDIA_VERSION", "cursor": 0, "limit": 1},
        )
        assert targets.status_code == 200, targets.text
        page = targets.json()
        assert set(page) == {
            "items", "cursor", "limit", "total", "next_cursor", "target_kinds",
            "include_resolved", "read_only", "request_shape",
        }
        assert page["total"] == 1
        assert page["items"][0]["target_id"] == media_id
        assert page["items"][0]["target_kind"] == "MEDIA_VERSION"
        assert page["items"][0]["shot_id"] == shot["id"]
        assert "rel_path" not in page["items"][0]
        assert page["items"][0]["allowed_actions"] == ["SUBMIT_REVIEW_DECISION"]

        deep_link = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "MEDIA_VERSION", "target_id": media_id, "limit": 1},
        )
        assert deep_link.status_code == 200, deep_link.text
        assert deep_link.json()["total"] == 1
        assert deep_link.json()["items"][0]["target_id"] == media_id
        missing = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "MEDIA_VERSION", "target_id": "missing-media", "limit": 1},
        )
        assert missing.status_code == 200, missing.text
        assert missing.json()["items"] == []
        assert missing.json()["total"] == 0

        paths = client.get("/api/v1/openapi.json").json()["paths"]
        assert paths["/api/v2/episodes/{episode_id}/post/overview"]["get"]["operationId"] == "getEpisodePostOverviewV2"
        assert paths["/api/v2/episodes/{episode_id}/review-targets"]["get"]["operationId"] == "listEpisodeReviewTargetsV2"
        retired = {
            ("/api/v1/subjects/{subject_type}/{subject_id}/review-context", "get"),
            ("/api/v1/subjects/{subject_type}/{subject_id}/reviews", "post"),
            ("/api/v1/reviews/{review_id}:void", "post"),
            ("/api/v1/reviews/batch:preflight", "post"),
            ("/api/v1/reviews/batch:commit", "post"),
            ("/api/v1/reviews/formal-selection:preflight", "post"),
            ("/api/v1/reviews/formal-selection:commit", "post"),
            ("/api/v1/media-versions/{media_version_id}:select", "post"),
            ("/api/v1/media-versions/{media_version_id}/annotations", "get"),
            ("/api/v1/media-versions/{media_version_id}/annotations", "post"),
        }
        assert {(path, method) for path, method in retired if method in paths.get(path, {})} == set()


def test_post_v2_rejects_unknown_episode_and_target_kind(workspace, database) -> None:
    _project, episode, _shot = _episode(workspace, database)
    with TestClient(create_app(workspace)) as client:
        assert client.get("/api/v2/episodes/missing/post/overview").status_code == 404
        invalid = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "SHOT"},
        )
        assert invalid.status_code == 422


def test_review_targets_prioritize_newest_material_and_keep_older_targets_paginable(workspace, database) -> None:
    project, episode, shot = _episode(workspace, database)
    oldest = _keyframe(workspace, database, str(project["id"]), str(shot["id"]), "oldest")
    middle = _keyframe(workspace, database, str(project["id"]), str(shot["id"]), "middle")
    newest = _keyframe(workspace, database, str(project["id"]), str(shot["id"]), "newest")
    with database.connect() as connection:
        for media_id, created_at in (
            (oldest, "2026-01-01T00:00:01+00:00"),
            (middle, "2026-01-01T00:00:02+00:00"),
            (newest, "2026-01-01T00:00:03+00:00"),
        ):
            connection.execute("UPDATE media_versions SET created_at=? WHERE id=?", (created_at, media_id))

    with TestClient(create_app(workspace)) as client:
        first_page = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "MEDIA_VERSION", "cursor": 0, "limit": 2},
        )
        assert first_page.status_code == 200, first_page.text
        assert first_page.json()["total"] == 3
        assert [item["target_id"] for item in first_page.json()["items"]] == [newest, middle]
        assert first_page.json()["next_cursor"] == 2

        second_page = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "MEDIA_VERSION", "cursor": 2, "limit": 2},
        )
        assert second_page.status_code == 200, second_page.text
        assert [item["target_id"] for item in second_page.json()["items"]] == [oldest]
        assert second_page.json()["next_cursor"] is None

        deep_link = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "MEDIA_VERSION", "target_id": oldest, "limit": 1},
        )
        assert deep_link.status_code == 200, deep_link.text
        assert deep_link.json()["total"] == 1
        assert deep_link.json()["items"][0]["target_id"] == oldest


def test_review_decision_v2_is_revision_safe_idempotent_audited_and_revocable(workspace, database) -> None:
    project, episode, shot = _episode(workspace, database)
    media_id = _keyframe(workspace, database, str(project["id"]), str(shot["id"]), "review-command")
    with TestClient(create_app(workspace)) as client:
        target = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "MEDIA_VERSION"},
        ).json()["items"][0]
        with database.connect() as connection:
            items = connection.execute(
                "SELECT items_json FROM review_templates WHERE id=?", (target["template_version_id"],)
            ).fetchone()[0]
        import json
        checks = [{"item_id": item["id"], "result": "PASS"} for item in json.loads(items)]
        payload = {
            "target_kind": "MEDIA_VERSION",
            "target_id": media_id,
            "template_version_id": target["template_version_id"],
            "expected_revision": target["subject_revision"],
            "decision": "APPROVED",
            "checks": checks,
            "comment": "画面与连续性通过",
            "idempotency_key": "review-v2-create",
        }
        created = client.post("/api/v2/review-decisions", json=payload)
        assert created.status_code == 201, created.text
        decision = created.json()["decision"]
        assert decision["decision"] == "APPROVED"
        assert decision["idempotent_replay"] is False

        replay = client.post("/api/v2/review-decisions", json=payload)
        assert replay.status_code == 201
        assert replay.json()["decision"] == {**decision, "idempotent_replay": True}
        mismatch = client.post("/api/v2/review-decisions", json={**payload, "comment": "不同内容"})
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "REVIEW_IDEMPOTENCY_PAYLOAD_MISMATCH"

        unresolved = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "MEDIA_VERSION"},
        ).json()
        assert unresolved["total"] == 0
        resolved = client.get(
            f"/api/v2/episodes/{episode['id']}/review-targets",
            params={"target_kind": "MEDIA_VERSION", "include_resolved": True},
        ).json()["items"][0]
        assert resolved["latest_decision_id"] == decision["id"]

        revoked = client.post(
            f"/api/v2/review-decisions/{decision['id']}:revoke",
            json={"expected_revision": 1, "reason": "发现角色手部问题", "idempotency_key": "review-v2-revoke"},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["decision"]["decision"] == "VOIDED"
        assert revoked.json()["decision"]["revision"] == 2
        with database.connect() as connection:
            asset = connection.execute(
                """SELECT approved_version_id FROM media_assets ma JOIN media_versions mv
                ON mv.media_asset_id=ma.id WHERE mv.id=?""", (media_id,)
            ).fetchone()
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE subject_id=?", (decision["id"],)
            ).fetchone()[0]
            event_count = connection.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE subject_id=? AND type='ReviewDecisionChanged'",
                (decision["id"],),
            ).fetchone()[0]
        assert asset["approved_version_id"] is None
        assert audit_count == 2
        assert event_count == 2


def test_review_annotation_v2_is_bounded_revision_safe_idempotent_and_audited(workspace, database) -> None:
    project, media = _project_video(workspace, database, "post_annotation_v2")
    media_id = str(media["media_version_id"])
    with database.connect() as connection:
        revision = int(connection.execute(
            """SELECT ma.revision FROM media_assets ma JOIN media_versions mv
            ON mv.media_asset_id=ma.id WHERE mv.id=?""", (media_id,)
        ).fetchone()[0])
    payload = {
        "expected_revision": revision,
        "timecode_ms": 400,
        "category": "FLICKER",
        "comment": "画面亮度在此处跳变",
        "idempotency_key": "annotation-v2-create",
    }
    path = f"/api/v2/review-targets/MEDIA_VERSION/{media_id}/annotations"
    with TestClient(create_app(workspace)) as client:
        created = client.post(path, json=payload)
        assert created.status_code == 201, created.text
        annotation = created.json()["annotation"]
        assert annotation["target_id"] == media_id
        assert annotation["idempotent_replay"] is False

        replay = client.post(path, json=payload)
        assert replay.status_code == 201
        assert replay.json()["annotation"] == {**annotation, "idempotent_replay": True}
        mismatch = client.post(path, json={**payload, "comment": "不同内容"})
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "REVIEW_IDEMPOTENCY_PAYLOAD_MISMATCH"

        page = client.get(path, params={"cursor": 0, "limit": 1})
        assert page.status_code == 200, page.text
        assert set(page.json()) == {"items", "cursor", "limit", "total", "next_cursor", "read_only", "request_shape"}
        assert page.json()["total"] == 1
        assert page.json()["items"][0]["comment"] == "画面亮度在此处跳变"

        stale = client.post(path, json={**payload, "expected_revision": revision + 1, "idempotency_key": "annotation-v2-stale"})
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVIEW_REVISION_CONFLICT"
        paths = client.get("/api/v1/openapi.json").json()["paths"]
        assert paths["/api/v2/review-targets/{target_kind}/{target_id}/annotations"]["post"]["operationId"] == "createReviewAnnotationV2"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='REVIEW_ANNOTATION_CREATED' AND subject_id=?", (media_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE type='ReviewAnnotationChanged' AND subject_id=?", (media_id,)
        ).fetchone()[0] == 1

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.domain.director_intent import normalize_director_intent_v3
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import REQUIRED_SHOT_FIELDS
from local_drama.main import create_app
from tests.test_director_fields import _published_camera_profile


def _project_and_shot(workspace: Settings, database, code: str) -> tuple[dict, dict, dict, ProjectService]:
    service = ProjectService(database, workspace.projects_root)
    project = service.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = service.list_seasons(str(project["id"]))[0]
    episode = service.list_episodes(str(season["id"]))[0]
    shot = service.create_shot(str(episode["id"]), "SHOT-001", 4_000)
    return project, episode, shot, service


@pytest.mark.parametrize("legacy_version", (None, "director-intent.v1", "director-intent.v2"))
def test_legacy_director_intent_normalizer_preserves_only_supplied_facts(legacy_version: str | None) -> None:
    legacy: dict[str, object] = {
        "composition": "centered medium shot",
        "action": "walks to the window",
        "emotion": "uncertain",
        "screen_direction": "camera left",
        "source_ranges": [{"start": 10, "end": 20}],
    }
    if legacy_version is not None:
        legacy["schema_version"] = legacy_version
    before = json.loads(json.dumps(legacy))

    normalized = normalize_director_intent_v3(legacy)

    assert legacy == before
    assert normalized["schema_version"] == "director-intent.v3"
    assert normalized["composition"] == {
        "preset": "centered medium shot",
        "framing": None,
        "subject_position": None,
        "headroom": None,
        "lead_room": None,
        "screen_direction": "camera left",
        "axis_rule": None,
        "depth_plan": None,
    }
    assert normalized["subject_action"] == "walks to the window"
    assert normalized["performance"]["emotion"] == "uncertain"
    assert normalized["performance"]["intensity"] is None
    assert normalized["shot_type"] is None
    assert normalized["camera_plan"] is None
    assert normalized["target_duration_ms"] is None
    assert normalized["source_ranges"] == [{"start": 10, "end": 20}]


def _v3_payload() -> dict[str, object]:
    return {
        "schema_version": "director-intent.v3",
        "shot_type": "CLOSEUP",
        "composition": {
            "preset": "close portrait",
            "framing": "tight",
            "subject_position": "center",
            "headroom": "low",
            "lead_room": "right",
            "screen_direction": "camera left",
            "axis_rule": "hold",
            "depth_plan": "shallow",
        },
        "subject_action": "turns toward camera",
        "performance": {
            "emotion": "resolved",
            "intensity": 0.75,
            "body_action": "still",
            "facial_action": "small smile",
            "eye_line": "lens",
            "blocking_summary": "holds mark",
        },
        "camera_plan": None,
        "target_duration_ms": 4_000,
        "dialogue": [],
        "environment": "interior night",
        "continuity": "same wardrobe",
        "transition_plan": {"kind": "CUT"},
        "sound_plan": {"room_tone": True},
        "creative_intent": "land the decision",
        "staging": {"mark": "A"},
        "staging_3d": {"axis": "x"},
        "source_ranges": [{"start": 10, "end": 20}],
    }


def test_v3_typed_api_roundtrips_canonical_intent(workspace, database) -> None:
    _project, _episode, shot, _service = _project_and_shot(workspace, database, "director_v3_roundtrip")
    payload = _v3_payload()

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/projects/shots/{shot['id']}/revisions",
            json={"fields": payload, "freeze": True, "expected_revision_no": 1},
        )

    assert response.status_code == 201, response.text
    result = response.json()["shot_revision"]
    assert result["revision_no"] == 2
    assert result["is_frozen"] is True
    assert result["fields"] == payload
    with database.connect() as connection:
        stored = connection.execute("SELECT fields_json FROM shot_revisions WHERE id=?", (result["id"],)).fetchone()
    assert json.loads(stored["fields_json"]) == payload


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value["performance"].update(intensity=1.5),
        lambda value: value.update(target_duration_ms=True),
        lambda value: value["composition"].update(unknown_layout_fact="invented"),
    ),
)
def test_explicit_v3_api_rejects_invalid_typed_facts(workspace, database, mutation) -> None:
    _project, _episode, shot, _service = _project_and_shot(workspace, database, f"director_v3_422_{abs(hash(str(mutation))) % 100000}")
    payload = _v3_payload()
    mutation(payload)

    with TestClient(create_app(workspace)) as client:
        response = client.post(f"/api/v1/projects/shots/{shot['id']}/revisions", json={"fields": payload})

    assert response.status_code == 422
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM shot_revisions WHERE shot_id=?", (shot["id"],)).fetchone()[0] == 1


def test_revision_audit_outbox_and_conflict_are_transactional(workspace, database) -> None:
    project, _episode, shot, service = _project_and_shot(workspace, database, "director_v3_revision_events")
    created = service.create_shot_revision(
        str(shot["id"]), {"subject_action": "walk"}, freeze=True, expected_revision_no=1, actor="director-test",
    )
    with database.connect() as connection:
        audit = connection.execute(
            "SELECT actor,metadata_redacted_json FROM audit_events WHERE action='SHOT_REVISION_CREATED' AND subject_id=?",
            (shot["id"],),
        ).fetchone()
        outbox = connection.execute(
            "SELECT project_id,subject_type,subject_id,payload_json FROM outbox_events WHERE type='SHOT_REVISION_CREATED' AND subject_id=?",
            (shot["id"],),
        ).fetchone()
        event_counts = (
            connection.execute("SELECT COUNT(*) FROM audit_events WHERE subject_id=?", (shot["id"],)).fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM outbox_events WHERE subject_id=?", (shot["id"],)).fetchone()[0],
        )
    assert audit["actor"] == "director-test"
    assert json.loads(audit["metadata_redacted_json"])["revision_id"] == created["id"]
    assert outbox["project_id"] == project["id"] and outbox["subject_type"] == "SHOT"
    assert json.loads(outbox["payload_json"])["schema_version"] == "director-intent.v3"

    with TestClient(create_app(workspace)) as client:
        conflict = client.post(
            f"/api/v1/projects/shots/{shot['id']}/revisions",
            json={"fields": _v3_payload(), "expected_revision_no": 1},
        )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "REVISION_CONFLICT"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM shot_revisions WHERE shot_id=?", (shot["id"],)).fetchone()[0] == 2
        assert (
            connection.execute("SELECT COUNT(*) FROM audit_events WHERE subject_id=?", (shot["id"],)).fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM outbox_events WHERE subject_id=?", (shot["id"],)).fetchone()[0],
        ) == event_counts


def test_ready_audit_outbox_and_invalid_transition_emit_nothing(workspace, database) -> None:
    project, _episode, shot, service = _project_and_shot(workspace, database, "director_v3_ready_events")
    complete = {
        field: "" if field in {"dialogue", "environment"} else 4_000 if field == "target_duration_ms" else field
        for field in REQUIRED_SHOT_FIELDS
    }
    profile_id = _published_camera_profile(workspace, database, "NATIVE")
    complete["camera_plan"] = {
        "mode": "NATIVE",
        "shot_type": "CLOSEUP",
        "movement": "PUSH_IN",
        "prompt_text": "",
        "direction": "FORWARD",
        "intensity": 0.5,
        "curve": "EASE_IN_OUT",
        "profile_version_id": profile_id,
    }
    revision = service.create_shot_revision(str(shot["id"]), complete, freeze=True, expected_revision_no=1)
    ready = service.mark_shot_production_ready(str(shot["id"]), actor="director-ready")
    assert ready["status"] == "READY"
    with database.connect() as connection:
        audit = connection.execute(
            "SELECT actor,metadata_redacted_json FROM audit_events WHERE action='SHOT_MARKED_PRODUCTION_READY' AND subject_id=?",
            (shot["id"],),
        ).fetchone()
        outbox = connection.execute(
            "SELECT project_id,payload_json FROM outbox_events WHERE type='SHOT_PRODUCTION_READY' AND subject_id=?",
            (shot["id"],),
        ).fetchone()
        event_counts = (
            connection.execute("SELECT COUNT(*) FROM audit_events WHERE subject_id=?", (shot["id"],)).fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM outbox_events WHERE subject_id=?", (shot["id"],)).fetchone()[0],
        )
    assert audit["actor"] == "director-ready"
    assert json.loads(audit["metadata_redacted_json"]) == {
        "from_status": "DIRECTED",
        "revision_id": revision["id"],
        "shot_id": shot["id"],
        "to_status": "READY",
    }
    assert outbox["project_id"] == project["id"]
    assert json.loads(outbox["payload_json"])["to_status"] == "READY"

    with pytest.raises(DomainRuleError) as conflict:
        service.mark_shot_production_ready(str(shot["id"]), actor="director-ready")
    assert conflict.value.code == "INVALID_STATE_TRANSITION"
    with database.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM audit_events WHERE subject_id=?", (shot["id"],)).fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM outbox_events WHERE subject_id=?", (shot["id"],)).fetchone()[0],
        ) == event_counts

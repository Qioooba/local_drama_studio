from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import local_drama.application.episode_shot_ready as ready_module
from local_drama.application.episode_replan import EpisodeReplanService
from local_drama.application.episode_shot_ready import EpisodeShotReadyService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app
from tests.test_episode_replan import _context, _draft_row
from tests.test_breakdown_apply import _persisted_draft
from local_drama.application.breakdown_apply import BreakdownApplyService


def _profile(profile_id: str) -> dict[str, object]:
    return {
        "id": profile_id,
        "status": "PUBLISHED",
        "workflow_version_id": f"workflow-{profile_id}",
        "parameter_schema": {
            "capabilities": {
                "camera": {"support": "PROMPT_FALLBACK", "prompt_fallback": True}
            }
        },
    }


def _materialize_directed_plan(workspace, database, *, shot_count: int = 11):
    project, episode, imported = _context(workspace, database)
    projects = ProjectService(database, workspace.projects_root)
    existing = min(8, shot_count)
    for index in range(1, existing + 1):
        projects.create_shot(str(episode["id"]), f"S{index:03d}", 7_500, "STANDARD")
    _draft_row(
        workspace,
        database,
        project,
        episode,
        imported,
        target_seconds=120,
        shot_count=shot_count,
        stamp=1,
        with_dialogue=True,
    )
    replan = EpisodeReplanService(database, workspace)
    plan = replan.plan(str(episode["id"]))
    replan.apply(
        str(episode["id"]),
        expected_episode_revision=int(plan["expected_episode_revision"]),
        expected_plan_hash=str(plan["plan_hash"]),
        idempotency_key=f"apply-{uuid.uuid4()}",
    )
    with database.connect() as connection:
        revision = int(connection.execute(
            "SELECT revision FROM episodes WHERE id=?", (str(episode["id"]),)
        ).fetchone()[0])
    return project, episode, revision


@pytest.mark.parametrize("with_invalid_draft", [False, True])
def test_confirm_fresh_breakdown_drafts_validates_every_shot_atomically(workspace, database, monkeypatch, with_invalid_draft) -> None:
    _, episode, draft_id = _persisted_draft(workspace, database)
    episode_id = str(episode["id"])
    BreakdownApplyService(database, workspace).apply_draft(draft_id, episode_id)
    if with_invalid_draft:
        ProjectService(database, workspace.projects_root).create_shot(episode_id, "UNFINISHED", 3000, "STANDARD")
    monkeypatch.setattr(ready_module, "effective_video_profile", lambda _connection, _project_id: _profile("video-own-project"))
    with database.connect() as connection:
        revision = int(connection.execute("SELECT revision FROM episodes WHERE id=?", (episode_id,)).fetchone()[0])
        before = [tuple(row) for row in connection.execute("SELECT id,status,current_revision_id,revision FROM shots WHERE episode_id=? ORDER BY id", (episode_id,))]
    if with_invalid_draft:
        with pytest.raises(DomainRuleError) as captured:
            EpisodeShotReadyService(database).confirm(episode_id, expected_episode_revision=revision, idempotency_key="fresh-invalid")
        assert captured.value.code == "EPISODE_SHOTS_READY_VALIDATION_FAILED"
        with database.connect() as connection:
            assert [tuple(row) for row in connection.execute("SELECT id,status,current_revision_id,revision FROM shots WHERE episode_id=? ORDER BY id", (episode_id,))] == before
    else:
        result = EpisodeShotReadyService(database).confirm(episode_id, expected_episode_revision=revision, idempotency_key="fresh-valid")
        assert result["ready_shot_count"] == 4
        with database.connect() as connection:
            assert {row[0] for row in connection.execute("SELECT status FROM shots WHERE episode_id=?", (episode_id,))} == {"READY"}


def test_confirm_eleven_shots_ready_via_route_is_idempotent(workspace, database, monkeypatch) -> None:
    project, episode, revision = _materialize_directed_plan(workspace, database)
    monkeypatch.setattr(ready_module, "effective_video_profile", lambda _connection, project_id: _profile(f"video-{project_id}"))
    payload = {"expected_episode_revision": revision, "idempotency_key": "ready-eleven"}

    with TestClient(create_app(workspace)) as client:
        first = client.post(
            f"/api/v2/episodes/{episode['id']}/production:shots:ready", json=payload
        )
        replay = client.post(
            f"/api/v2/episodes/{episode['id']}/production:shots:ready", json=payload
        )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()["ready"]["ready_shot_count"] == 11
    assert first.json()["ready"]["profile_version_id"] == f"video-{project['id']}"
    assert replay.json()["ready"]["idempotent_replay"] is True
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT s.status,sr.fields_json FROM shots s
            JOIN shot_revisions sr ON sr.id=s.current_revision_id
            WHERE s.episode_id=? AND s.archived_at IS NULL""",
            (str(episode["id"]),),
        ).fetchall()
    assert len(rows) == 11
    assert {str(row["status"]) for row in rows} == {"READY"}
    assert all(f'"profile_version_id":"video-{project["id"]}"' in str(row["fields_json"]) for row in rows)


def test_confirm_shots_requires_project_video_profile(workspace, database, monkeypatch) -> None:
    _project, episode, revision = _materialize_directed_plan(workspace, database, shot_count=2)
    monkeypatch.setattr(ready_module, "effective_video_profile", lambda _connection, _project_id: None)

    with pytest.raises(DomainRuleError) as captured:
        EpisodeShotReadyService(database).confirm(
            str(episode["id"]),
            expected_episode_revision=revision,
            idempotency_key="missing-profile",
        )

    assert captured.value.code == "VIDEO_PROFILE_REQUIRED"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shots WHERE episode_id=? AND status='READY'",
            (str(episode["id"]),),
        ).fetchone()[0] == 0


def test_confirm_shots_uses_each_projects_own_profile(workspace, database, monkeypatch) -> None:
    first_project, first_episode, first_revision = _materialize_directed_plan(workspace, database, shot_count=2)
    second_project, second_episode, second_revision = _materialize_directed_plan(workspace, database, shot_count=2)
    profile_by_project = {
        str(first_project["id"]): _profile("video-first"),
        str(second_project["id"]): _profile("video-second"),
    }
    monkeypatch.setattr(ready_module, "effective_video_profile", lambda _connection, project_id: profile_by_project[project_id])

    first = EpisodeShotReadyService(database).confirm(
        str(first_episode["id"]), expected_episode_revision=first_revision, idempotency_key="first"
    )
    second = EpisodeShotReadyService(database).confirm(
        str(second_episode["id"]), expected_episode_revision=second_revision, idempotency_key="second"
    )

    assert first["profile_version_id"] == "video-first"
    assert second["profile_version_id"] == "video-second"

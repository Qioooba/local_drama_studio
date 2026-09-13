from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from local_drama.application.documents import DocumentImportService
from local_drama.application.episode_front_half_actions import EpisodeFrontHalfActionService
from local_drama.application.episode_preparation import EpisodePreparationService
from local_drama.application.episode_replan import EpisodeReplanService
from local_drama.application.episode_source_binding import (
    resolve_episode_source_binding,
    validate_episode_source_binding,
)
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def _project_episode(workspace, database, code: str):
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
    return project, projects.list_episodes(str(season["id"]))[0]


def _committed_source(workspace, database, project_id: str, name: str, text: str):
    path = workspace.work_root / name
    path.write_text(text, encoding="utf-8")
    documents = DocumentImportService(database, workspace)
    imported = documents.import_document(project_id, path)
    documents.commit(
        str(imported["import_session_id"]),
        str(imported["preview_hash"]),
        source_paragraph_start=1,
        source_paragraph_end=1,
    )
    return imported


def _ready_draft(database, project_id: str, episode_id: str, source: dict, *, start: int = 1) -> str:
    draft_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO script_breakdown_drafts
            (id,project_id,source_document_version_id,import_session_id,draft_json,confidence_json,
             status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,'DRAFT_READY',?,?,'test',1,'v2')""",
            (
                draft_id,
                project_id,
                source["source_document_version_id"],
                source["import_session_id"],
                json.dumps({"scenes": []}),
                json.dumps(
                    {
                        "target_episode_id": episode_id,
                        "target_duration_seconds": 60,
                        "source_paragraph_start": start,
                        "source_paragraph_end": start,
                    }
                ),
                now,
                now,
            ),
        )
    return draft_id


def test_episode_keeps_explicit_source_a_after_project_imports_source_b(
    workspace, database, monkeypatch,
) -> None:
    project, episode = _project_episode(workspace, database, "episode_source_a_b")
    source_a = _committed_source(
        workspace, database, str(project["id"]), "source-a.md", "阿宁走进旧车站。"
    )
    with database.connect() as connection:
        source_a_hash = str(
            connection.execute(
                "SELECT text_sha256 FROM source_document_versions WHERE id=?",
                (source_a["source_document_version_id"],),
            ).fetchone()[0]
        )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE episodes SET source_range_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        "start_paragraph": 1,
                        "end_paragraph": 1,
                        "source_document_version_id": source_a["source_document_version_id"],
                        "import_session_id": source_a["import_session_id"],
                        "text_sha256": source_a_hash,
                    }
                ),
                episode["id"],
            ),
        )
    source_b = _committed_source(
        workspace, database, str(project["id"]), "source-b.md", "伯宁登上远洋货轮。"
    )

    _ready_draft(database, str(project["id"]), str(episode["id"]), source_b)

    context = EpisodePreparationService(database, workspace)._context(str(episode["id"]))
    assert context["import_session_id"] == source_a["import_session_id"]
    assert context["source_binding"]["source_document_version_id"] == source_a[
        "source_document_version_id"
    ]
    assert context["source_binding"]["source_document_version_id"] != source_b[
        "source_document_version_id"
    ]
    assert context["ready_draft_id"] is None
    breakdown, _ = EpisodeFrontHalfActionService(database, workspace).script_breakdown(
        str(episode["id"])
    )
    assert breakdown["machine_check"]["code"] == "BREAKDOWN_DRAFT_REQUIRED"

    captured: dict[str, str] = {}
    monkeypatch.setattr(
        EpisodeReplanService,
        "_project_context",
        lambda *_args: {"profile_version_id": "profile-story"},
    )

    def enqueue(_self, import_session_id, _profile_version_id, _key, **_kwargs):
        captured["import_session_id"] = import_session_id
        return {"id": "job-a", "idempotent_replay": False}

    monkeypatch.setattr(
        "local_drama.application.episode_replan.LocalLLMService.enqueue_breakdown", enqueue
    )
    result = EpisodeReplanService(database, workspace).request(
        str(episode["id"]), idempotency_key="replan-source-a"
    )
    assert captured["import_session_id"] == source_a["import_session_id"]
    assert result["source_binding"]["source_document_version_id"] == source_a[
        "source_document_version_id"
    ]


def test_legacy_episode_with_multiple_committed_sources_is_ambiguous(
    workspace, database,
) -> None:
    project, episode = _project_episode(workspace, database, "episode_source_ambiguous")
    _committed_source(workspace, database, str(project["id"]), "legacy-a.md", "甲本人物进入房间。")
    _committed_source(workspace, database, str(project["id"]), "legacy-b.md", "乙本人物离开码头。")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE episodes SET source_range_json=? WHERE id=?",
            (json.dumps({"start_paragraph": 1, "end_paragraph": 1}), episode["id"]),
        )

    with database.connect() as connection, pytest.raises(DomainRuleError) as error:
        resolve_episode_source_binding(connection, str(episode["id"]))

    assert error.value.code == "EPISODE_SOURCE_BINDING_AMBIGUOUS"


def test_bound_source_content_change_blocks_preparation(workspace, database) -> None:
    project, episode = _project_episode(workspace, database, "episode_source_changed")
    source = _committed_source(
        workspace, database, str(project["id"]), "source-changed.md", "原始不可变正文。"
    )
    with database.connect() as connection:
        binding = resolve_episode_source_binding(connection, str(episode["id"]))
    extracted = workspace.resolve_project_root(binding["project_root_rel"]) / binding[
        "extracted_text_rel"
    ]
    extracted.write_text("遭到改写的正文。", encoding="utf-8")

    with pytest.raises(DomainRuleError) as error:
        validate_episode_source_binding(workspace, binding)
    assert error.value.code == "EPISODE_SOURCE_TEXT_CHANGED"
    with pytest.raises(DomainRuleError) as preparation_error:
        EpisodePreparationService(database, workspace)._context(str(episode["id"]))
    assert preparation_error.value.code == "EPISODE_SOURCE_TEXT_CHANGED"
    assert source["source_document_version_id"] == binding["source_document_version_id"]


def test_one_manual_shot_does_not_claim_the_episode_plan_is_complete(
    workspace, database, monkeypatch,
) -> None:
    service = EpisodePreparationService(database, workspace)
    monkeypatch.setattr(
        service,
        "_context",
        lambda _episode_id: {
            "shot_count": 1,
            "applied_plan_count": 0,
            "ready_draft_id": None,
            "source_range_json": json.dumps({"start_paragraph": 1, "end_paragraph": 3}),
            "import_session_id": "import-1",
            "execution": {"profile_version_id": "profile-1"},
        },
    )
    captured: dict[str, object] = {}

    def enqueue(_self, import_session_id, profile_version_id, key, **kwargs):
        captured.update(
            import_session_id=import_session_id,
            profile_version_id=profile_version_id,
            key=key,
            kwargs=kwargs,
        )
        return {"id": "planning-job-1"}

    monkeypatch.setattr(
        "local_drama.application.episode_preparation.LocalLLMService.enqueue_breakdown", enqueue
    )
    result = service.prepare("episode-1", idempotency_key="prepare-incomplete-manual-plan")

    assert result == {
        "status": "QUEUED",
        "episode_id": "episode-1",
        "job_id": "planning-job-1",
        "shot_count": 0,
    }
    assert captured["kwargs"] == {
        "target_episode_id": "episode-1",
        "source_paragraph_start": 1,
        "source_paragraph_end": 3,
        "automatic_apply": True,
    }


def test_explicit_cross_project_and_missing_source_versions_are_rejected(
    workspace, database,
) -> None:
    project_a, episode_a = _project_episode(workspace, database, "episode_source_project_a")
    project_b, _episode_b = _project_episode(workspace, database, "episode_source_project_b")
    source_b = _committed_source(
        workspace, database, str(project_b["id"]), "project-b-source.md", "乙项目原稿。"
    )
    base_scope = {
        "start_paragraph": 1,
        "end_paragraph": 1,
        "import_session_id": source_b["import_session_id"],
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE episodes SET source_range_json=? WHERE id=?",
            (
                json.dumps(
                    {
                        **base_scope,
                        "source_document_version_id": source_b["source_document_version_id"],
                    }
                ),
                episode_a["id"],
            ),
        )
    with database.connect() as connection, pytest.raises(DomainRuleError) as cross_project:
        resolve_episode_source_binding(connection, str(episode_a["id"]))
    assert cross_project.value.code == "EPISODE_SOURCE_VERSION_NOT_FOUND"

    with database.transaction() as connection:
        connection.execute(
            "UPDATE episodes SET source_range_json=? WHERE id=?",
            (
                json.dumps({**base_scope, "source_document_version_id": str(uuid.uuid4())}),
                episode_a["id"],
            ),
        )
    with database.connect() as connection, pytest.raises(DomainRuleError) as missing:
        resolve_episode_source_binding(connection, str(episode_a["id"]))
    assert missing.value.code == "EPISODE_SOURCE_VERSION_NOT_FOUND"
    assert project_a["id"] != project_b["id"]

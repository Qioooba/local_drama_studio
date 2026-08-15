from __future__ import annotations

import json

from fastapi.testclient import TestClient

from local_drama.application.audit import AuditService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _event(database, *, actor: str, action: str, subject_type: str, subject_id: str, metadata: dict[str, object], occurred_at: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json, occurred_at) VALUES (?, 'operator', ?, ?, ?, ?, ?, ?)",
            (actor, action, subject_type, subject_id, "audit test token=do-not-leak", json.dumps(metadata), occurred_at),
        )


def test_audit_history_has_stable_cursor_project_scope_and_redaction(workspace, database) -> None:
    first = ProjectService(database, workspace.projects_root).create_project(
        code="audit_one", title="Audit one", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    second = ProjectService(database, workspace.projects_root).create_project(
        code="audit_two", title="Audit two", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    first_id = str(first["id"])
    second_id = str(second["id"])
    _event(
        database,
        actor="alice",
        action="AUDIT_FILTER_ME",
        subject_type="project",
        subject_id=first_id,
        metadata={"token": "secret-token", "machine_path_ref": "C:/private/model.safetensors", "nested": {"password": "secret"}},
        occurred_at="2026-08-15 10:00:00",
    )
    _event(database, actor="bob", action="AUDIT_OTHER", subject_type="project", subject_id=second_id, metadata={"ok": True}, occurred_at="2026-08-15 11:00:00")
    service = AuditService(database)
    page = service.list_page(project_id=first_id, action="AUDIT_FILTER_ME", actor="alice", limit=1)
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["project_id"] == first_id
    assert item["metadata"]["token"] == "[REDACTED]"
    assert item["metadata"]["machine_path_ref"] == "[LOCAL_PATH_REDACTED]"
    assert item["metadata"]["nested"]["password"] == "[REDACTED]"
    assert "do-not-leak" not in item["summary"]
    assert item["metadata_redacted"] is True
    assert item["network_contacted"] is False
    assert service.list_page(project_id=second_id, action="AUDIT_FILTER_ME")["items"] == []

    # Insert a second matching event and prove the next cursor excludes the
    # already-returned event even when records are added between page reads.
    _event(database, actor="alice", action="AUDIT_FILTER_ME", subject_type="project", subject_id=first_id, metadata={}, occurred_at="2026-08-15 09:00:00")
    first_page = service.list_page(project_id=first_id, action="AUDIT_FILTER_ME", limit=1)
    assert first_page["next_cursor"] is not None
    second_page = service.list_page(project_id=first_id, action="AUDIT_FILTER_ME", cursor=int(first_page["next_cursor"]), limit=10)
    assert all(int(row["event_id"]) < int(first_page["items"][0]["event_id"]) for row in second_page["items"])


def test_audit_history_route_supports_time_and_subject_filters(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="audit_route", title="Audit route", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    _event(database, actor="route-user", action="NETWORK_POLICY_REJECTED", subject_type="project", subject_id=project_id, metadata={"project_id": project_id}, occurred_at="2026-08-15 12:00:00")
    with TestClient(create_app(workspace)) as client:
        response = client.get(
            "/api/v1/audit-events",
            params={"project_id": project_id, "action": "NETWORK_POLICY_REJECTED", "subject_type": "project", "occurred_after": "2026-08-15T11:00:00Z", "occurred_before": "2026-08-15T13:00:00Z", "limit": 20},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"]
    assert payload["items"][0]["action"] == "NETWORK_POLICY_REJECTED"
    assert payload["items"][0]["project_id"] == project_id
    assert payload["local_only"] is True
    assert payload["mutated"] is False


def test_audit_history_export_proof_is_bounded_and_deterministic(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="audit_proof", title="Audit proof", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1,
        target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    for index in range(3):
        _event(
            database,
            actor="proof-user",
            action="PROOF_EVENT",
            subject_type="project",
            subject_id=project_id,
            metadata={"index": index, "token": "must-not-appear"},
            occurred_at=f"2026-08-15 12:0{index}:00",
        )
    service = AuditService(database)
    proof = service.export_proof(project_id=project_id, action="PROOF_EVENT", max_events=2)
    assert proof["algorithm"] == "SHA-256-chain-v1"
    assert proof["scope"] == "filtered-redacted-audit-export"
    assert proof["event_count"] == 2
    assert proof["truncated"] is True
    assert proof["first_event_id"] < proof["last_event_id"]
    assert len(str(proof["chain_sha256"])) == 64
    assert proof["metadata_redacted"] is True
    assert proof["network_contacted"] is False
    assert proof == service.export_proof(project_id=project_id, action="PROOF_EVENT", max_events=2)

    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/audit-events/proof", params={"project_id": project_id, "action": "PROOF_EVENT", "max_events": 2})
    assert response.status_code == 200
    assert response.json()["chain_sha256"] == proof["chain_sha256"]
    assert "must-not-appear" not in response.text

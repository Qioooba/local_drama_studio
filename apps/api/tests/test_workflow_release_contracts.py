from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


class _OfflineComfy:
    base_url = "http://127.0.0.1:8188"

    def object_info(self) -> dict[str, object]:
        return {"LoadImage": {}, "SaveImage": {}}


def _workflow() -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "1": {"class_type": "LoadImage", "inputs": {"image": "fixture.png"}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "release"}},
        },
        {"OUTPUT_PREFIX": {"node_id": "2", "input": "filename_prefix"}},
    )


def test_revoke_requires_operator_reason_and_preserves_audit(workspace, database) -> None:
    service = WorkflowService(database)
    workflow, bindings = _workflow()
    version = service.register_package("release_reason", "Release reason", workflow, {}, bindings)

    with pytest.raises(DomainRuleError) as raised:
        service.revoke(str(version["id"]), "  ")
    assert raised.value.code == "WORKFLOW_REVOKE_REASON_REQUIRED"

    revoked = service.revoke(str(version["id"]), "node dependency was retired")
    assert revoked["status"] == "RETIRED"
    with database.connect() as connection:
        event = connection.execute(
            "SELECT metadata_redacted_json FROM audit_events WHERE subject_type='workflow_version' AND action='WORKFLOW_REVOKED' ORDER BY occurred_at DESC LIMIT 1"
        ).fetchone()
    assert json.loads(str(event["metadata_redacted_json"]))["reason"] == "node dependency was retired"


def test_revoke_route_rejects_missing_reason_and_accepts_explicit_reason(workspace, database) -> None:
    service = WorkflowService(database)
    workflow, bindings = _workflow()
    version = service.register_package("release_api_reason", "Release API reason", workflow, {}, bindings)
    with TestClient(create_app(workspace)) as client:
        missing = client.post(f"/api/v1/workflow-versions/{version['id']}:revoke", json={})
        assert missing.status_code == 422
        response = client.post(
            f"/api/v1/workflow-versions/{version['id']}:revoke",
            json={"reason": "operator requested rollback"},
        )
    assert response.status_code == 200
    assert response.json()["workflow_version"]["status"] == "RETIRED"


def test_rollback_is_a_fresh_validation_gated_republish(workspace, database) -> None:
    service = WorkflowService(database)
    workflow, bindings = _workflow()
    old = service.register_package("release_rollback", "Release rollback", workflow, {}, bindings)
    current = service.register_package("release_rollback", "Release rollback", workflow, {}, bindings)
    old_validation = service.validate_against_comfy(str(old["id"]), _OfflineComfy())
    current_validation = service.validate_against_comfy(str(current["id"]), _OfflineComfy())
    service.publish(str(current["id"]), str(current_validation["validation_id"]))

    rolled_back = service.rollback(str(old["id"]), str(old_validation["validation_id"]))
    assert rolled_back["status"] == "PUBLISHED"
    assert service.get_version(str(current["id"]))["status"] == "RETIRED"
    with database.connect() as connection:
        event = connection.execute(
            "SELECT COUNT(*) AS count FROM audit_events WHERE subject_type='workflow_version' AND action='WORKFLOW_ROLLED_BACK' AND subject_id=?",
            (old["id"],),
        ).fetchone()
    assert int(event["count"]) == 1


def test_registration_rejects_missing_semantic_binding_before_persisting(workspace, database) -> None:
    service = WorkflowService(database)
    workflow, _ = _workflow()
    with pytest.raises(DomainRuleError) as raised:
        service.register_package(
            "release_missing_binding",
            "Release missing binding",
            workflow,
            {"input_slots": {"PROMPT": {"required": True}}},
            {"OUTPUT_PREFIX": {"node_id": "missing", "input": "filename_prefix"}},
        )
    assert raised.value.code == "WORKFLOW_BINDING_INVALID"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflows WHERE code='release_missing_binding'").fetchone()[0] == 0

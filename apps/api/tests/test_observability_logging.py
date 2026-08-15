from __future__ import annotations

import json
import logging
import uuid

from fastapi.testclient import TestClient

from local_drama.main import create_app


def test_request_log_contains_trace_job_project_context_without_sensitive_values(workspace, database) -> None:
    del database
    trace_id = "trace-obs-001"
    project_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    logger = logging.getLogger("local_drama.observability")
    logger.setLevel(logging.INFO)
    # Alembic's test migration config disables pre-existing loggers.
    logger.disabled = False
    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record.getMessage())
    logger.addHandler(handler)
    try:
        with TestClient(create_app(workspace)) as client:
            response = client.get(
                "/api/v1/capacity/snapshot",
                params={"job_id": job_id, "token": "secret-value"},
                headers={"X-Trace-Id": trace_id, "X-Project-Id": project_id, "Authorization": "Bearer secret-value"},
            )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"] == trace_id
    messages = records
    assert messages
    completed = json.loads(next(message for message in messages if '"event": "request.completed"' in message))
    assert completed["trace_id"] == trace_id
    assert completed["request_id"]
    assert completed["project_id"] == project_id
    assert completed["job_id"] == job_id
    assert completed["status_code"] == 200
    assert "secret-value" not in "\n".join(messages)


def test_request_log_extracts_ids_from_resource_path(workspace, database) -> None:
    del database
    project_id = str(uuid.uuid4())
    logger = logging.getLogger("local_drama.observability")
    logger.disabled = False
    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record.getMessage())
    logger.addHandler(handler)
    try:
        with TestClient(create_app(workspace)) as client:
            response = client.get(f"/api/v1/projects/{project_id}", headers={"X-Request-Id": "request-obs-002"})
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 404
    message = next(message for message in records if '"event": "request.completed"' in message)
    payload = json.loads(message)
    assert payload["trace_id"] == "request-obs-002"
    assert payload["project_id"] == project_id
    assert payload["status_code"] == 404


def test_security_rejection_is_also_correlated_without_logging_origin(workspace, database) -> None:
    del database
    logger = logging.getLogger("local_drama.observability")
    logger.disabled = False
    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record.getMessage())
    logger.addHandler(handler)
    try:
        with TestClient(create_app(workspace)) as client:
            response = client.post(
                "/api/v1/projects",
                json={"code": "OBS", "title": "OBS", "episode_count": 1},
                headers={"Origin": "https://secret-origin.example", "X-Trace-Id": "reject-obs-003"},
            )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 403
    message = next(message for message in records if '"event": "request.rejected"' in message)
    payload = json.loads(message)
    assert payload["trace_id"] == "reject-obs-003"
    assert payload["error_type"] == "ORIGIN_NOT_ALLOWED"
    assert "secret-origin.example" not in message

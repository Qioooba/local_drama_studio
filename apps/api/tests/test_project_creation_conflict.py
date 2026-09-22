"""HTTP-01: duplicate project creation and Idempotency-Key must be a stable contract.

The original defect was a 500 ``text/plain`` for a repeated ``POST /api/v1/projects``
and an ``Idempotency-Key`` header that was declared but discarded.  These tests
assert the corrected behaviour against both the HTTP contract and the database.
"""

from __future__ import annotations

import concurrent.futures
import json

import pytest
from fastapi.testclient import TestClient

from local_drama.main import create_app

PAYLOAD = {
    "code": "conflict_project",
    "title": "重复创建",
    "episode_count": 1,
    "aspect_ratio": "16:9",
    "fps": {"numerator": 24, "denominator": 1},
    "target_duration_ms": 60_000,
    "allow_unconfigured_capabilities": True,
}


def _table_counts(database) -> dict[str, int]:
    with database.connect() as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("projects", "seasons", "episodes", "audit_events", "outbox_events")
        }


def test_same_code_resubmitted_is_a_stable_409_and_preserves_the_original(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        first = client.post("/api/v1/projects", json=PAYLOAD)
        assert first.status_code == 201, first.text
        project_id = first.json()["project"]["id"]
        marker = workspace.projects_root / PAYLOAD["code"] / "keep-me.txt"
        marker.write_text("original", encoding="utf-8")
        before = _table_counts(database)

        second = client.post("/api/v1/projects", json=PAYLOAD)

    assert second.status_code == 409, second.text
    assert second.headers["content-type"].startswith("application/json")
    assert second.json()["error"]["code"] == "PROJECT_CODE_EXISTS"
    assert "Internal Server Error" not in second.text
    assert marker.read_text(encoding="utf-8") == "original"
    assert _table_counts(database) == before
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects WHERE code=?", (PAYLOAD["code"],)).fetchone()[0] == 1
        assert connection.execute("SELECT id FROM projects WHERE code=?", (PAYLOAD["code"],)).fetchone()[0] == project_id
    assert not list(workspace.projects_root.glob(f".{PAYLOAD['code']}.partial-*"))


def test_idempotency_key_replay_returns_the_original_project(workspace, database) -> None:
    headers = {"Idempotency-Key": "project-create-key-1"}
    with TestClient(create_app(workspace)) as client:
        first = client.post("/api/v1/projects", json=PAYLOAD, headers=headers)
        assert first.status_code == 201, first.text
        replayed = client.post("/api/v1/projects", json=PAYLOAD, headers=headers)
        changed = client.post("/api/v1/projects", json={**PAYLOAD, "title": "不同请求"}, headers=headers)

    assert replayed.status_code == 201, replayed.text
    assert replayed.json()["idempotent_replay"] is True
    assert replayed.json()["project"]["id"] == first.json()["project"]["id"]
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 1
        receipt = connection.execute(
            "SELECT scope, payload_hash, response_json FROM command_idempotencies WHERE idempotency_key=?",
            ("project-create-key-1",),
        ).fetchone()
    assert receipt is not None and receipt["scope"] == "project:create"
    assert len(str(receipt["payload_hash"])) == 64
    assert json.loads(str(receipt["response_json"]))["project_id"] == first.json()["project"]["id"]


def test_directory_without_database_row_is_a_409_and_is_never_deleted(workspace, database) -> None:
    orphan = workspace.projects_root / PAYLOAD["code"]
    orphan.mkdir(parents=True)
    (orphan / "user-data.txt").write_text("pre-existing", encoding="utf-8")

    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v1/projects", json=PAYLOAD)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "PROJECT_ROOT_EXISTS"
    assert (orphan / "user-data.txt").read_text(encoding="utf-8") == "pre-existing"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    assert not list(workspace.projects_root.glob(f".{PAYLOAD['code']}.partial-*"))


def test_two_concurrent_identical_codes_yield_one_project_and_one_conflict(workspace, database) -> None:
    app = create_app(workspace)

    def submit() -> tuple[int, str]:
        with TestClient(app) as client:
            response = client.post("/api/v1/projects", json={**PAYLOAD, "code": "concurrent_conflict"})
            if response.status_code == 201:
                return 201, "created"
            return response.status_code, str(response.json()["error"]["code"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in [pool.submit(submit), pool.submit(submit)]]

    statuses = sorted(status for status, _ in outcomes)
    assert statuses[0] == 201, outcomes
    assert statuses[1] == 409, outcomes
    assert {code for status, code in outcomes if status == 409} <= {"PROJECT_CODE_EXISTS", "PROJECT_ROOT_EXISTS"}
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects WHERE code='concurrent_conflict'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM episodes WHERE season_id IN (SELECT id FROM seasons)").fetchone()[0] == 1
    assert not list(workspace.projects_root.glob(".concurrent_conflict.partial-*"))


def test_blank_idempotency_key_is_rejected_without_creating_anything(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v1/projects", json=PAYLOAD, headers={"Idempotency-Key": "   "})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM command_idempotencies").fetchone()[0] == 0


@pytest.mark.parametrize("duration_ms", [1, 86_400_000])
def test_extreme_but_valid_project_defaults_round_trip(workspace, database, duration_ms: int) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v1/projects", json={**PAYLOAD, "code": f"boundary_{duration_ms}", "target_duration_ms": duration_ms})

    assert response.status_code == 201, response.text
    assert response.json()["project"]["target_duration_ms"] == duration_ms

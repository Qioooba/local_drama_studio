from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.capacity import CapacitySnapshotService
from local_drama.application.projects import ProjectService
from local_drama.application.worker_sessions import WorkerSessionService
from local_drama.main import create_app


def test_capacity_snapshot_is_read_only_and_observed(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="capacity_snapshot", title="Capacity snapshot", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    before = database.connect()
    try:
        before_count = int(before.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    finally:
        before.close()
    snapshot = CapacitySnapshotService(database).inspect(str(project["id"]))
    assert snapshot["observation_status"] == "OBSERVED_NOT_BENCHMARKED"
    assert snapshot["webhook_status"] == "LOOPBACK_EXPLICIT_BOUNDED"
    assert snapshot["would_create_jobs"] is False
    assert snapshot["runtime_contacted"] is False
    assert snapshot["network_contacted"] is False
    with database.connect() as connection:
        assert int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]) == before_count


def test_capacity_counts_live_supervisor_even_when_no_attempt_is_running(workspace, database) -> None:
    session = WorkerSessionService(database, workspace).start_session(
        "capacity-worker",
        worker_version=workspace.app_version,
        api_version=workspace.app_version,
        channels=["CPU", "GPU_H3"],
    )
    try:
        snapshot = CapacitySnapshotService(database, workspace).inspect()
        assert snapshot["active_attempt_count"] == 0
        assert snapshot["active_worker_count"] == 1
    finally:
        WorkerSessionService(database, workspace).stop(str(session["id"]))


def test_capacity_snapshot_route_rejects_unknown_project(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/capacity/snapshot?project_id=missing")
    assert response.status_code == 404

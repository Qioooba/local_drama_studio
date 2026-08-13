from __future__ import annotations

from local_drama.application.g7_readiness import G7ReadinessService
from local_drama.application.network_e2e import NetworkE2EService
from local_drama.application.projects import ProjectService


def test_network_e2e_uses_real_loopback_clients_and_blocks_public_connect(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="network_e2e", title="Network e2e", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    result = NetworkE2EService(database).run(str(project["id"]))
    assert result["status"] == "PASS"
    assert result["runtime_contacted"] is False
    assert result["network_contacted"] is False
    assert result["blocked_public_attempts"] == ["203.0.113.1"]
    assert {"GET /system_stats", "GET /object_info", "GET /queue", "GET /history/local-network-harness", "GET /api/tags", "POST /api/generate"} <= set(result["local_requests"])
    readiness = G7ReadinessService(database).inspect(str(project["id"]))
    assert next(item for item in readiness["checks"] if item["code"] == "ZERO_PUBLIC_NETWORK_E2E")["passed"] is True

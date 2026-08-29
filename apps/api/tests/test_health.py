from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from local_drama.api.contract_version import API_CONTRACT_HEADER, API_CONTRACT_VERSION
from local_drama.application.worker_sessions import WorkerSessionService
from local_drama.config import Settings
from local_drama.main import create_app


def test_local_only_settings_reject_non_loopback_bind_host() -> None:
    with pytest.raises(ValidationError, match="literal loopback"):
        Settings(host="0.0.0.0")
    with pytest.raises(ValidationError, match="literal loopback"):
        Settings(host="192.168.1.42")


def test_local_only_settings_accept_literal_loopback_bind_hosts() -> None:
    assert Settings(host="127.0.0.1").host == "127.0.0.1"
    assert Settings(host="LOCALHOST").host == "localhost"
    assert Settings(host="::1").host == "::1"


def test_live_is_local_only(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["checks"]["mode"] == "LOCAL_ONLY"
    assert response.headers["X-Request-Id"]
    assert response.headers[API_CONTRACT_HEADER] == API_CONTRACT_VERSION


def test_api_contract_discovery_and_business_request_version_are_consistent(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    with TestClient(create_app(settings)) as client:
        contract = client.get("/api/v1/system/contract")
        mismatch = client.get(
            "/api/v1/session/bootstrap",
            headers={API_CONTRACT_HEADER: "localdrama.api.stale"},
        )
        matched = client.get(
            "/api/v1/session/bootstrap",
            headers={API_CONTRACT_HEADER: API_CONTRACT_VERSION},
        )
    assert contract.status_code == 200
    assert contract.json()["api_contract_version"] == API_CONTRACT_VERSION
    assert contract.headers[API_CONTRACT_HEADER] == API_CONTRACT_VERSION
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "API_CONTRACT_MISMATCH"
    assert mismatch.json()["error"]["details"] == {
        "expected": API_CONTRACT_VERSION,
        "observed": "localdrama.api.stale",
    }
    assert mismatch.headers[API_CONTRACT_HEADER] == API_CONTRACT_VERSION
    assert matched.status_code == 200


def test_ready_reports_g2_database_boundary(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/v1/health/ready").json()
    assert payload["checks"]["database"] == "not_configured_until_g2"


def test_dependencies_report_live_loopback_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from local_drama.api.routes import health

    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    monkeypatch.setattr(health, "_probe_loopback", lambda _url: ("PASS", {"loopback": True}))
    monkeypatch.setattr(health.shutil, "which", lambda executable: f"/test/{executable}" if executable == "ffmpeg" else None)
    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/v1/health/dependencies").json()
    assert payload["checks"]["comfy_designer"] == "ready"


def test_dependencies_fail_closed_when_loopback_is_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from local_drama.api.routes import health

    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    monkeypatch.setattr(health, "_probe_loopback", lambda _url: ("BLOCKED", {"reason": "ConnectionRefusedError"}))
    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/v1/health/dependencies").json()
    assert payload["status"] == "DEGRADED"
    assert payload["checks"]["comfy_designer"] == "blocked:ConnectionRefusedError"


def test_dependencies_only_claim_worker_ready_for_live_compatible_session(
    monkeypatch: pytest.MonkeyPatch,
    workspace,
    database,
) -> None:
    from local_drama.api.routes import health

    monkeypatch.setattr(health, "_probe_loopback", lambda _url: ("PASS", {"loopback": True}))
    monkeypatch.setattr(health.shutil, "which", lambda executable: f"/test/{executable}" if executable == "ffmpeg" else None)
    session = WorkerSessionService(database, workspace).start_session(
        "health-worker",
        worker_version=workspace.app_version,
        api_version=workspace.app_version,
        channels=["CPU", "GPU_H3"],
    )
    try:
        with TestClient(create_app(workspace)) as client:
            payload = client.get("/api/v1/health/dependencies").json()
        assert payload["checks"]["worker_supervisor"] == "ready:CPU,GPU_H3"
        assert payload["status"] == "HEALTHY"
    finally:
        WorkerSessionService(database, workspace).stop(str(session["id"]))


def test_untrusted_origin_is_rejected_for_writes(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/system/contract", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    assert response.json()["error"]["details"] == {}
    assert "evil.example" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_trusted_origin_requires_valid_instance_token_for_writes(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    origin = "http://127.0.0.1:5173"
    with TestClient(create_app(settings)) as client:
        missing = client.post("/api/v1/system/contract", headers={"Origin": origin})
        invalid = client.post(
            "/api/v1/system/contract",
            headers={"Origin": origin, "X-Local-Instance-Token": "invalid"},
        )
        bootstrap = client.get("/api/v1/session/bootstrap", headers={"Origin": origin})
        accepted_boundary = client.post(
            "/api/v1/system/contract",
            headers={"Origin": origin, "X-Local-Instance-Token": bootstrap.json()["token"]},
        )
    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_TOKEN_REQUIRED"
    assert missing.headers["X-Content-Type-Options"] == "nosniff"
    assert missing.headers["Content-Security-Policy"] == "default-src 'self'; frame-ancestors 'self'"
    assert missing.headers["Cache-Control"] == "no-store"
    # The route is GET-only, so reaching routing after middleware proves the
    # valid token crossed the CSRF boundary without mutating any state.
    assert accepted_boundary.status_code == 405
    assert bootstrap.headers["Cache-Control"] == "no-store"


def test_loopback_dev_origin_remains_writable_when_frontend_port_drifts(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    origin = "http://127.0.0.1:5175"
    with TestClient(create_app(settings)) as client:
        bootstrap = client.get("/api/v1/session/bootstrap", headers={"Origin": origin})
        accepted_boundary = client.post(
            "/api/v1/system/contract",
            headers={"Origin": origin, "X-Local-Instance-Token": bootstrap.json()["token"]},
        )
    assert accepted_boundary.status_code == 405


def test_lookalike_or_non_http_local_origins_are_rejected(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    with TestClient(create_app(settings)) as client:
        lookalike = client.post("/api/v1/system/contract", headers={"Origin": "http://localhost.evil.example:5175"})
        non_http = client.post("/api/v1/system/contract", headers={"Origin": "https://127.0.0.1:5175"})
    assert lookalike.status_code == 403
    assert non_http.status_code == 403
    assert lookalike.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"

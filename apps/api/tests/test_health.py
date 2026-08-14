from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.config import Settings
from local_drama.main import create_app


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
    # The route is GET-only, so reaching routing after middleware proves the
    # valid token crossed the CSRF boundary without mutating any state.
    assert accepted_boundary.status_code == 405
    assert bootstrap.headers["Cache-Control"] == "no-store"

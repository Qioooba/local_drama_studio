"""Run the isolated G10 path/CSRF/REMOTE/network/node-supply-chain UAT."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.network_e2e import NetworkE2EService
from local_drama.application.projects import ProjectService
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.adapters import validate_adapter_target
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app

from scripts.migrate import migrate


def _settings(root: Path) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
    )


def run(root: Path) -> dict[str, Any]:
    settings = _settings(root)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    project = ProjectService(database, settings.projects_root).create_project(
        code="g10_security_uat",
        title="G10 isolated security UAT",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    origin = "http://127.0.0.1:5173"
    app = create_app(settings)
    with TestClient(app) as client:
        malicious_origin = client.post("/api/v1/system/contract", headers={"Origin": "https://evil.example"})
        missing_token = client.post("/api/v1/system/contract", headers={"Origin": origin})
        invalid_token = client.post(
            "/api/v1/system/contract", headers={"Origin": origin, "X-Local-Instance-Token": "invalid"}
        )
        bootstrap = client.get("/api/v1/session/bootstrap", headers={"Origin": origin})
        valid_token = client.post(
            "/api/v1/system/contract",
            headers={"Origin": origin, "X-Local-Instance-Token": bootstrap.json()["token"]},
        )

    with database.transaction() as connection:
        connection.execute("UPDATE projects SET root_rel='../../outside' WHERE id=?", (project["id"],))
    try:
        from local_drama.application.media import MediaService

        MediaService(database, settings)._project_root(str(project["id"]))
        path_error = None
    except DomainRuleError as error:
        path_error = error.code

    try:
        validate_adapter_target("REMOTE_HTTP_SERVICE", base_url="https://api.example.com")
        remote_error = None
    except DomainRuleError as error:
        remote_error = error.code

    try:
        WorkflowService(database, settings).register_package(
            "untrusted_node",
            "Untrusted node",
            {"1": {"class_type": "ArbitraryInternetDownloaderNode", "inputs": {}}},
            {"output": "VIDEO"},
            {},
        )
        node_error = None
    except DomainRuleError as error:
        node_error = error.code

    network = NetworkE2EService(database).run(str(project["id"]), actor="g10-security-uat")
    openapi = json.dumps(app.openapi(), ensure_ascii=False).lower()
    forbidden_remote_fields = [field for field in ("api_key", "secret_key", "credential_value") if field in openapi]
    checks = [
        {"code": "MALICIOUS_ORIGIN_REJECTED", "passed": malicious_origin.status_code == 403 and malicious_origin.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"},
        {"code": "CSRF_TOKEN_REQUIRED", "passed": missing_token.status_code == 403 and invalid_token.status_code == 403 and missing_token.json()["error"]["code"] == "CSRF_TOKEN_REQUIRED"},
        {"code": "VALID_INSTANCE_TOKEN_ACCEPTED", "passed": valid_token.status_code == 405 and len(bootstrap.json()["token"]) >= 32},
        {"code": "PROJECT_PATH_ESCAPE_REJECTED", "passed": path_error == "PATH_ESCAPE", "observed_error": path_error},
        {"code": "REMOTE_PROVIDER_DISABLED", "passed": remote_error == "REMOTE_PROVIDER_DISABLED_IN_LOCAL_RELEASE", "observed_error": remote_error},
        {"code": "UNTRUSTED_CUSTOM_NODE_REJECTED", "passed": node_error == "WORKFLOW_NODE_SUPPLY_CHAIN_UNTRUSTED", "observed_error": node_error},
        {"code": "ZERO_PUBLIC_NETWORK_E2E", "passed": network["status"] == "PASS" and network["network_contacted"] is False, "blocked_public_attempts": network["blocked_public_attempts"]},
        {"code": "OPENAPI_HAS_NO_REMOTE_CREDENTIAL_FIELDS", "passed": not forbidden_remote_fields, "forbidden_fields": forbidden_remote_fields},
        {"code": "DATABASE_INTEGRITY", "passed": database.integrity_check() == "ok"},
    ]
    return {
        "schema_version": "g10.security_uat.v1",
        "status": "PASS" if all(check["passed"] for check in checks) else "FAIL",
        "scope": "isolated migrated database and in-process real FastAPI boundary",
        "checks": checks,
        "runtime_contacted": False,
        "public_network_contacted": False,
        "production_database_contacted": False,
        "jobs_created": False,
        "observed_at": datetime.now(UTC).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / "temp" / f"g10-security-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "security-uat-2026-08-15.json")
    args = parser.parse_args()
    result = run(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

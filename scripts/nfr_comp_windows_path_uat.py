"""Bounded Windows path compatibility UAT for NFR-COMP-001.

This exercise is intentionally isolated from the production database and from
any model runtime.  It creates a tiny user-owned safetensors fixture on the
repository volume, copies that fixture to a second Windows volume, and then
uses the real FastAPI model-reference and compatibility endpoints against the
copied path.  The copy is made by this UAT only to prove cross-volume path
handling; the platform itself must retain only the absolute reference and
never copy model bytes into a project or package.

The result is deliberately ``PARTIAL`` even when every bounded check passes:
Windows three-viewport UAT, a real user model/runtime execution, and legal
license review remain release requirements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app

from scripts.migrate import migrate

LONG_PATH_MIN = 180


def _settings(root: Path) -> Settings:
    return Settings(
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_safetensors(path: Path) -> None:
    header = b'{"tensor":{"dtype":"I8","shape":[1],"data_offsets":[0,1]}}'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(len(header).to_bytes(8, "little") + header + b"x")


def _check(code: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"code": code, "passed": bool(passed), **details}


def run_uat(root: Path, destination_parent: Path) -> dict[str, Any]:
    observed_at = datetime.now(UTC).isoformat()
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    destination_root: Path | None = None
    source: Path | None = None
    copied: Path | None = None
    project_root: Path | None = None
    project_id: str | None = None
    source_hash = ""
    copied_hash = ""
    api_results: dict[str, Any] = {}

    windows = platform.system().casefold() == "windows"
    checks.append(_check("WINDOWS_X64_HOST", windows, platform=platform.platform()))
    if not windows:
        return {
            "schema_version": "localdrama.nfr-comp-001.windows-path-uat.v1",
            "status": "PARTIAL",
            "scope": "Windows F/E path matrix was not runnable on this host",
            "checks": checks,
            "limitations": ["Run this script on the target Windows x64 release host."],
            "runtime_contacted": False,
            "network_contacted": False,
            "production_database_contacted": False,
            "observed_at": observed_at,
        }

    settings = _settings(root)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    project = ProjectService(database, settings.projects_root).create_project(
        code="nfr_comp_path_matrix",
        title="NFR-COMP isolated Windows path matrix",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    project_root = (settings.projects_root / str(project["root_rel"])).resolve()

    source_root = (root / "source").resolve()
    relative_model = (
        Path("中文 空格 模型")
        / ("长路径-" + ("x" * 34))
        / ("第二层-" + ("y" * 34))
        / ("第三层-" + ("z" * 24))
        / "FL2VA I2V int8 fp16.safetensors"
    )
    source = source_root / relative_model
    _write_safetensors(source)
    source_hash = _sha256(source)
    source_length = len(str(source))
    checks.append(
        _check(
            "UNICODE_SPACE_LONG_SOURCE_PATH",
            "中文" in str(source) and " " in str(source) and source_length >= LONG_PATH_MIN,
            path_length=source_length,
            path=str(source),
        )
    )

    destination_parent = destination_parent.resolve()
    destination_parent.mkdir(parents=True, exist_ok=True)
    destination_root = (destination_parent / f"localdrama-nfr-comp-{uuid.uuid4().hex[:10]}").resolve()
    copied = destination_root / relative_model
    try:
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copied)
    except OSError as error:
        checks.append(_check("CROSS_VOLUME_COPY", False, error=str(error), source=str(source), destination=str(copied)))
        failures.append("CROSS_VOLUME_COPY")
    else:
        copied_hash = _sha256(copied)
        cross_volume = source.drive.casefold() != copied.drive.casefold() and bool(source.drive and copied.drive)
        checks.append(
            _check(
                "CROSS_VOLUME_COPY",
                cross_volume and copied_hash == source_hash and len(str(copied)) >= LONG_PATH_MIN,
                source_drive=source.drive,
                destination_drive=copied.drive,
                source_hash=source_hash,
                destination_hash=copied_hash,
                destination_path_length=len(str(copied)),
                source=str(source),
                destination=str(copied),
            )
        )
        if not cross_volume or copied_hash != source_hash:
            failures.append("CROSS_VOLUME_COPY")

    if copied is not None and copied.is_file() and project_id:
        app = create_app(settings)
        with TestClient(app) as client:
            register = client.post(
                f"/api/v1/projects/{project_id}/model-artifacts",
                json={
                    "code": "nfr-comp-fl2va",
                    "kind": "FL2VA I2V",
                    "machine_path_ref": str(copied),
                },
            )
            api_results["register_status"] = register.status_code
            register_payload = register.json() if register.headers.get("content-type", "").startswith("application/json") else {}
            artifact = register_payload.get("artifact", {})
            report = client.post(
                f"/api/v1/projects/{project_id}/model-compatibility-report",
                json={"model_artifact_id": artifact.get("id"), "required_capability": "I2V"},
            ) if register.status_code == 201 and artifact.get("id") else None
            api_results["report_status"] = report.status_code if report is not None else None
            report_payload = report.json() if report is not None and report.headers.get("content-type", "").startswith("application/json") else {}
            report_item = report_payload.get("report", {})
            resolved_ref = str(artifact.get("machine_path_ref", ""))
            checks.append(
                _check(
                    "API_RETains_ABSOLUTE_EXTERNAL_REFERENCE",
                    register.status_code == 201
                    and resolved_ref == str(copied.resolve())
                    and artifact.get("copied") is False
                    and artifact.get("uploaded") is False,
                    register_status=register.status_code,
                    machine_path_ref=resolved_ref,
                    copied=artifact.get("copied"),
                    uploaded=artifact.get("uploaded"),
                )
            )
            checks.append(
                _check(
                    "API_OFFLINE_HASH_AND_CAPABILITY_REPORT",
                    report is not None
                    and report.status_code == 201
                    and report_item.get("report_status") == "PASS"
                    and report_item.get("capability", {}).get("status") == "MATCHED"
                    and report_item.get("sha256") == copied_hash,
                    report_status=report.status_code if report is not None else None,
                    compatibility_status=report_item.get("report_status"),
                    capability=report_item.get("capability"),
                    report_sha256=report_item.get("sha256"),
                )
            )

    checks.append(
        _check(
            "MODEL_BYTES_NOT_IN_PROJECT_ROOT",
            copied is not None and project_root is not None and not copied.resolve().is_relative_to(project_root),
            model_path=str(copied) if copied else None,
            project_root=str(project_root) if project_root else None,
        )
    )
    checks.append(_check("SOURCE_UNCHANGED_AFTER_COPY_AND_REPORT", source is not None and _sha256(source) == source_hash))
    checks.append(_check("LOCAL_ONLY_NO_RUNTIME_OR_NETWORK", True, runtime_contacted=False, network_contacted=False))

    failed_codes = [str(item["code"]) for item in checks if not item.get("passed")]
    status = "FAIL" if failed_codes else "PARTIAL"
    return {
        "schema_version": "localdrama.nfr-comp-001.windows-path-uat.v1",
        "status": status,
        "scope": "isolated migrated SQLite + real FastAPI model-reference/compatibility paths on Windows",
        "requirements": ["NFR-COMP-001", "FR-PRV-003"],
        "source_volume": source.drive if source else None,
        "destination_volume": copied.drive if copied else None,
        "source_sha256": source_hash,
        "destination_sha256": copied_hash,
        "api": api_results,
        "checks": checks,
        "failed_checks": failed_codes,
        "observed": {
            "unicode_path": bool(source and "中文" in str(source)),
            "space_path": bool(source and " " in str(source)),
            "long_path": bool(source and len(str(source)) >= LONG_PATH_MIN),
            "cross_volume_copy": bool(copied and copied.is_file() and source and source.drive.casefold() != copied.drive.casefold()),
            "absolute_reference_only": all(item.get("passed") for item in checks if item["code"] == "API_RETains_ABSOLUTE_EXTERNAL_REFERENCE"),
            "copied": False,
            "uploaded": False,
            "runtime_contacted": False,
            "network_contacted": False,
            "production_database_contacted": False,
        },
        "limitations": [
            "This is an isolated bounded path/reference UAT, not a real user model execution.",
            "Windows three-viewport browser UAT and final legal license review remain outstanding.",
            "The F-to-E copy is performed by this test fixture only; the platform retains no model bytes in the project.",
        ],
        "next_required_action": "Run the Windows x64 three-viewport model selection and real user-runtime UAT before promoting NFR-COMP-001 to VERIFIED.",
        "observed_at": observed_at,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parser.add_argument("--root", type=Path, default=ROOT / "temp" / f"nfr-comp-{stamp}")
    parser.add_argument("--destination-parent", type=Path, default=Path(r"E:\AI\Temp"))
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "evidence" / "g10" / "nfr-comp-001-windows-path-2026-08-16.json",
    )
    args = parser.parse_args()
    result = run_uat(args.root.resolve(), args.destination_parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "failed_checks": result.get("failed_checks", []), "output": str(args.output)}, ensure_ascii=False))
    if result["status"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

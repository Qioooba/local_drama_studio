from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from email.parser import Parser
from pathlib import Path

import tomllib

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _copytree(source: Path, target: Path, *, ignore: Callable[[str, list[str]], set[str]] | None = None) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_id(channel: str) -> str:
    configured = os.environ.get("LOCAL_DRAMA_BUILD_ID")
    if configured:
        return configured
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True
    )
    commit = result.stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    if dirty and channel != "development":
        raise RuntimeError("stable release builds require a clean Git worktree")
    return f"{commit}-dirty" if dirty else commit


def _build_timestamp() -> str:
    configured = os.environ.get("SOURCE_DATE_EPOCH")
    if configured:
        from datetime import UTC, datetime

        return datetime.fromtimestamp(int(configured), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = subprocess.run(
        ["git", "show", "-s", "--format=%cI", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _manifest(
    payload: Path,
    identity: dict[str, object],
    platform: str,
    runtime_version: str,
) -> dict[str, object]:
    files = []
    for path in sorted(item for item in payload.rglob("*") if item.is_file()):
        files.append(
            {
                "path": path.relative_to(payload).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    os_name, arch = platform.split("-", maxsplit=1)
    migration_contract = json.loads(
        (REPOSITORY_ROOT / "docs" / "release" / "migration-contract.json").read_text(encoding="utf-8")
    )
    accepted_heads = [str(item["revision"]) for item in migration_contract["release_migrations"]]
    return {
        "schema_version": 1,
        "product": "LocalDramaStudio",
        "version": str(identity["version"]),
        "build_id": _build_id(str(identity["channel"])),
        "channel": str(identity["channel"]),
        "platform": {"os": os_name, "arch": arch},
        "host": {
            "protocol": int(identity["host_protocol"]),
            "minimum_version": str(identity.get("minimum_host_version", identity["version"])),
        },
        "python": {"version": runtime_version.removeprefix("Python "), "abi": "cp312"},
        "database": {
            "accepted_heads": accepted_heads,
            "target_heads": [str(item) for item in migration_contract["expected_heads"]],
            "requires_backup": True,
        },
        "config_schema": {"minimum": 1, "target": int(identity["config_schema"])},
        "files": files,
        "sbom": "sbom.spdx.json",
        "signature": None,
    }


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _validate_version_alignment(identity: dict[str, object]) -> None:
    expected = str(identity["version"])
    api = tomllib.loads((REPOSITORY_ROOT / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8"))
    web = json.loads((REPOSITORY_ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    observed = {"release/version.json": expected, "apps/api/pyproject.toml": str(api["project"]["version"]), "apps/web/package.json": str(web["version"])}
    mismatched = {name: version for name, version in observed.items() if version != expected}
    if mismatched:
        raise ValueError(f"release version is not aligned: expected {expected}, got {mismatched}")


def _verify_wheelhouse(root: Path) -> None:
    manifest_path = root / "wheelhouse-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"wheelhouse manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("python_abi") != "cp312":
        raise ValueError("wheelhouse manifest is incompatible")
    expected = {str(item["file"]): item for item in manifest.get("files", [])}
    actual = {path.name for path in root.glob("*.whl")}
    if actual != set(expected):
        raise ValueError("wheelhouse contents do not match its manifest")
    for name, entry in expected.items():
        path = root / name
        if path.stat().st_size != int(entry["bytes"]) or _sha256(path) != str(entry["sha256"]):
            raise ValueError(f"wheelhouse file failed verification: {name}")


def _write_sbom(payload: Path, identity: dict[str, object], platform: str) -> None:
    packages: list[dict[str, object]] = [
        {
            "SPDXID": "SPDXRef-LocalDramaStudio",
            "name": "LocalDramaStudio",
            "versionInfo": str(identity["version"]),
            "downloadLocation": "NOASSERTION",
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "filesAnalyzed": False,
        }
    ]
    relationships: list[dict[str, str]] = []
    for index, metadata_path in enumerate(sorted((payload / "app").glob("*.dist-info/METADATA")), start=1):
        metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8", errors="replace"))
        package_id = f"SPDXRef-PythonPackage-{index}"
        packages.append(
            {
                "SPDXID": package_id,
                "name": metadata.get("Name", metadata_path.parent.name),
                "versionInfo": metadata.get("Version", "NOASSERTION"),
                "downloadLocation": "NOASSERTION",
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": metadata.get("License-Expression") or metadata.get("License") or "NOASSERTION",
                "filesAnalyzed": False,
            }
        )
        relationships.append(
            {"spdxElementId": "SPDXRef-LocalDramaStudio", "relationshipType": "DEPENDS_ON", "relatedSpdxElement": package_id}
        )
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"LocalDramaStudio-{identity['version']}-{platform}",
        "documentNamespace": f"https://localdramastudio.local/spdx/{identity['version']}/{platform}/{_build_id(str(identity['channel']))}",
        "creationInfo": {"created": _build_timestamp(), "creators": ["Tool: LocalDramaStudio release builder"]},
        "packages": packages,
        "relationships": relationships,
    }
    (payload / "sbom.spdx.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> Path:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("release assembly must use Python 3.12")
    identity = json.loads((REPOSITORY_ROOT / "release" / "version.json").read_text(encoding="utf-8"))
    _validate_version_alignment(identity)
    version = str(identity["version"])
    output_root = Path(args.output).resolve()
    package_root = output_root / f"LocalDramaStudio-{version}-{args.platform}"
    if package_root.exists():
        shutil.rmtree(package_root)
    payload = package_root / "payload"
    payload.mkdir(parents=True)

    runtime_target = payload / "runtime" / "python"
    _copytree(Path(args.python_runtime).resolve(), runtime_target)
    python_name = "python.exe" if args.platform.startswith("windows") else "bin/python3"
    runtime_python = runtime_target / python_name
    if not runtime_python.is_file():
        raise FileNotFoundError(f"private Python runtime is incomplete: {runtime_python}")
    version_probe = subprocess.run(
        [str(runtime_python), "--version"], capture_output=True, text=True, check=True
    )
    runtime_version = (version_probe.stdout or version_probe.stderr).strip()
    if not runtime_version.startswith("Python 3.12."):
        raise RuntimeError(f"private runtime must be Python 3.12, got {runtime_version}")

    pip_command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-compile",
        "--target",
        str(payload / "app"),
    ]
    if args.offline:
        if not args.wheelhouse:
            raise ValueError("--wheelhouse is required with --offline")
        wheelhouse = Path(args.wheelhouse).resolve()
        _verify_wheelhouse(wheelhouse)
        pip_command.extend(["--no-index", "--find-links", str(wheelhouse)])
    pip_command.extend(["-r", str(REPOSITORY_ROOT / "apps" / "api" / "requirements-runtime.lock")])
    _run(pip_command)
    _copytree(
        REPOSITORY_ROOT / "apps" / "api" / "local_drama",
        payload / "app" / "local_drama",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    validation_environment = dict(os.environ)
    validation_environment["PYTHONPATH"] = str(payload / "app")
    subprocess.run(
        [str(runtime_python), "-c", "import alembic, fastapi, local_drama, sqlalchemy, uvicorn"],
        cwd=payload,
        env=validation_environment,
        check=True,
    )
    subprocess.run(
        [
            str(runtime_python),
            "-m",
            "compileall",
            "-q",
            "--invalidation-mode",
            "checked-hash",
            "-s",
            str(payload / "app"),
            str(payload / "app"),
        ],
        cwd=payload,
        env=validation_environment,
        check=True,
    )

    _copytree(REPOSITORY_ROOT / "apps" / "web" / "dist", payload / "web")
    _copytree(REPOSITORY_ROOT / "apps" / "api" / "alembic", payload / "migrations")
    _copytree(REPOSITORY_ROOT / "contracts", payload / "contracts")
    shutil.copy2(REPOSITORY_ROOT / "scripts" / "model_platform_release_gate.py", payload / "app" / "model_platform_release_gate.py")
    shutil.copy2(REPOSITORY_ROOT / "release" / "version.json", payload / "version.json")
    if args.ffmpeg_runtime:
        ffmpeg_runtime = Path(args.ffmpeg_runtime).resolve()
        executable = "ffmpeg.exe" if args.platform.startswith("windows") else "ffmpeg"
        probe = "ffprobe.exe" if args.platform.startswith("windows") else "ffprobe"
        if not (ffmpeg_runtime / executable).is_file() or not (ffmpeg_runtime / probe).is_file():
            raise FileNotFoundError("FFmpeg runtime must contain ffmpeg and ffprobe executables")
        _copytree(ffmpeg_runtime, payload / "tools" / "ffmpeg")
    migration_contract = REPOSITORY_ROOT / "docs" / "release" / "migration-contract.json"
    if migration_contract.is_file():
        shutil.copy2(migration_contract, payload / "migration-contract.json")

    _write_sbom(payload, identity, args.platform)

    host_source = Path(args.host).resolve()
    host_name = "local-drama-host.exe" if args.platform.startswith("windows") else "local-drama-host"
    host_target = package_root / "host" / host_name
    host_target.parent.mkdir(parents=True)
    shutil.copy2(host_source, host_target)
    if args.platform.startswith("windows") and not args.launcher:
        raise ValueError("Windows releases require --launcher")
    if args.launcher:
        shutil.copy2(Path(args.launcher).resolve(), package_root / "host" / "local-drama-launcher.exe")

    manifest = _manifest(payload, identity, args.platform, runtime_version)
    (payload / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (package_root / "active-release.json").write_text(
        json.dumps(
            {"schema_version": 1, "active": version, "previous": "", "generation": 1},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.platform.startswith("windows"):
        (package_root / "portable-run.cmd").write_text(
            "@echo off\r\n"
            "set LOCAL_DRAMA_INSTALL_ROOT=%~dp0\r\n"
            "set LOCAL_DRAMA_INSTANCE_ROOT=%LOCALAPPDATA%\\LocalDramaStudio\r\n"
            "\"%~dp0host\\local-drama-launcher.exe\"\r\n",
            encoding="utf-8",
        )
    return package_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble a reproducible Local Drama Studio release")
    parser.add_argument("--platform", required=True, choices=("windows-amd64", "linux-amd64"))
    parser.add_argument("--python-runtime", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--launcher")
    parser.add_argument("--output", default=str(REPOSITORY_ROOT / "dist"))
    parser.add_argument("--wheelhouse")
    parser.add_argument("--ffmpeg-runtime")
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        built = build(parse_args())
    except Exception as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(built)

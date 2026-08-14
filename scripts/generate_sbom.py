"""Generate an offline SPDX package inventory from the locked dependencies.

This command never installs packages or contacts a registry.  It reads the
Python and PNPM lockfiles plus local package metadata when available.  The
result intentionally remains ``release_status=DRAFT`` until the final release
review verifies licenses, provenance, and the complete runtime image.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
PYTHON_LOCK = ROOT / "apps" / "api" / "requirements.lock"
PNPM_LOCK = ROOT / "pnpm-lock.yaml"
WEB_NODE_MODULES = ROOT / "apps" / "web" / "node_modules"
NODE_MODULES = ROOT / "node_modules" / ".pnpm"


def _sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _python_license(name: str) -> str:
    try:
        metadata = importlib.metadata.metadata(name)
    except importlib.metadata.PackageNotFoundError:
        return "NOASSERTION"
    license_name = (metadata.get("License") or "").strip()
    if license_name and license_name.lower() not in {"unknown", "n/a"}:
        return license_name
    classifiers = [value for value in metadata.get_all("Classifier") or [] if value.startswith("License ::")]
    return classifiers[0].removeprefix("License :: ").strip() if classifiers else "NOASSERTION"


def _python_packages() -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for line in PYTHON_LOCK.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        name, separator, version = value.partition("==")
        if not separator:
            continue
        packages.append(
            {
                "SPDXID": f"SPDXRef-pypi-{name.lower().replace('-', '_')}",
                "name": name,
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "licenseConcluded": _python_license(name),
                "licenseDeclared": _python_license(name),
                "sourceInfo": "apps/api/requirements.lock",
                "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": f"pkg:pypi/{name.lower()}@{version}"}],
            }
        )
    return packages


def _split_pnpm_key(key: str) -> tuple[str, str]:
    clean = key.lstrip("/")
    match = re.match(r"(?P<name>@[^@]+|[^@]+)@(?P<version>[^()]+)", clean)
    if not match:
        return clean, "UNKNOWN"
    return match.group("name"), match.group("version")


def _node_package_json(name: str, version: str) -> dict[str, Any] | None:
    direct = WEB_NODE_MODULES / name / "package.json"
    if direct.is_file():
        try:
            package = json.loads(direct.read_text(encoding="utf-8"))
            if str(package.get("version", version)) == version:
                return package
        except (OSError, json.JSONDecodeError):
            pass
    if not NODE_MODULES.is_dir():
        return None
    pnpm_name = name.replace("/", "+")
    candidates = sorted(NODE_MODULES.glob(f"{pnpm_name}@{version}*/node_modules/{name}/package.json"))
    if not candidates:
        return None
    try:
        return json.loads(candidates[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _node_license(name: str, version: str) -> str:
    package = _node_package_json(name, version)
    if not package:
        return "NOASSERTION"
    license_value = package.get("license")
    if isinstance(license_value, str) and license_value.strip():
        return license_value.strip()
    if isinstance(license_value, dict) and isinstance(license_value.get("type"), str):
        return license_value["type"].strip()
    return "NOASSERTION"


def _node_packages() -> list[dict[str, Any]]:
    lock = yaml.safe_load(PNPM_LOCK.read_text(encoding="utf-8"))
    package_entries = lock.get("packages", {}) if isinstance(lock, dict) else {}
    packages: list[dict[str, Any]] = []
    for key, details in sorted(package_entries.items()):
        name, version = _split_pnpm_key(str(key))
        resolution = details.get("resolution", {}) if isinstance(details, dict) else {}
        integrity = resolution.get("integrity") if isinstance(resolution, dict) else None
        package: dict[str, Any] = {
            "SPDXID": f"SPDXRef-npm-{name.replace('/', '_').replace('@', '')}-{version.replace('.', '_')}",
            "name": name,
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "licenseConcluded": _node_license(name, version),
            "licenseDeclared": _node_license(name, version),
            "sourceInfo": "pnpm-lock.yaml",
            "externalRefs": [{"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": f"pkg:npm/{name}@{version}"}],
        }
        if integrity:
            package["checksums"] = [{"algorithm": "SHA-512", "checksumValue": str(integrity).removeprefix("sha512-")}]
        packages.append(package)
    return packages


def generate() -> dict[str, Any]:
    lock_hash = _sha256([PYTHON_LOCK, PNPM_LOCK])
    packages = _python_packages() + _node_packages()
    return {
        "bomFormat": "SPDX",
        "specVersion": "2.3",
        "release_status": "DRAFT",
        "name": "LocalDramaStudio",
        "versionInfo": "unreleased",
        "SPDXID": "SPDXRef-DOCUMENT",
        "documentNamespace": f"https://localdramastudio.local/spdx/{lock_hash}",
        "creationInfo": {"created": datetime.now(UTC).isoformat(), "creators": ["Tool: LocalDramaStudio offline SBOM generator"]},
        "completeness": "LOCKFILES_PLUS_LOCAL_METADATA;FINAL_LICENSE_REVIEW_REQUIRED",
        "comment": "Generated without network access. Package inventory and lockfile integrity are captured, but release_status remains DRAFT until final license/provenance/runtime review.",
        "source_lockfiles": ["apps/api/requirements.lock", "pnpm-lock.yaml"],
        "lockfile_sha256": lock_hash,
        "package_count": len(packages),
        "packages": packages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "release" / "sbom.json")
    args = parser.parse_args()
    result = generate()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": output.relative_to(ROOT).as_posix(), "package_count": result["package_count"], "release_status": result["release_status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

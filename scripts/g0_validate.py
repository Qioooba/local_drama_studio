"""Read-only G0 gate validator.

Default (portable) mode validates the versioned in-repo contract
``docs/release/g0-contract.json``: required code/contract/migration/test/lock
components, versioned schemas, the API contract version, the committed
OpenAPI/TS generator output, the alembic head graph, and the declared quality
gates.  It needs nothing outside the repository, so a clean checkout in any
directory can run it.

The former external blueprint + ``model_manifest.json`` audit is retained as an
opt-in extended mode (``--blueprint-root`` and ``--manifest``).  When those
inputs are not supplied the extended audit reports ``NOT_CONFIGURED`` and points
at the licensed local UAT path; it never fabricates a PASS.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from g0_validate_core import (
    DEFAULT_SPEC,
    NOT_CONFIGURED,
    REPO_ROOT,
    G0ValidationError,
    ValidationReport,
    audit_blueprint,
    validate_repository,
)


def _print_report(report: ValidationReport) -> None:
    print(f"mode={report.mode}")
    for check in report.checks:
        print(f"{check.code}={check.status} ({check.detail})")
        for finding in check.findings:
            print(f"  - {finding}")
    print(f"g0_status={report.status}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository root to validate (default: the root containing this script)",
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC, help="in-repo G0 contract specification")
    parser.add_argument(
        "--no-verify-generator",
        action="store_true",
        help="skip re-running scripts/generate_client.py --check (structural artifact checks still run)",
    )
    parser.add_argument(
        "--blueprint-root",
        type=Path,
        default=None,
        help="OPT-IN extended audit: directory holding LocalDramaStudio_Blueprint_v2 documents",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="OPT-IN extended audit: real model_manifest.json path",
    )
    args = parser.parse_args(argv)
    if (args.blueprint_root is None) != (args.manifest is None):
        parser.error("the extended blueprint audit needs BOTH --blueprint-root and --manifest, or neither")
    try:
        report = validate_repository(args.repo_root.resolve(), args.spec, verify_generator=not args.no_verify_generator)
        if args.blueprint_root is not None or args.manifest is not None:
            blueprint = audit_blueprint(args.blueprint_root, args.manifest, args.spec)
            report = ValidationReport(mode=f"{report.mode}+{blueprint.mode}", checks=report.checks + blueprint.checks)
    except G0ValidationError as error:
        print("g0_status=FAIL")
        print(f"  - {error}")
        return 1
    _print_report(report)
    if report.status == NOT_CONFIGURED:
        return 2
    return 1 if report.status == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())

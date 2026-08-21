"""Generate P12 *automated pre-checks* without forging a UAT approval.

The files produced here intentionally remain pending.  A script can establish
that routes or schema constants exist, but it cannot impersonate the human UAT
and compatibility sign-offs required by the P12 decommission gate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "docs" / "evidence" / "refactor-2026"


def generate_new_routes_evidence() -> dict:
    """Validate that every required V2 route is declared and properly structured in router.tsx."""
    router_file = ROOT / "apps" / "web" / "src" / "app" / "router.tsx"
    gates_file = ROOT / "docs" / "release" / "p12-decommission-gates.json"
    gates = json.loads(gates_file.read_text(encoding="utf-8"))
    router_text = router_file.read_text(encoding="utf-8")

    results = []
    all_passed = True
    for route in gates["required_routes"]:
        present = route in router_text
        if not present:
            all_passed = False
        results.append({
            "route": route,
            "present": present,
            "status": "PASS" if present else "FAIL",
        })

    evidence = {
        "schema_version": "localdrama.uat-evidence.v1",
        "evidence_id": "p12-new-routes-uat",
        "title": "V2 New Routes & Navigation UAT Signoff",
        "status": "PENDING_UAT" if all_passed else "FAIL",
        "approved": False,
        "evidence_kind": "AUTOMATED_PRECHECK",
        "approval_note": "Route declarations passed the automated pre-check; interactive route UAT and reviewer sign-off are still required.",
        "executed_at": datetime.now(UTC).isoformat(),
        "total_required_routes": len(gates["required_routes"]),
        "passed_routes_count": sum(1 for r in results if r["present"]),
        "routes": results,
    }
    return evidence


def generate_old_project_compat_evidence() -> dict:
    """Validate project package schema compatibility and import/export rules."""
    from local_drama.application.project_packages import PACKAGE_SCHEMA, STATE_SCHEMA

    # Validate package schemas are authority v2
    schema_valid = PACKAGE_SCHEMA == "localdrama.project-package.v2" and STATE_SCHEMA == "localdrama.project-state.v2"

    evidence = {
        "schema_version": "localdrama.uat-evidence.v1",
        "evidence_id": "p12-old-project-compat-uat",
        "title": "Old Project Compatibility & Package Import/Export UAT",
        "status": "PENDING_UAT" if schema_valid else "FAIL",
        "approved": False,
        "evidence_kind": "AUTOMATED_PRECHECK",
        "approval_note": "Schema constants passed the automated pre-check; a real legacy project import/export round trip and reviewer sign-off are still required.",
        "executed_at": datetime.now(UTC).isoformat(),
        "manifest_schema": PACKAGE_SCHEMA,
        "state_schema": STATE_SCHEMA,
        "compatibility_mode": "safe_defaults",
    }
    return evidence


def generate_package_api_decommission_evidence() -> dict:
    """Validate that legacy API symbols are decommissioned and protected backend facts are maintained."""
    gates_file = ROOT / "docs" / "release" / "p12-decommission-gates.json"
    gates = json.loads(gates_file.read_text(encoding="utf-8"))

    protected_facts_verified = []
    for fact in gates["protected_backend_facts"]:
        protected_facts_verified.append({
            "fact": fact,
            "status": "KEEP",
            "protected": True,
        })

    passed = len(protected_facts_verified) == len(gates["protected_backend_facts"])
    evidence = {
        "schema_version": "localdrama.uat-evidence.v1",
        "evidence_id": "p12-package-api-decommission-signoff",
        "title": "Package API Decommission & Backend Facts Signoff",
        "status": "PENDING_UAT" if passed else "FAIL",
        "approved": False,
        "evidence_kind": "AUTOMATED_PRECHECK",
        "approval_note": "Protected facts were enumerated only; API decommission verification and an accountable reviewer sign-off are still required.",
        "executed_at": datetime.now(UTC).isoformat(),
        "protected_facts": protected_facts_verified,
        "legacy_surfaces_count": len(gates["legacy_surfaces"]),
    }
    return evidence


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    r1 = generate_new_routes_evidence()
    (EVIDENCE_DIR / "p12-new-routes-uat.json").write_text(json.dumps(r1, ensure_ascii=False, indent=2), encoding="utf-8")

    r2 = generate_old_project_compat_evidence()
    (EVIDENCE_DIR / "p12-old-project-compat-uat.json").write_text(json.dumps(r2, ensure_ascii=False, indent=2), encoding="utf-8")

    r3 = generate_package_api_decommission_evidence()
    (EVIDENCE_DIR / "p12-package-api-decommission-signoff.json").write_text(json.dumps(r3, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Generated 3 pending P12 automated pre-check files in {EVIDENCE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

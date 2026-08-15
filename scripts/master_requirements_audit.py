"""Report evidence-backed closure against every release FR, NFR and named TC."""

from __future__ import annotations

import json

from release_audit import ROOT, _requirements_mapping_summary


def main() -> None:
    summary = _requirements_mapping_summary(ROOT / "docs" / "evidence" / "g10" / "master-requirements-map.json")
    report = {
        "status": "PASS" if not summary["missing_fr"] and not summary["missing_nfr"] and not summary["missing_tc"] and summary["valid"] else "IN_PROGRESS",
        "mapping_valid": summary["valid"],
        "problems": summary["problems"],
        "verified": {
            "release_fr": len(summary["passed_fr"]),
            "nfr": len(summary["passed_nfr"]),
            "tc": len(summary["passed_tc"]),
        },
        "required": {"release_fr": 84, "nfr": 15, "tc": 85},
        "missing": {
            "release_fr": sorted(summary["missing_fr"]),
            "nfr": sorted(summary["missing_nfr"]),
            "tc": sorted(summary["missing_tc"]),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

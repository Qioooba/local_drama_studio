from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("generate_sbom", ROOT / "scripts" / "generate_sbom.py")
assert SPEC and SPEC.loader
generate_sbom = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_sbom)


def test_target_applicability_rejects_other_platform_optional_packages() -> None:
    assert generate_sbom._node_target_applicability({"os": ["linux"], "cpu": ["x64"]}) == "LOCK_ONLY_NON_TARGET_PLATFORM"
    assert generate_sbom._node_target_applicability({"os": ["win32"], "cpu": ["arm64"]}) == "LOCK_ONLY_NON_TARGET_PLATFORM"
    assert generate_sbom._node_target_applicability({"os": ["win32"], "cpu": ["x64"]}) == "TARGET_RUNTIME"
    assert generate_sbom._node_target_applicability({}) == "TARGET_RUNTIME"


def test_generated_sbom_has_no_unresolved_target_runtime_license() -> None:
    document = generate_sbom.generate()
    summary = document["license_summary"]
    assert document["release_status"] == "DRAFT"
    assert summary["noassertion_total"] > 0
    assert summary["noassertion_total"] == summary["noassertion_lock_only_non_target_platform"]
    assert summary["noassertion_target_runtime"] == 0

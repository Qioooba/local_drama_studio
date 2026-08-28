"""Run read-only maintainability and regression-contract checks.

The blueprint treats NFR-MAINT-001 and NFR-TEST-001 as release requirements,
but a static audit cannot replace Windows/browser UAT.  This command therefore
checks the part that is deterministic in source control (layer imports, UI
component size, and domain-test inventory) and records the remaining gates as
explicitly pending.  It never starts the API, worker, ComfyUI, or a model.

Exit status is non-zero only for deterministic contract failures:

* a framework/infrastructure import from ``domain``;
* a non-generated React component over 700 lines; or
* an uncovered domain module (the P0 domain-rule regression floor).

Application-to-infrastructure imports are reported as warnings.  The current
application uses concrete SQLite/adapter implementations while the migration
to explicit ports is still in progress; hiding that debt would make the audit
misleading.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "apps" / "web" / "src"
API_SRC = ROOT / "apps" / "api" / "local_drama"
API_TESTS = ROOT / "apps" / "api" / "tests"

UI_RECOMMENDED_LINES = 500
UI_WARNING_LINES = 700


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _python_files(root: Path) -> Iterable[Path]:
    return (path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> list[str]:
    """Return import roots without importing application code."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return []
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.append(node.module)
    return values


def _scan_dependencies() -> dict[str, Any]:
    forbidden_domain_prefixes = (
        "fastapi",
        "sqlalchemy",
        "pydantic",
        "local_drama.api",
        "local_drama.infrastructure",
        "local_drama.config",
    )
    domain_forbidden: list[dict[str, str]] = []
    application_infrastructure: list[dict[str, str]] = []
    route_infrastructure: list[dict[str, str]] = []
    application_root = API_SRC / "application"
    domain_root = API_SRC / "domain"
    route_root = API_SRC / "api" / "routes"
    for path in _python_files(domain_root):
        for module in _imports(path):
            if module == "" or module.startswith("."):
                continue
            if module.startswith(forbidden_domain_prefixes):
                domain_forbidden.append({"file": _relative(path), "import": module})
    for path in _python_files(application_root):
        for module in _imports(path):
            if module.startswith("local_drama.infrastructure"):
                application_infrastructure.append({"file": _relative(path), "import": module})
    for path in _python_files(route_root):
        for module in _imports(path):
            if module.startswith("local_drama.infrastructure"):
                route_infrastructure.append({"file": _relative(path), "import": module})
    return {
        "rules": {
            "domain_forbidden_imports": list(forbidden_domain_prefixes),
            "application_may_not_reach_concrete_infrastructure": True,
            "routes_may_not_reach_concrete_infrastructure": True,
        },
        "domain_forbidden_imports": domain_forbidden,
        "application_infrastructure_warnings": application_infrastructure,
        "route_infrastructure_warnings": route_infrastructure,
        "domain_boundary_passed": not domain_forbidden,
        "concrete_infrastructure_boundary_passed": not application_infrastructure and not route_infrastructure,
    }


_COMPONENT_DECLARATION = re.compile(
    r"^(?:export\s+)?(?:default\s+)?function\s+([A-Z][A-Za-z0-9_]*)\b"
)


def _component_spans(path: Path) -> list[tuple[str, int]]:
    """Measure top-level React functions instead of treating a primitives barrel as one component."""

    lines = path.read_text(encoding="utf-8").splitlines()
    declarations = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := _COMPONENT_DECLARATION.match(line))
    ]
    if not declarations:
        return [(path.stem, len(lines))]
    spans: list[tuple[str, int]] = []
    for position, (start, name) in enumerate(declarations):
        end = declarations[position + 1][0] if position + 1 < len(declarations) else len(lines)
        spans.append((name, end - start))
    return spans


def _scan_components() -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    for path in sorted(WEB_SRC.rglob("*.tsx")):
        # Generated API and tests are not page components.  The generated file
        # is intentionally large and is checked by client generation instead.
        if "generated" in path.parts or path.name.endswith(".test.tsx"):
            continue
        for component_name, line_count in _component_spans(path):
            components.append(
                {
                    "path": _relative(path),
                    "component": component_name,
                    "lines": line_count,
                    "recommended_limit_exceeded": line_count > UI_RECOMMENDED_LINES,
                    "warning_limit_exceeded": line_count > UI_WARNING_LINES,
                }
            )
    recommended = [item for item in components if item["recommended_limit_exceeded"]]
    warnings = [item for item in components if item["warning_limit_exceeded"]]
    return {
        "scope": "apps/web/src/**/*.tsx excluding generated client and tests",
        "recommended_limit_lines": UI_RECOMMENDED_LINES,
        "warning_limit_lines": UI_WARNING_LINES,
        "component_count": len(components),
        "largest_components": sorted(components, key=lambda item: item["lines"], reverse=True)[:10],
        "components_needing_split": recommended,
        "components_over_warning_limit": warnings,
        "size_contract_passed": not warnings,
    }


def _scan_domain_test_inventory() -> dict[str, Any]:
    domain_modules = sorted(
        path
        for path in _python_files(API_SRC / "domain")
        if path.name != "__init__.py"
    )
    test_sources = list(_python_files(API_TESTS))
    test_text = "\n".join(path.read_text(encoding="utf-8") for path in test_sources)
    covered: list[str] = []
    uncovered: list[str] = []
    for path in domain_modules:
        module_name = path.stem
        # Import-based inventory is deliberately conservative: a test that
        # imports a domain module is a signal that its public rules are in the
        # automated test pyramid.  Behavioural assertions still live in pytest.
        token = f"local_drama.domain.{module_name}"
        item = {"module": _relative(path), "import_token": token}
        if token in test_text:
            covered.append(item["module"])
        else:
            uncovered.append(item["module"])
    test_functions = 0
    for path in test_sources:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        test_functions += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        )
    return {
        "domain_module_count": len(domain_modules),
        "domain_modules_covered_by_tests": covered,
        "domain_modules_without_direct_test_import": uncovered,
        "domain_rule_coverage_floor_passed": not uncovered,
        "api_test_file_count": len(test_sources),
        "api_test_function_count": test_functions,
        "web_test_file_count": len(list(WEB_SRC.rglob("*.test.tsx"))) + len(list(WEB_SRC.rglob("*.test.ts"))),
        "coverage_method": "static import inventory; full pytest/Vitest and Playwright remain separate gates",
    }


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def audit() -> dict[str, Any]:
    dependencies = _scan_dependencies()
    components = _scan_components()
    domain_tests = _scan_domain_test_inventory()
    hard_failures: list[str] = []
    if not dependencies["domain_boundary_passed"]:
        hard_failures.append("domain imports framework/concrete infrastructure")
    if not components["size_contract_passed"]:
        hard_failures.append("non-generated React component exceeds 700 lines")
    if not domain_tests["domain_rule_coverage_floor_passed"]:
        hard_failures.append("one or more domain modules have no direct automated test import")
    deterministic_passed = not hard_failures
    # A release PASS additionally needs Windows x64 browser/UAT and a complete
    # full-chain run.  This command intentionally records those as pending.
    status = "PARTIAL" if deterministic_passed else "BLOCKED"
    return {
        "schema_version": "nfr.maintainability.regression.audit.v1",
        "requirement_ids": ["NFR-MAINT-001", "NFR-TEST-001"],
        "status": status,
        "observed_at": datetime.now(UTC).isoformat(),
        "git_revision": _git_revision(),
        "deterministic_contracts_passed": deterministic_passed,
        "hard_failures": hard_failures,
        "dependencies": dependencies,
        "ui_component_size": components,
        "domain_test_inventory": domain_tests,
        "regression_gate": {
            "full_local_gate_command": "scripts/check.ps1",
            "full_local_gate_status": "NOT_RUN_BY_AUDIT",
            "playwright_core_path_status": "PENDING_WINDOWS_X64_UAT",
            "note": "Run scripts/check.ps1 and the Windows x64 three-viewport Playwright path before release PASS; this read-only audit does not substitute for either.",
        },
        "safety": {
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
            "jobs_created": False,
        },
        "interpretation": "PARTIAL means deterministic source/test checks are green but complete NFR closure still requires the full local gate and Windows/browser UAT. Application concrete-infrastructure imports are reported as migration warnings, not hidden.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "evidence" / "g10" / "nfr-maint-test-audit-2026-08-16.json",
    )
    args = parser.parse_args()
    result = audit()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": _relative(output), "hard_failures": result["hard_failures"]}, ensure_ascii=False))
    if result["hard_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

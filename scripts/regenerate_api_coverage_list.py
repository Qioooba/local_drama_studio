"""Regenerate the docs/gpt6 API coverage list from the live application.

The delivered coverage list (631 operations) is a trustworthy inventory, but its
per-operation source line references drift as the code changes.  This script
rebuilds the table from two authoritative sources instead of from memory:

* the running FastAPI application's OpenAPI document, for the method, path and
  ``operationId`` of every operation;
* an AST scan of ``apps/api/local_drama`` for the decorator line that declares
  each ``operation_id``, for the source location.

Usage::

    python scripts/regenerate_api_coverage_list.py
    python scripts/regenerate_api_coverage_list.py --check      # report drift only
    python scripts/regenerate_api_coverage_list.py --output <path>

``--check`` never writes; it exits 1 when the committed list disagrees with the
live contract, so the documentation cannot silently fall behind the code.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
API_SRC = REPO_ROOT / "apps" / "api"
DEFAULT_TARGET = REPO_ROOT / "docs" / "gpt6" / "LocalDramaStudio_接口覆盖清单_2026-09-22.md"
BASELINE_COMMIT = "8a63c604a1a13556dbe277d312ecf73bebb52883"

_ROW_RE = re.compile(
    r"^\|\s*(GET|POST|PUT|DELETE|PATCH|HEAD)\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|([^|]*)\|\s*`([^`]+)`\s*\|",
    re.MULTILINE,
)
_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head")


def live_operations() -> dict[tuple[str, str], str]:
    """Return ``{(METHOD, path): operationId}`` from the running application."""
    sys.path.insert(0, str(API_SRC))
    from local_drama.config import Settings
    from local_drama.main import create_app

    app = create_app(Settings())
    spec = app.openapi()
    operations: dict[tuple[str, str], str] = {}
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            operations[(method.upper(), path)] = str(operation.get("operationId") or "")
    return operations


def operation_locations() -> dict[str, tuple[str, int]]:
    """Map ``operation_id`` -> ``(repo-relative path, decorator line)`` via AST."""
    locations: dict[str, tuple[str, int]] = {}
    for source in sorted(API_SRC.rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                if call is None:
                    continue
                operation_id = next(
                    (
                        keyword.value.value
                        for keyword in call.keywords
                        if keyword.arg == "operation_id" and isinstance(keyword.value, ast.Constant)
                    ),
                    None,
                )
                if operation_id:
                    line = int(getattr(decorator, "lineno", node.lineno))
                    locations[str(operation_id)] = (
                        source.relative_to(REPO_ROOT).as_posix(),
                        line,
                    )
    return locations


def parse_committed(path: Path) -> list[tuple[str, str, str, str, str]]:
    return _ROW_RE.findall(path.read_text(encoding="utf-8"))


def build_report() -> dict[str, Any]:
    live = live_operations()
    locations = operation_locations()
    rows: list[dict[str, Any]] = []
    missing_location: list[str] = []
    for (method, route), operation_id in sorted(live.items(), key=lambda item: (item[0][1], item[0][0])):
        location = locations.get(operation_id)
        if location is None:
            missing_location.append(operation_id)
        rows.append(
            {
                "method": method,
                "path": route,
                "operation_id": operation_id,
                "source": f"{location[0]}:{location[1]}" if location else "",
            }
        )
    return {
        "schema_version": "localdrama.api-coverage-report.v1",
        "baseline_commit": BASELINE_COMMIT,
        "operation_count": len(rows),
        "methods": {method: sum(1 for row in rows if row["method"] == method) for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD")},
        "missing_source_location": missing_location,
        "operations": rows,
    }


def drift(committed: list[tuple[str, str, str, str, str]], report: dict[str, Any]) -> dict[str, Any]:
    committed_index = {(row[0], row[1]): row[2] for row in committed}
    live_index = {(row["method"], row["path"]): row["operation_id"] for row in report["operations"]}
    committed_locations = {row[2]: row[4] for row in committed}
    live_locations = {row["operation_id"]: row["source"] for row in report["operations"]}
    return {
        "committed_rows": len(committed),
        "live_operations": len(live_index),
        "documented_but_absent": sorted(set(committed_index) - set(live_index)),
        "absent_from_document": sorted(set(live_index) - set(committed_index)),
        "operation_id_mismatch": sorted(
            (key, committed_index[key], live_index[key])
            for key in set(committed_index) & set(live_index)
            if committed_index[key] != live_index[key]
        ),
        "stale_source_lines": sorted(
            (operation_id, committed_locations[operation_id], live_locations[operation_id])
            for operation_id in set(committed_locations) & set(live_locations)
            if committed_locations[operation_id] != live_locations[operation_id]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET, help="coverage list markdown to inspect")
    parser.add_argument("--output", type=Path, default=None, help="write the machine-readable report here")
    parser.add_argument("--check", action="store_true", help="report drift and exit 1 instead of writing")
    args = parser.parse_args()

    report = build_report()
    result = drift(parse_committed(args.target), report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({**report, "drift": result}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report={args.output}")

    print(f"documented_rows={result['committed_rows']} live_operations={result['live_operations']}")
    print(f"documented_but_absent={len(result['documented_but_absent'])}")
    print(f"absent_from_document={len(result['absent_from_document'])}")
    for item in result["absent_from_document"]:
        print(f"  missing from list: {item[0]} {item[1]}")
    print(f"operation_id_mismatch={len(result['operation_id_mismatch'])}")
    print(f"stale_source_lines={len(result['stale_source_lines'])}")
    if report["missing_source_location"]:
        print(f"operations_without_a_source_location={len(report['missing_source_location'])}")

    if args.check and (
        result["documented_but_absent"]
        or result["absent_from_document"]
        or result["operation_id_mismatch"]
        or result["stale_source_lines"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

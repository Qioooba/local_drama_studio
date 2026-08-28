"""Inventory concrete legacy dependencies without treating them as architecture.

The committed manifest is an allow-list: existing entries may be removed, but
new entries fail the guard until they have an owner and removal slice.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api" / "local_drama"
MANIFEST_PATH = ROOT / "docs" / "architecture" / "legacy-debt-manifest.json"


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _returns_raw_response(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    annotation = node.returns
    if isinstance(annotation, ast.Name):
        return annotation.id in {"FileResponse", "StreamingResponse", "Response"}
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        return annotation.value.id in {"FileResponse", "StreamingResponse", "Response"}
    return False


def audit() -> dict[str, Any]:
    concrete_database: list[dict[str, Any]] = []
    service_construction: list[dict[str, Any]] = []
    routes_without_response_model: list[dict[str, Any]] = []

    for path in sorted((API_ROOT / "application").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "local_drama.infrastructure.database.sqlite" and any(alias.name == "Database" for alias in node.names):
                concrete_database.append({"id": f"{_relative(path)}:{node.lineno}", "file": _relative(path), "line": node.lineno, "owner": "legacy-application", "remove_by_slice": 8})
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.endswith("Service") and node.func.id != path.stem.title().replace("_", ""):
                service_construction.append({"id": f"{_relative(path)}:{node.lineno}:{node.func.id}", "file": _relative(path), "line": node.lineno, "service": node.func.id, "owner": "legacy-application", "remove_by_slice": 8})

    for path in sorted((API_ROOT / "api" / "routes").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _returns_raw_response(node):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "router" or decorator.func.attr not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if any(keyword.arg == "response_model" for keyword in decorator.keywords):
                    continue
                operation_id = next((keyword.value.value for keyword in decorator.keywords if keyword.arg == "operation_id" and isinstance(keyword.value, ast.Constant)), node.name)
                routes_without_response_model.append({"id": f"{_relative(path)}:{operation_id}", "file": _relative(path), "line": node.lineno, "operation_id": operation_id, "owner": "legacy-api", "remove_by_slice": 8})

    return {
        "schema_version": "localdrama.architecture-debt.v1",
        "policy": "allow-list; entries may be removed but no new id may be introduced",
        "categories": {
            "concrete_database_dependencies": concrete_database,
            "cross_service_construction": service_construction,
            "routes_without_response_model": routes_without_response_model,
        },
    }


def main() -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(audit(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST_PATH}")


if __name__ == "__main__":
    main()

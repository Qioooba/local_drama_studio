"""P0-03 architecture boundaries for the V2 refactor.

New application layers (commands / queries / orchestration) must depend on
ports, never on concrete infrastructure.  This test hard-fails the new
directories so the migration debt in legacy application modules cannot
silently spread into the new code structure.
"""

from __future__ import annotations

import ast
from pathlib import Path

API_SRC = Path(__file__).resolve().parents[1] / "local_drama"

# Modules that belong to the new bounded application structure.
NEW_APPLICATION_DIRS = ("application/commands", "application/queries", "application/orchestration")

# Concrete infrastructure roots that new application code must not import.
FORBIDDEN_INFRASTRUCTURE_ROOTS = (
    "local_drama.infrastructure.database",
    "local_drama.infrastructure.comfy",
    "local_drama.infrastructure.manifest",
    "local_drama.infrastructure.adapters",
    "local_drama.infrastructure.local_http",
    "local_drama.infrastructure.local_llm",
)

# Direct application-module imports of concrete DB/runtime singletons.
FORBIDDEN_APPLICATION_MODULES = (
    "local_drama.application.comfy_jobs",
    "local_drama.application.comfy_lab",
)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> list[str]:
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


def _new_application_files() -> list[Path]:
    files: list[Path] = []
    for relative in NEW_APPLICATION_DIRS:
        root = API_SRC / relative
        if root.exists():
            files.extend(_python_files(root))
    return files


def test_new_application_dirs_never_import_concrete_infrastructure() -> None:
    violations: list[tuple[str, str]] = []
    for path in _new_application_files():
        for module in _imports(path):
            if module.startswith(FORBIDDEN_INFRASTRUCTURE_ROOTS) or module in FORBIDDEN_APPLICATION_MODULES:
                violations.append((path.relative_to(API_SRC).as_posix(), module))
    assert violations == [], (
        "New application/commands|queries|orchestration must depend on ports, not concrete infrastructure; "
        f"violations: {violations}"
    )


def test_new_application_dirs_never_import_fastapi() -> None:
    violations: list[tuple[str, str]] = []
    for path in _new_application_files():
        for module in _imports(path):
            if module == "fastapi" or module.startswith("fastapi."):
                violations.append((path.relative_to(API_SRC).as_posix(), module))
    assert violations == [], (
        "Application layer must stay web-framework-free; HTTP concerns live in api/routes. "
        f"violations: {violations}"
    )


def test_new_application_dirs_do_not_import_legacy_application_monoliths() -> None:
    """New command/query code must not reach into legacy application services.

    This prevents the new structure from silently growing dependencies on the
    monolithic services that still hold concrete SQLite usage.
    """

    violations: list[tuple[str, str]] = []
    for path in _new_application_files():
        for module in _imports(path):
            if module.startswith("local_drama.application.") and not module.startswith(
                ("local_drama.application.commands", "local_drama.application.queries", "local_drama.application.orchestration", "local_drama.application.ports")
            ):
                violations.append((path.relative_to(API_SRC).as_posix(), module))
    assert violations == [], f"New application layers must compose via ports; violations: {violations}"

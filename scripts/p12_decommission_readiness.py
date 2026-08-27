"""Read-only P12 decommission readiness audit.

The audit proves why legacy surfaces must stay or may be removed. It never
edits source, evidence, packages, APIs, or databases.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "docs" / "release" / "p12-decommission-gates.json"
STATIC_IMPORT_RE = re.compile(r"\bimport(?!\s*\()\s+(?P<clause>[^;]+?)\s+from\s+[\"'](?P<specifier>[^\"']+)[\"']", re.MULTILINE)
DYNAMIC_IMPORT_RE = re.compile(r"\bimport\s*\(\s*[\"'](?P<specifier>[^\"']+)[\"']\s*\)")
# Only root-query URLs belong to the retired ``/?view=...`` contract. V2 pages
# legitimately use ``?view=`` for tabs such as timeline/export and settings.
QUERY_RE = re.compile(r"[\"'`]/\?[^\"'`\n]*\bview=")
CONTEXT_KEYS = ("project", "episode", "shot")


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _resolve_import(source: Path, specifier: str) -> list[Path]:
    if not specifier.startswith("."):
        return []
    base = (source.parent / specifier).resolve()
    return [base.with_suffix(ext) for ext in (".ts", ".tsx", ".js", ".jsx")] + [
        base / f"index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx")
    ]


def _typescript_files(root: Path) -> list[Path]:
    web = root / "apps" / "web" / "src"
    return sorted((*web.rglob("*.ts"), *web.rglob("*.tsx"))) if web.exists() else []


def _import_records(source: Path) -> list[dict[str, Any]]:
    text = source.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for match in STATIC_IMPORT_RE.finditer(text):
        records.append({
            "specifier": match.group("specifier"),
            "line": text.count("\n", 0, match.start()) + 1,
            "kind": "static",
            "clause": " ".join(match.group("clause").split()),
        })
    for match in DYNAMIC_IMPORT_RE.finditer(text):
        records.append({
            "specifier": match.group("specifier"),
            "line": text.count("\n", 0, match.start()) + 1,
            "kind": "dynamic",
            "clause": "",
        })
    return records


def _surface_callers(root: Path, target: Path, sources: list[Path]) -> list[dict[str, Any]]:
    resolved_target = target.resolve()
    callers: list[dict[str, Any]] = []
    for source in sources:
        if source.resolve() == resolved_target or source.name.endswith(".test.tsx") or source.name.endswith(".test.ts"):
            continue
        for record in _import_records(source):
            if resolved_target in _resolve_import(source, record["specifier"]):
                callers.append({"path": _relative(root, source), "line": record["line"], "import": record["specifier"], "kind": record["kind"]})
    return callers


def _entrypoint_app_imports(root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    app = (root / "apps" / "web" / "src" / "app" / "App.tsx").resolve()
    violations = []
    for relative in config.get("legacy_app_forbidden_importers", []):
        source = root / relative
        if not source.is_file():
            continue
        for record in _import_records(source):
            if app in _resolve_import(source, record["specifier"]):
                violations.append({"path": relative, "line": record["line"], "kind": record["kind"], "import": record["specifier"]})
    return violations


def _root_legacy_mount(root: Path) -> dict[str, Any]:
    """Conservatively prove whether the root route can reach App.tsx through imports."""
    router = root / "apps" / "web" / "src" / "app" / "router.tsx"
    app = (root / "apps" / "web" / "src" / "app" / "App.tsx").resolve()
    if not router.is_file():
        return {"root_component": None, "can_mount_legacy_shell": False, "import_chain": []}
    text = router.read_text(encoding="utf-8")
    match = re.search(r"path\s*:\s*[\"']/[\"'][\s\S]{0,500}?element\s*:\s*<([A-Za-z_$][\w$]*)", text)
    component = match.group(1) if match else None
    root_targets: list[Path] = []
    for record in _import_records(router):
        if component and re.search(rf"\b{re.escape(component)}\b", record["clause"]):
            root_targets.extend(candidate.resolve() for candidate in _resolve_import(router, record["specifier"]) if candidate.is_file())
    if component == "App":
        root_targets.append(app)
    queue: list[tuple[Path, list[str]]] = [
        (target, [_relative(root, router), _relative(root, target)]) for target in root_targets
    ]
    seen: set[Path] = set()
    while queue:
        source, chain = queue.pop(0)
        if source == app:
            return {"root_component": component, "can_mount_legacy_shell": True, "import_chain": chain}
        if source in seen or not source.is_file():
            continue
        seen.add(source)
        for record in _import_records(source):
            for candidate in _resolve_import(source, record["specifier"]):
                if not candidate.is_file():
                    continue
                next_chain = [*chain, _relative(root, candidate)]
                if candidate.resolve() == app:
                    return {"root_component": component, "can_mount_legacy_shell": True, "import_chain": next_chain}
                queue.append((candidate.resolve(), next_chain))
    return {"root_component": component, "can_mount_legacy_shell": False, "import_chain": []}


def _legacy_query_context_losses(root: Path) -> list[dict[str, Any]]:
    """Flag legacy view redirects that cannot carry an incoming context key onward."""
    path = root / "apps" / "web" / "src" / "app" / "legacyRoute.tsx"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    starts = list(re.finditer(r"if\s*\(\s*view\s*===\s*[\"']([^\"']+)[\"']", text))
    losses: list[dict[str, Any]] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else text.find("return null", match.end())
        body = text[match.start() : end if end >= 0 else len(text)]
        # A null return keeps the original URL/query in the legacy shell and is not a redirect loss.
        if not re.search(r"return\s+(?:<Navigate|[\"'`/])", body):
            continue
        for key in CONTEXT_KEYS:
            key_is_referenced = bool(
                re.search(rf"\b{key}Id\b", body)
                or re.search(rf"(?:params|searchParams)\.get\(\s*[\"']{key}[\"']\s*\)", body)
                or re.search(rf"(?:params|searchParams)\.set\(\s*[\"']{key}[\"']", body)
            )
            if not key_is_referenced:
                losses.append({
                    "view": match.group(1),
                    "context_key": key,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "reason": "redirect_does_not_reference_incoming_context",
                })
    return losses


def _app_exclusive_components(root: Path, sources: list[Path]) -> list[dict[str, Any]]:
    app = root / "apps" / "web" / "src" / "app" / "App.tsx"
    if not app.is_file():
        return []
    imported_components: list[dict[str, Any]] = []
    for record in _import_records(app):
        clause = record["clause"]
        symbols = re.findall(r"\b[A-Z][A-Za-z0-9_$]*\b", clause)
        if not symbols:
            continue
        targets = [candidate for candidate in _resolve_import(app, record["specifier"]) if candidate.is_file()]
        if not targets or not any(part in {"features", "components"} for part in targets[0].parts):
            continue
        imported_components.append({"record": record, "target": targets[0].resolve(), "symbols": symbols})

    caller_index: dict[Path, list[dict[str, Any]]] = {item["target"]: [] for item in imported_components}
    for source in sources:
        if source.resolve() == app.resolve() or ".test." in source.name:
            continue
        for record in _import_records(source):
            for candidate in _resolve_import(source, record["specifier"]):
                target = candidate.resolve()
                if target in caller_index:
                    caller_index[target].append({
                        "path": _relative(root, source),
                        "line": record["line"],
                        "import": record["specifier"],
                        "kind": record["kind"],
                    })

    inventory = []
    for item in imported_components:
        record = item["record"]
        target = item["target"]
        non_app_callers = caller_index[target]
        if not non_app_callers:
            inventory.append({
                "module": _relative(root, target),
                "symbols": item["symbols"],
                "actual_callers": [{"path": _relative(root, app), "line": record["line"], "kind": record["kind"]}],
            })
    return inventory


def _token_references(root: Path, token: str, sources: list[Path], excluded: set[Path]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for source in sources:
        if source.resolve() in excluded or ".test." in source.name:
            continue
        for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(rf"\b{re.escape(token)}\s*\(", line):
                references.append({"path": _relative(root, source), "line": line_no})
    return references


def _uat_gate(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        return {"path": relative, "passed": False, "reason": "missing"}
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"path": relative, "passed": False, "reason": "invalid_json"}
    passed = evidence.get("status") == "PASS" and evidence.get("approved") is True
    return {"path": relative, "passed": passed, "reason": "pass" if passed else "requires_PASS_and_approved_true"}


def audit(root: Path = ROOT, config_path: Path | None = None) -> dict[str, Any]:
    config_file = config_path or root / CONFIG.relative_to(ROOT)
    config = json.loads(config_file.read_text(encoding="utf-8"))
    sources = _typescript_files(root)
    router_path = root / "apps" / "web" / "src" / "app" / "router.tsx"
    router_text = router_path.read_text(encoding="utf-8") if router_path.is_file() else ""
    routes = [{"route": route, "present": route in router_text} for route in config["required_routes"]]
    entrypoint_app_imports = _entrypoint_app_imports(root, config)
    root_legacy_mount = _root_legacy_mount(root)
    legacy_query_context_losses = _legacy_query_context_losses(root)
    app_exclusive_components = _app_exclusive_components(root, sources)

    surfaces = []
    target_paths: set[Path] = set()
    for item in config["legacy_surfaces"]:
        target = root / item["path"]
        target_paths.add(target.resolve())
        callers = _surface_callers(root, target, sources) if target.is_file() else []
        surfaces.append({**item, "exists": target.is_file(), "caller_count": len(callers), "callers": callers})

    query_links = []
    for source in sources:
        if ".test." in source.name:
            continue
        for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if QUERY_RE.search(line):
                query_links.append({"path": _relative(root, source), "line": line_no})

    generated = (root / "apps" / "web" / "src" / "generated" / "api.ts").resolve()
    api_symbols = []
    for item in config["legacy_api_symbols"]:
        refs = _token_references(root, item["symbol"], sources, {generated})
        api_symbols.append({**item, "caller_count": len(refs), "callers": refs})

    protected_sources = []
    package_file = root / "apps" / "api" / "local_drama" / "application" / "project_packages.py"
    route_root = root / "apps" / "api" / "local_drama" / "api" / "routes"
    if package_file.is_file():
        protected_sources.append(package_file)
    if route_root.exists():
        protected_sources.extend(sorted(route_root.rglob("*.py")))
    protected = []
    for fact in config["protected_backend_facts"]:
        refs = []
        for source in protected_sources:
            for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
                if fact in line:
                    refs.append({"path": _relative(root, source), "line": line_no})
        protected.append({"fact": fact, "status": "KEEP", "reference_count": len(refs), "references": refs})

    uat = [_uat_gate(root, item) for item in config["required_uat_evidence"]]
    blockers = []
    blockers.extend(f"missing_route:{item['route']}" for item in routes if not item["present"])
    blockers.extend(f"legacy_callers:{item['id']}={item['caller_count']}" for item in surfaces if item["caller_count"])
    if query_links:
        blockers.append(f"legacy_query_links={len(query_links)}")
    blockers.extend(f"legacy_api_callers:{item['symbol']}={item['caller_count']}" for item in api_symbols if item["caller_count"])
    blockers.extend(f"uat:{item['path']}:{item['reason']}" for item in uat if not item["passed"])
    hard_blockers = []
    hard_blockers.extend(f"entrypoint_imports_app:{item['path']}:{item['line']}" for item in entrypoint_app_imports)
    if root_legacy_mount["can_mount_legacy_shell"]:
        hard_blockers.append("v2_root_can_mount_legacy_shell")
    hard_blockers.extend(
        f"legacy_query_context_loss:{item['view']}:{item['context_key']}" for item in legacy_query_context_losses
    )
    blockers = [*hard_blockers, *blockers]
    return {
        "schema_version": "localdrama.p12-decommission-readiness.v2",
        "status": "READY" if not blockers else ("BLOCKED" if hard_blockers else "DEFERRED"),
        "read_only": True,
        "routes": routes,
        "entrypoint_app_imports": entrypoint_app_imports,
        "v2_root_legacy_mount": root_legacy_mount,
        "legacy_query_context_losses": legacy_query_context_losses,
        "app_exclusive_components": app_exclusive_components,
        "legacy_surfaces": surfaces,
        "legacy_query_links": query_links,
        "legacy_api_symbols": api_symbols,
        "retained_query_links": config.get("retained_query_links", []),
        "retained_api_callers": config.get("retained_api_callers", []),
        "protected_backend_facts": protected,
        "uat_evidence": uat,
        "hard_blockers": hard_blockers,
        "blockers": blockers,
        "interpretation": "READY authorizes a reviewed removal change; it does not delete anything. BLOCKED is a hard entrypoint/context invariant failure. DEFERRED requires keeping legacy code and routes.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    report = audit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["hard_blockers"]:
        return 2
    return 1 if args.require_ready and report["status"] != "READY" else 0


if __name__ == "__main__":
    raise SystemExit(main())

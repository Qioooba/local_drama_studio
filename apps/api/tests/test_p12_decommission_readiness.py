from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _module():
    spec = importlib.util.spec_from_file_location("p12_decommission_readiness", ROOT / "scripts" / "p12_decommission_readiness.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_tree_remains_deferred_without_real_uat_signoff() -> None:
    report = _module().audit(ROOT)
    assert report["status"] == "DEFERRED"
    assert report["read_only"] is True
    assert report["entrypoint_app_imports"] == []
    assert report["v2_root_legacy_mount"]["can_mount_legacy_shell"] is False
    assert report["legacy_query_context_losses"] == []
    assert report["hard_blockers"] == []
    assert report["blockers"]
    assert all(item["status"] == "KEEP" for item in report["protected_backend_facts"])
    assert all(item["passed"] is False for item in report["uat_evidence"])


def test_ready_requires_zero_callers_routes_and_approved_uat(tmp_path: Path) -> None:
    root = tmp_path
    router = root / "apps" / "web" / "src" / "app" / "router.tsx"
    router.parent.mkdir(parents=True)
    config = json.loads((ROOT / "docs" / "release" / "p12-decommission-gates.json").read_text(encoding="utf-8"))
    router.write_text("\n".join(config["required_routes"]), encoding="utf-8")
    config["legacy_surfaces"] = []
    config["legacy_api_symbols"] = []
    config["protected_backend_facts"] = []
    config_path = root / "gates.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    for relative in config["required_uat_evidence"]:
        evidence = root / relative
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"status":"PASS","approved":true}', encoding="utf-8")
    report = _module().audit(root, config_path)
    assert report["status"] == "READY"
    assert report["blockers"] == []


def test_static_and_dynamic_app_imports_are_hard_blockers(tmp_path: Path) -> None:
    root = tmp_path
    app_dir = root / "apps" / "web" / "src" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "App.tsx").write_text("export function App() { return null }", encoding="utf-8")
    (app_dir / "router.tsx").write_text(
        'import { LegacyRouteBoundary } from "./legacyRoute";\n'
        'export const routes = [{ path: "/", element: <LegacyRouteBoundary /> }];',
        encoding="utf-8",
    )
    (app_dir / "legacyRoute.tsx").write_text(
        'const LegacyApp = lazy(() => import("./App"));\n'
        'export function LegacyRouteBoundary() { return <LegacyApp /> }',
        encoding="utf-8",
    )
    (root / "apps" / "web" / "src" / "main.tsx").write_text('import { App } from "./app/App";', encoding="utf-8")
    config = {
        "required_routes": [],
        "legacy_app_forbidden_importers": [
            "apps/web/src/main.tsx",
            "apps/web/src/app/router.tsx",
            "apps/web/src/app/legacyRoute.tsx",
        ],
        "legacy_surfaces": [],
        "legacy_api_symbols": [],
        "protected_backend_facts": [],
        "required_uat_evidence": [],
    }
    config_path = root / "gates.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    report = _module().audit(root, config_path)

    assert report["status"] == "BLOCKED"
    assert {item["kind"] for item in report["entrypoint_app_imports"]} == {"static", "dynamic"}
    assert report["v2_root_legacy_mount"]["can_mount_legacy_shell"] is True

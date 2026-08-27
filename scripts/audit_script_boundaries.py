"""Inventory developer scripts and enforce their architectural boundary.

This tool is read-only. It classifies every top-level script from observable
source facts and emits JSON suitable for CI or architecture review.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
ABSOLUTE_WINDOWS_PATH = re.compile(r"(?i)(?:r?[\"'])[a-z]:\\")
UUID_LITERAL = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.IGNORECASE)


def category(path: Path, source: str) -> str:
    name = path.stem.lower()
    if name in {"migrate", "run_api", "worker", "comfy_gpu_worker"} or path.suffix.lower() in {".ps1", ".sh"} and any(token in name for token in ("start", "stop", "run", "worker")):
        return "RUNTIME_ENTRYPOINT"
    if any(token in name for token in ("generate", "build_", "update_", "append_")):
        return "ARTIFACT_GENERATOR"
    if any(token in name for token in ("uat", "e2e", "test", "audit", "diagnose", "benchmark", "crawl")):
        return "VERIFICATION"
    if "local_drama.application" in source or any(token in name for token in ("setup_", "execute_")):
        return "DEVELOPER_DATA_TOOL"
    return "DEVELOPER_TOOL"


def inspect(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "category": category(path, source),
        "page_callable": False,
        "configuration": {
            "cli": "argparse" in source or "process.argv" in source or "$args" in source,
            "environment": "os.environ" in source or "process.env" in source or "$env:" in source,
        },
        "integrations": {
            "comfyui": any(token in source.lower() for token in ("comfyui", "/prompt", "/object_info", "comfyclient")),
            "local_application_service": "local_drama.application" in source,
        },
        "effects": {
            "generates_artifacts": any(token in source for token in ("write_text(", "write_bytes(", "writeFile", "open(", "Set-Content", "Out-File")),
            "hardcoded_uuid_literals": len(set(UUID_LITERAL.findall(source))),
        },
        "violations": ["ABSOLUTE_WINDOWS_PATH"] if ABSOLUTE_WINDOWS_PATH.search(source) else [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify scripts and enforce no machine-specific absolute paths")
    parser.add_argument("--strict", action="store_true", help="return non-zero when boundary violations exist")
    parser.add_argument("--output", type=Path, help="optional report path; parent must already exist")
    args = parser.parse_args()
    items = [inspect(path) for path in sorted(SCRIPTS.iterdir()) if path.is_file() and path.name != Path(__file__).name and path.suffix.lower() in {".py", ".ps1", ".sh", ".mjs", ".js"}]
    report = {
        "schema_version": "localdrama.script-boundaries.v1",
        "root": str(ROOT),
        "summary": {
            "script_count": len(items),
            "categories": {name: sum(item["category"] == name for item in items) for name in sorted({str(item["category"]) for item in items})},
            "violation_count": sum(len(item["violations"]) for item in items),
            "page_callable_count": 0,
        },
        "items": items,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        target = args.output.resolve()
        if not target.parent.is_dir():
            raise SystemExit("--output parent must already exist")
        target.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 1 if args.strict and report["summary"]["violation_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

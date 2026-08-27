"""Inventory the API surface for §11.3/Slice-8 cleanup decisions.

Two reports, both evidence-only (no writes):

1. Response-model coverage: every route file whose POST/GET/... operations lack
   a typed ``response_model``, grouped by module — feeds the
   ``routes_without_response_model`` debt category retirement order.
2. Zero-web-consumer candidates: OpenAPI paths never referenced by any
   hand-written web source (``generated/api.ts`` is excluded because it mirrors
   the whole spec), grouped by first path segments. These need an owner/
   equivalence decision before deletion; this script does not decide.

Usage::

    python scripts/api_surface_audit.py [--unreferenced]
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROUTES = ROOT / "apps" / "api" / "local_drama" / "api" / "routes"
SPEC_PATH = ROOT / "docs" / "openapi" / "openapi.json"
WEB_SRC = ROOT / "apps" / "web" / "src"

_DECORATOR_RE = re.compile(r"@router\.(get|post|put|patch|delete)\(")


def response_model_gaps() -> dict[str, int]:
    gaps: Counter[str] = Counter()
    for path in sorted(API_ROUTES.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        operations = _DECORATOR_RE.findall(text)
        if not operations:
            continue
        covered = len(re.findall(r"response_model\s*=", text))
        missing = len(operations) - covered
        if missing > 0:
            gaps[path.name] += missing
    return dict(sorted(gaps.items(), key=lambda item: -item[1]))


def load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def hand_written_web_corpus() -> str:
    chunks: list[str] = []
    for path in WEB_SRC.rglob("*"):
        if path.suffix in {".ts", ".tsx"} and path.is_file() and "generated" not in path.parts:
            try:
                chunks.append(path.read_text(encoding="utf-8"))
            except OSError:
                continue
    return "\n".join(chunks)


def unreferenced_paths(spec: dict, corpus: str) -> list[str]:
    unused: list[str] = []
    for raw in spec.get("paths", {}):
        probe = re.sub(r"\{[^}]+\}", r"[^\"'`)]+", raw)
        if len(re.findall(probe, corpus)) == 0:
            unused.append(raw)
    return unused


def main() -> int:
    verbose = "--unreferenced" in sys.argv
    gaps = response_model_gaps()
    total_missing = sum(gaps.values())
    print(f"operations without response_model by file (total {total_missing}):")
    for name, count in gaps.items():
        print(f"  {count:4d}  {name}")

    spec = load_spec()
    corpus = hand_written_web_corpus()
    unused = unreferenced_paths(spec, corpus)
    print(f"\nOpenAPI paths with no hand-written web reference: {len(unused)} / {len(spec.get('paths', {}))}")
    for prefix, count in sorted(Counter("/".join(u.split("/")[:3]) for u in unused).items(), key=lambda kv: -kv[1]):
        print(f"  {count:4d}  {prefix}")
    if verbose:
        for raw in unused:
            print(raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())

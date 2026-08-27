"""Report non-test source files in apps/web/src unreachable from app entries.

Entries: main.tsx, App.tsx, app/router.tsx (+ everything it imports).
Test files are ignored: a module consumed only by tests is still reported.
Exit code 0 always; this is an inventory tool for Slice 8 cleanup.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "apps" / "web" / "src"
IMPORT_RE = re.compile(
    r"""(?:from\s+|import\s*\(\s*|import\s+)["']([^"']+)["']"""
)
ENTRIES = ("main.tsx", "App.tsx")


def normalize(spec: str, importer: Path) -> Path | None:
    if not spec.startswith("."):
        return None
    target = (importer.parent / spec).resolve()
    candidates = [target, target.with_suffix(".ts"), target.with_suffix(".tsx")]
    candidates += [target / "index.ts", target / "index.tsx"]
    for candidate in candidates:
        if candidate.suffix in {".ts", ".tsx"} and candidate.is_file():
            return candidate
    return None


def main() -> int:
    all_files = {
        p.resolve(): p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*")
        if p.suffix in {".ts", ".tsx"} and "node_modules" not in str(p)
    }
    import_map = {path: set() for path in all_files}
    for path in list(all_files):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in IMPORT_RE.finditer(text):
            resolved = normalize(match.group(1), path)
            if resolved is not None:
                import_map[path].add(resolved)

    roots = [path for path in all_files if all_files[path] in ENTRIES]
    seen: set[Path] = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(import_map.get(current, ()))

    orphans = sorted(all_files[path] for path in all_files.keys() - seen
                     if ".test." not in all_files[path])
    print(f"reachable: {len(seen)} / {len(all_files)} files")
    if orphans:
        print("ORPHANS (non-test, never imported from entries):")
        for orphan in orphans:
            print(f"  {orphan}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Read-only G0 validator for the v2 blueprint and local H3 manifest."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT = ROOT / "LocalDramaStudio_Blueprint_v2"
MANIFEST = ROOT / "model_manifest.json"
IN_SCOPE = [*range(11), *range(12, 18)]


def in_scope_docs() -> list[Path]:
    return [BLUEPRINT / f"{number:02d}_" for number in IN_SCOPE]  # type: ignore[list-item]


def resolve_docs() -> list[Path]:
    paths: list[Path] = []
    for number in IN_SCOPE:
        matches = sorted(BLUEPRINT.glob(f"{number:02d}_*.md"))
        if len(matches) != 1:
            raise RuntimeError(
                f"expected exactly one in-scope document for {number:02d}"
            )
        paths.append(matches[0])
    return paths


def requirement_ids(text: str, prefix: str) -> set[str]:
    return set(
        re.findall(rf"^\|\s*({re.escape(prefix)}[A-Z]+-\d{{3}})\b", text, re.MULTILINE)
    )


def main() -> None:
    docs = resolve_docs()
    requirements = (BLUEPRINT / "01_产品需求与验收范围.md").read_text(encoding="utf-8")
    tests = (BLUEPRINT / "10_测试策略_用例矩阵与发布检查.md").read_text(
        encoding="utf-8"
    )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(f"blueprint={BLUEPRINT}")
    print(f"documents_in_scope={len(docs)}")
    print(
        f"document_11_present={int((BLUEPRINT / '11_老屋灯火迁移_试运行与上线.md').exists())}"
    )
    print(f"fr_count={len(requirement_ids(requirements, 'FR-'))}")
    print(f"nfr_count={len(requirement_ids(requirements, 'NFR-'))}")
    print(f"tc_count={len(set(re.findall(r'\bTC-[A-Z]+-\d{3}\b', tests)))}")
    authoritative = manifest["authoritative_current_state"]
    route_status = authoritative["route_status"]
    print(f"manifest_version={manifest['manifest_version']}")
    print(f"worker_policy={authoritative['worker_policy']}")
    print(f"native_t2v={route_status['native_t2v']}")
    print(f"native_i2v={route_status['native_i2v']}")
    print(f"native_ref2v={route_status['native_ref2v']}")
    print(f"native_first_last={route_status['native_first_last']}")
    print(f"forbidden_assets={len(authoritative['forbidden_assets'])}")
    for path in docs:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"sha256[{path.name}]={digest}")


if __name__ == "__main__":
    main()

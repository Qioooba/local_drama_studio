"""Seed the deterministic reference images a Qwen-Image-2.1 edit smoke needs.

``TextEncodeQwenImage21`` receives references through ``LoadImage``, so the
capability smoke for the edit workflows can only run when those files exist in
the *runtime's* Comfy input directory.  Downloading upstream example PNGs would
add a network dependency to a local-only acceptance path, so the two reference
images are generated here instead: a neutral composition base and a flat colour
reference.  Both are byte-stable, so re-running never disturbs an existing file.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

# Must match the constants the workflow definitions put in their smoke contract.
REFERENCE_1 = "local_drama_qwen21_smoke_base.png"
REFERENCE_2 = "local_drama_qwen21_smoke_ref.png"

_WIDTH = 768
_HEIGHT = 768


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    """Encode 8-bit RGB rows as a PNG without any third-party dependency."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _base_image() -> bytes:
    """A vertically graded scene with a centred subject block.

    Deliberately simple and deterministic: the smoke asserts that the pipeline
    produces a decodable image of the expected size, not that the model is
    artistic.
    """

    rows: list[bytes] = []
    for y in range(_HEIGHT):
        row = bytearray()
        for x in range(_WIDTH):
            in_subject = (_WIDTH // 4) <= x < (3 * _WIDTH // 4) and (_HEIGHT // 5) <= y < (4 * _HEIGHT // 5)
            if in_subject:
                row += bytes((176, 92, 64))
            else:
                row += bytes((40 + y * 60 // _HEIGHT, 60 + y * 70 // _HEIGHT, 110 + y * 80 // _HEIGHT))
        rows.append(bytes(row))
    return _png(_WIDTH, _HEIGHT, rows)


def _reference_image() -> bytes:
    """A flat mid-blue swatch used as image_2 (colour/attribute reference)."""

    row = bytes((38, 92, 196)) * _WIDTH
    return _png(_WIDTH, _HEIGHT, [row] * _HEIGHT)


def seed(input_root: Path, *, force: bool = False) -> dict[str, object]:
    input_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    for name, payload in ((REFERENCE_1, _base_image()), (REFERENCE_2, _reference_image())):
        target = input_root / name
        if target.is_file() and not force:
            # Never rewrite an existing file during acceptance: a changed input
            # would silently invalidate a previously recorded run.
            unchanged = target.read_bytes() == payload
            results.append({"file": str(target), "status": "PRESENT" if unchanged else "PRESENT_DIFFERENT", "bytes": target.stat().st_size})
            continue
        target.write_bytes(payload)
        results.append({"file": str(target), "status": "WRITTEN", "bytes": len(payload)})
    return {"schema_version": "localdrama.qwen21-smoke-inputs.v1", "input_root": str(input_root), "files": results}


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    default_pin = repo_root / "config" / "comfyui-qwen21-runtime.json"
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pin", type=Path, default=default_pin)
    parser.add_argument("--input-root", type=Path, help="Override the runtime's Comfy input directory.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if args.input_root is not None:
        input_root = args.input_root
    else:
        pin = json.loads(args.pin.read_text(encoding="utf-8"))
        input_root = Path(str(pin["io_root"])) / "input"
    report = seed(input_root, force=args.force)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Download the locked production dependency wheelhouse")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error("wheelhouse preparation requires Python 3.12")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--dest",
            str(output),
            "-r",
            str(REPOSITORY_ROOT / "apps" / "api" / "requirements-runtime.lock"),
        ],
        check=True,
    )
    files = []
    for path in sorted(output.glob("*.whl")):
        files.append(
            {
                "file": path.name,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (output / "wheelhouse-manifest.json").write_text(
        json.dumps({"schema_version": 1, "python_abi": "cp312", "files": files}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

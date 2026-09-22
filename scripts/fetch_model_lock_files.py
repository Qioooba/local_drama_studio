"""Fetch pinned model-lock components with byte and SHA-256 verification.

``config/model-lock.json`` is the authority for *which* bytes a model release
owns.  This script is the only supported way to materialise a missing component
from the public Hugging Face mirror recorded in the lock.  It never trusts a
partial file and never overwrites a component that already matches its pinned
digest.

Guarantees:

* Downloads resume; an interrupted transfer is continued with a Range request.
* A component is only promoted into the canonical model root after its real
  byte count *and* SHA-256 match the lock.
* An already-correct component is left untouched (idempotent, no re-download).
* A component whose bytes exist but whose digest disagrees is reported as
  ``DIGEST_MISMATCH`` and is never promoted.
* No file is written outside ``canonical_root``.

Example::

    python scripts/fetch_model_lock_files.py --model qwen-image-2.1-int8-convrot
    python scripts/fetch_model_lock_files.py --model qwen-image-2.1-int8-convrot --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_CHUNK = 1024 * 1024
_DEFAULT_ENDPOINT = "https://huggingface.co"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_url(endpoint: str, repository: str, revision: str, path: str) -> str:
    return f"{endpoint.rstrip('/')}/{repository}/resolve/{revision}/{path}"


def _content_length(url: str) -> int | None:
    request = Request(url, method="HEAD")
    with urlopen(request, timeout=60) as response:  # noqa: S310 - pinned https endpoint
        raw = response.headers.get("Content-Length")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _download(url: str, destination: Path) -> None:
    """Stream ``url`` into ``destination``, resuming when it already has bytes.

    ``curl`` is preferred as the transfer engine.  The Hugging Face CDN
    intermittently ignores a ``Range`` header and answers ``200`` with the whole
    body; a naive ``ab`` append would silently corrupt the file, and reopening
    with ``wb`` restarts a multi-gigabyte transfer.  ``curl -C -`` detects that
    case and fails instead of producing a wrong file, and it sustains a much
    higher throughput than a single-threaded ``urllib`` read.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    curl = shutil.which("curl")
    if curl:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                curl,
                "--location",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "8",
                "--retry-delay",
                "5",
                "--retry-all-errors",
                "--continue-at",
                "-",
                "--output",
                str(destination),
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise OSError(f"curl exited {result.returncode}: {result.stderr.strip()[:400]}")
        return
    _download_urllib(url, destination)


def _download_urllib(url: str, destination: Path) -> None:
    existing = destination.stat().st_size if destination.is_file() else 0
    request = Request(url)
    if existing:
        request.add_header("Range", f"bytes={existing}-")
    with urlopen(request, timeout=120) as response:  # noqa: S310 - pinned https endpoint
        if existing and response.status != 206:
            raise OSError("server ignored the resume range; refusing to append a second copy")
        mode = "ab" if existing else "wb"
        with destination.open(mode) as handle:
            while chunk := response.read(_CHUNK):
                handle.write(chunk)


def _verify(path: Path, expected_bytes: int | None, expected_sha: str | None) -> tuple[str, str | None]:
    if not path.is_file():
        return "MISSING", None
    actual_bytes = path.stat().st_size
    if expected_bytes is not None and actual_bytes != int(expected_bytes):
        return "SIZE_MISMATCH", None
    if expected_sha:
        actual_sha = _sha256(path)
        if actual_sha.lower() != str(expected_sha).lower():
            return "DIGEST_MISMATCH", actual_sha
        return "PASS", actual_sha
    return "PRESENT_UNPINNED", None


def _iter_components(model: dict[str, Any]) -> Iterator[dict[str, Any]]:
    repository = model.get("source_repository")
    revision = model.get("source_revision")
    for item in model.get("files", []):
        yield {
            "repository": repository,
            "revision": revision,
            "path": str(item["path"]),
            "source_path": item.get("source_path") or str(item["path"]),
            "bytes": item.get("bytes"),
            "sha256": item.get("sha256"),
        }


def fetch_model(
    model: dict[str, Any],
    *,
    canonical_root: Path,
    staging_root: Path,
    endpoint: str,
    dry_run: bool,
    allow_unpinned: bool,
) -> dict[str, Any]:
    code = str(model["code"])
    files: list[dict[str, Any]] = []
    for component in _iter_components(model):
        target = canonical_root / component["path"]
        expected_bytes = component["bytes"]
        expected_sha = component["sha256"]
        record: dict[str, Any] = {
            "path": component["path"],
            "target": str(target),
            "expected_bytes": expected_bytes,
            "expected_sha256": expected_sha,
        }
        if not expected_sha and not allow_unpinned:
            record["status"] = "UNPINNED_COMPONENT_SKIPPED"
            files.append(record)
            continue
        status, actual_sha = _verify(target, expected_bytes, expected_sha)
        if status == "PASS" or (status == "PRESENT_UNPINNED" and allow_unpinned):
            record.update(status="ALREADY_PRESENT" if status != "PASS" else "PASS", actual_sha256=actual_sha)
            files.append(record)
            continue
        if dry_run:
            record.update(status="WOULD_FETCH", current_status=status)
            files.append(record)
            continue
        if not component["repository"] or not component["revision"]:
            record.update(status="SOURCE_NOT_PINNED", current_status=status)
            files.append(record)
            continue
        url = _remote_url(endpoint, str(component["repository"]), str(component["revision"]), str(component["source_path"]))
        partial = staging_root / code / Path(component["source_path"]).name
        record["url"] = url
        # A staged file that already has the pinned size but the wrong digest is
        # corrupt; `curl -C -` would refuse to touch it, so drop it explicitly.
        if partial.is_file() and expected_bytes is not None and partial.stat().st_size == int(expected_bytes):
            staged_status, _ = _verify(partial, expected_bytes, expected_sha)
            if staged_status not in {"PASS", "PRESENT_UNPINNED"}:
                partial.unlink()
        started = time.monotonic()
        try:
            if not partial.is_file() or partial.stat().st_size != (expected_bytes or -1):
                _download(url, partial)
        except (HTTPError, URLError, OSError) as error:
            record.update(status="DOWNLOAD_FAILED", error=f"{type(error).__name__}: {error}")
            files.append(record)
            continue
        record["seconds"] = round(time.monotonic() - started, 3)
        staged_status, staged_sha = _verify(partial, expected_bytes, expected_sha)
        if staged_status not in {"PASS", "PRESENT_UNPINNED"}:
            record.update(status=f"STAGED_{staged_status}", actual_sha256=staged_sha)
            files.append(record)
            continue
        # Promote only verified bytes, and never clobber a different existing file.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(partial), str(target))
        final_status, final_sha = _verify(target, expected_bytes, expected_sha)
        record.update(status="FETCHED" if final_status == "PASS" else final_status, actual_sha256=final_sha)
        files.append(record)
    complete = bool(files) and all(item["status"] in {"PASS", "ALREADY_PRESENT", "FETCHED", "PRESENT_UNPINNED"} for item in files)
    return {"code": code, "complete": complete, "files": files}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lock", type=Path, default=Path(__file__).resolve().parents[1] / "config" / "model-lock.json")
    parser.add_argument("--model", action="append", default=[], help="Model code to fetch; repeatable. Default: every model in the lock.")
    parser.add_argument("--canonical-root", type=Path, help="Override the lock's canonical_root.")
    parser.add_argument("--staging-root", type=Path, help="Where partial transfers live. Default: <canonical_root>/Staging.")
    parser.add_argument("--endpoint", default=os.environ.get("LOCAL_DRAMA_HF_ENDPOINT", _DEFAULT_ENDPOINT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-unpinned", action="store_true", help="Permit files without a pinned sha256 (size check only).")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    canonical_root = (args.canonical_root or Path(str(lock["canonical_root"]))).resolve()
    staging_root = (args.staging_root or canonical_root / "Staging").resolve()
    wanted = {code.strip() for code in args.model}
    models = [item for item in lock["models"] if not wanted or str(item["code"]) in wanted]
    missing_codes = sorted(wanted - {str(item["code"]) for item in lock["models"]})
    if missing_codes:
        print(json.dumps({"error": "MODEL_CODE_NOT_IN_LOCK", "codes": missing_codes}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    results = [
        fetch_model(
            model,
            canonical_root=canonical_root,
            staging_root=staging_root,
            endpoint=args.endpoint,
            dry_run=args.dry_run,
            allow_unpinned=args.allow_unpinned,
        )
        for model in models
    ]
    report = {
        "schema_version": "localdrama.model-fetch.v1",
        "canonical_root": str(canonical_root),
        "staging_root": str(staging_root),
        "endpoint": args.endpoint,
        "dry_run": args.dry_run,
        "models": results,
        "all_complete": bool(results) and all(item["complete"] for item in results),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["all_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


def _format_status(path: Path, declared: str) -> tuple[bool, str]:
    if path.with_name(path.name + ".aria2").exists():
        return False, "INCOMPLETE_ARIA2"
    if declared == "GGUF":
        with path.open("rb") as source:
            return (source.read(4) == b"GGUF", "GGUF_MAGIC")
    if declared == "SAFETENSORS":
        with path.open("rb") as source:
            prefix = source.read(8)
            if len(prefix) != 8:
                return False, "TRUNCATED_HEADER"
            size = struct.unpack("<Q", prefix)[0]
            if size <= 1 or size > min(100 * 1024 * 1024, path.stat().st_size - 8):
                return False, "INVALID_HEADER_SIZE"
            try:
                header = json.loads(source.read(size).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return False, "INVALID_HEADER_JSON"
            return (isinstance(header, dict) and len(header) > 0, f"SAFETENSORS_KEYS={len(header)}")
    return True, "EXISTS"


def _ollama_models() -> set[str] | None:
    """Probe only the loopback Ollama registry; never contact an external host."""

    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return {
        str(item.get("name"))
        for item in payload.get("models", [])
        if isinstance(item, dict) and item.get("name")
    }


def verify(lock_path: Path, *, probe_ollama: bool = False) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    root = Path(str(lock["canonical_root"]))
    models: list[dict[str, Any]] = []
    installed_ollama = _ollama_models() if probe_ollama else None
    for model in lock["models"]:
        if model.get("ollama_model"):
            name = str(model["ollama_model"])
            if installed_ollama is None:
                status = "OLLAMA_LOOPBACK_UNAVAILABLE" if probe_ollama else "PENDING_RUNTIME_PROBE"
            else:
                status = "PASS" if name in installed_ollama else "INCOMPLETE_RUNTIME_MODEL_NOT_PRESENT"
            models.append({"code": model["code"], "status": status, "ollama_model": name, "files": []})
            continue
        files: list[dict[str, Any]] = []
        for expected in model.get("files", []):
            path = root / expected["path"]
            item = {"path": str(path), "expected_bytes": expected["bytes"]}
            if not path.is_file():
                item.update(status="MISSING", actual_bytes=None, valid=False)
            else:
                actual = path.stat().st_size
                valid_format, detail = _format_status(path, str(expected["format"]))
                valid = actual == int(expected["bytes"]) and valid_format
                item.update(status="PASS" if valid else detail, actual_bytes=actual, valid=valid)
            files.append(item)
        models.append(
            {
                "code": model["code"],
                "status": "PASS" if files and all(item["valid"] for item in files) else "INCOMPLETE",
                "files": files,
            }
        )
    return {
        "schema_version": "localdrama.model-verification.v1",
        "canonical_root": str(root),
        "models": models,
        "all_complete": all(model["status"] == "PASS" for model in models),
        "network_contacted": False,
        "ollama_probe": "LOOPBACK_ONLY" if probe_ollama else "NOT_REQUESTED",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=Path(__file__).resolve().parents[1] / "config" / "model-lock.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--probe-ollama", action="store_true", help="Probe only http://127.0.0.1:11434/api/tags")
    args = parser.parse_args()
    report = verify(args.lock, probe_ollama=args.probe_ollama)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["all_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

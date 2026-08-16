"""Probe the user-owned Windows LOCAL_ONLY runtimes without bundling assets.

The smoke run is deliberately explicit about its boundaries:

* FFmpeg/FFprobe and Windows System.Speech are exercised on short, temporary
  fixtures.  No source media is read and no file is left in the project tree.
* ComfyUI is discovered from ``model_manifest.json`` and may be queried only
  through a literal loopback endpoint.  A read-only health probe is always
  safe; the optional ``--execute-comfy-smoke`` flag submits a model-free
  LoadImage -> SaveImage graph and removes the transient input/output files.
* Model paths are inventoried as user-owned references.  They are never
  copied, uploaded, hashed in bulk, or included in a package.

This is runtime evidence, not a claim that a user-selected H3 model can make
production media.  That requires a separate real model/profile/license/UAT
and remains PARTIAL when the model or Comfy runtime is unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT.parent / "model_manifest.json"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LOOPBACK_OPENER = build_opener(ProxyHandler({}))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(code: str, status: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "status": status, "passed": status == "PASS", **details}


def _run(executable: str | None, arguments: list[str], *, timeout: float = 30) -> tuple[str, str, int | None, str | None]:
    if not executable or not Path(executable).is_file():
        return "BLOCKED", "", None, "executable_not_found"
    try:
        completed = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "FAIL", "", None, type(error).__name__
    output = (completed.stdout or completed.stderr or "").strip()
    return ("PASS" if completed.returncode == 0 else "FAIL"), output, completed.returncode, None


def _resolve_executable(env_name: str, command: str, fallback: str | None = None) -> str | None:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        return configured
    found = shutil.which(command)
    if found:
        return found
    if fallback and Path(fallback).is_file():
        return fallback
    return None


def _run_ffmpeg_smoke(root: Path) -> dict[str, Any]:
    ffmpeg = _resolve_executable("LOCAL_DRAMA_FFMPEG", "ffmpeg", r"E:\Tools\ffmpeg\bin\ffmpeg.exe")
    ffprobe = _resolve_executable("LOCAL_DRAMA_FFPROBE", "ffprobe", r"E:\Tools\ffmpeg\bin\ffprobe.exe")
    output = root / "ffmpeg-smoke.mp4"
    status, version, returncode, error = _run(ffmpeg, ["-version"])
    if status != "PASS":
        return {"status": status, "ffmpeg": ffmpeg, "ffprobe": ffprobe, "version": version, "error": error}
    command_status, command_output, command_code, command_error = _run(
        ffmpeg,
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=2:duration=1",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-an",
            "-y",
            str(output),
        ],
    )
    probe_status, probe_output, probe_code, probe_error = _run(
        ffprobe,
        ["-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(output)],
    ) if command_status == "PASS" and output.is_file() else ("BLOCKED", "", None, "fixture_not_created")
    probe: dict[str, Any] = {}
    if probe_status == "PASS":
        try:
            probe = json.loads(probe_output)
        except json.JSONDecodeError:
            probe_status = "FAIL"
            probe_error = "ffprobe_json_invalid"
    passed = command_status == "PASS" and probe_status == "PASS" and output.stat().st_size > 0
    return {
        "status": "PASS" if passed else "FAIL" if command_status == "FAIL" or probe_status == "FAIL" else "BLOCKED",
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "version": (version.splitlines() or [""])[0],
        "ffmpeg_exit_code": returncode,
        "fixture_command": {"status": command_status, "exit_code": command_code, "error": command_error, "output": command_output[-500:]},
        "probe": {"status": probe_status, "exit_code": probe_code, "error": probe_error, "streams": len(probe.get("streams", [])), "format": probe.get("format", {})},
        "artifact": {"path_rel": output.relative_to(ROOT).as_posix(), "sha256": _sha256(output), "bytes": output.stat().st_size} if output.is_file() else None,
    }


def _powershell_executable() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("pwsh")


def _run_sapi_smoke(root: Path) -> dict[str, Any]:
    powershell = _powershell_executable()
    if not powershell:
        return {"status": "BLOCKED", "powershell": None, "error": "powershell_not_found", "runtime_contacted": False}
    output = root / "sapi-smoke.wav"
    escaped = str(output).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; "
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voices=@($s.GetInstalledVoices() | ForEach-Object { @{name=$_.VoiceInfo.Name; culture=$_.VoiceInfo.Culture.Name; ref=('sapi:' + $_.VoiceInfo.Name)} }); "
        f"$s.SetOutputToWaveFile('{escaped}'); $s.Speak('LocalDramaStudio offline runtime smoke test'); $s.Dispose(); "
        "[Console]::Write(($voices | ConvertTo-Json -Compress))"
    )
    try:
        completed = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "FAIL", "powershell": powershell, "error": type(error).__name__, "runtime_contacted": True}
    if completed.returncode != 0:
        return {"status": "BLOCKED", "powershell": powershell, "error": "system_speech_unavailable", "stderr": completed.stderr[-500:], "runtime_contacted": True}
    try:
        voices = json.loads(completed.stdout.strip() or "[]")
    except json.JSONDecodeError:
        voices = []
    if isinstance(voices, dict):
        voices = [voices]
    return {
        "status": "PASS" if voices and output.is_file() and output.stat().st_size > 44 else "BLOCKED",
        "powershell": powershell,
        "voices": voices,
        "artifact": {"path_rel": output.relative_to(ROOT).as_posix(), "sha256": _sha256(output), "bytes": output.stat().st_size} if output.is_file() else None,
        "runtime_contacted": True,
        "network_contacted": False,
    }


def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return {}
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _model_inventory(manifest: dict[str, Any]) -> dict[str, Any]:
    root_raw = str(dict(manifest.get("canonical_model_root", {})).get("path", "")).strip()
    model_root = Path(root_raw) if root_raw else None
    if model_root is None or not model_root.is_dir():
        return {"status": "BLOCKED", "root": root_raw or None, "reason": "canonical_model_root_missing", "reference_only": True, "bundled": False, "uploaded": False}
    files = [path for path in model_root.rglob("*") if path.is_file() and not path.is_symlink()]
    total_bytes = sum(path.stat().st_size for path in files)
    runtime = dict(manifest.get("runtime", {}))
    loaders = dict(dict(manifest.get("authoritative_current_state", {})).get("loader_assets", {}))
    declared = [str(value) for key, value in loaders.items() if key.endswith("_runtime_path") and value]
    declared_items = [{"path": value, "exists": Path(value).is_file()} for value in declared]
    return {
        "status": "PASS" if files else "BLOCKED",
        "root": str(model_root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "declared_runtime_assets": declared_items,
        "comfy_root": runtime.get("comfyui_root"),
        "reference_only": True,
        "bundled": False,
        "uploaded": False,
        "runtime_contacted": False,
        "network_contacted": False,
    }


def _http_json(base_url: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 5) -> tuple[int | None, dict[str, Any], str | None]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(f"{base_url.rstrip('/')}{path}", method=method, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with _LOOPBACK_OPENER.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw or "{}"), None
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        return None, {}, type(error).__name__


def _comfy_inventory(manifest: dict[str, Any], execute_smoke: bool, root: Path) -> dict[str, Any]:
    runtime = dict(manifest.get("runtime", {}))
    comfy_root = Path(str(runtime.get("comfyui_root", ""))) if runtime.get("comfyui_root") else None
    python = Path(str(runtime.get("comfyui_python_executable", ""))) if runtime.get("comfyui_python_executable") else None
    base_url = str(dict(runtime.get("comfyui_api", {})).get("base_url", "http://127.0.0.1:8188"))
    parsed = urlparse(base_url)
    endpoint_local = parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS and not parsed.query and not parsed.fragment and not parsed.username and not parsed.password
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "root": str(comfy_root) if comfy_root else None,
        "python": str(python) if python else None,
        "main_py_exists": bool(comfy_root and (comfy_root / "main.py").is_file()),
        "python_exists": bool(python and python.is_file()),
        "endpoint": base_url,
        "endpoint_local": endpoint_local,
        "runtime_contacted": False,
        "network_contacted": False,
        "generation_smoke": "NOT_RUN",
    }
    if not endpoint_local:
        result["reason"] = "non_loopback_endpoint_rejected"
        return result
    status_code, system_stats, error = _http_json(base_url, "/system_stats")
    result["runtime_contacted"] = status_code is not None
    result["health"] = {"status_code": status_code, "error": error, "version": system_stats.get("system", {}).get("comfyui_version"), "devices": system_stats.get("devices", [])}
    if status_code != 200:
        result["reason"] = "loopback_backend_unavailable"
        return result
    object_code, object_info, object_error = _http_json(base_url, "/object_info")
    queue_code, queue, queue_error = _http_json(base_url, "/queue")
    result["object_info"] = {"status_code": object_code, "error": object_error, "node_count": len(object_info) if isinstance(object_info, dict) else 0}
    result["queue"] = {"status_code": queue_code, "error": queue_error, "running": len(queue.get("queue_running", [])), "pending": len(queue.get("queue_pending", []))}
    result["status"] = "PASS"
    if not execute_smoke:
        return result
    result.update(_execute_comfy_smoke(base_url, manifest, root))
    return result


def _execute_comfy_smoke(base_url: str, manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    """Submit a model-free LoadImage -> SaveImage graph to the live loopback runtime."""

    runtime = dict(manifest.get("runtime", {}))
    comfy_root = Path(str(runtime.get("comfyui_root", "")))
    input_root = comfy_root / "input"
    output_root = comfy_root / "output"
    token = f"runtime_smoke_{uuid.uuid4().hex}"
    input_name = f"{token}.png"
    source = root / input_name
    # FFmpeg writes a tiny deterministic PNG; this does not read user media.
    ffmpeg = _resolve_executable("LOCAL_DRAMA_FFMPEG", "ffmpeg", r"E:\Tools\ffmpeg\bin\ffmpeg.exe")
    status, _, _, error = _run(ffmpeg, ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=8x8", "-frames:v", "1", "-y", str(source)])
    if status != "PASS" or not source.is_file() or not input_root.is_dir():
        return {"generation_smoke": "BLOCKED", "generation_reason": error or "local_input_root_missing", "runtime_mutated": False}
    destination = input_root / input_name
    output_prefix = token
    try:
        shutil.copy2(source, destination)
    except OSError as copy_error:
        return {"generation_smoke": "BLOCKED", "generation_reason": "comfy_input_copy_failed", "copy_error": type(copy_error).__name__, "runtime_mutated": False}
    prompt = {
        "1": {"class_type": "LoadImage", "inputs": {"image": input_name}},
        "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": output_prefix, "images": ["1", 0]}},
    }
    prompt_code, prompt_response, prompt_error = _http_json(base_url, "/prompt", method="POST", payload={"prompt": prompt, "client_id": token}, timeout=10)
    result: dict[str, Any] = {"generation_smoke": "BLOCKED", "prompt_status_code": prompt_code, "prompt_error": prompt_error, "runtime_mutated": True}
    prompt_id = str(prompt_response.get("prompt_id", ""))
    result["prompt_id"] = prompt_id or None
    output_path: Path | None = None
    try:
        if prompt_code == 200 and prompt_id:
            deadline = time.monotonic() + 45
            history: dict[str, Any] = {}
            while time.monotonic() < deadline:
                history_code, history, _history_error = _http_json(base_url, f"/history/{prompt_id}", timeout=5)
                if history_code == 200 and prompt_id in history:
                    break
                time.sleep(0.5)
            item = history.get(prompt_id, {})
            outputs = dict(item.get("outputs", {}))
            images = [dict(image) for node in outputs.values() if isinstance(node, dict) for image in node.get("images", []) if isinstance(image, dict)]
            if images:
                filename = Path(str(images[0].get("filename", ""))).name
                candidate = output_root / filename
                if candidate.is_file() and candidate.resolve().is_relative_to(output_root.resolve()):
                    output_path = candidate
                    result.update({"generation_smoke": "PASS", "history_status": item.get("status", {}), "output": {"filename": filename, "bytes": candidate.stat().st_size, "sha256": _sha256(candidate)}})
            if result["generation_smoke"] != "PASS":
                result["generation_reason"] = "history_output_missing"
        else:
            result["generation_reason"] = "prompt_rejected"
    finally:
        for path in (destination, source, output_path):
            if path and path.is_file():
                try:
                    path.unlink()
                except OSError:
                    result.setdefault("cleanup_failures", []).append(str(path))
    if result.get("cleanup_failures"):
        result["generation_smoke"] = "FAIL"
        result["generation_reason"] = "transient_runtime_artifact_cleanup_failed"
    return result


def run(root: Path, *, execute_comfy_smoke: bool = False) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest()
    ffmpeg = _run_ffmpeg_smoke(root)
    sapi = _run_sapi_smoke(root)
    models = _model_inventory(manifest)
    comfy = _comfy_inventory(manifest, execute_comfy_smoke, root)
    checks = [
        _check("FFMPEG_FFPROBE_REAL_MEDIA", ffmpeg["status"], observed=ffmpeg),
        _check("WINDOWS_SYSTEM_SPEECH_REAL_WAV", sapi["status"], observed=sapi),
        _check("LOCAL_MODEL_REFERENCE_INVENTORY", models["status"], observed=models),
        _check("COMFYUI_LOOPBACK_HEALTH", comfy["status"], observed=comfy),
    ]
    statuses = {item["status"] for item in checks}
    overall = "PASS" if statuses == {"PASS"} else "FAIL" if "FAIL" in statuses else "BLOCKED"
    return {
        "schema_version": "g10.runtime_smoke_uat.v1",
        "status": overall,
        "scope": "Windows x64 user-owned local FFmpeg/System.Speech/ComfyUI/model references; no bundled or uploaded assets",
        "checks": checks,
        "runtime_contacted": bool(sapi.get("runtime_contacted") or comfy.get("runtime_contacted")),
        "network_contacted": False,
        "production_database_contacted": False,
        "production_mutated": False,
        "observed_at": _now(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "temp" / f"runtime-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / "g10" / "runtime-smoke-uat-2026-08-16.json")
    parser.add_argument("--execute-comfy-smoke", action="store_true", help="submit a model-free LoadImage -> SaveImage graph to loopback ComfyUI")
    args = parser.parse_args()
    result = run(args.root.resolve(), execute_comfy_smoke=args.execute_comfy_smoke)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "checks": {item["code"]: item["status"] for item in result["checks"]}}, ensure_ascii=False))
    if result["status"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

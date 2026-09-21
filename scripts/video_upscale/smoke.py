"""Run a real, local Real-ESRGAN NCNN/Vulkan smoke and write redacted evidence.

This tool never downloads assets and never marks a Model Platform profile as
published.  A PASS proves that the explicitly supplied executable, model
files and Vulkan device produced real scaled frames on this machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _probe(ffprobe: Path, path: Path) -> dict[str, Any]:
    completed = _run(
        [str(ffprobe), "-v", "error", "-show_entries", "stream=codec_type,width,height,pix_fmt", "-of", "json", str(path)],
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError("ffprobe failed for NCNN output")
    payload = json.loads(completed.stdout)
    return next(stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video")


def _redact(text: str, paths: list[Path]) -> str:
    result = text
    for path in sorted(paths, key=lambda item: len(str(item)), reverse=True):
        result = result.replace(str(path), f"<{path.name or 'path'}>")
    return result[-2000:]


def _model_files(model_dir: Path, model_name: str) -> list[Path]:
    params = {item.stem: item for item in model_dir.glob(f"{model_name}*.param") if item.is_file()}
    bins = {item.stem: item for item in model_dir.glob(f"{model_name}*.bin") if item.is_file()}
    stems = sorted(set(params) & set(bins))
    if not stems:
        raise RuntimeError(f"model directory has no matching .param/.bin pair for {model_name}")
    return [item for stem in stems for item in (params[stem], bins[stem])]


def smoke(args: argparse.Namespace) -> dict[str, Any]:
    executable = Path(args.executable).resolve()
    model_dir = Path(args.model_dir).resolve()
    ffmpeg = Path(args.ffmpeg).resolve() if args.ffmpeg else Path(shutil.which("ffmpeg") or "").resolve()
    ffprobe = Path(args.ffprobe).resolve() if args.ffprobe else Path(shutil.which("ffprobe") or "").resolve()
    if not executable.is_file() or executable.is_symlink():
        raise RuntimeError("NCNN executable is missing or is a symbolic link")
    if not model_dir.is_dir() or model_dir.is_symlink():
        raise RuntimeError("model directory is missing or is a symbolic link")
    if not ffmpeg.is_file() or not ffprobe.is_file():
        raise RuntimeError("ffmpeg and ffprobe are required")
    model_files = _model_files(model_dir, args.model_name)
    started_at = datetime.now(UTC).isoformat()
    with tempfile.TemporaryDirectory(prefix="localdrama-ncnn-smoke-") as raw_temp:
        root = Path(raw_temp)
        inputs = root / "input"
        outputs = root / "output"
        inputs.mkdir()
        outputs.mkdir()
        fixture = _run(
            [
                str(ffmpeg), "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=32x24:rate=2:duration=1", "-frames:v", "2", str(inputs / "%08d.png"),
            ],
            timeout=30,
        )
        if fixture.returncode != 0 or len(list(inputs.glob("*.png"))) != 2:
            raise RuntimeError("could not create the isolated smoke frames")
        command = [
            str(executable), "-i", str(inputs), "-o", str(outputs), "-n", args.model_name,
            "-s", str(args.scale), "-t", str(args.tile), "-m", str(model_dir), "-g", str(args.gpu_device),
            "-j", f"{args.load_threads}:{args.proc_threads}:{args.save_threads}", "-f", "png",
        ]
        completed = _run(command, timeout=args.timeout_seconds)
        output_files = sorted(outputs.glob("*.png"))
        if completed.returncode != 0:
            raise RuntimeError(f"NCNN smoke exited {completed.returncode}: {_redact(completed.stderr, [executable, model_dir, root])}")
        if len(output_files) != 2:
            raise RuntimeError(f"NCNN smoke produced {len(output_files)} frames instead of 2")
        probes = [_probe(ffprobe, path) for path in output_files]
        expected = {"width": 32 * args.scale, "height": 24 * args.scale}
        if any({"width": int(item.get("width") or 0), "height": int(item.get("height") or 0)} != expected for item in probes):
            raise RuntimeError("NCNN smoke output geometry does not match the requested native scale")
        runtime_log = _redact(f"{completed.stdout}\n{completed.stderr}", [executable, model_dir, root])
        return {
            "schema_version": "localdrama.ncnn-video-upscale-smoke.v1",
            "status": "PASS",
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "adapter_code": "ncnn.realesrgan.video.v1",
            "runtime": {"executable_name": executable.name, "sha256": _sha256(executable)},
            "model": {
                "name": args.model_name,
                "native_scale": args.scale,
                "files": [{"name": path.name, "sha256": _sha256(path), "byte_size": path.stat().st_size} for path in model_files],
            },
            "device": {"vulkan_index": args.gpu_device, "runtime_log_excerpt": runtime_log},
            "parameters": {"tile": args.tile, "threads": [args.load_threads, args.proc_threads, args.save_threads]},
            "fixture": {"input_frames": 2, "output_frames": 2, "input": {"width": 32, "height": 24}, "output": expected},
            "network_used": False,
            "profile_published": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, help="Path to realesrgan-ncnn-vulkan executable")
    parser.add_argument("--model-dir", required=True, help="Directory containing matching .param/.bin files")
    parser.add_argument("--model-name", default="realesr-animevideov3")
    parser.add_argument("--scale", type=int, choices=(2, 3, 4), default=2)
    parser.add_argument("--gpu-device", type=int, default=0)
    parser.add_argument("--tile", type=int, choices=(0, 64, 128, 256, 512, 1024), default=0)
    parser.add_argument("--load-threads", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--proc-threads", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--save-threads", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--output", required=True, help="Evidence JSON path; parent must already exist")
    parsed = parser.parse_args()
    output = Path(parsed.output).resolve()
    if not output.parent.is_dir():
        parser.error("--output parent directory must already exist")
    try:
        evidence = smoke(parsed)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False))
        return 1
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "evidence": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

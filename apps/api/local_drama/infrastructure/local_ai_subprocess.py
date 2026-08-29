"""Offline subprocess boundary for the F-drive PyTorch model runtimes."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from local_drama.config import Settings


@dataclass(frozen=True)
class LocalAiExecution:
    command: tuple[str, ...]
    payload: dict[str, Any]


class LocalAiSubprocessRuntime:
    """Run one GPU model per child process and force every Hugging Face read offline."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _offline_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
        environment = os.environ.copy()
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            environment.pop(name, None)
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "NO_PROXY": "127.0.0.1,localhost",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        if extra:
            environment.update(extra)
        return environment

    @staticmethod
    def _require_file(path: Path | None, label: str) -> Path:
        if path is None or not path.is_file():
            raise RuntimeError(f"{label} is not configured or missing: {path}")
        return path

    @staticmethod
    def _require_dir(path: Path | None, label: str) -> Path:
        if path is None or not path.is_dir():
            raise RuntimeError(f"{label} is not configured or missing: {path}")
        return path

    def run_task(self, task: str, arguments: Sequence[str] = (), *, timeout: float = 1800) -> LocalAiExecution:
        python = self._require_file(self.settings.local_ai_python, "local_ai_python")
        adapter = self._require_file(self.settings.local_ai_adapter, "local_ai_adapter")
        model_root = self._require_dir(self.settings.local_ai_model_root, "local_ai_model_root")
        site_packages = model_root / "Runtimes" / "QwenVox" / "site-packages"
        command = (str(python), str(adapter), task, "--model-root", str(model_root), *arguments)
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=self._offline_environment({"PYTHONPATH": str(site_packages), "HF_HOME": str(model_root / "Runtimes" / "QwenVox" / "cache")}),
        )
        if result.returncode != 0:
            raise RuntimeError(f"local AI task {task} failed ({result.returncode}): {result.stderr[-2000:]}")
        payload = json.loads(result.stdout)
        if payload.get("status") != "PASS" or payload.get("network_used") is not False:
            raise RuntimeError(f"local AI task {task} returned an invalid execution receipt")
        return LocalAiExecution(command=command, payload=payload)

    def embed(self, texts: Sequence[str], *, instruction: str | None = None) -> LocalAiExecution:
        if not texts:
            raise ValueError("at least one text is required")
        arguments: list[str] = ["--include-vectors"]
        if instruction:
            arguments.extend(("--instruction", instruction))
        for value in texts:
            arguments.extend(("--text", value))
        return self.run_task("embedding", arguments)

    def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        prompt_audio: Path | None = None,
        prompt_text: str | None = None,
    ) -> LocalAiExecution:
        if not text.strip():
            raise ValueError("speech text must not be empty")
        arguments = ["--text", text, "--audio-output", str(output_path)]
        if prompt_audio is not None:
            arguments += ("--prompt-audio", str(prompt_audio))
        if prompt_text:
            arguments += ("--prompt-text", prompt_text)
        return self.run_task("voxcpm2", arguments)

    def transcribe(self, audio_path: Path) -> LocalAiExecution:
        return self.run_task("asr", ("--audio-input", str(audio_path)))

    def align(self, audio_path: Path, transcript: str, *, language: str = "Chinese") -> LocalAiExecution:
        return self.run_task(
            "alignment",
            ("--audio-input", str(audio_path), "--transcript", transcript, "--language", language),
        )

    def run_lipsync(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
        inference_steps: int = 20,
        timeout: float = 3600,
    ) -> LocalAiExecution:
        python = self._require_file(self.settings.latentsync_python, "latentsync_python")
        root = self._require_dir(self.settings.latentsync_root, "latentsync_root")
        if not video_path.is_file() or not audio_path.is_file():
            raise RuntimeError("LatentSync input video and audio must exist")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = (
            str(python),
            "-m",
            "scripts.inference",
            "--unet_config_path",
            "configs/unet/stage2_512.yaml",
            "--inference_ckpt_path",
            "checkpoints/latentsync_unet.pt",
            "--inference_steps",
            str(inference_steps),
            "--guidance_scale",
            "1.5",
            "--video_path",
            str(video_path.resolve()),
            "--audio_path",
            str(audio_path.resolve()),
            "--video_out_path",
            str(output_path.resolve()),
            "--temp_dir",
            str((output_path.parent / f"{output_path.stem}-temp").resolve()),
        )
        torch_lib = root / ".venv" / "Lib" / "site-packages" / "torch" / "lib"
        environment = self._offline_environment(
            {
                "PYTHONPATH": str(root),
                "HF_HOME": str(root / "cache"),
                "LATENTSYNC_VAE_PATH": str(root / "models" / "sd-vae-ft-mse"),
                "PATH": os.pathsep.join((str(torch_lib), str(Path(self.settings.ffmpeg_path or "").parent), os.environ.get("PATH", ""))),
            }
        )
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
        if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError(f"LatentSync failed ({result.returncode}): {result.stderr[-2000:]}{result.stdout[-2000:]}")
        payload = {
            "task": "latentsync",
            "status": "PASS",
            "video_input": str(video_path.resolve()),
            "audio_input": str(audio_path.resolve()),
            "output": str(output_path.resolve()),
            "output_bytes": output_path.stat().st_size,
            "inference_steps": inference_steps,
            "network_used": False,
        }
        return LocalAiExecution(command=command, payload=payload)

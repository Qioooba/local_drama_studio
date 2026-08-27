from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from local_drama.platform.contracts import TtsRuntimeError


class WindowsSapiRuntime:
    name = "WINDOWS_SAPI"

    @property
    def available(self) -> bool:
        return bool(shutil.which("powershell.exe") or shutil.which("powershell"))

    def capability_summary(self) -> dict[str, object]:
        return {"status": "AVAILABLE" if self.available else "UNAVAILABLE", "runtime": self.name}

    @staticmethod
    def _powershell() -> str | None:
        return shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")

    def discover_voices(self) -> dict[str, object]:
        powershell = self._powershell()
        base = {"runtime_contacted": False, "network_contacted": False, "mutated": False}
        if not powershell:
            return {"status": "UNAVAILABLE", "items": [], "message": "本机未找到 PowerShell/System.Speech runtime", **base}
        script = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "try { $s.GetInstalledVoices() | ForEach-Object { $v=$_.VoiceInfo; "
            "[pscustomobject]@{name=$v.Name; culture=$v.Culture.Name; gender=$v.Gender.ToString(); "
            "age=$v.Age.ToString(); voice_ref=('sapi:' + $v.Name)} } | ConvertTo-Json -Compress } "
            "finally { $s.Dispose() }"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return {"status": "UNAVAILABLE", "items": [], "message": f"本机 SAPI 音色扫描失败：{type(error).__name__}", "runtime_contacted": True, "network_contacted": False, "mutated": False}
        if result.returncode != 0:
            return {"status": "UNAVAILABLE", "items": [], "message": "System.Speech 未能读取本机音色", "runtime_contacted": True, "network_contacted": False, "mutated": False}
        try:
            payload: Any = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            payload = []
        rows = payload if isinstance(payload, list) else [payload]
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            if name:
                items.append({
                    "name": name,
                    "culture": str(row.get("culture", "")).strip(),
                    "gender": str(row.get("gender", "")).strip(),
                    "age": str(row.get("age", "")).strip(),
                    "voice_ref": f"sapi:{name}",
                })
        return {"status": "AVAILABLE" if items else "EMPTY", "items": items, "message": None, "runtime_contacted": True, "network_contacted": False, "mutated": False}

    def synthesize(self, *, voice: str, text: str, output: Path, rate: int = 0, timeout: int = 120) -> None:
        powershell = self._powershell()
        if not powershell:
            raise TtsRuntimeError("PowerShell/System.Speech runtime is unavailable")
        output.parent.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update({
            "LOCAL_DRAMA_TTS_VOICE": voice,
            "LOCAL_DRAMA_TTS_RATE": str(max(-10, min(10, rate))),
            "LOCAL_DRAMA_TTS_OUTPUT": str(output),
            "LOCAL_DRAMA_TTS_TEXT": text,
        })
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "try { $s.SelectVoice($env:LOCAL_DRAMA_TTS_VOICE); $s.Rate=[int]$env:LOCAL_DRAMA_TTS_RATE; "
            "$s.SetOutputToWaveFile($env:LOCAL_DRAMA_TTS_OUTPUT); $s.Speak($env:LOCAL_DRAMA_TTS_TEXT) } "
            "finally { $s.Dispose() }"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TtsRuntimeError(type(error).__name__) from error
        if result.returncode != 0:
            raise TtsRuntimeError(result.stderr[-500:])
        if not output.is_file() or output.stat().st_size <= 44:
            raise TtsRuntimeError("TTS runtime did not produce a valid WAV file")

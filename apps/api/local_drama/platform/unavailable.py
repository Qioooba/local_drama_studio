from __future__ import annotations

from pathlib import Path

from local_drama.domain.errors import DomainRuleError

from .contracts import FilePickerRequest, FilePickerResult, SecretRef, TtsRuntimeError


class UnavailableSecretStore:
    name = "UNAVAILABLE"
    available = False

    def get(self, ref: SecretRef) -> str | None:
        del ref
        return None

    def put(self, ref: SecretRef, value: str) -> None:
        del ref, value
        raise OSError("secure secret store is unavailable on this platform")

    def delete(self, ref: SecretRef) -> bool:
        del ref
        return False


class UnavailableFilePicker:
    name = "UNAVAILABLE"
    available = False

    def choose(self, request: FilePickerRequest) -> FilePickerResult:
        del request
        raise DomainRuleError(
            "LOCAL_FILE_PICKER_UNAVAILABLE",
            "当前运行配置不提供服务器桌面文件选择器",
            suggested_action="请使用浏览器上传，或从管理员配置的资源库选择",
        )


class UnavailableTtsRuntime:
    name = "UNAVAILABLE"
    available = False

    def capability_summary(self) -> dict[str, object]:
        return {"status": "UNAVAILABLE", "runtime": self.name}

    def discover_voices(self) -> dict[str, object]:
        return {
            "status": "UNAVAILABLE",
            "items": [],
            "message": "当前平台没有已配置的本地 TTS 运行时",
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def synthesize(self, *, voice: str, text: str, output: Path, rate: int = 0, timeout: int = 120) -> None:
        del voice, text, output, rate, timeout
        raise TtsRuntimeError("local TTS runtime is unavailable")

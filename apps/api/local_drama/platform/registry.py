from __future__ import annotations

import os
from dataclasses import dataclass

from local_drama.config import Settings
from local_drama.platform.contracts import FilePicker, SecretStore, TtsRuntime
from local_drama.platform.unavailable import UnavailableFilePicker, UnavailableSecretStore, UnavailableTtsRuntime


@dataclass(frozen=True)
class PlatformServices:
    os_name: str
    install_profile: str
    secret_store: SecretStore
    file_picker: FilePicker
    tts_runtime: TtsRuntime

    def public_capabilities(self) -> dict[str, object]:
        return {
            "os": self.os_name,
            "install_profile": self.install_profile,
            "interactive": self.file_picker.available,
            "capabilities": {
                "native_file_picker": "AVAILABLE" if self.file_picker.available else "UNAVAILABLE_BY_PROFILE",
                "local_tts": "AVAILABLE" if self.tts_runtime.available else "UNAVAILABLE",
                "secret_store": "AVAILABLE" if self.secret_store.available else "UNAVAILABLE",
            },
            "adapters": {
                "file_picker": self.file_picker.name,
                "tts": self.tts_runtime.name,
                "secret_store": self.secret_store.name,
            },
        }


def create_platform_services(settings: Settings) -> PlatformServices:
    if os.name == "nt":
        from local_drama.platform.windows.credentials import WindowsCredentialStore
        from local_drama.platform.windows.file_picker import WindowsFilePicker
        from local_drama.platform.windows.tts import WindowsSapiRuntime

        picker: FilePicker = WindowsFilePicker() if settings.install_profile == "DESKTOP" else UnavailableFilePicker()
        return PlatformServices("windows", settings.install_profile, WindowsCredentialStore(), picker, WindowsSapiRuntime())
    if os.name == "posix":
        from local_drama.platform.linux.credentials import LinuxFileSecretStore

        return PlatformServices(
            "linux",
            settings.install_profile,
            LinuxFileSecretStore(settings.instance_root / "config" / "secrets"),
            UnavailableFilePicker(),
            UnavailableTtsRuntime(),
        )
    return PlatformServices(os.name, settings.install_profile, UnavailableSecretStore(), UnavailableFilePicker(), UnavailableTtsRuntime())

from __future__ import annotations

from pathlib import Path

from local_drama.config import Settings
from local_drama.infrastructure.local_ai_subprocess import LocalAiSubprocessRuntime


def test_local_ai_runtime_removes_proxy_and_forces_offline(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    environment = LocalAiSubprocessRuntime._offline_environment()
    assert "HTTP_PROXY" not in environment
    assert "HTTPS_PROXY" not in environment
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert environment["NO_PROXY"] == "127.0.0.1,localhost"


def test_machine_config_resolves_f_drive_local_ai_runtime_paths() -> None:
    settings = Settings.from_env()
    assert settings.local_ai_model_root == Path(r"F:\AI_Models\LocalDramaStudio")
    assert settings.local_ai_adapter is not None and settings.local_ai_adapter.is_file()
    assert settings.local_ai_python is not None and settings.local_ai_python.is_file()
    assert settings.latentsync_root is not None and settings.latentsync_root.is_dir()
    assert settings.latentsync_python is not None and settings.latentsync_python.is_file()

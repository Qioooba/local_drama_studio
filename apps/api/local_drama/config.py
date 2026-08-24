from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseModel):
    app_name: str = "LocalDramaStudio"
    app_version: str = "0.1.0-g1"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 3210
    mode: str = Field(default="LOCAL_ONLY", pattern="^LOCAL_ONLY$")
    data_root: Path = REPO_ROOT / "data"
    projects_root: Path = REPO_ROOT / "projects"
    work_root: Path = REPO_ROOT / "work"
    cache_root: Path = REPO_ROOT / "cache"
    logs_root: Path = REPO_ROOT / "logs"
    backups_root: Path = REPO_ROOT / "backups"
    comfy_base_url: str = "http://127.0.0.1:8188"
    comfy_output_root: Path | None = REPO_ROOT / "work" / "comfy-production" / "output"
    comfy_input_root: Path | None = REPO_ROOT / "work" / "comfy-production" / "input"
    llm_provider: str = "OLLAMA_LOOPBACK"
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str | None = None
    llm_api_key: str | None = None
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:3210",
        "http://localhost:3210",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )

    @field_validator("host")
    @classmethod
    def validate_local_bind_host(cls, value: str) -> str:
        """Fail closed instead of allowing a LOCAL_ONLY API on a LAN/public bind."""

        normalized = value.strip().casefold()
        if normalized not in _LOCAL_BIND_HOSTS:
            raise ValueError("LOCAL_ONLY API host must be a literal loopback address")
        return normalized

    @property
    def workspace_root(self) -> Path:
        return REPO_ROOT

    @property
    def manifest_path(self) -> Path:
        local_in_repo = self.workspace_root / "model_manifest.json"
        if local_in_repo.exists():
            return local_in_repo
        return self.workspace_root.parent / "model_manifest.json"

    @property
    def ffmpeg_path(self) -> str | None:
        configured = os.environ.get("LOCAL_DRAMA_FFMPEG")
        if configured and Path(configured).exists():
            return configured
        which_path = shutil.which("ffmpeg")
        if which_path:
            return which_path
        candidate = Path(r"E:\Tools\ffmpeg\bin\ffmpeg.exe")
        return str(candidate) if candidate.exists() else None

    @property
    def ffprobe_path(self) -> str | None:
        configured = os.environ.get("LOCAL_DRAMA_FFPROBE")
        if configured and Path(configured).exists():
            return configured
        which_path = shutil.which("ffprobe")
        if which_path:
            return which_path
        candidate = Path(r"E:\Tools\ffmpeg\bin\ffprobe.exe")
        return str(candidate) if candidate.exists() else None

    @property
    def database_path(self) -> Path:
        return self.data_root / "local_drama.sqlite3"

    @property
    def workflow_packages_root(self) -> Path:
        return self.work_root / "workflow_packages"

    @classmethod
    def from_env(cls) -> "Settings":
        values: dict[str, Any] = {}
        for field_name in ("host", "environment", "mode", "comfy_base_url", "llm_provider", "llm_base_url", "llm_model", "llm_api_key"):
            env_name = f"LOCAL_DRAMA_{field_name.upper()}"
            if env_name in os.environ:
                values[field_name] = os.environ[env_name]
        # Support alternative/convenience environment variable aliases for OpenAI compatibility
        if "LOCAL_DRAMA_OPENAI_COMPAT_BASE_URL" in os.environ and "llm_base_url" not in values:
            values["llm_base_url"] = os.environ["LOCAL_DRAMA_OPENAI_COMPAT_BASE_URL"]
            values.setdefault("llm_provider", "OPENAI_COMPAT")
        if "LOCAL_DRAMA_OPENAI_COMPAT_MODEL" in os.environ and "llm_model" not in values:
            values["llm_model"] = os.environ["LOCAL_DRAMA_OPENAI_COMPAT_MODEL"]
            values.setdefault("llm_provider", "OPENAI_COMPAT")
        if "LOCAL_DRAMA_OPENAI_COMPAT_API_KEY" in os.environ and "llm_api_key" not in values:
            values["llm_api_key"] = os.environ["LOCAL_DRAMA_OPENAI_COMPAT_API_KEY"]
        elif "DEEPSEEK_API_KEY" in os.environ and "llm_api_key" not in values:
            values["llm_api_key"] = os.environ["DEEPSEEK_API_KEY"]
        elif "OPENAI_API_KEY" in os.environ and "llm_api_key" not in values:
            values["llm_api_key"] = os.environ["OPENAI_API_KEY"]

        if "LOCAL_DRAMA_PORT" in os.environ:
            values["port"] = int(os.environ["LOCAL_DRAMA_PORT"])
        if "LOCAL_DRAMA_ALLOWED_ORIGINS" in os.environ:
            # Comma-separated list of additional allowed origins (e.g. a LAN
            # origin like http://192.168.1.120:5173 for cross-machine dev
            # access). Appends to the loopback defaults; never replaces them.
            extra = tuple(
                origin.strip()
                for origin in os.environ["LOCAL_DRAMA_ALLOWED_ORIGINS"].split(",")
                if origin.strip()
            )
            if extra:
                defaults = cls.model_fields["allowed_origins"].default
                values["allowed_origins"] = tuple(defaults) + extra
        if "LOCAL_DRAMA_COMFY_OUTPUT_ROOT" in os.environ:
            values["comfy_output_root"] = Path(os.environ["LOCAL_DRAMA_COMFY_OUTPUT_ROOT"])
        else:
            values["comfy_output_root"] = REPO_ROOT / "work" / "comfy-production" / "output"
        if "LOCAL_DRAMA_COMFY_INPUT_ROOT" in os.environ:
            values["comfy_input_root"] = Path(os.environ["LOCAL_DRAMA_COMFY_INPUT_ROOT"])
        else:
            values["comfy_input_root"] = REPO_ROOT / "work" / "comfy-production" / "input"
        return cls(**values)

    def ensure_roots(self) -> None:
        for path in (
            self.data_root,
            self.projects_root,
            self.work_root,
            self.cache_root,
            self.logs_root,
            self.backups_root,
            self.workflow_packages_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

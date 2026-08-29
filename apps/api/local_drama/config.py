from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from local_drama.bootstrap.build_identity import load_build_identity
from local_drama.bootstrap.config_loader import load_machine_config, settings_values
from local_drama.bootstrap.resource_locator import ResourceLocator
from local_drama.domain.network_policy import NetworkMode, normalize_network_mode, validate_bind_host
from local_drama.infrastructure.filesystem.path_policy import absolute_path, controlled_path, is_reparse_point

REPO_ROOT = Path(__file__).resolve().parents[3]


class StorageSettings(BaseModel):
    data_root: Path
    projects_root: Path
    work_root: Path
    cache_root: Path
    logs_root: Path
    backups_root: Path
    comfy_input_root: Path
    comfy_output_root: Path


class UploadLimits(BaseModel):
    image_mb: int = Field(gt=0)
    video_mb: int = Field(gt=0)
    audio_mb: int = Field(gt=0)
    document_mb: int = Field(gt=0)
    project_resource_mb: int = Field(gt=0)
    project_package_mb: int = Field(gt=0)


class RuntimeSettings(BaseModel):
    comfy_base_url: str
    llm_provider: str
    llm_base_url: str
    llm_model: str | None
    allow_private_network: bool


class Settings(BaseModel):
    app_name: str = "LocalDramaStudio"
    app_version: str = Field(default_factory=lambda: load_build_identity().version)
    environment: str = "development"
    instance_id: str = "default"
    install_profile: str = Field(default="DESKTOP", pattern="^(DESKTOP|SERVER)$")
    host: str = "127.0.0.1"
    port: int = Field(default=3210, ge=1, le=65535)
    mode: str = Field(default="LOCAL_ONLY", pattern="^LOCAL_ONLY$")
    network_mode: NetworkMode = NetworkMode.LOCAL_ONLY
    data_root: Path = REPO_ROOT / "data"
    projects_root: Path = REPO_ROOT / "projects"
    work_root: Path = REPO_ROOT / "work"
    cache_root: Path = REPO_ROOT / "cache"
    logs_root: Path = REPO_ROOT / "logs"
    backups_root: Path = REPO_ROOT / "backups"
    comfy_base_url: str = "http://127.0.0.1:8188"
    comfy_output_root: Path | None = None
    comfy_input_root: Path | None = None
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
    tool_fallback_dirs: tuple[str, ...] = ()
    ffmpeg_override: Path | None = Field(default=None, exclude=True)
    ffprobe_override: Path | None = Field(default=None, exclude=True)
    upload_max_image_mb: int = Field(default=25, gt=0)
    upload_max_video_mb: int = Field(default=100, gt=0)
    upload_max_audio_mb: int = Field(default=50, gt=0)
    upload_max_document_mb: int = Field(default=25, gt=0)
    upload_max_project_resource_mb: int = Field(default=50, gt=0)
    upload_max_project_package_mb: int = Field(default=2048, gt=0)
    model_root: Path | None = Field(default=None, exclude=True)
    model_library_roots: tuple[Path, ...] = ()
    model_download_source_hosts: tuple[str, ...] = ()
    worker_channels: tuple[str, ...] = ("CPU", "GPU_H3")
    local_ai_python: Path | None = Field(default=None, exclude=True)
    local_ai_adapter: Path | None = Field(default=None, exclude=True)
    local_ai_model_root: Path | None = Field(default=None, exclude=True)
    latentsync_python: Path | None = Field(default=None, exclude=True)
    latentsync_root: Path | None = Field(default=None, exclude=True)
    frontend_dist_root: Path | None = None
    model_manifest_override: Path | None = None
    trusted_lan_unauthenticated: bool = False
    release_root: Path = Field(default=REPO_ROOT, exclude=True)
    instance_root: Path = Field(default=REPO_ROOT, exclude=True)
    config_path: Path | None = Field(default=None, exclude=True)

    @field_validator("network_mode", mode="before")
    @classmethod
    def validate_network_mode(cls, value: str | NetworkMode) -> NetworkMode:
        return normalize_network_mode(value)

    @model_validator(mode="after")
    def resolve_dependent_settings(self) -> "Settings":
        self.host = validate_bind_host(self.host, self.network_mode)
        if self.is_lan_service and not self.trusted_lan_unauthenticated:
            raise ValueError(
                "LAN_SERVICE requires explicit trusted_lan_unauthenticated=true "
                "until administrator authentication is configured"
            )
        if not self.is_lan_service and self.trusted_lan_unauthenticated:
            raise ValueError("LOCAL_ONLY must not enable trusted_lan_unauthenticated")
        self.release_root = absolute_path(self.release_root)
        self.instance_root = absolute_path(self.instance_root)
        for field_name in (
            "data_root",
            "projects_root",
            "work_root",
            "cache_root",
            "logs_root",
            "backups_root",
        ):
            setattr(self, field_name, absolute_path(getattr(self, field_name), base=self.instance_root))
        storage_roots = (
            self.data_root,
            self.projects_root,
            self.work_root,
            self.cache_root,
            self.logs_root,
            self.backups_root,
        )
        if len(set(storage_roots)) != len(storage_roots):
            raise ValueError("mutable storage roots must resolve to distinct directories")
        for field_name in (
            "ffmpeg_override",
            "ffprobe_override",
            "local_ai_python",
            "local_ai_adapter",
            "local_ai_model_root",
            "latentsync_python",
            "latentsync_root",
            "model_root",
            "frontend_dist_root",
            "model_manifest_override",
        ):
            value = getattr(self, field_name)
            if value is not None:
                setattr(self, field_name, absolute_path(value, base=self.instance_root))
        self.model_library_roots = tuple(
            absolute_path(root, base=self.instance_root) for root in self.model_library_roots
        )
        self.model_download_source_hosts = tuple(item.strip().casefold() for item in self.model_download_source_hosts if item.strip())
        self.tool_fallback_dirs = tuple(
            str(absolute_path(directory, base=self.instance_root)) for directory in self.tool_fallback_dirs
        )
        if self.comfy_input_root is None:
            self.comfy_input_root = self.work_root / "comfy-production" / "input"
        else:
            self.comfy_input_root = absolute_path(self.comfy_input_root, base=self.instance_root)
        if self.comfy_output_root is None:
            self.comfy_output_root = self.work_root / "comfy-production" / "output"
        else:
            self.comfy_output_root = absolute_path(self.comfy_output_root, base=self.instance_root)
        return self

    @property
    def is_lan_service(self) -> bool:
        return self.network_mode is NetworkMode.LAN_SERVICE

    @property
    def allows_private_network(self) -> bool:
        return self.is_lan_service

    @property
    def storage(self) -> StorageSettings:
        assert self.comfy_input_root is not None and self.comfy_output_root is not None
        return StorageSettings(
            data_root=self.data_root,
            projects_root=self.projects_root,
            work_root=self.work_root,
            cache_root=self.cache_root,
            logs_root=self.logs_root,
            backups_root=self.backups_root,
            comfy_input_root=self.comfy_input_root,
            comfy_output_root=self.comfy_output_root,
        )

    @property
    def uploads(self) -> UploadLimits:
        return UploadLimits(
            image_mb=self.upload_max_image_mb,
            video_mb=self.upload_max_video_mb,
            audio_mb=self.upload_max_audio_mb,
            document_mb=self.upload_max_document_mb,
            project_resource_mb=self.upload_max_project_resource_mb,
            project_package_mb=self.upload_max_project_package_mb,
        )

    @property
    def runtime(self) -> RuntimeSettings:
        return RuntimeSettings(
            comfy_base_url=self.comfy_base_url,
            llm_provider=self.llm_provider,
            llm_base_url=self.llm_base_url,
            llm_model=self.llm_model,
            allow_private_network=self.allows_private_network,
        )

    @property
    def workspace_root(self) -> Path:
        return self.release_root

    @property
    def manifest_path(self) -> Path:
        if self.model_manifest_override is not None:
            return self.model_manifest_override
        local_in_repo = self.workspace_root / "model_manifest.json"
        if local_in_repo.exists():
            return local_in_repo
        return self.workspace_root.parent / "model_manifest.json"

    def _resolve_tool(self, executable: str, configured: Path | None) -> str | None:
        if configured is not None and configured.is_file():
            return str(configured)
        which_path = shutil.which(executable)
        if which_path:
            return which_path
        for directory in self.tool_fallback_dirs:
            candidate = Path(directory) / f"{executable}.exe"
            if candidate.is_file():
                return str(candidate)
        return None

    @property
    def ffmpeg_path(self) -> str | None:
        return self._resolve_tool("ffmpeg", self.ffmpeg_override)

    @property
    def ffprobe_path(self) -> str | None:
        return self._resolve_tool("ffprobe", self.ffprobe_override)

    @property
    def database_path(self) -> Path:
        return self.data_root / "local_drama.sqlite3"

    @property
    def workflow_packages_root(self) -> Path:
        return self.work_root / "workflow_packages"

    def resolve_project_root(self, root_rel: str | Path, *, must_exist: bool = True) -> Path:
        """Resolve the canonical database ``projects.root_rel`` contract."""
        root = controlled_path(
            self.projects_root,
            root_rel,
            must_exist=must_exist,
            code="PATH_ESCAPE",
        )
        if must_exist and (not root.is_dir() or is_reparse_point(root)):
            from local_drama.domain.errors import DomainRuleError

            raise DomainRuleError("PATH_ESCAPE", "项目目录不存在或不是安全目录")
        return root

    @property
    def resolved_frontend_dist_root(self) -> Path | None:
        configured = self.frontend_dist_root
        if configured is not None:
            if not configured.is_dir() or not (configured / "index.html").is_file():
                raise ValueError("LOCAL_DRAMA_FRONTEND_DIST must contain an index.html build artifact")
            return configured
        candidates = (
            self.release_root / "web",
            self.release_root / "apps" / "web" / "dist",
        )
        return next((path for path in candidates if (path / "index.html").is_file()), None)

    @classmethod
    def from_env(cls) -> "Settings":
        locator = ResourceLocator.discover()
        machine_config = load_machine_config(
            locator.config_path,
            release_root=locator.release_root,
            instance_root=locator.instance_root,
        )
        # A packaged instance always starts from instance-owned mutable roots.
        # A partial machine config must never fall back to source/build paths.
        values: dict[str, Any] = locator.storage_roots()
        if machine_config is not None:
            values.update(settings_values(machine_config))
        values.update(
            {
                "release_root": locator.release_root,
                "instance_root": locator.instance_root,
                "config_path": locator.config_path,
            }
        )
        if machine_config is None and locator.packaged:
            values["frontend_dist_root"] = locator.frontend_dist
            values["model_manifest_override"] = locator.default_manifest_path
        for field_name in ("host", "environment", "mode", "network_mode", "comfy_base_url", "llm_provider", "llm_base_url", "llm_model", "llm_api_key"):
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
            extra = tuple(origin.strip() for origin in os.environ["LOCAL_DRAMA_ALLOWED_ORIGINS"].split(",") if origin.strip())
            if extra:
                defaults = cls.model_fields["allowed_origins"].default
                values["allowed_origins"] = tuple(defaults) + extra
        for field_name in ("data_root", "projects_root", "work_root", "cache_root", "logs_root", "backups_root"):
            env_name = f"LOCAL_DRAMA_{field_name.upper()}"
            if env_name in os.environ:
                values[field_name] = Path(os.environ[env_name])
        for field_name in ("comfy_output_root", "comfy_input_root"):
            env_name = f"LOCAL_DRAMA_{field_name.upper()}"
            if env_name in os.environ:
                values[field_name] = Path(os.environ[env_name])
        for field_name in (
            "local_ai_python",
            "local_ai_adapter",
            "local_ai_model_root",
            "latentsync_python",
            "latentsync_root",
        ):
            env_name = f"LOCAL_DRAMA_{field_name.upper()}"
            if env_name in os.environ:
                values[field_name] = Path(os.environ[env_name])
        if "LOCAL_DRAMA_TOOL_FALLBACK_DIRS" in os.environ:
            values["tool_fallback_dirs"] = tuple(part.strip() for part in os.environ["LOCAL_DRAMA_TOOL_FALLBACK_DIRS"].split(";") if part.strip())
        if "LOCAL_DRAMA_FFMPEG" in os.environ:
            values["ffmpeg_override"] = Path(os.environ["LOCAL_DRAMA_FFMPEG"])
        if "LOCAL_DRAMA_FFPROBE" in os.environ:
            values["ffprobe_override"] = Path(os.environ["LOCAL_DRAMA_FFPROBE"])
        for field_name in (
            "upload_max_image_mb",
            "upload_max_video_mb",
            "upload_max_audio_mb",
            "upload_max_document_mb",
            "upload_max_project_resource_mb",
            "upload_max_project_package_mb",
        ):
            env_name = f"LOCAL_DRAMA_{field_name.upper()}"
            if env_name in os.environ:
                values[field_name] = int(os.environ[env_name])
        if "LOCAL_DRAMA_MODEL_LIBRARY_ROOTS" in os.environ:
            values["model_library_roots"] = tuple(Path(part.strip()) for part in os.environ["LOCAL_DRAMA_MODEL_LIBRARY_ROOTS"].split(";") if part.strip())
        if "LOCAL_DRAMA_MODEL_DOWNLOAD_SOURCE_HOSTS" in os.environ:
            values["model_download_source_hosts"] = tuple(part.strip() for part in os.environ["LOCAL_DRAMA_MODEL_DOWNLOAD_SOURCE_HOSTS"].split(";") if part.strip())
        if "LOCAL_DRAMA_MODEL_ROOT" in os.environ:
            values["model_root"] = Path(os.environ["LOCAL_DRAMA_MODEL_ROOT"])
        if "LOCAL_DRAMA_FRONTEND_DIST" in os.environ:
            values["frontend_dist_root"] = Path(os.environ["LOCAL_DRAMA_FRONTEND_DIST"])
        return cls(**values)

    def ensure_roots(self) -> None:
        paths = [
            self.data_root,
            self.projects_root,
            self.work_root,
            self.cache_root,
            self.logs_root,
            self.backups_root,
            self.workflow_packages_root,
        ]
        if self.model_root is not None:
            paths.extend((
                self.model_root,
                self.model_root / "downloads",
                self.model_root / "staging",
                self.model_root / "quarantine",
            ))
        paths.extend(self.model_library_roots)
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir() or is_reparse_point(path):
                raise ValueError(f"mutable storage root is not a safe directory: {path}")

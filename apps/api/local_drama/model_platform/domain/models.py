"""Path-safe identity objects for model releases, components, and runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath


class RuntimeKind(str, Enum):
    OLLAMA = "OLLAMA"
    # A llama-server child process owned by the application's GPU runtime
    # coordinator (LLAMA_CPP scheduler runtime); distinct from OLLAMA because
    # its lifecycle is process-managed rather than service-managed.
    LLAMA_CPP_MANAGED = "LLAMA_CPP_MANAGED"
    COMFYUI = "COMFYUI"
    PYTORCH_PROCESS = "PYTORCH_PROCESS"
    OS_NATIVE = "OS_NATIVE"
    TOOL_PROCESS = "TOOL_PROCESS"
    LOCAL_HTTP = "LOCAL_HTTP"
    REMOTE_HTTP = "REMOTE_HTTP"


class ArtifactKind(str, Enum):
    FILE = "FILE"
    DIRECTORY_MANIFEST = "DIRECTORY_MANIFEST"
    RUNTIME_TAG_MANIFEST = "RUNTIME_TAG_MANIFEST"


class ComponentRole(str, Enum):
    PRIMARY_MODEL = "PRIMARY_MODEL"
    DIFFUSION_MODEL = "DIFFUSION_MODEL"
    TEXT_ENCODER = "TEXT_ENCODER"
    VISION_ENCODER = "VISION_ENCODER"
    VAE_IMAGE = "VAE_IMAGE"
    VAE_VIDEO = "VAE_VIDEO"
    VAE_AUDIO = "VAE_AUDIO"
    TOKENIZER = "TOKENIZER"
    PROCESSOR = "PROCESSOR"
    LORA = "LORA"
    CONTROLNET = "CONTROLNET"
    UPSCALER = "UPSCALER"
    SYNC_NETWORK = "SYNC_NETWORK"
    AUXILIARY_MODEL = "AUXILIARY_MODEL"
    CONFIG = "CONFIG"


@dataclass(frozen=True, slots=True)
class ModelArtifactIdentity:
    """Content identity independent of any server or disk location."""

    kind: ArtifactKind
    content_sha256: str
    size_bytes: int
    format: str

    def __post_init__(self) -> None:
        digest = self.content_sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("content_sha256 must be a 64-character lowercase-or-uppercase SHA-256 digest")
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")
        if not self.format.strip():
            raise ValueError("format is required")
        object.__setattr__(self, "content_sha256", digest)


@dataclass(frozen=True, slots=True)
class ModelArtifactLocation:
    """A node-specific location expressed only through a library and path.

    The storage resolver owns the library root.  Keeping absolute paths out of
    this object means a profile remains reproducible when F: changes, a model
    is copied to another node, or a deployment selects a different data disk.
    """

    library_code: str
    relative_path: str

    def __post_init__(self) -> None:
        if not self.library_code.strip():
            raise ValueError("library_code is required")
        raw = self.relative_path.replace("\\", "/")
        windows_path = PureWindowsPath(raw)
        posix_path = PurePosixPath(raw)
        if not raw or windows_path.is_absolute() or posix_path.is_absolute() or windows_path.drive:
            raise ValueError("relative_path must not be an absolute Windows or POSIX path")
        if any(segment in ("", ".", "..") for segment in posix_path.parts):
            raise ValueError("relative_path must stay within its model library")
        object.__setattr__(self, "relative_path", posix_path.as_posix())


@dataclass(frozen=True, slots=True)
class ModelReleaseIdentity:
    """A stable upstream/version identity for one model release."""

    family_code: str
    release_code: str
    upstream_id: str
    revision: str
    format: str
    quantization: str | None = None

    def __post_init__(self) -> None:
        required = (self.family_code, self.release_code, self.upstream_id, self.revision, self.format)
        if any(not value.strip() for value in required):
            raise ValueError("family_code, release_code, upstream_id, revision, and format are required")


@dataclass(frozen=True, slots=True)
class ModelComponent:
    """A release dependency; shared encoders/VAEs stay components, not models."""

    release_code: str
    artifact_sha256: str
    role: ComponentRole
    ordinal: int = 0
    required: bool = True
    shared: bool = False

    def __post_init__(self) -> None:
        if not self.release_code.strip():
            raise ValueError("release_code is required")
        if self.ordinal < 0:
            raise ValueError("ordinal cannot be negative")


@dataclass(frozen=True, slots=True)
class RuntimeModelInstallation:
    """How one release is addressed by a concrete runtime installation.

    ``native_locator`` is an Ollama tag/digest, a Comfy category-relative
    locator, a PyTorch package identifier, an OS voice token, or a remote model
    identifier.  It is never a page-submitted path or secret-bearing URL.
    """

    release_code: str
    runtime_kind: RuntimeKind
    runtime_installation_version_id: str
    native_locator: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.release_code, self.runtime_installation_version_id, self.native_locator)):
            raise ValueError("release_code, runtime_installation_version_id, and native_locator are required")

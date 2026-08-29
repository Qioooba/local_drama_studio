"""Runtime-neutral discovery contracts and the Ollama implementation.

Discovery describes what a runtime reports.  It never declares an installation
complete, validates a capability, or publishes a Profile.  That separation is
intentional: a transient Ollama catalog must not silently rewire production.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, cast

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.local_model_catalog import DECLARED_MODEL_CAPABILITIES
from local_drama.model_platform.domain.capabilities import GenerationCapability
from local_drama.model_platform.domain.models import RuntimeKind
from local_drama.model_platform.domain.states import PresenceStatus, RuntimeStatus


class OllamaCatalogClient(Protocol):
    """The read-only subset of the Ollama API used by discovery."""

    def tags(self) -> list[dict[str, Any]]: ...

    def show(self, model: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AdapterEvidence:
    code: str
    message: str
    details: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    """A native-runtime declaration, deliberately not a published offering."""

    capability: str
    reason: str


@dataclass(frozen=True, slots=True)
class NativeModelObservation:
    runtime_kind: RuntimeKind
    native_locator: str
    digest: str | None
    size_bytes: int | None
    modified_at: str | None
    presence: PresenceStatus
    metadata: Mapping[str, object]
    candidate_capabilities: tuple[CapabilityCandidate, ...]
    detail_refreshed: bool


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    runtime_kind: RuntimeKind
    runtime_status: RuntimeStatus
    observations: tuple[NativeModelObservation, ...]
    evidence: tuple[AdapterEvidence, ...]
    read_only: bool = True


class RuntimeAdapter(Protocol):
    """Common V2 adapter boundary.

    Additional lifecycle methods are introduced beside this interface as their
    contracts land; discovery must remain usable without loading model weights.
    """

    runtime_kind: RuntimeKind

    def discover(self, *, known_native_digests: Mapping[str, str] | None = None) -> DiscoveryReport: ...


class OllamaRuntimeAdapter:
    """Normalize Ollama tags and refreshed native metadata into V2 observations."""

    runtime_kind = RuntimeKind.OLLAMA

    def __init__(self, catalog: OllamaCatalogClient) -> None:
        self.catalog = catalog

    def discover(self, *, known_native_digests: Mapping[str, str] | None = None) -> DiscoveryReport:
        known_native_digests = known_native_digests or {}
        try:
            tags = self.catalog.tags()
        except DomainRuleError as error:
            return DiscoveryReport(
                runtime_kind=self.runtime_kind,
                runtime_status=RuntimeStatus.UNREACHABLE,
                observations=(),
                evidence=(AdapterEvidence(error.code, error.message, dict(error.details)),),
            )

        observations: list[NativeModelObservation] = []
        evidence: list[AdapterEvidence] = []
        for tag in sorted(tags, key=_ollama_name):
            name = _ollama_name(tag)
            if not name:
                evidence.append(
                    AdapterEvidence(
                        "OLLAMA_TAG_INVALID", "Ollama 返回的 tag 缺少名称，已跳过。", {"tag": dict(tag)}
                    )
                )
                continue
            digest = _optional_text(tag.get("digest"))
            refresh_details = known_native_digests.get(name) != digest
            details: dict[str, Any] = _as_dict(tag.get("details"))
            if refresh_details:
                try:
                    details = {**details, **_as_dict(self.catalog.show(name))}
                except DomainRuleError as error:
                    evidence.append(
                        AdapterEvidence(
                            "OLLAMA_SHOW_FAILED",
                            "Ollama tag 已发现，但详情读取失败；保留为未验证候选。",
                            {"native_locator": name, "error_code": error.code},
                        )
                    )
            metadata = _ollama_metadata(tag, details)
            observations.append(
                NativeModelObservation(
                    runtime_kind=self.runtime_kind,
                    native_locator=name,
                    digest=digest,
                    size_bytes=_optional_int(tag.get("size")),
                    modified_at=_optional_text(tag.get("modified_at")),
                    presence=PresenceStatus.PRESENT,
                    metadata=metadata,
                    candidate_capabilities=_candidate_capabilities(metadata),
                    detail_refreshed=refresh_details,
                )
            )
        return DiscoveryReport(
            runtime_kind=self.runtime_kind,
            runtime_status=RuntimeStatus.READY,
            observations=tuple(observations),
            evidence=tuple(evidence),
        )


class ModelLockRuntimeAdapter:
    """Discover controlled ComfyUI or PyTorch model bundles from ``model-lock``.

    The local root is a node binding used only during scanning.  Observations
    retain release code and relative artifact paths, never the Windows absolute
    path, so the same report can migrate to another server or data disk.
    """

    def __init__(
        self,
        runtime_kind: RuntimeKind,
        lock_path: Path,
        model_root: Path,
        *,
        lock_path_prefix: str | None = None,
    ) -> None:
        if runtime_kind not in {RuntimeKind.COMFYUI, RuntimeKind.PYTORCH_PROCESS}:
            raise ValueError("ModelLockRuntimeAdapter supports COMFYUI or PYTORCH_PROCESS only")
        self.runtime_kind = runtime_kind
        self.lock_path = lock_path
        self.model_root = model_root
        self.lock_path_prefix = lock_path_prefix

    def discover(self, *, known_native_digests: Mapping[str, str] | None = None) -> DiscoveryReport:
        del known_native_digests  # File discovery deliberately records each observed snapshot.
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            models = payload.get("models")
            if not isinstance(models, list):
                raise ValueError("models must be an array")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return DiscoveryReport(
                runtime_kind=self.runtime_kind,
                runtime_status=RuntimeStatus.UNREACHABLE,
                observations=(),
                evidence=(AdapterEvidence("MODEL_LOCK_UNAVAILABLE", "无法读取受控模型清单。", {"reason": type(error).__name__}),),
            )

        runtime_value = "COMFY" if self.runtime_kind is RuntimeKind.COMFYUI else "PYTORCH"
        observations: list[NativeModelObservation] = []
        evidence: list[AdapterEvidence] = []
        for item in sorted((entry for entry in models if isinstance(entry, dict)), key=lambda entry: str(entry.get("code") or "")):
            if str(item.get("runtime") or "").upper() != runtime_value:
                continue
            code = _optional_text(item.get("code"))
            files = item.get("files")
            if not code or not isinstance(files, list) or not files:
                evidence.append(AdapterEvidence("MODEL_LOCK_ENTRY_INVALID", "模型清单条目缺少 code 或 files。", {"entry": dict(item)}))
                continue
            file_observations: list[dict[str, object]] = []
            valid = True
            for file_spec in files:
                result = _observe_locked_file(self.model_root, file_spec, lock_path_prefix=self.lock_path_prefix)
                if result is None:
                    valid = False
                    evidence.append(AdapterEvidence("MODEL_LOCK_PATH_INVALID", "模型清单包含越界或无效相对路径。", {"model_code": code}))
                    continue
                valid = valid and result["present"] is True and result["size_matches"] is True
                file_observations.append(result)
            presence = PresenceStatus.PRESENT if valid else PresenceStatus.MISSING
            observations.append(
                NativeModelObservation(
                    runtime_kind=self.runtime_kind,
                    native_locator=code,
                    digest=None,
                    size_bytes=sum(int(cast(int, file["expected_size_bytes"])) for file in file_observations),
                    modified_at=None,
                    presence=presence,
                    metadata={"release_code": code, "files": tuple(file_observations), "declared_runtime": runtime_value},
                    candidate_capabilities=tuple(
                        CapabilityCandidate(capability, "Declared in LocalDramaStudio model catalog; validation required")
                        for capability in DECLARED_MODEL_CAPABILITIES.get(code, ())
                    ),
                    detail_refreshed=True,
                )
            )
        return DiscoveryReport(
            runtime_kind=self.runtime_kind,
            runtime_status=RuntimeStatus.UNKNOWN,
            observations=tuple(observations),
            evidence=tuple(evidence),
        )


def lock_path_prefix_for_library(runtime_kind: RuntimeKind, library_root: Path) -> str | None:
    """Return the one canonical legacy-lock prefix for a split V2 library."""
    name = library_root.name.casefold()
    if runtime_kind is RuntimeKind.COMFYUI and name == "comfyui":
        return "ComfyUI"
    if runtime_kind is RuntimeKind.PYTORCH_PROCESS and name == "pytorch":
        return "Services"
    return None


def _ollama_name(tag: Mapping[str, Any]) -> str:
    return _optional_text(tag.get("name")) or _optional_text(tag.get("model")) or ""


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _ollama_metadata(tag: Mapping[str, Any], details: Mapping[str, Any]) -> dict[str, object]:
    tag_details = _as_dict(tag.get("details"))
    show_details = _as_dict(details.get("details"))
    merged_details = {**tag_details, **show_details, **details}
    capabilities = merged_details.get("capabilities")
    families = merged_details.get("families")
    return {
        "format": _optional_text(merged_details.get("format")),
        "family": _optional_text(merged_details.get("family")),
        "families": tuple(str(item) for item in families if item) if isinstance(families, list) else (),
        "parameter_size": _optional_text(merged_details.get("parameter_size")),
        "quantization_level": _optional_text(merged_details.get("quantization_level")),
        "context_length": _optional_int(merged_details.get("context_length")),
        "capabilities": tuple(str(item).strip().lower() for item in capabilities if str(item).strip())
        if isinstance(capabilities, list)
        else (),
    }


def _candidate_capabilities(metadata: Mapping[str, object]) -> tuple[CapabilityCandidate, ...]:
    native_capabilities = set(cast("tuple[str, ...]", metadata.get("capabilities") or ()))
    candidates: list[CapabilityCandidate] = []
    if native_capabilities.intersection({"completion", "generate", "chat"}):
        candidates.extend(
            CapabilityCandidate(capability.value, "Ollama native completion capability declared")
            for capability in (
                GenerationCapability.LLM_STORY_PARSE,
                GenerationCapability.LLM_EPISODE_PLAN,
                GenerationCapability.LLM_STORYBOARD,
                GenerationCapability.LLM_PROMPT_REWRITE,
            )
        )
    if "embedding" in native_capabilities or "embeddings" in native_capabilities:
        candidates.append(
            CapabilityCandidate(GenerationCapability.EMBEDDING_TEXT.value, "Ollama native embedding capability declared")
        )
    if "vision" in native_capabilities:
        candidates.append(
            CapabilityCandidate(GenerationCapability.QC_VISUAL.value, "Ollama native vision capability declared; smoke validation required")
        )
    return tuple(candidates)


def _observe_locked_file(
    model_root: Path,
    file_spec: Any,
    *,
    lock_path_prefix: str | None = None,
) -> dict[str, object] | None:
    if not isinstance(file_spec, Mapping):
        return None
    relative_raw = _optional_text(file_spec.get("path"))
    expected_size = _optional_int(file_spec.get("bytes"))
    if not relative_raw or expected_size is None or expected_size < 0:
        return None
    relative = PurePosixPath(relative_raw.replace("\\", "/"))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return None
    prefix = PurePosixPath(lock_path_prefix) if lock_path_prefix else None
    if prefix and (prefix.is_absolute() or any(part in {"", ".", ".."} for part in prefix.parts) or tuple(relative.parts[:len(prefix.parts)]) != prefix.parts):
        return None
    library_relative = PurePosixPath(*relative.parts[len(prefix.parts):]) if prefix else relative
    if not library_relative.parts:
        return None
    local_path = model_root.joinpath(*library_relative.parts)
    try:
        resolved_root = model_root.resolve()
        resolved_path = local_path.resolve()
        resolved_path.relative_to(resolved_root)
        present = resolved_path.is_file()
        observed_size = resolved_path.stat().st_size if present else None
    except (OSError, ValueError):
        present, observed_size = False, None
    return {
        "relative_path": library_relative.as_posix(),
        "format": _optional_text(file_spec.get("format")),
        "expected_size_bytes": expected_size,
        "present": present,
        "observed_size_bytes": observed_size,
        "size_matches": observed_size == expected_size,
    }

"""Read-only GGUF directory discovery for the managed llama.cpp runtime.

The adapter scans one operator-configured directory for ``*.gguf`` files,
parses each container's metadata header (architecture, model name,
quantization) without loading any weight, and reports content digests so a
truncated download is caught at discovery time instead of first inference.
Vision encoders (CLIP/mmproj) are intentionally not emitted as observations.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Mapping

from local_drama.model_platform.application.runtime_adapters import (
    AdapterEvidence,
    CapabilityCandidate,
    DiscoveryReport,
    NativeModelObservation,
    RuntimeAdapter,
)
from local_drama.model_platform.domain.models import RuntimeKind
from local_drama.model_platform.domain.states import PresenceStatus, RuntimeStatus

_GGUF_MAGIC = b"GGUF"
_TEXT_CAPABILITIES = ("LLM_STORY_PARSE", "LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE")
_MAX_PARSED_HEADER_BYTES = 128 * 1024 * 1024
_HASH_CHUNK_BYTES = 8 * 1024 * 1024

# GGUF general.file_type enum (subset); unknown values stay numeric.
_FILE_TYPES: dict[int, str] = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1",
    10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M",
    16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S",
    22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M",
    28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16",
    36: "TQ1_0", 37: "TQ2_0",
}

_SCALAR_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


@dataclass(frozen=True, slots=True)
class GgufHeaderInfo:
    architecture: str | None
    model_name: str | None
    quantization: str | None
    is_vision_encoder: bool


def parse_gguf_header(path: Path) -> GgufHeaderInfo:
    """Read the metadata KV section of one GGUF container.

    Raises ``ValueError`` for a non-GGUF file, a truncated header, an
    unsupported version, or a metadata section exceeding the parse budget.
    Only ``general.*`` keys are retained; every other value (including
    multi-megabyte tokenizer arrays) is skipped without buffering it.
    """

    wanted = {"general.architecture", "general.name", "general.file_type"}
    values: dict[str, object] = {}
    with open(path, "rb") as handle:
        if _read_exact(handle, 4) != _GGUF_MAGIC:
            raise ValueError("not a GGUF container")
        (version,) = struct.unpack("<I", _read_exact(handle, 4))
        if version not in (2, 3):
            raise ValueError(f"unsupported GGUF version {version}")
        _read_exact(handle, 8)  # u64 tensor_count
        (kv_count,) = struct.unpack("<Q", _read_exact(handle, 8))
        parsed = 24
        for _ in range(kv_count):
            key_raw, key_bytes = _read_string(handle)
            parsed += key_bytes + 4
            key = key_raw.decode("utf-8", errors="replace")
            (value_type,) = struct.unpack("<I", _read_exact(handle, 4))
            value, value_bytes = _read_value(handle, value_type, keep=key in wanted)
            parsed += value_bytes
            if key in wanted and value is not None:
                values[key] = value
            if parsed > _MAX_PARSED_HEADER_BYTES:
                raise ValueError("GGUF metadata section exceeds parse budget")
    architecture = values.get("general.architecture")
    architecture_text = str(architecture) if isinstance(architecture, str) else None
    file_type = values.get("general.file_type")
    quantization = _FILE_TYPES.get(file_type, f"gguf_type_{file_type}") if isinstance(file_type, int) else None
    return GgufHeaderInfo(
        architecture=architecture_text,
        model_name=str(values["general.name"]) if isinstance(values.get("general.name"), str) else None,
        quantization=quantization,
        is_vision_encoder=architecture_text is not None and architecture_text.casefold() == "clip",
    )


def _read_exact(handle: IO[bytes], size: int) -> bytes:
    raw = handle.read(size)
    if len(raw) != size:
        raise ValueError("truncated GGUF header")
    return raw


def _read_string(handle: IO[bytes]) -> tuple[bytes, int]:
    (length,) = struct.unpack("<Q", _read_exact(handle, 8))
    return _read_exact(handle, length), 8 + length


def _read_value(handle: IO[bytes], value_type: int, *, keep: bool) -> tuple[object, int]:
    if value_type == 8:  # string
        raw, consumed = _read_string(handle)
        return (raw.decode("utf-8", errors="replace") if keep else None), consumed
    if value_type in _SCALAR_SIZES:
        raw = _read_exact(handle, _SCALAR_SIZES[value_type])
        if not keep:
            return None, len(raw)
        if value_type in (2, 4):
            return struct.unpack("<I", raw)[0], len(raw)
        if value_type == 10:
            return struct.unpack("<Q", raw)[0], len(raw)
        if value_type in (1, 3, 5):
            return struct.unpack("<i", raw)[0], len(raw)
        if value_type == 11:
            return struct.unpack("<q", raw)[0], len(raw)
        if value_type == 6:
            return struct.unpack("<f", raw)[0], len(raw)
        if value_type == 12:
            return struct.unpack("<d", raw)[0], len(raw)
        if value_type == 7:
            return raw[0] == 1, len(raw)
        return raw[0], len(raw)
    if value_type == 9:  # array: u32 elem_type + u64 count + count elements
        (elem_type,) = struct.unpack("<I", _read_exact(handle, 4))
        (count,) = struct.unpack("<Q", _read_exact(handle, 8))
        consumed = 12
        for _ in range(count):
            _, item_bytes = _read_value(handle, elem_type, keep=False)
            consumed += item_bytes
            if consumed > _MAX_PARSED_HEADER_BYTES:
                raise ValueError("GGUF metadata section exceeds parse budget")
        return None, consumed
    raise ValueError(f"unsupported GGUF metadata type {value_type}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


class GgufDirectoryRuntimeAdapter(RuntimeAdapter):
    """Discover text GGUF files under one configured directory root."""

    runtime_kind = RuntimeKind.LLAMA_CPP_MANAGED

    def __init__(self, root: Path, *, known_native_digests: Mapping[str, str] | None = None) -> None:
        self.root = root
        self._known_native_digests: Mapping[str, str] = known_native_digests or {}

    def discover(self, *, known_native_digests: Mapping[str, str] | None = None) -> DiscoveryReport:
        del known_native_digests  # directory scans deliberately record every observed snapshot
        evidence: list[AdapterEvidence] = []
        observations: list[NativeModelObservation] = []
        if not self.root.is_dir():
            return DiscoveryReport(
                runtime_kind=self.runtime_kind,
                runtime_status=RuntimeStatus.UNREACHABLE,
                observations=(),
                evidence=(AdapterEvidence("GGUF_ROOT_UNAVAILABLE", "GGUF 模型目录不存在。", {"root": str(self.root)}),),
            )
        files = sorted(path for path in self.root.rglob("*.gguf") if path.is_file())
        for path in files:
            native_locator = path.relative_to(self.root).as_posix()
            try:
                header = parse_gguf_header(path)
                digest = _sha256(path)
                size_bytes = path.stat().st_size
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
            except (OSError, ValueError) as error:
                evidence.append(
                    AdapterEvidence(
                        "GGUF_FILE_UNREADABLE",
                        "GGUF 文件无法解析，已跳过。",
                        {"native_locator": native_locator, "reason": type(error).__name__, "detail": str(error)[:200]},
                    )
                )
                continue
            if header.is_vision_encoder:
                evidence.append(
                    AdapterEvidence(
                        "GGUF_VISION_ENCODER_SKIPPED",
                        "跳过视觉编码器（mmproj/CLIP），它不是文本生成候选。",
                        {"native_locator": native_locator},
                    )
                )
                continue
            observations.append(
                NativeModelObservation(
                    runtime_kind=self.runtime_kind,
                    native_locator=native_locator,
                    digest=digest,
                    size_bytes=size_bytes,
                    modified_at=modified_at,
                    presence=PresenceStatus.PRESENT,
                    metadata={
                        "family": header.architecture or "gguf",
                        "model_name": header.model_name or path.stem,
                        "quantization_level": header.quantization,
                        "container": "GGUF",
                    },
                    candidate_capabilities=tuple(
                        CapabilityCandidate(capability, "GGUF text architecture; validation required")
                        for capability in _TEXT_CAPABILITIES
                    ),
                    detail_refreshed=True,
                )
            )
        return DiscoveryReport(
            runtime_kind=self.runtime_kind,
            runtime_status=RuntimeStatus.READY if observations else RuntimeStatus.UNKNOWN,
            observations=tuple(observations),
            evidence=tuple(evidence),
        )

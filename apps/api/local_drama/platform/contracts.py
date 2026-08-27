from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True)
class SecretRef:
    namespace: str
    key: str


class SecretStore(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def get(self, ref: SecretRef) -> str | None: ...

    def put(self, ref: SecretRef, value: str) -> None: ...

    def delete(self, ref: SecretRef) -> bool: ...


@dataclass(frozen=True)
class FilePickerRequest:
    kind: Literal["MODEL", "DOCUMENT"]
    title: str
    allowed_suffixes: tuple[str, ...]


@dataclass(frozen=True)
class FilePickerResult:
    selected: bool
    path: Path | None

    def public(self) -> dict[str, object]:
        return {
            "selected": self.selected,
            "path": str(self.path) if self.path is not None else None,
            "uploaded": False,
            "copied": False,
        }


class FilePicker(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def choose(self, request: FilePickerRequest) -> FilePickerResult: ...


class TtsRuntime(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def capability_summary(self) -> dict[str, object]: ...

    def discover_voices(self) -> dict[str, object]: ...

    def synthesize(self, *, voice: str, text: str, output: Path, rate: int = 0, timeout: int = 120) -> None: ...


class TtsRuntimeError(RuntimeError):
    pass

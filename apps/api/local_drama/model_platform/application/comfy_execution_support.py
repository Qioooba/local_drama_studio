"""Shared, path-safe output handling for immutable Comfy V2 executions."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Sequence

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.atomic import replace_path

_SUFFIXES = {
    "IMAGE": frozenset({".png", ".jpg", ".jpeg", ".webp"}),
    "VIDEO": frozenset({".mp4", ".mov", ".webm"}),
    "AUDIO": frozenset({".wav", ".mp3", ".flac", ".ogg"}),
}


def assert_comfy_outputs(outputs: Sequence[Path], *, media_kind: str, min_count: int, max_count: int, error_prefix: str) -> None:
    if not min_count <= len(outputs) <= max_count:
        raise DomainRuleError(f"{error_prefix}_OUTPUT_COUNT_INVALID", "Comfy 输出数量不符合冻结合同。")
    allowed = _SUFFIXES.get(media_kind)
    if allowed is None or any(path.suffix.casefold() not in allowed for path in outputs):
        raise DomainRuleError(f"{error_prefix}_OUTPUT_KIND_INVALID", "Comfy 输出类型不符合冻结合同。")


def copy_comfy_outputs(outputs: Sequence[Path], output_root: Path, *, folder: str) -> tuple[Path, ...]:
    destination_root = output_root / folder
    destination_root.mkdir(parents=True, exist_ok=True)
    copies: list[Path] = []
    for index, source in enumerate(outputs, start=1):
        target = destination_root / f"output-{index}{source.suffix.casefold()}"
        partial = target.with_name(f".partial-{uuid.uuid4().hex}{target.suffix}")
        try:
            shutil.copyfile(source, partial)
            replace_path(partial, target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        copies.append(target)
    return tuple(copies)

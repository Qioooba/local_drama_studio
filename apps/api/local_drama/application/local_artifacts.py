from __future__ import annotations

from pathlib import Path
from typing import Literal

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import absolute_path, controlled_path, safe_filename

ArtifactScope = Literal["PROJECT", "DATA"]
ArtifactKind = Literal["FILE", "DIRECTORY"]


def local_artifact_reference(
    *,
    root: Path,
    path: Path,
    scope: ArtifactScope,
    kind: ArtifactKind,
    display_name: str,
    download_url: str,
    download_filename: str,
    error_code: str = "LOCAL_ARTIFACT_PATH_INVALID",
) -> dict[str, str]:
    """Describe a verified server artifact without weakening path policy."""
    resolved_root = absolute_path(root)
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    try:
        relative = candidate.relative_to(resolved_root).as_posix()
    except ValueError as error:
        raise DomainRuleError(error_code, "本地产物超出受控存储根目录") from error
    resolved_path = controlled_path(
        resolved_root,
        relative,
        must_exist=True,
        require_file=kind == "FILE",
        code=error_code,
    )
    if kind == "DIRECTORY" and not resolved_path.is_dir():
        raise DomainRuleError(error_code, "本地产物超出受控存储根目录")
    return {
        "scope": scope,
        "kind": kind,
        "display_name": str(display_name),
        "server_absolute_path": str(resolved_path),
        "rel_path": resolved_path.relative_to(resolved_root).as_posix(),
        "download_url": str(download_url),
        "download_filename": safe_filename(download_filename, default="download.bin"),
    }

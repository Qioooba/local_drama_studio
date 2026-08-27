from __future__ import annotations

import uuid
import zipfile
from pathlib import Path

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.atomic import replace_path


def materialize_verified_export_archive(directory: Path) -> Path:
    """Create an immutable ZIP download beside an already verified export."""
    if not directory.is_dir() or directory.is_symlink():
        raise DomainRuleError("EXPORT_DIRECTORY_INVALID", "导出目录不存在或不安全")
    files: list[Path] = []
    for candidate in directory.rglob("*"):
        if candidate.is_symlink():
            raise DomainRuleError("EXPORT_ARCHIVE_SYMLINK", "导出目录包含不安全链接")
        if candidate.is_file():
            resolved = candidate.resolve()
            if not resolved.is_relative_to(directory.resolve()):
                raise DomainRuleError("EXPORT_ARCHIVE_PATH_ESCAPE", "导出文件路径越界")
            files.append(resolved)
    if not files:
        raise DomainRuleError("EXPORT_ARCHIVE_EMPTY", "导出目录为空")
    archive = directory.parent / f"{directory.name}.zip"
    partial = directory.parent / f".partial-{uuid.uuid4().hex}.zip"
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as output:
            for source in sorted(files, key=lambda path: path.relative_to(directory).as_posix()):
                output.write(source, f"{directory.name}/{source.relative_to(directory).as_posix()}")
        replace_path(partial, archive)
    finally:
        partial.unlink(missing_ok=True)
    return archive

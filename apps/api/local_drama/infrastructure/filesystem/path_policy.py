"""Canonical filesystem path rules for server-owned storage.

The browser never owns a filesystem path.  Paths crossing an API or database
boundary are either an explicitly labelled server absolute path or a POSIX
relative path scoped to one configured storage root.  All operating-system
path joining and Windows filename handling lives here so individual features
cannot accidentally invent a different policy.
"""

from __future__ import annotations

import re
import stat
import unicodedata
from collections.abc import Iterator
from os import path as os_path
from os import walk
from pathlib import Path, PurePosixPath

from local_drama.domain.errors import DomainRuleError

_WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_INVALID_WINDOWS_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def absolute_path(value: str | Path, *, base: Path | None = None) -> Path:
    """Return an absolute normalized server path independent of process cwd."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (base or Path.cwd()) / candidate
    return candidate.resolve(strict=False)


def safe_filename(value: str, *, default: str = "upload.bin", max_length: int = 180) -> str:
    """Create a portable basename accepted by Windows filesystems.

    Remote clients may run Linux or macOS and can therefore submit names that
    Windows itself could never create (``CON.txt``, ``a:b.png``, trailing dots,
    or a full path).  The returned name is display-friendly but never trusted
    as identity; callers still prefix it with a UUID or content hash.
    """
    raw = unicodedata.normalize("NFKC", str(value or "")).replace("\\", "/").rsplit("/", 1)[-1]
    candidate = _INVALID_WINDOWS_FILENAME.sub("_", raw).strip().rstrip(". ")
    if candidate in {"", ".", ".."}:
        candidate = default
    suffix = Path(candidate).suffix
    stem = candidate[: -len(suffix)] if suffix else candidate
    if stem.upper() in _WINDOWS_RESERVED_STEMS:
        candidate = f"_{candidate}"
    if len(candidate) > max_length:
        suffix = Path(candidate).suffix[:20]
        candidate = f"{candidate[: max(1, max_length - len(suffix))]}{suffix}".rstrip(". ")
    if not candidate:
        candidate = default
    return candidate


def canonical_relative_path(value: str | Path, *, code: str = "PATH_INVALID", allow_root: bool = False) -> str:
    """Validate and normalize a persisted root-relative path as POSIX text."""
    raw = str(value or "").replace("\\", "/")
    if _WINDOWS_DRIVE.match(raw):
        raise DomainRuleError(code, "路径必须是受控根目录下的相对路径")
    if raw == "." and allow_root:
        return "."
    raw_parts = raw.split("/")
    if not raw or any(part in {"", ".", ".."} for part in raw_parts):
        raise DomainRuleError(code, "路径必须是受控根目录下的规范相对路径")
    relative = PurePosixPath(raw)
    if relative.is_absolute():
        raise DomainRuleError(code, "路径必须是受控根目录下的规范相对路径")
    for part in relative.parts:
        if part.rstrip(". ") != part or _INVALID_WINDOWS_FILENAME.search(part):
            raise DomainRuleError(code, "相对路径包含 Windows 不支持的名称")
        stem = part.split(".", 1)[0]
        if stem.upper() in _WINDOWS_RESERVED_STEMS:
            raise DomainRuleError(code, "相对路径包含 Windows 保留名称")
    return relative.as_posix()


def is_reparse_point(path: Path) -> bool:
    """Return true for symlinks and Windows junction/reparse entries."""
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def controlled_path(
    root: str | Path,
    relative: str | Path,
    *,
    must_exist: bool = False,
    require_file: bool = False,
    code: str = "PATH_ESCAPE",
) -> Path:
    """Resolve a canonical relative path below a storage root without links."""
    resolved_root = absolute_path(root)
    canonical = canonical_relative_path(relative, code=code)
    candidate = resolved_root.joinpath(*PurePosixPath(canonical).parts)
    current = resolved_root
    if current.exists() and is_reparse_point(current):
        raise DomainRuleError(code, "受控存储根目录不能是 symlink 或 Windows junction")
    for part in PurePosixPath(canonical).parts:
        current /= part
        if current.exists() and is_reparse_point(current):
            raise DomainRuleError(code, "受控路径不能经过 symlink 或 Windows junction")
    try:
        resolved = candidate.resolve(strict=must_exist)
    except OSError as error:
        raise DomainRuleError(code, "受控路径不存在或无法访问") from error
    if not resolved.is_relative_to(resolved_root):
        raise DomainRuleError(code, "路径超出受控存储根目录")
    if require_file and (not resolved.is_file() or is_reparse_point(resolved)):
        raise DomainRuleError(code, "受控路径不是普通文件")
    return resolved


def path_is_within_roots(path: str | Path, roots: tuple[Path, ...], *, require_file: bool = False) -> bool:
    """Check an absolute server path against configured, non-link roots."""
    lexical = Path(os_path.abspath(Path(path).expanduser()))
    candidate = lexical.resolve(strict=False)
    if require_file and (not candidate.is_file() or is_reparse_point(lexical) or is_reparse_point(candidate)):
        return False
    for configured_root in roots:
        root = absolute_path(configured_root)
        if (
            not root.is_dir()
            or is_reparse_point(root)
            or not lexical.is_relative_to(root)
            or not candidate.is_relative_to(root)
        ):
            continue
        current = root
        try:
            relative_parts = lexical.relative_to(root).parts
        except ValueError:
            continue
        linked = False
        for part in relative_parts:
            current /= part
            if current.exists() and is_reparse_point(current):
                linked = True
                break
        if not linked:
            return True
    return False


def iter_controlled_files(root: str | Path) -> Iterator[Path]:
    """Walk regular files without entering symlinks or Windows junctions."""
    resolved_root = absolute_path(root)
    if not resolved_root.is_dir() or is_reparse_point(resolved_root):
        return
    for current_text, directories, filenames in walk(resolved_root, followlinks=False):
        current = Path(current_text)
        directories[:] = [name for name in directories if not is_reparse_point(current / name)]
        for name in filenames:
            candidate = current / name
            if candidate.is_file() and not is_reparse_point(candidate):
                yield candidate


def client_is_server_loopback(host: str | None) -> bool:
    """Classify a request peer without confusing the browser Host header."""
    import ipaddress

    candidate = str(host or "").strip()
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return candidate.casefold() in {"localhost", "testclient"}

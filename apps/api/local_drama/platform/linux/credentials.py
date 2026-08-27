from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

from local_drama.platform.contracts import SecretRef


class LinuxFileSecretStore:
    """Service-owned 0600 secret files; intended for systemd server mode."""

    name = "LINUX_SERVICE_SECRET_FILE"

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def available(self) -> bool:
        return os.name == "posix"

    def _path(self, ref: SecretRef) -> Path:
        safe = f"{ref.namespace}-{ref.key}"
        if not safe.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ValueError("secret reference format is invalid")
        return self.root / safe

    def get(self, ref: SecretRef) -> str | None:
        path = self._path(ref)
        if not path.is_file() or path.is_symlink():
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                raise OSError("secret file permissions are not 0600")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                return stream.read().strip() or None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def put(self, ref: SecretRef, value: str) -> None:
        secret = value.strip()
        if not secret:
            raise ValueError("secret cannot be empty")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise OSError("secret root cannot be a symlink")
        self.root.chmod(0o700)
        path = self._path(ref)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, secret.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)

    def delete(self, ref: SecretRef) -> bool:
        path = self._path(ref)
        if not path.exists():
            return False
        path.unlink()
        return True

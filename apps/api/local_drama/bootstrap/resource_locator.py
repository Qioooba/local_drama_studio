from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_REPO_ROOT = Path(__file__).resolve().parents[4]

# Canonical directory names for the mutable storage roots. Settings fields are
# named ``<name>_root`` (``data_root``...) but the directories themselves use
# the bare name (``data``...). This tuple is the single owner of that mapping.
STORAGE_ROOTS = ("data", "projects", "work", "cache", "logs", "backups")


def _absolute(value: str | Path, *, base: Path | None = None) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = (base or Path.cwd()) / candidate
    return candidate.resolve()


@dataclass(frozen=True)
class ResourceLocator:
    """Resolve immutable release resources separately from mutable instance data."""

    release_root: Path
    instance_root: Path
    config_path: Path
    source_repo_root: Path
    packaged: bool

    @classmethod
    def discover(cls) -> "ResourceLocator":
        packaged = bool(getattr(sys, "frozen", False)) or os.environ.get("LOCAL_DRAMA_PACKAGED") == "1"
        configured_release = os.environ.get("LOCAL_DRAMA_RELEASE_ROOT")
        if configured_release:
            release_root = _absolute(configured_release)
        elif packaged:
            release_root = Path(sys.executable).resolve().parent
        else:
            release_root = SOURCE_REPO_ROOT

        configured_instance = os.environ.get("LOCAL_DRAMA_INSTANCE_ROOT")
        configured_config = os.environ.get("LOCAL_DRAMA_CONFIG")
        if configured_instance:
            instance_root = _absolute(configured_instance)
        elif configured_config:
            config = _absolute(configured_config)
            instance_root = config.parent.parent if config.parent.name.casefold() == "config" else config.parent
        elif not packaged:
            instance_root = SOURCE_REPO_ROOT
        elif os.name == "nt":
            base = Path(os.environ.get("PROGRAMDATA") or os.environ.get("LOCALAPPDATA") or release_root)
            instance_root = _absolute(base / "LocalDramaStudio")
        else:
            instance_root = Path("/var/lib/local-drama-studio")

        config_path = (
            _absolute(configured_config, base=instance_root)
            if configured_config
            else instance_root / "config" / "config.json"
        )
        return cls(
            release_root=release_root,
            instance_root=instance_root,
            config_path=config_path,
            source_repo_root=SOURCE_REPO_ROOT,
            packaged=packaged,
        )

    def default_root(self, name: str) -> Path:
        """Resolve one default mutable root by its canonical directory name.

        ``name`` must be a directory name from ``STORAGE_ROOTS`` (``data``),
        never a Settings field name (``data_root``). Use :meth:`storage_roots`
        to map field names to directories.
        """
        if not self.packaged and self.instance_root == self.source_repo_root:
            return self.source_repo_root / name
        return self.instance_root / name

    def storage_roots(self) -> dict[str, Path]:
        """Map Settings storage field names to their default mutable roots.

        This is the only place allowed to translate a field name such as
        ``data_root`` into a directory path such as ``<instance>/data``;
        callers must never pass field names to :meth:`default_root` directly.
        """
        return {f"{name}_root": self.default_root(name) for name in STORAGE_ROOTS}

    @property
    def frontend_dist(self) -> Path | None:
        candidates = (
            self.release_root / "web",
            self.release_root / "apps" / "web" / "dist",
        )
        return next((path for path in candidates if (path / "index.html").is_file()), None)

    @property
    def migrations_root(self) -> Path:
        packaged = self.release_root / "migrations"
        return packaged if packaged.is_dir() else self.source_repo_root / "apps" / "api" / "alembic"

    @property
    def version_path(self) -> Path:
        packaged = self.release_root / "version.json"
        return packaged if packaged.is_file() else self.source_repo_root / "release" / "version.json"

    @property
    def default_manifest_path(self) -> Path:
        candidates = (
            self.release_root / "model_manifest.json",
            self.source_repo_root / "model_manifest.json",
            self.source_repo_root.parent / "model_manifest.json",
        )
        return next((path for path in candidates if path.is_file()), candidates[0])

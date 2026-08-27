"""Ports for timeline application services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class TimelineUnitOfWork(Protocol):
    """Minimum persistence abstraction used by timeline services."""

    def connect(self) -> Any:  # pragma: no cover - protocol boundary
        ...

    def transaction(self) -> Any:  # pragma: no cover - protocol boundary
        ...


class TimelineMediaPort(Protocol):
    """Media capabilities required by timeline rendering flows."""

    def get_version(self, media_version_id: str) -> dict[str, Any]:
        ...

    def content_path(self, media_version_id: str) -> tuple[dict[str, Any], Path]:
        ...

    def verify_content_integrity(self, media_version_id: str, *, connection: Any | None = None) -> dict[str, Any]:
        ...

    def import_file(
        self,
        project_id: str,
        source_path: str,
        *,
        purpose: str,
        owner_type: str,
        owner_id: str,
        media_kind: str,
        stage: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        ...

    def cached_video_thumbnail(
        self,
        source: Path,
        *,
        cache_namespace: str,
        source_sha256: str,
        duration_ms: int | None,
        size: str = "small",
        frame: str = "poster",
    ) -> tuple[Path, str, str, str]:
        ...

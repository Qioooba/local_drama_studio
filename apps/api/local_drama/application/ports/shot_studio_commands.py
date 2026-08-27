from __future__ import annotations

from typing import Any, Protocol


class ShotStudioCommandPort(Protocol):
    """Persistence boundary for Shot Studio-owned mutations."""

    def camera_profile(self, profile_version_id: str) -> dict[str, Any] | None: ...

    def current_draft(self, shot_id: str) -> dict[str, Any]: ...

    def save_draft(
        self,
        shot_id: str,
        fields: dict[str, Any],
        *,
        freeze: bool,
        expected_revision_no: int | None,
        actor: str,
    ) -> dict[str, Any]: ...

    def mark_ready(
        self,
        shot_id: str,
        *,
        fields: dict[str, Any] | None,
        freeze: bool,
        expected_revision_no: int | None,
        actor: str,
    ) -> dict[str, Any]: ...

    def adopt_working_version(self, media_version_id: str, *, actor: str) -> dict[str, Any]: ...

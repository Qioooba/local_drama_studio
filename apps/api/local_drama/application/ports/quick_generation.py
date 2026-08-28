"""Collaborator ports for standalone quick generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ProfileReader(Protocol):
    def get_version(self, version_id: str) -> dict[str, Any]: ...

    def resolve_camera_plan(
        self,
        profile_version_id: str,
        *,
        shot_type: str,
        movement: str,
        direction: str,
        intensity: float,
        curve: str,
        prompt_text: str = "",
    ) -> dict[str, Any]: ...


class WorkflowReader(Protocol):
    def get_version(self, version_id: str) -> dict[str, Any]: ...


class QuickGenerationJobPort(Protocol):
    def create_job(
        self,
        project_id: str | None,
        job_type: str,
        subject_type: str,
        subject_id: str,
        channel: str,
        input_snapshot: dict[str, Any],
        idempotency_key: str,
        *,
        execution_profile_version_id: str | None = None,
        priority: int = 100,
        max_attempts: int = 3,
        depends_on_job_ids: list[str] | None = None,
        actor: str = "local-user",
        subject_kind: str | None = None,
        scope_kind: str | None = None,
        scope_project_id: str | None = None,
        scope_episode_id: str | None = None,
        scope_shot_id: str | None = None,
        stage_code: str | None = None,
    ) -> dict[str, Any]: ...

    def get_job(self, job_id: str) -> dict[str, Any]: ...

    def cancel(self, job_id: str) -> dict[str, Any]: ...

    def retry(self, job_id: str) -> dict[str, Any]: ...


class QuickGenerationMediaPort(Protocol):
    def content_path(self, media_version_id: str) -> tuple[dict[str, Any], Path]: ...

    def cached_visual_thumbnail(
        self,
        source: Path,
        *,
        cache_namespace: str,
        source_sha256: str,
        media_kind: str,
        mime_type: str,
        duration_ms: int | None = None,
        size: str = "small",
        frame: str = "poster",
    ) -> tuple[Path, str, str, str]: ...


class QuickGenerationLlmPort(Protocol):
    def expand_video_prompt(
        self,
        profile_version_id: str,
        story: str,
        *,
        api_key: str | None = None,
        remember_api_key: bool = False,
        allow_remote_outbound: bool = False,
        language: str = "zh-CN",
        output_spec: dict[str, float | int | str] | None = None,
        inference_options: dict[str, Any] | None = None,
        target_kind: str = "VIDEO",
    ) -> dict[str, Any]: ...

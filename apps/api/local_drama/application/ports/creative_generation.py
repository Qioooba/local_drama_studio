"""Dependency ports for story-to-visual production orchestration."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol


class GenerationCommandPort(Protocol):
    def create_intent(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def preflight_variant(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def submit_confirmed_variant(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def create_shot_intent(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def preflight_shot_base_variant(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def submit_shot_base_variant(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


class GenerationPreferenceResolverPort(Protocol):
    def resolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


class MediaPromotionPort(Protocol):
    def promote_job_artifact(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def submit_default_derivatives(self, media_version_id: str) -> Any: ...


class AssetBibleCommandPort(Protocol):
    def add_reference(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


class StoryPipelineJobPort(Protocol):
    def create_job_in_transaction(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def cancel(self, job_id: str) -> dict[str, Any]: ...
    def retry(self, job_id: str) -> dict[str, Any]: ...


class DocumentImportPort(Protocol):
    def import_document(self, project_id: str, source_path: str | Path, actor: str = "local-user") -> dict[str, Any]: ...

    def get_session(self, session_id: str) -> dict[str, Any]: ...

    def commit(
        self,
        session_id: str,
        expected_preview_hash: str,
        actor: str = "local-user",
        *,
        source_paragraph_start: int | None = None,
        source_paragraph_end: int | None = None,
    ) -> dict[str, Any]: ...


class StoryAIGenerationPort(Protocol):
    def readiness(self, profile_version_id: str | None = None) -> dict[str, Any]: ...
    def generate(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...


class LocalLLMClientProviderPort(Protocol):
    def client(self, profile_version_id: str | None = None) -> Any: ...


class GenerationOutputCompletionPort(Protocol):
    def finalize_job(self, job_id: str, artifacts: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> dict[str, Any] | None: ...


class AssetImageCompletionPort(GenerationOutputCompletionPort, Protocol):
    def record_finalization_failure(self, job_id: str, error: Exception) -> bool: ...


class ShotKeyframeCompletionPort(GenerationOutputCompletionPort, Protocol):
    def record_failure(self, job_id: str, error: Exception) -> bool: ...


class AssetBibleCommandFactory(Protocol):
    def __call__(self, connection: sqlite3.Connection) -> AssetBibleCommandPort: ...


class GenerationPreferenceResolverFactory(Protocol):
    def __call__(self, connection: sqlite3.Connection) -> GenerationPreferenceResolverPort: ...

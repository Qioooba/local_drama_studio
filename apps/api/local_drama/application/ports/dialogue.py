from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol


class DialogueUnitOfWork(Protocol):
    def connect(self) -> AbstractContextManager[sqlite3.Connection]: ...

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]: ...


class DialogueJobPort(Protocol):
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


class DialogueMediaPort(Protocol):
    def verify_content_integrity(self, media_version_id: str) -> dict[str, Any]: ...

    def promote_job_artifact(
        self,
        artifact_id: str,
        *,
        purpose: str = "GENERATED_OUTPUT",
        media_kind: str | None = None,
        stage: str = "PROXY",
        actor: str = "worker",
    ) -> dict[str, Any]: ...

    def get_version(self, media_version_id: str) -> dict[str, Any]: ...

    def _probe(self, path: Path, kind: str) -> dict[str, Any]: ...

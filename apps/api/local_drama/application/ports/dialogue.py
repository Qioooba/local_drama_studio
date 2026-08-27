from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from typing import Any, Protocol


class DialogueUnitOfWork(Protocol):
    def connect(self) -> AbstractContextManager[sqlite3.Connection]: ...

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]: ...


class DialogueJobPort(Protocol):
    def create_job(
        self,
        project_id: str,
        job_type: str,
        subject_type: str,
        subject_id: str,
        channel: str,
        input_snapshot: dict[str, Any],
        idempotency_key: str,
        **options: Any,
    ) -> dict[str, Any]: ...


class DialogueMediaPort(Protocol):
    def verify_content_integrity(self, media_version_id: str) -> dict[str, Any]: ...

    def promote_job_artifact(self, artifact_id: str, **options: Any) -> dict[str, Any]: ...

    def get_version(self, media_version_id: str) -> dict[str, Any]: ...

    def _probe(self, path: Any, expected_kind: str | None = None) -> dict[str, Any]: ...

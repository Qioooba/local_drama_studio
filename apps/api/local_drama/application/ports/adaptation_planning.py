"""Ports for the Adaptation Planning bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class SourceDocumentSnapshot:
    project_id: str
    source_document_id: str
    source_document_version_id: str
    title: str
    source_name: str
    source_sha256: str
    text_sha256: str
    text: str


class AdaptationPlanningRepository(Protocol):
    def source_snapshot(self, *, project_id: str, source_document_version_id: str) -> SourceDocumentSnapshot: ...

    def list_source_versions(self, *, project_id: str) -> list[dict[str, Any]]: ...

    def find_plan_for_request(self, *, project_id: str, request_fingerprint: str) -> dict[str, Any] | None: ...

    def create_plan(
        self,
        *,
        plan: dict[str, Any],
        revision: dict[str, Any],
        run: dict[str, Any],
        source_units: list[dict[str, Any]],
        actor: str,
    ) -> dict[str, Any]: ...

    def list_plans(self, *, project_id: str) -> list[dict[str, Any]]: ...

    def workspace(self, *, plan_id: str) -> dict[str, Any]: ...

    def planning_context(self, *, plan_id: str) -> dict[str, Any]: ...

    def save_analysis_manifest(
        self,
        *,
        plan_id: str,
        revision_id: str,
        run_id: str,
        nodes: list[dict[str, Any]],
        actor: str,
    ) -> dict[str, Any]: ...

    def analysis_readiness(self, *, plan_id: str, profile_version_id: str) -> dict[str, Any]: ...

    def approve_plan(
        self, *, plan_id: str, expected_content_sha256: str, actor: str
    ) -> dict[str, Any]: ...

    def materialization_preflight(self, *, plan_id: str) -> dict[str, Any]: ...

    def materialize_plan(
        self, *, plan_id: str, expected_content_sha256: str, idempotency_key: str, actor: str
    ) -> dict[str, Any]: ...

    def enqueue_analysis_run(
        self,
        *,
        plan_id: str,
        profile_version_id: str,
        allow_remote_outbound: bool,
        idempotency_key: str,
        actor: str,
        create_job: Callable[..., dict[str, Any]],
    ) -> dict[str, Any]: ...

    def analysis_execution_context(self, *, job_id: str, snapshot: dict[str, Any]) -> dict[str, Any]: ...

    def active_provider_connection(self, *, connection_id: str) -> dict[str, Any]: ...

    def record_analysis_invocation(
        self,
        *,
        job_id: str,
        run_node_id: str,
        profile_version_id: str,
        provider: str,
        model: str,
        request_sha256: str,
    ) -> str: ...

    def persist_analysis_node_success(
        self, *, snapshot: dict[str, Any], output: dict[str, Any], latency_ms: int, invocation_id: str
    ) -> None: ...

    def record_analysis_failure(self, *, invocation_id: str, latency_ms: int, error_type: str) -> None: ...

from __future__ import annotations

from typing import Any, Protocol


class StoryboardGenerationPreflightPort(Protocol):
    def video_generation_preflight(
        self,
        episode_id: str,
        *,
        target_shot_ids: tuple[str, ...],
    ) -> dict[str, Any]: ...


class StoryboardGenerationWorkflowPort(Protocol):
    def create_workflow(
        self,
        project_id: str,
        *,
        code: str,
        title: str,
        mode: str,
        nodes: list[dict[str, Any]],
        batch_items: list[dict[str, Any]],
        conditions: list[dict[str, Any]],
        max_iterations: int,
        max_tasks: int,
        max_disk_bytes: int,
        human_gate: str = "ON_CONDITION",
        repeat_batch: bool = False,
        template_code: str | None = None,
        source_fingerprint: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]: ...

    def start_run(
        self,
        workflow_id: str,
        *,
        plan_hash: str,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]: ...

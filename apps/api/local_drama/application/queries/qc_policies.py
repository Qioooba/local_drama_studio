"""Project → episode → shot QC policy inheritance projection."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.qc_policies import QcPolicyRepository
from local_drama.domain.errors import DomainRuleError

QC_STAGES = frozenset({"IMAGE", "VIDEO", "AUDIO", "CONTINUITY", "DELIVERY"})


class QcPolicyQueryService:
    def __init__(self, repository: QcPolicyRepository) -> None:
        self.repository = repository

    def list_current(self, project_id: str) -> list[dict[str, Any]]:
        if self.repository.owner_project_id("PROJECT", project_id) != project_id:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        return self.repository.list_current(project_id)

    def resolve(
        self, *, project_id: str, stage: str, episode_id: str | None = None, shot_id: str | None = None,
    ) -> dict[str, Any] | None:
        context = {"project_id": project_id, "episode_id": episode_id, "shot_id": shot_id, "stage": stage.upper()}
        if context["stage"] not in QC_STAGES:
            raise DomainRuleError("QC_POLICY_STAGE_INVALID", "QC policy stage 不受支持", {"stage": context["stage"]})
        if self.repository.owner_project_id("PROJECT", project_id) != project_id:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        if episode_id and self.repository.owner_project_id("EPISODE", episode_id) != project_id:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在或不属于当前项目")
        if shot_id and self.repository.owner_project_id("SHOT", shot_id) != project_id:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在或不属于当前项目")
        if shot_id and episode_id and self.repository.shot_episode_id(shot_id) != episode_id:
            raise DomainRuleError("QC_POLICY_SCOPE_MISMATCH", "镜头不属于请求中的分集")
        return self.resolve_for_context(context=context)

    def resolve_for_context(self, *, context: dict[str, Any]) -> dict[str, Any] | None:
        project_id, stage = str(context["project_id"]), str(context.get("stage") or "VIDEO").upper()
        scopes = []
        if context.get("shot_id"):
            scopes.append(("SHOT", str(context["shot_id"])))
        if context.get("episode_id"):
            scopes.append(("EPISODE", str(context["episode_id"])))
        scopes.append(("PROJECT", project_id))
        for source, owner_id in scopes:
            policy = self.repository.current_policy(project_id, source, owner_id, stage)
            if policy is not None:
                return {**policy, "source": source}
        return None

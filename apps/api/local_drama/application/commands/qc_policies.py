"""QC policy commands; machine evidence never becomes human approval."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.qc_policies import QcPolicyRepository
from local_drama.application.queries.qc_policies import QcPolicyQueryService
from local_drama.domain.errors import DomainRuleError

QC_CATEGORIES = frozenset({"VISUAL", "FACE", "IDENTITY", "COMPOSITION", "MOTION", "CONTINUITY", "AUDIO", "TECHNICAL"})
QC_STAGES = frozenset({"IMAGE", "VIDEO", "AUDIO", "CONTINUITY", "DELIVERY"})


class QcPolicyCommandService:
    def __init__(self, repository: QcPolicyRepository) -> None:
        self.repository = repository

    def put(
        self, *, project_id: str, owner_type: str, owner_id: str, stage: str,
        policy: dict[str, Any], max_auto_rerolls: int, auto_reroll_categories: list[str],
        reason: str = "", actor: str = "local-user", expected_revision: int | None = None,
    ) -> dict[str, Any]:
        owner_type, stage = owner_type.upper(), stage.upper()
        categories = sorted({item.upper() for item in auto_reroll_categories})
        if owner_type not in {"PROJECT", "EPISODE", "SHOT"}:
            raise DomainRuleError("QC_POLICY_OWNER_INVALID", "QC policy owner_type 必须是 PROJECT、EPISODE 或 SHOT")
        if stage not in QC_STAGES:
            raise DomainRuleError("QC_POLICY_STAGE_INVALID", "QC policy stage 不受支持", {"stage": stage})
        if not 0 <= max_auto_rerolls <= 10:
            raise DomainRuleError("QC_POLICY_REROLL_LIMIT_INVALID", "自动重抽上限必须在 0 到 10 之间")
        invalid = sorted(set(categories) - QC_CATEGORIES)
        if invalid:
            raise DomainRuleError("QC_POLICY_CATEGORY_INVALID", "QC 自动重抽类别不受支持", {"categories": invalid})
        if max_auto_rerolls == 0 and categories:
            raise DomainRuleError("QC_POLICY_CATEGORY_WITHOUT_RETRY", "自动重抽上限为 0 时不能配置自动类别")
        if self.repository.owner_project_id(owner_type, owner_id) != project_id:
            raise DomainRuleError("QC_POLICY_OWNER_NOT_FOUND", "QC policy 所有者不存在或不属于当前项目")
        return self.repository.put_policy(
            project_id=project_id, owner_type=owner_type, owner_id=owner_id, stage=stage,
            policy=policy, max_auto_rerolls=max_auto_rerolls, auto_reroll_categories=categories,
            reason=reason.strip(), actor=actor, expected_revision=expected_revision,
        )

    def decide(
        self, *, variant_id: str, machine_check_run_id: str, category: str,
        actor: str = "qc-agent",
    ) -> dict[str, Any]:
        category = category.upper()
        if category not in QC_CATEGORIES:
            raise DomainRuleError("QC_POLICY_CATEGORY_INVALID", "QC 类别不受支持", {"category": category})
        existing = self.repository.qc_link(variant_id, machine_check_run_id)
        if existing is not None:
            return {**existing, "idempotent_replay": True}
        context = self.repository.variant_context(variant_id)
        if context is None:
            raise DomainRuleError("GENERATION_VARIANT_NOT_FOUND", "GenerationVariant 不存在")
        machine = self.repository.machine_check(machine_check_run_id)
        if machine is None:
            raise DomainRuleError("MACHINE_CHECK_RUN_NOT_FOUND", "机器 QC 记录不存在")
        if machine.get("project_id") not in {None, context["project_id"]}:
            raise DomainRuleError("MACHINE_CHECK_PROJECT_MISMATCH", "机器 QC 与候选不属于同一项目")
        policy = QcPolicyQueryService(self.repository).resolve_for_context(context=context)
        if policy is None:
            raise DomainRuleError("QC_POLICY_REQUIRED", "当前候选没有可用 QC policy")

        retry_count = self.repository.auto_retry_count(variant_id)
        status = str(machine["status"]).upper()
        allowed_categories = set(policy["auto_reroll_categories"])
        if status == "PASS":
            disposition, reason = "PASS", "machine check passed"
        elif status in {"ATTENTION", "WARN", "WARNING"}:
            disposition, reason = "ATTENTION", "human selection requires explicit policy confirmation"
        elif category in allowed_categories and retry_count < int(policy["max_auto_rerolls"]):
            disposition, reason = "AUTO_REROLL_ALLOWED", "bounded auto-reroll allowed"
        else:
            disposition, reason = "WAITING_GATE", "retry limit reached or category requires human gate"
        return self.repository.record_qc_link(
            variant_id=variant_id, machine_check_run_id=machine_check_run_id,
            policy_version_id=str(policy["policy_version_id"]), category=category,
            disposition=disposition, retry_ordinal=retry_count, reason=reason, actor=actor,
        )

    def attach_child(
        self, *, link_id: str, parent_variant_id: str, child_variant_id: str, actor: str = "qc-agent",
    ) -> dict[str, Any]:
        return self.repository.attach_child(link_id, parent_variant_id, child_variant_id, actor)

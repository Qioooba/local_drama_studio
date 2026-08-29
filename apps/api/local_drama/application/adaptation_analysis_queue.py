"""Durable queue orchestration for one immutable adaptation analysis run."""

from __future__ import annotations

from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.ports.adaptation_planning import AdaptationPlanningRepository
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError


class AdaptationAnalysisQueueService:
    """Freeze an explicit Profile and translate a manifest into a Job DAG.

    This command intentionally stores only source offsets and immutable hashes
    in Jobs. The worker re-reads and verifies the source file immediately
    before an LLM call, so source prose is not duplicated in the queue. All
    transactional queue state lives behind the repository port.
    """

    def __init__(self, repository: AdaptationPlanningRepository, jobs: JobService, settings: Settings) -> None:
        self.repository = repository
        self.jobs = jobs
        self.settings = settings

    def enqueue(
        self,
        *,
        plan_id: str,
        profile_version_id: str,
        allow_remote_outbound: bool,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "提交分层分析需要 Idempotency-Key")
        return self.repository.enqueue_analysis_run(
            plan_id=plan_id,
            profile_version_id=profile_version_id,
            allow_remote_outbound=allow_remote_outbound,
            idempotency_key=idempotency_key,
            actor=actor,
            create_job=self.jobs.create_job_in_transaction,
        )

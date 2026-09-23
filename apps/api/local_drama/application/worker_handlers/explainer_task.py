"""EXPLAINER_TASK job handler: one dispatcher for every explainer stage code.

Registration convention (read from ``application/worker.py``)
-------------------------------------------------------------
``worker.py`` keeps a single declarative registry:

    ``_EXTRACTED_HANDLER_PROVIDERS: dict[str, Callable[[LocalMediaWorker], Callable[[dict, Path], tuple[str, str]]]]``

with the comment "Declarative registry: job type -> business handler factory.
Every queue-dispatched job family's business flow lives under
application/worker_handlers; the runner binds each flow to its ports through
_EXTRACTED_HANDLER_PROVIDERS at dispatch time (design §13.2)."  Each entry is a
``_make_<family>_handler(worker)`` factory returning a
``Callable[[dict[str, Any], Path], tuple[str, str]]``; ``_dispatcher`` adapts
those 2-tuples into :class:`WorkerExecution`, while the richer
``AUTOMATION_WORKFLOW_TASK`` handler is registered directly in ``handlers`` and
returns a 4-tuple ``(kind, relative_path, report, produced_bytes)``.

This module follows the richer convention: :func:`run_explainer_task` returns
``(kind, relative_path, report, produced_bytes)`` and :func:`make_explainer_task_handler`
is the ``_make_*_handler``-shaped factory a future registry entry would call.
Wiring the entry into ``_EXTRACTED_HANDLER_PROVIDERS`` is intentionally left to
the worker owner because this task is restricted to these three files.

Deliberately NOT done here
--------------------------
* No model, GPU, FFmpeg, network or filesystem business logic: the stage work is
  an injected ``handlers`` callable per ``task_code``.
* No ``explainer_decisions`` write of kind ``POLICY_ACCEPTED``.  That row is
  produced by the dedicated ``EXPLAINER_POLICY_EVALUATE`` path through the
  injected ``policy_evaluator`` port; this module only records what that port
  returned and refuses to accept a policy decision from any other handler.
* **No HUMAN approval is ever minted.**  A payload or report that asks for one
  raises :class:`DomainRuleError` (``EXPLAINER_WORKER_HUMAN_APPROVAL_FORBIDDEN``).
* No second queue and no lease handling: the runner still owns claims,
  heartbeats and artifact registration.
"""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ExplainerContractError, utc_now_iso
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

#: The job type a queue-dispatched explainer stage job carries.
EXPLAINER_JOB_TYPE = "EXPLAINER_TASK"

#: Every explainer job stage this module can dispatch.  The text stages are backed
#: by the local text planner and the QC stages by the layered QC handler; including
#: them here means a stage job no longer fails with "unsupported stage code" before
#: its real handler is even consulted.
EXPLAINER_TASK_CODES: tuple[str, ...] = (
    "EXPLAINER_STORYBOARD",
    "EXPLAINER_POLICY_EVALUATE",
    "EXPLAINER_VISUAL_QC",
    "COMPOSITION_QC",
    "FACT_EXTRACT",
    "NARRATION_WRITE",
    "RESEARCH_ACQUIRE",
    # The production stages that turn the frozen plan into a finished film.  They
    # are part of the same dispatch family so one workflow item can execute exactly
    # one planned step without a second queue.
    "IDENTITY_ASSETS",
    "NARRATION_TTS",
    "NARRATION_ALIGN",
    "VISUAL_GENERATION",
    "SUBTITLE_BUILD",
    "COMPOSITION_RENDER",
    "EXPLAINER_EXPORT",
)

#: The only stage code allowed to carry a machine policy acceptance.
POLICY_TASK_CODE = "EXPLAINER_POLICY_EVALUATE"

#: Decision kinds that only a real human operator may create.
HUMAN_ONLY_DECISION_KINDS: frozenset[str] = frozenset({"HUMAN_APPROVED", "PUBLICATION_AUTHORIZED"})

#: Step statuses that end a projection row's run.
_TERMINAL_STEP_STATUSES: frozenset[str] = frozenset(
    {"SUCCEEDED", "RETRYABLE_FAILED", "TERMINAL_FAILED", "CANCELLED", "SKIPPED_WITH_REASON"}
)

AtomicWriter = Callable[[Path, Callable[[Path], object]], None]
CancelCheck = Callable[[], bool]

#: ``task_code -> callable(job, context)``; the callable returns the business
#: report (optionally ``(report, produced_bytes)``).
ExplainerTaskHandler = Callable[[dict[str, Any], dict[str, Any]], Any]


class ExplainerStepStore(Protocol):
    """Minimum projection-store port for ``explainer_step_bindings``."""

    def find_step(self, step_binding_id: str) -> Mapping[str, Any] | None:  # pragma: no cover - protocol boundary
        ...

    def run_cancel_requested(self, run_id: str) -> bool:  # pragma: no cover - protocol boundary
        ...

    def mark_step(
        self,
        step_binding_id: str,
        status: str,
        *,
        skip_reason: str | None = None,
        blocker_code: str | None = None,
        output_ref: Mapping[str, Any] | None = None,
        bump_attempt: bool = False,
    ) -> Mapping[str, Any] | None:  # pragma: no cover - protocol boundary
        ...


class ExplainerPolicyEvaluatorPort(Protocol):
    """The dedicated machine policy path.

    The port owns the rule/evidence/threshold evaluation *and* the
    ``explainer_decisions`` write; this handler only asserts the shape of what
    came back and never invents a decision itself.
    """

    def evaluate(
        self, *, project_id: str, video_id: str, semantic_inputs: Mapping[str, Any]
    ) -> Mapping[str, Any]:  # pragma: no cover - protocol boundary
        ...


class RepositoryExplainerStepStore:
    """``explainer_step_bindings`` projection store over :class:`ExplainerRepository`.

    Two construction modes exist on purpose:

    * ``RepositoryExplainerStepStore(repo)`` shares the caller's connection and is
      what unit tests use.
    * ``RepositoryExplainerStepStore(db, repo_factory=...)`` opens one short
      write transaction per projection update.  A worker must not hold a SQLite
      transaction across a GPU wait, so the runner supplies a factory that yields
      a repository inside ``Database.transaction()``.
    """

    def __init__(
        self,
        repo: ExplainerRepository | None = None,
        *,
        database: Any | None = None,
        repo_factory: Callable[[], AbstractContextManager[ExplainerRepository]] | None = None,
        on_step_settled: Callable[[str, str, Mapping[str, Any], str | None], Any] | None = None,
    ) -> None:
        if repo is None and repo_factory is None:
            raise DomainRuleError(
                "EXPLAINER_STEP_STORE_REQUIRED",
                "步骤投影存储必须提供仓库或仓库工厂",
            )
        self._repo = repo
        self._repo_factory = repo_factory
        self._database = database
        self._on_step_settled = on_step_settled

    def _use(self, function: Callable[[ExplainerRepository], Any]) -> Any:
        if self._repo is not None:
            return function(self._repo)
        assert self._repo_factory is not None
        with self._repo_factory() as repository:
            return function(repository)

    def find_step(self, step_binding_id: str) -> dict[str, Any] | None:
        return self._use(lambda repo: repo.find("explainer_step_bindings", step_binding_id))

    def run_cancel_requested(self, run_id: str) -> bool:
        def read(repo: ExplainerRepository) -> bool:
            run = repo.find("explainer_runs", run_id)
            return bool(run and run.get("cancel_requested_at"))

        return bool(self._use(read))

    def mark_step(
        self,
        step_binding_id: str,
        status: str,
        *,
        skip_reason: str | None = None,
        blocker_code: str | None = None,
        output_ref: Mapping[str, Any] | None = None,
        bump_attempt: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": status}
        if status == "RUNNING":
            payload["started_at"] = utc_now_iso()
        if status in _TERMINAL_STEP_STATUSES:
            payload["finished_at"] = utc_now_iso()
        if skip_reason is not None:
            payload["skip_reason"] = str(skip_reason)
        if blocker_code is not None:
            payload["blocker_code"] = str(blocker_code)
        if output_ref is not None:
            payload["output_ref_json"] = dict(output_ref)

        def write(repo: ExplainerRepository) -> dict[str, Any]:
            row = repo.update("explainer_step_bindings", step_binding_id, payload)
            if bump_attempt:
                repo.bump("explainer_step_bindings", step_binding_id, attempt_count=1)
                row = repo.get("explainer_step_bindings", step_binding_id)
            return row

        return self._use(write)

    def notify_run_advanced(
        self, *, status: str, task_code: str, report: Mapping[str, Any], blocker_code: str | None = None
    ) -> Any:
        """Tell the runner that one stage settled, with its final status.

        The worker injects a callback here; the store itself never starts or
        advances anything, so ``explainer_step_bindings`` stays a projection of
        the existing workflow/Job authority rather than a second state machine.
        """

        if self._on_step_settled is None:
            return None
        return self._on_step_settled(status, task_code, dict(report), blocker_code)


def _assert_no_human_approval(payload: Mapping[str, Any], *, where: str) -> None:
    """Worker code may never mint a human approval or a publication authorization."""

    if payload.get("human_approval_id"):
        raise DomainRuleError(
            "EXPLAINER_WORKER_HUMAN_APPROVAL_FORBIDDEN",
            "worker 不能生成人工批准；请由人工操作入口记录批准",
            {"where": where, "field": "human_approval_id"},
        )
    if payload.get("human_approved") is True:
        raise DomainRuleError(
            "EXPLAINER_WORKER_HUMAN_APPROVAL_FORBIDDEN",
            "worker 不能生成人工批准；请由人工操作入口记录批准",
            {"where": where, "field": "human_approved"},
        )
    if str(payload.get("authority") or "").upper() == "HUMAN":
        raise DomainRuleError(
            "EXPLAINER_WORKER_HUMAN_APPROVAL_FORBIDDEN",
            "worker 不能以 HUMAN 权威执行操作",
            {"where": where, "field": "authority"},
        )
    candidates: list[Any] = [payload.get("decision_kind"), payload.get("decision")]
    decisions = payload.get("decisions")
    if isinstance(decisions, Sequence) and not isinstance(decisions, (str, bytes)):
        candidates.extend(decisions)
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            kind = str(candidate.get("decision_kind") or "")
        elif isinstance(candidate, str):
            kind = candidate
        else:
            continue
        if kind in HUMAN_ONLY_DECISION_KINDS:
            raise DomainRuleError(
                "EXPLAINER_WORKER_HUMAN_APPROVAL_FORBIDDEN",
                "worker 不能生成人工批准或发布授权；请由人工操作入口记录",
                {"where": where, "decision_kind": kind},
            )


def _as_semantic_inputs(job: Mapping[str, Any]) -> dict[str, Any]:
    raw = job.get("input_snapshot") or job.get("semantic_inputs") or {}
    if not isinstance(raw, Mapping):
        raise DomainRuleError("JOB_INPUT_INVALID", "解说任务 Job 的 semantic_inputs 必须是对象")
    semantic_inputs = dict(raw)
    nested = semantic_inputs.get("semantic_inputs")
    if isinstance(nested, Mapping):
        semantic_inputs = {**semantic_inputs, **dict(nested)}
    return semantic_inputs


def _resolve_task_code(job: Mapping[str, Any], semantic_inputs: Mapping[str, Any]) -> str:
    task_code = str(
        semantic_inputs.get("task_code") or job.get("task_code") or job.get("stage_code") or ""
    ).strip()
    if not task_code:
        raise DomainRuleError(
            "JOB_INPUT_INVALID", "解说任务 Job 缺少 task_code/stage_code", {"job_id": str(job.get("id") or "")}
        )
    if task_code not in EXPLAINER_TASK_CODES:
        raise DomainRuleError(
            "EXPLAINER_TASK_CODE_UNSUPPORTED",
            "不支持的解说阶段代码",
            {"task_code": task_code, "supported": list(EXPLAINER_TASK_CODES)},
        )
    return task_code


def _policy_decision_from_evaluator(
    evaluator: ExplainerPolicyEvaluatorPort,
    *,
    project_id: str,
    video_id: str,
    semantic_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the injected policy path and assert it really is a machine acceptance.

    This module never writes ``explainer_decisions`` itself: the port owns that
    write, and the shape checks below are what stop a policy acceptance from
    being fabricated by any other stage.
    """

    raw = evaluator.evaluate(
        project_id=project_id, video_id=video_id, semantic_inputs=dict(semantic_inputs)
    )
    if not isinstance(raw, Mapping):
        raise DomainRuleError(
            "EXPLAINER_POLICY_EVALUATOR_INVALID", "policy_evaluator 必须返回决策对象"
        )
    decision = dict(raw)
    _assert_no_human_approval(decision, where="policy_evaluator")
    kind = str(decision.get("decision_kind") or "")
    if kind != "POLICY_ACCEPTED":
        raise DomainRuleError(
            "EXPLAINER_POLICY_EVALUATOR_INVALID",
            "EXPLAINER_POLICY_EVALUATE 只能记录 POLICY_ACCEPTED 机器判定",
            {"decision_kind": kind},
        )
    if str(decision.get("actor_type") or "") != "MACHINE":
        raise DomainRuleError(
            "EXPLAINER_POLICY_EVALUATOR_INVALID",
            "POLICY_ACCEPTED 必须由 MACHINE 身份产生",
            {"actor_type": decision.get("actor_type")},
        )
    if not str(decision.get("policy_processor") or "").strip():
        raise DomainRuleError(
            "EXPLAINER_POLICY_EVALUATOR_INVALID",
            "POLICY_ACCEPTED 必须记录 policy_processor",
            {"policy_processor": decision.get("policy_processor")},
        )
    return decision


def _normalize_handler_result(result: Any) -> tuple[dict[str, Any], int]:
    produced_extra = 0
    if isinstance(result, tuple):
        if len(result) != 2:
            raise DomainRuleError(
                "EXPLAINER_TASK_HANDLER_RESULT_INVALID", "解说任务处理器必须返回 (报告, 字节数)"
            )
        report, produced_extra = result
    else:
        report = result
    if not isinstance(report, Mapping):
        raise DomainRuleError(
            "EXPLAINER_TASK_HANDLER_RESULT_INVALID", "解说任务处理器必须返回报告对象"
        )
    return dict(report), max(0, int(produced_extra or 0))


def run_explainer_task(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    atomic_writer: AtomicWriter,
    handlers: Mapping[str, ExplainerTaskHandler],
    step_store: ExplainerStepStore | None = None,
    cancel_check: CancelCheck | None = None,
    policy_evaluator: ExplainerPolicyEvaluatorPort | None = None,
    worker_id: str = "local-worker",
    actor: str = "local-user",
) -> tuple[str, str, dict[str, Any], int]:
    """Execute one explainer stage task job.

    Returns the richer handler shape ``(kind, relative_path, report,
    produced_bytes)`` (the same shape ``run_automation_task`` uses).  A cancelled
    job raises ``JOB_CANCELLED`` before starting new work so the runner never
    registers an artifact for it.
    """

    semantic_inputs = _as_semantic_inputs(job)
    _assert_no_human_approval(semantic_inputs, where="semantic_inputs")
    step_binding_id = str(semantic_inputs.get("step_binding_id") or "").strip()
    step_row: Mapping[str, Any] | None = None
    if step_binding_id:
        if step_store is None:
            raise DomainRuleError(
                "EXPLAINER_STEP_STORE_REQUIRED",
                "任务带有 step_binding_id 时必须注入 explainer_step_bindings 投影存储",
                {"step_binding_id": step_binding_id},
            )
        step_row = step_store.find_step(step_binding_id)
        if step_row is None:
            raise DomainRuleError(
                "EXPLAINER_STEP_BINDING_NOT_FOUND",
                "解说步骤投影不存在",
                {"step_binding_id": step_binding_id},
            )
    task_code = str(
        semantic_inputs.get("task_code") or (step_row or {}).get("planned_step_code") or ""
    ).strip()
    if not task_code:
        task_code = _resolve_task_code(job, semantic_inputs)
    elif task_code not in EXPLAINER_TASK_CODES:
        raise DomainRuleError(
            "EXPLAINER_TASK_CODE_UNSUPPORTED",
            "不支持的解说阶段代码",
            {"task_code": task_code, "supported": list(EXPLAINER_TASK_CODES)},
        )
    run_id = str(
        semantic_inputs.get("run_id")
        or semantic_inputs.get("automation_run_id")
        or (step_row or {}).get("run_id")
        or ""
    )
    video_id = str(semantic_inputs.get("video_id") or "")
    project_id = str(semantic_inputs.get("project_id") or job.get("project_id") or "")

    def cancelled() -> bool:
        if cancel_check is not None and bool(cancel_check()):
            return True
        if step_store is not None and run_id and step_store.run_cancel_requested(run_id):
            return True
        return False

    if step_store is not None and step_binding_id:
        step_store.mark_step(step_binding_id, "RUNNING", bump_attempt=True)
    if cancelled():
        if step_store is not None and step_binding_id:
            step_store.mark_step(step_binding_id, "CANCELLED", blocker_code="JOB_CANCELLED")
        raise DomainRuleError("JOB_CANCELLED", "已请求取消，不会启动新的解说任务", {"task_code": task_code})

    context: dict[str, Any] = {
        "task_code": task_code,
        "semantic_inputs": semantic_inputs,
        "step_binding_id": step_binding_id or None,
        "run_id": run_id or None,
        "video_id": video_id or None,
        "project_id": project_id or None,
        "work_root": work_root,
        "output_root": output_root,
        "worker_id": worker_id,
        "actor": actor,
    }

    report: dict[str, Any]
    produced_extra = 0
    try:
        if task_code == POLICY_TASK_CODE:
            if policy_evaluator is None:
                raise DomainRuleError(
                    "EXPLAINER_POLICY_EVALUATOR_REQUIRED",
                    "EXPLAINER_POLICY_EVALUATE 必须注入 policy_evaluator 端口",
                    {"task_code": task_code},
                )
            decision = _policy_decision_from_evaluator(
                policy_evaluator,
                project_id=project_id,
                video_id=video_id,
                semantic_inputs=semantic_inputs,
            )
            # The verdict has three outcomes, not two.  A verdict that asks for an
            # operator (``workflow_effect=REQUEST_HUMAN``) is the designed pause of
            # a review-first run, not a terminal failure of the step: reporting it as
            # FAIL made the projection show "终止失败" for a run that was merely
            # waiting for the human approval it went on to receive.
            accepted = bool(decision.get("accepted"))
            requests_human = str(decision.get("workflow_effect") or "") == "REQUEST_HUMAN"
            verdict_status = "PASS" if accepted else ("NEEDS_HITL" if requests_human else "FAIL")
            report = {
                "status": verdict_status,
                "machine_check": {
                    "status": verdict_status,
                    "ok": accepted,
                    "workflow_effect": str(decision.get("workflow_effect") or ""),
                    "rule_version": str(decision.get("rule_version") or ""),
                    "policy_processor": str(decision.get("policy_processor") or ""),
                },
                "produced": {"policy_decision": decision},
                "summary": "机器政策判定已由专用策略路径记录（不构成人工批准）",
                "policy_decision": decision,
            }
        else:
            handler = handlers.get(task_code)
            if handler is None:
                raise DomainRuleError(
                    "EXPLAINER_TASK_HANDLER_MISSING",
                    "缺少该解说阶段的任务处理器",
                    {"task_code": task_code, "registered": sorted(handlers)},
                )
            report, produced_extra = _normalize_handler_result(handler(job, context))
            _assert_no_human_approval(report, where=f"handler:{task_code}")
            if report.get("policy_decision") or report.get("policy_decisions"):
                raise DomainRuleError(
                    "EXPLAINER_DECISION_PATH_FORBIDDEN",
                    "POLICY_ACCEPTED 只能由 EXPLAINER_POLICY_EVALUATE 专用路径记录",
                    {"task_code": task_code},
                )
    except DomainRuleError as error:
        if step_store is not None and step_binding_id:
            step_store.mark_step(
                step_binding_id,
                "CANCELLED" if error.code == "JOB_CANCELLED" else "RETRYABLE_FAILED",
                blocker_code=error.code,
            )
        raise
    except ExplainerContractError as error:
        # The explainer domain's own contract error is a business failure like any
        # other: it must settle the step projection before it leaves this module, or
        # the run keeps a step that is "running" with nothing behind it.
        if step_store is not None and step_binding_id:
            step_store.mark_step(step_binding_id, "RETRYABLE_FAILED", blocker_code=error.code)
        raise
    if cancelled():
        if step_store is not None and step_binding_id:
            step_store.mark_step(step_binding_id, "CANCELLED", blocker_code="JOB_CANCELLED")
        raise DomainRuleError(
            "JOB_CANCELLED", "已请求取消，本次解说任务输出不会登记", {"task_code": task_code}
        )

    machine_check = report.get("machine_check")
    if not isinstance(machine_check, Mapping):
        machine_check = {"status": str(report.get("status") or "FAIL"), "ok": report.get("status") == "PASS"}
    status = str(machine_check.get("status") or report.get("status") or "FAIL")
    full_report: dict[str, Any] = {
        "schema_version": "localdrama.explainer-task-report.v1",
        "job_id": str(job.get("id") or ""),
        "task_code": task_code,
        "step_binding_id": step_binding_id or None,
        "run_id": run_id or None,
        "video_id": video_id or None,
        "project_id": project_id or None,
        "worker_id": worker_id,
        "actor": actor,
        "status": status,
        "machine_check": dict(machine_check),
        "produced": dict(report.get("produced") or {}),
        "summary": str(report.get("summary") or ""),
        "handler_report": {key: value for key, value in report.items() if key not in {"machine_check", "produced", "summary", "status"}},
        "created_at": datetime.now(UTC).isoformat(),
        "human_approval_written": False,
        "publication_authorized": False,
    }
    relative = write_explainer_report(
        output_root=output_root, work_root=work_root, report=full_report, atomic_writer=atomic_writer
    )
    produced_bytes = max(0, int((output_root / "report.json").stat().st_size) + produced_extra)
    step_status = {
        "PASS": "SUCCEEDED",
        "SKIPPED": "SUCCEEDED",
        "NEEDS_HITL": "BLOCKED",
        "BLOCKED": "BLOCKED",
    }.get(status, "TERMINAL_FAILED")
    if step_store is not None and step_binding_id:
        step_store.mark_step(
            step_binding_id,
            step_status,
            blocker_code=None if step_status == "SUCCEEDED" else status,
            output_ref={"report_rel_path": relative, "status": status},
        )
        notify = getattr(step_store, "notify_run_advanced", None)
        if callable(notify):
            # Advancing the owning workflow run happens only after the artifact
            # and the projection are both durably written, so a crash cannot
            # advance the graph past a step whose report was never registered.
            notify(
                status=step_status,
                task_code=task_code,
                report=full_report,
                blocker_code=None if step_status == "SUCCEEDED" else status,
            )
    return "EXPLAINER_TASK_REPORT", relative, full_report, produced_bytes


def write_explainer_report(
    *,
    output_root: Path,
    work_root: Path,
    report: Mapping[str, Any],
    atomic_writer: AtomicWriter,
) -> str:
    """Write the standard report artifact and return its work-root relative path."""

    path = output_root / "report.json"
    atomic_writer(
        path,
        lambda target: target.write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        ),
    )
    return path.relative_to(work_root).as_posix()


def make_explainer_task_handler(
    *,
    work_root: Path,
    atomic_writer: AtomicWriter,
    handlers: Mapping[str, ExplainerTaskHandler],
    step_store: ExplainerStepStore | None = None,
    cancel_check: CancelCheck | None = None,
    policy_evaluator: ExplainerPolicyEvaluatorPort | None = None,
    worker_id: str = "local-worker",
    actor: str = "local-user",
) -> Callable[[dict[str, Any], Path], tuple[str, str, dict[str, Any], int]]:
    """Provider-shaped factory mirroring ``worker.py``'s ``_make_*_handler`` binders."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str, dict[str, Any], int]:
        return run_explainer_task(
            job,
            output_root,
            work_root=work_root,
            atomic_writer=atomic_writer,
            handlers=handlers,
            step_store=step_store,
            cancel_check=cancel_check,
            policy_evaluator=policy_evaluator,
            worker_id=worker_id,
            actor=actor,
        )

    return handler

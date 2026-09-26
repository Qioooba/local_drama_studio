"""Bridge one explainer workflow step to the explainer task family.

The explainer graph is expressed as an ordinary automation workflow with one batch
item per planned step (``production.ExplainerProductionService.ensure_workflow``),
and the workflow's own executor creates one ``AUTOMATION_WORKFLOW_TASK`` job per
item.  That generic handler requires a ``payload.action`` and an ``episode_id``,
which an explainer item has neither of — every explainer run therefore failed its
first job with ``AUTOMATION_TASK_PAYLOAD_INVALID`` and the run could never leave
``RESEARCH_ACQUIRE``.

This module is the missing dispatch.  It recognizes an explainer batch item,
resolves the business identity the stage family needs (the explainer run, its
video, and the step projection row) and hands the work to
:func:`~local_drama.application.worker_handlers.explainer_task.run_explainer_task`
with the same frozen inputs the corresponding standalone stage command would use.

Only the item's *identity* is taken from the workflow.  Everything the stage
executes against comes from the durable projection and the frozen revisions, so a
tampered item payload cannot redirect a stage to another video.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from local_drama.application.explainers.production import TASK_SKELETON
from local_drama.domain.errors import DomainRuleError

__all__ = [
    "EXPLAINER_STEP_ITEM_PREFIX",
    "explainer_step_code_from_payload",
    "is_explainer_workflow_item",
    "run_explainer_workflow_step",
]

#: ``item_key`` shape: ``<iteration>:explainer:<STEP_CODE>``.
EXPLAINER_STEP_ITEM_PREFIX = "explainer:"

_STEP_CODES = frozenset(str(step["step_code"]) for step in TASK_SKELETON)

#: The QC layers each QC stage asks for.  The picture layers need a decoder or a
#: frame sampler; the subtitle and fact layers are always available, and a layer
#: whose port is missing is reported ``LAYER_NOT_RUN`` instead of passing.
_QC_LAYERS: dict[str, tuple[str, ...]] = {
    "EXPLAINER_VISUAL_QC": ("SUBTITLE", "FACT"),
    "COMPOSITION_QC": ("TECHNICAL", "SUBTITLE", "FACT"),
}


def explainer_step_code_from_payload(payload: Mapping[str, Any] | None) -> str | None:
    """The planned step code an explainer workflow item carries, if it is one."""

    if not isinstance(payload, Mapping):
        return None
    candidate = str(payload.get("step_code") or payload.get("task_code") or "").strip().upper()
    return candidate if candidate in _STEP_CODES else None


def is_explainer_workflow_item(payload: Mapping[str, Any] | None) -> bool:
    return explainer_step_code_from_payload(payload) is not None


def _load_payload(job: Mapping[str, Any], database: Any) -> dict[str, Any]:
    snapshot = job.get("input_snapshot") or {}
    run_id = str(snapshot.get("automation_run_id") or "")
    task_id = str(snapshot.get("automation_task_id") or "")
    item_key = str(snapshot.get("item_key") or "")
    payload: Mapping[str, Any] = {}
    if run_id and task_id:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT item_json FROM automation_workflow_run_tasks WHERE id=? AND run_id=?",
                (task_id, run_id),
            ).fetchone()
        if row is not None:
            try:
                item = json.loads(str(row["item_json"] or "{}"))
            except (TypeError, ValueError):
                item = {}
            raw = item.get("payload") if isinstance(item, dict) else None
            if isinstance(raw, Mapping):
                payload = raw
    if not payload and ":" in item_key:
        payload = {"step_code": item_key.split(":", 1)[1]}
    return dict(payload)


def run_explainer_workflow_step(
    job: Mapping[str, Any],
    output_root: Path,
    *,
    database: Any,
    work_root: Path,
    worker_id: str,
    handlers: Mapping[str, Callable[[dict[str, Any], Mapping[str, Any]], Any]],
    step_store: Any,
    atomic_writer: Callable[[Path, Callable[[Path], object]], None],
    policy_evaluator: Any | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[str, str, dict[str, Any], int]:
    """Execute the explainer stage one workflow item stands for."""

    from local_drama.application.worker_handlers.explainer_task import run_explainer_task

    payload = _load_payload(job, database)
    step_code = explainer_step_code_from_payload(payload)
    if step_code is None:
        raise DomainRuleError(
            "EXPLAINER_WORKFLOW_ITEM_UNSUPPORTED",
            "该 workflow 任务不是解说阶段任务",
            {"item_payload": sorted(payload)},
        )
    snapshot = job.get("input_snapshot") or {}
    automation_run_id = str(snapshot.get("automation_run_id") or "")
    if not automation_run_id:
        raise DomainRuleError(
            "EXPLAINER_WORKFLOW_ITEM_UNSUPPORTED",
            "解说 workflow 任务缺少 automation_run_id",
            {"step_code": step_code},
        )
    with database.connect() as connection:
        run = connection.execute(
            "SELECT * FROM explainer_runs WHERE automation_workflow_run_id=?", (automation_run_id,)
        ).fetchone()
        if run is None:
            raise DomainRuleError(
                "EXPLAINER_RUN_NOT_FOUND",
                "找不到该 workflow 运行对应的解说生产运行",
                {"automation_workflow_run_id": automation_run_id, "step_code": step_code},
            )
        step_binding = connection.execute(
            "SELECT * FROM explainer_step_bindings WHERE run_id=? AND planned_step_code=?",
            (str(run["id"]), step_code),
        ).fetchone()
        if step_binding is None:
            raise DomainRuleError(
                "EXPLAINER_STEP_BINDING_NOT_FOUND",
                "解说步骤投影不存在",
                {"run_id": str(run["id"]), "step_code": step_code},
            )
        editions = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM explainer_editions WHERE video_id=? ORDER BY edition_key", (str(run["video_id"]),)
            )
        ]
        primary_render = None
        if step_code in {"COMPOSITION_QC", "EXPLAINER_POLICY_EVALUATE"} and editions:
            # Both the composition QC and the machine policy judge the *rendered
            # film*, not the edition row: the policy only accepts a QC report that
            # examined the exact render hash.  Resolving the render only for the QC
            # step left the policy evaluating an edition that can never have a
            # report, so it always answered REQUEST_HUMAN.
            primary_render = connection.execute(
                """SELECT * FROM composition_renders WHERE edition_id=? AND render_kind='FULL' AND status='SUCCEEDED'
                ORDER BY revision_no DESC LIMIT 1""",
                (str(editions[0]["id"]),),
            ).fetchone()
    semantic_inputs: dict[str, Any] = {
        "task_code": step_code,
        "stage_code": step_code,
        "run_id": str(run["id"]),
        "automation_run_id": automation_run_id,
        "video_id": str(run["video_id"]),
        "project_id": str(run["project_id"]),
        "step_binding_id": str(step_binding["id"]),
    }
    if editions:
        semantic_inputs["edition_id"] = str(editions[0]["id"])
        # A graph step covers the whole work, not the first edition: the stages that
        # accept an optional per-edition scope must therefore treat this call as
        # "every declared edition".  Without the marker, a work declaring a clean and
        # a captioned output rendered and packaged only the first one.
        semantic_inputs["edition_scope"] = "VIDEO"
    # The frozen capability snapshot is the authority for which local model a stage
    # may use.  VISUAL_GENERATION executes the bound image profile recorded at
    # preflight time, so the snapshot travels with the job instead of the worker
    # guessing a "latest" profile.
    raw_snapshot = run["capability_snapshot_json"] if "capability_snapshot_json" in run.keys() else None
    if isinstance(raw_snapshot, str) and raw_snapshot.strip():
        try:
            semantic_inputs["capability_snapshot"] = json.loads(raw_snapshot)
        except (TypeError, ValueError):
            pass
    elif isinstance(raw_snapshot, Mapping):
        semantic_inputs["capability_snapshot"] = dict(raw_snapshot)
    if step_code == "EXPLAINER_STORYBOARD":
        # The storyboard may only plan render types the frozen capability snapshot
        # authorized.  The deterministic still-picture types (``STILL_MOTION`` /
        # ``PARALLAX``) are retired: an explainer's moving pictures come from real AI
        # image-to-video, so ``I2V`` is offered only when the snapshot really has a
        # published ``video.image_to_video`` binding, and it is offered first so the
        # planner prefers it over the remaining graphic / licensed-material types.
        raw_capabilities = raw_snapshot
        capabilities: Any = {}
        if isinstance(raw_capabilities, str) and raw_capabilities.strip():
            try:
                capabilities = json.loads(raw_capabilities)
            except (TypeError, ValueError):
                capabilities = {}
        elif isinstance(raw_capabilities, Mapping):
            capabilities = dict(raw_capabilities)
        usable: list[str] = []
        entries = capabilities.get("capabilities") if isinstance(capabilities, Mapping) else None
        if isinstance(entries, list):
            available = {
                str(item.get("capability"))
                for item in entries
                if isinstance(item, Mapping) and item.get("available")
            }
            if "video.image_to_video" in available:
                usable.append("I2V")
        usable.extend(["INFOGRAPHIC", "LICENSED_MEDIA"])
        semantic_inputs["usable_render_types"] = usable
    if step_code in _QC_LAYERS:
        layers = list(_QC_LAYERS[step_code])
        if step_code == "COMPOSITION_QC" and primary_render is not None:
            semantic_inputs["subject_kind"] = "COMPOSITION_RENDER"
            semantic_inputs["subject_revision_id"] = str(primary_render["id"])
        else:
            semantic_inputs["subject_kind"] = "EDITION"
        semantic_inputs["layers"] = layers
    if step_code == "EXPLAINER_POLICY_EVALUATE":
        if primary_render is not None:
            semantic_inputs["subject_kind"] = "COMPOSITION_RENDER"
            semantic_inputs["subject_revision_id"] = str(primary_render["id"])
            semantic_inputs["render_id"] = str(primary_render["id"])
        elif editions:
            semantic_inputs["subject_kind"] = "EDITION"
            semantic_inputs["subject_revision_id"] = str(editions[0]["id"])
    synthetic_job = {
        **dict(job),
        "input_snapshot": {"semantic_inputs": semantic_inputs},
    }
    return run_explainer_task(
        synthetic_job,
        output_root,
        work_root=work_root,
        atomic_writer=atomic_writer,
        handlers=handlers,
        step_store=step_store,
        cancel_check=cancel_check,
        policy_evaluator=policy_evaluator,
        worker_id=worker_id,
    )

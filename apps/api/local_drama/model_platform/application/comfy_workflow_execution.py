"""Formal V2 Worker handler for a Profile-frozen Comfy workflow binding."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from local_drama.application.comfy_smoke_contract import parse_comfy_smoke_contract
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.comfy_artifact_inputs import materialize_v2_comfy_artifact_inputs
from local_drama.model_platform.application.comfy_execution_support import assert_comfy_outputs, copy_comfy_outputs
from local_drama.model_platform.application.execution_job_links import WorkerExecutionSnapshot

_HANDLER_CODE = "comfy.workflow.v2"
_HANDLER_VERSION = "v1"
_ADAPTER_CODE = "comfy.workflow.v1"
_TEMPLATE = "comfy.workflow.profile.v1"


def make_comfy_workflow_handler(
    settings: Settings,
    *,
    workflows: WorkflowService | None = None,
    comfy: ComfyClient | None = None,
) -> Callable[[WorkerExecutionSnapshot, Path], tuple[str, str]]:
    """Build the only formal V2 handler for Profile-owned Comfy workflows."""

    workflows = workflows or WorkflowServicePlaceholder(settings)
    comfy = comfy or ComfyClient(
        settings.comfy_base_url,
        settings.comfy_output_root,
        allow_private_network=settings.allows_private_network,
    )

    def execute(snapshot: WorkerExecutionSnapshot, output_root: Path) -> tuple[str, str]:
        binding = _binding(snapshot)
        workflow = workflows.get_version(binding["workflow_version_id"])
        _assert_workflow(binding, workflow)
        semantic_inputs = materialize_v2_comfy_artifact_inputs(Database(settings.database_path), settings, snapshot.semantic_inputs)
        _assert_semantic_inputs(workflow, semantic_inputs)
        compiled = workflows.compile_semantic_inputs(binding["workflow_version_id"], semantic_inputs)
        response = comfy.queue_prompt(compiled["workflow"], client_id=f"local-drama-v2-{snapshot.job_id}")
        waited = comfy.wait_history(str(response["prompt_id"]), timeout_seconds=float(binding["timeout_seconds"]))
        if str(waited.get("status")) != "success":
            raise DomainRuleError("MP_COMFY_EXECUTION_FAILED", "正式 Comfy V2 执行没有成功完成。")
        outputs = comfy.collect_outputs(dict(waited.get("history") or {}))
        expected = binding["expected_output"]
        assert_comfy_outputs(
            outputs,
            media_kind=expected["media_kind"],
            min_count=expected["min_count"],
            max_count=expected["max_count"],
            error_prefix="MP_COMFY_EXECUTION",
        )
        copies = copy_comfy_outputs(outputs, output_root, folder="comfy-execution")
        try:
            relative = copies[0].relative_to(settings.work_root).as_posix()
        except ValueError as error:
            raise DomainRuleError("MP_EXECUTION_OUTPUT_ROOT_INVALID", "V2 Worker 输出目录不在受控 work_root 内。") from error
        return "COMFY_OUTPUT", relative

    return execute


class WorkflowServicePlaceholder:
    """Delay database construction until this process's handler is invoked."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._service: WorkflowService | None = None

    def _resolve(self) -> WorkflowService:
        if self._service is None:
            from local_drama.infrastructure.database.sqlite import Database

            self._service = WorkflowService(Database(self.settings.database_path), self.settings)
        return self._service

    def get_version(self, version_id: str):
        return self._resolve().get_version(version_id)

    def compile_semantic_inputs(self, version_id: str, semantic_inputs: dict[str, Any]):
        return self._resolve().compile_semantic_inputs(version_id, semantic_inputs)


def _binding(snapshot: WorkerExecutionSnapshot) -> dict[str, Any]:
    if snapshot.adapter_code != _ADAPTER_CODE or str(snapshot.network_policy.get("mode") or "").upper() != "LOCAL_ONLY":
        raise DomainRuleError("MP_COMFY_EXECUTION_SNAPSHOT_MISMATCH", "冻结快照不属于受控的 LOCAL_ONLY Comfy V2 Handler。")
    raw = snapshot.execution_binding
    expected = raw.get("expected_output") if isinstance(raw, Mapping) else None
    required = ("template", "workflow_binding_id", "workflow_version_id", "workflow_content_hash", "smoke_contract_hash", "timeout_seconds")
    if (
        not isinstance(raw, Mapping)
        or raw.get("template") != _TEMPLATE
        or any(not isinstance(raw.get(key), str) or not raw[key].strip() for key in required[:5])
        or not isinstance(raw.get("timeout_seconds"), int)
        or not 5 <= raw["timeout_seconds"] <= 300
        or not isinstance(expected, Mapping)
        or expected.get("media_kind") not in {"IMAGE", "VIDEO", "AUDIO"}
        or expected.get("min_count") != 1
        or expected.get("max_count") != 1
    ):
        raise DomainRuleError("MP_COMFY_EXECUTION_BINDING_INVALID", "冻结 Comfy Profile 缺少受控的工作流和单一主产物合同。")
    return {
        "workflow_binding_id": raw["workflow_binding_id"],
        "workflow_version_id": raw["workflow_version_id"],
        "workflow_content_hash": raw["workflow_content_hash"],
        "smoke_contract_hash": raw["smoke_contract_hash"],
        "timeout_seconds": raw["timeout_seconds"],
        "expected_output": {
            "media_kind": expected["media_kind"],
            "min_count": expected["min_count"],
            "max_count": expected["max_count"],
        },
    }


def _assert_workflow(binding: Mapping[str, Any], workflow: Mapping[str, Any]) -> None:
    if workflow.get("status") != "PUBLISHED" or str(workflow.get("content_hash") or "") != binding["workflow_content_hash"]:
        raise DomainRuleError("MP_COMFY_EXECUTION_WORKFLOW_STALE", "冻结 Comfy 工作流版本不再可用。")
    smoke = parse_comfy_smoke_contract(workflow.get("contract", {}), workflow.get("node_bindings", {}))
    if smoke.content_hash != binding["smoke_contract_hash"]:
        raise DomainRuleError("MP_COMFY_EXECUTION_SMOKE_CONTRACT_STALE", "冻结 Comfy 工作流的真实 smoke 合同已变化。")


def _assert_semantic_inputs(workflow: Mapping[str, Any], values: Mapping[str, Any]) -> None:
    contract = workflow.get("contract")
    bindings = workflow.get("node_bindings")
    slots = contract.get("input_slots") if isinstance(contract, Mapping) else None
    if not isinstance(slots, Mapping) or not isinstance(bindings, Mapping):
        raise DomainRuleError("MP_COMFY_EXECUTION_INPUT_CONTRACT_INVALID", "正式 Comfy 工作流必须声明 input_slots 与 semantic bindings。")
    unknown = sorted(str(key) for key in values if str(key) not in slots or str(key) not in bindings)
    missing = sorted(str(key) for key, spec in slots.items() if isinstance(spec, Mapping) and spec.get("required", True) and str(key) not in values)
    if unknown or missing:
        raise DomainRuleError("MP_COMFY_EXECUTION_INPUT_INVALID", "正式 Comfy 执行输入不符合工作流语义合同。", {"unknown": unknown, "missing": missing})
    for key, value in values.items():
        if isinstance(value, (dict, list)) or value is None or (isinstance(value, str) and (not value.strip() or len(value) > 8192)):
            raise DomainRuleError("MP_COMFY_EXECUTION_INPUT_INVALID", "正式 Comfy V2 仅接受有界标量语义输入。", {"field": str(key)})


def comfy_workflow_handler_identity() -> tuple[str, str]:
    return _HANDLER_CODE, _HANDLER_VERSION

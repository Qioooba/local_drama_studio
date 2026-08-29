"""Read-only V2 eligibility for the standalone Quick Create surface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.capability_resolution import CapabilityAssignmentService, CapabilityScopeContext

_MODE_CAPABILITIES = {
    "TEXT_TO_IMAGE": ("IMAGE_CONCEPT",),
    "TEXT_TO_VIDEO": ("VIDEO_T2V",),
    "TEXT_TO_IMAGE_TO_VIDEO": ("IMAGE_CONCEPT", "VIDEO_I2V"),
}


@dataclass(frozen=True, slots=True)
class QuickCreateV2Readiness:
    mode: str
    capability_code: str
    execution_profile_version_id: str | None
    ready: bool
    blocker: str | None


class QuickCreateV2ReadinessService:
    """Resolve SYSTEM V2 Assignments without touching V1 quick-generation runs."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.assignments = CapabilityAssignmentService(database)

    def list(self) -> tuple[QuickCreateV2Readiness, ...]:
        result: list[QuickCreateV2Readiness] = []
        for mode, capabilities in _MODE_CAPABILITIES.items():
            for capability_code in capabilities:
                resolution = self.assignments.resolve(capability_code, CapabilityScopeContext())
                blocker = resolution.blocked_reason
                if blocker is None and resolution.execution_profile_version_id is not None:
                    blocker = self._cutover_blocker(mode, resolution.execution_profile_version_id)
                result.append(QuickCreateV2Readiness(
                    mode, capability_code, resolution.execution_profile_version_id,
                    resolution.execution_profile_version_id is not None and blocker is None,
                    blocker,
                ))
        return tuple(result)

    def _cutover_blocker(self, mode: str, profile_version_id: str) -> str | None:
        """Prove a direct image command could consume this frozen V2 Profile.

        Video and image-to-video remain deliberately blocked: their prompt
        expansion, first-frame selection and multi-job artifact hand-off need
        their own immutable V2 aggregate, not a V1 quick-run shortcut.
        """
        if mode != "TEXT_TO_IMAGE":
            return "QUICK_CREATE_V2_MULTI_STAGE_PIPELINE_REQUIRED"
        return direct_image_profile_contract_blocker(self.database, profile_version_id)


def direct_image_profile_contract_blocker(database: Database, profile_version_id: str) -> str | None:
    """Check the safe one-prompt/one-image contract without exposing workflow wiring."""
    with database.connect() as connection:
        row = connection.execute(
            """SELECT runtime.adapter_code,profile.workflow_version_id,profile.payload_json,
                      workflow.status AS workflow_status,workflow.contract_json
               FROM mp_execution_profile_versions profile
               JOIN mp_runtime_installation_versions runtime ON runtime.id=profile.runtime_installation_version_id
               LEFT JOIN workflow_versions workflow ON workflow.id=profile.workflow_version_id
               WHERE profile.id=?""",
            (profile_version_id,),
        ).fetchone()
    if row is None:
        return "QUICK_CREATE_V2_PROFILE_NOT_FOUND"
    return _direct_image_contract_blocker(
        adapter_code=str(row["adapter_code"]),
        workflow_status=str(row["workflow_status"] or ""),
        workflow_contract=_json_object(row["contract_json"]),
        profile_payload=_json_object(row["payload_json"]),
    )


def candidate_image_profile_contract_blocker(database: Database, profile_version_id: str) -> str | None:
    """Require a deterministic optional SEED slot for a V2 candidate batch."""
    blocker = direct_image_profile_contract_blocker(database, profile_version_id)
    if blocker is not None:
        return blocker
    with database.connect() as connection:
        row = connection.execute(
            """SELECT workflow.contract_json FROM mp_execution_profile_versions profile
               JOIN workflow_versions workflow ON workflow.id=profile.workflow_version_id
               WHERE profile.id=? AND workflow.status='PUBLISHED'""",
            (profile_version_id,),
        ).fetchone()
    slots = _json_object(row["contract_json"]) if row is not None else {}
    seed = slots.get("input_slots", {}).get("SEED") if isinstance(slots.get("input_slots"), Mapping) else None
    if not isinstance(seed, Mapping) or seed.get("required", True):
        return "QUICK_CREATE_V2_CANDIDATE_SEED_SLOT_REQUIRED"
    return None


def image_to_video_profile_contract_blocker(database: Database, profile_version_id: str) -> str | None:
    """Prove a V2 Profile can consume one frozen image artifact as FIRST_FRAME."""
    with database.connect() as connection:
        row = connection.execute(
            """SELECT runtime.adapter_code,profile.payload_json,workflow.status AS workflow_status,workflow.contract_json
               FROM mp_execution_profile_versions profile
               JOIN mp_runtime_installation_versions runtime ON runtime.id=profile.runtime_installation_version_id
               LEFT JOIN workflow_versions workflow ON workflow.id=profile.workflow_version_id
               WHERE profile.id=?""",
            (profile_version_id,),
        ).fetchone()
    if row is None:
        return "QUICK_CREATE_V2_I2V_PROFILE_UNSUPPORTED"
    return _image_to_video_contract_blocker(
        adapter_code=str(row["adapter_code"]),
        workflow_status=str(row["workflow_status"] or ""),
        workflow_contract=_json_object(row["contract_json"]),
        profile_payload=_json_object(row["payload_json"]),
    )


def _direct_image_contract_blocker(
    *,
    adapter_code: str,
    workflow_status: str,
    workflow_contract: Mapping[str, Any],
    profile_payload: Mapping[str, Any],
) -> str | None:
    """Return a stable blocker without exposing workflow wiring to the page."""
    if adapter_code != "comfy.workflow.v1":
        return "QUICK_CREATE_V2_DIRECT_ADAPTER_UNSUPPORTED"
    if workflow_status != "PUBLISHED":
        return "QUICK_CREATE_V2_WORKFLOW_NOT_PUBLISHED"
    slots = workflow_contract.get("input_slots")
    if not isinstance(slots, Mapping) or "PROMPT" not in slots:
        return "QUICK_CREATE_V2_PROMPT_SLOT_REQUIRED"
    required = {
        str(name) for name, spec in slots.items()
        if isinstance(spec, Mapping) and spec.get("required", True)
    }
    if required != {"PROMPT"}:
        return "QUICK_CREATE_V2_DIRECT_INPUT_CONTRACT_UNSUPPORTED"
    binding = profile_payload.get("execution_binding")
    expected = binding.get("expected_output") if isinstance(binding, Mapping) else None
    if not isinstance(expected, Mapping) or expected.get("media_kind") != "IMAGE":
        return "QUICK_CREATE_V2_IMAGE_OUTPUT_CONTRACT_REQUIRED"
    return None


def _image_to_video_contract_blocker(
    *,
    adapter_code: str,
    workflow_status: str,
    workflow_contract: Mapping[str, Any],
    profile_payload: Mapping[str, Any],
) -> str | None:
    """Return the V2-only I2V contract result without revealing node wiring."""
    if adapter_code != "comfy.workflow.v1" or workflow_status != "PUBLISHED":
        return "QUICK_CREATE_V2_I2V_PROFILE_UNSUPPORTED"
    slots = workflow_contract.get("input_slots")
    required = {
        str(name) for name, spec in slots.items()
        if isinstance(spec, Mapping) and spec.get("required", True)
    } if isinstance(slots, Mapping) else set()
    if required != {"PROMPT", "FIRST_FRAME"}:
        return "QUICK_CREATE_V2_I2V_INPUT_CONTRACT_REQUIRED"
    binding = profile_payload.get("execution_binding")
    expected = binding.get("expected_output") if isinstance(binding, Mapping) else None
    if not isinstance(expected, Mapping) or expected.get("media_kind") != "VIDEO":
        return "QUICK_CREATE_V2_I2V_VIDEO_OUTPUT_REQUIRED"
    return None


def _json_object(value: object) -> Mapping[str, Any]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}

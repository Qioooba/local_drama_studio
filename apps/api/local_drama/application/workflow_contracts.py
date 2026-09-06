"""Resolve the versioned semantic contract used by an execution.

WorkflowVersion keeps its immutable legacy node bindings.  Once a published
Workflow App Contract is explicitly bound to a published runtime, that
contract becomes the executable semantic source for the job.  Keeping this
lookup in one application helper prevents the planner, prompt compiler and
Comfy worker from drifting apart.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any


def _object(raw: object) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def effective_workflow_contract(
    connection: Any,
    workflow_version_id: str,
    workflow_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the contract/bindings snapshot that execution must use.

    ``source=APP_CONTRACT`` is possible only for a published contract that is
    also the published-runtime binding for this WorkflowVersion.  Published
    but unbound contracts remain visible in the returned metadata so callers
    can expose an actionable blocker instead of silently selecting them.
    """

    row = workflow_row
    if row is None:
        row = connection.execute(
            "SELECT status,content_json,contract_json,node_bindings_json FROM workflow_versions WHERE id=?",
            (workflow_version_id,),
        ).fetchone()
    static_contract = _object(row["contract_json"]) if row is not None else {}
    static_bindings = _object(row["node_bindings_json"]) if row is not None else {}
    static_content = _object(row["content_json"]) if row is not None else {}

    published = connection.execute(
        """SELECT id,version_no,capability,contract_json,bindings_json,semantic_phases_json,status
        FROM workflow_app_contract_versions
        WHERE workflow_version_id=? AND status='PUBLISHED'
        ORDER BY version_no DESC,id DESC LIMIT 1""",
        (workflow_version_id,),
    ).fetchone()
    bound = connection.execute(
        """SELECT c.id,c.version_no,c.capability,c.contract_json,c.bindings_json,c.semantic_phases_json,
        r.id AS runtime_environment_version_id
        FROM workflow_runtime_bindings b
        JOIN workflow_app_contract_versions c ON c.id=b.contract_version_id
        JOIN runtime_environment_versions r ON r.id=b.runtime_environment_version_id
        WHERE b.workflow_version_id=? AND c.status='PUBLISHED' AND r.status='PUBLISHED'
        ORDER BY c.version_no DESC,c.id DESC LIMIT 1""",
        (workflow_version_id,),
    ).fetchone()
    selected = bound
    source = "APP_CONTRACT" if selected is not None else "WORKFLOW_VERSION"
    if selected is None:
        capability = str(static_contract.get("capability") or "")
        contract = static_contract
        bindings = static_bindings
        semantic_phases: list[dict[str, Any]] = []
        contract_id = None
        runtime_environment_version_id = None
    else:
        capability = str(selected["capability"] or "")
        contract = _object(selected["contract_json"])
        bindings = _object(selected["bindings_json"])
        semantic_phases = _object_list(selected["semantic_phases_json"])
        contract_id = str(selected["id"])
        runtime_environment_version_id = str(selected["runtime_environment_version_id"])
    published_capability = str(published["capability"] or "") if published is not None else None
    return {
        "workflow_version_id": workflow_version_id,
        "source": source,
        "workflow_status": str(row["status"]) if row is not None else None,
        "workflow_content": static_content,
        "workflow_version_contract": static_contract,
        "workflow_version_bindings": static_bindings,
        "workflow_contract": contract,
        "workflow_bindings": bindings,
        "semantic_phases": semantic_phases,
        "capability": capability,
        "contract_id": contract_id,
        "runtime_environment_version_id": runtime_environment_version_id,
        "published_contract_id": str(published["id"]) if published is not None else None,
        "published_contract_capability": published_capability,
        "published_contract_bound": bound is not None,
    }


def latest_bound_profile_for_capability(
    connection: Any,
    *,
    route_capability: str,
    profile_capabilities: tuple[str, ...] = ("IMAGE_CONCEPT", "IMAGE_CHARACTER", "IMAGE_SCENE"),
    identity_reference_count: int | None = None,
) -> dict[str, Any] | None:
    """Resolve the newest executable profile for a bound workflow route.

    A generation preference's ``AUTO`` choice is intentionally profile-centric
    and cannot see a later Workflow App Contract binding.  Shot keyframes are
    different: the executable route is the published workflow + published
    contract + published runtime tuple.  Keep this lookup next to
    ``effective_workflow_contract`` so the planner and worker share the same
    binding vocabulary.  The returned profile is still frozen into the job;
    execution subsequently resolves its contract by that profile's workflow
    version.

    ``None`` is a compatibility fallback for compact legacy test schemas.  It
    must never cause a caller to invent a contract; callers continue through
    their normal fail-closed profile/readiness checks.
    """

    normalized_route = str(route_capability or "").strip().upper()
    normalized_profiles = tuple(
        str(capability or "").strip().upper()
        for capability in profile_capabilities
        if str(capability or "").strip()
    )
    if not normalized_route or not normalized_profiles:
        return None
    placeholders = ",".join("?" for _ in normalized_profiles)
    try:
        rows = connection.execute(
            f"""SELECT p.*,c.bindings_json AS app_bindings_json,c.contract_json AS app_contract_json
            FROM execution_profile_versions p
            JOIN workflow_versions w ON w.id=p.workflow_version_id
            JOIN workflow_runtime_bindings b ON b.workflow_version_id=w.id
            JOIN workflow_app_contract_versions c ON c.id=b.contract_version_id
            JOIN runtime_environment_versions r ON r.id=b.runtime_environment_version_id
            WHERE p.status='PUBLISHED'
              AND UPPER(p.capability) IN ({placeholders})
              AND w.status='PUBLISHED'
              AND c.status='PUBLISHED'
              AND UPPER(c.capability)=?
              AND r.status='PUBLISHED'
            ORDER BY
              b.created_at DESC,
              c.version_no DESC,
              c.updated_at DESC,
              CASE WHEN UPPER(p.capability)=? THEN 0 ELSE 1 END,
              p.updated_at DESC,
              p.id DESC""",
            (*normalized_profiles, normalized_route, normalized_profiles[0]),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    for row in rows:
        if identity_reference_count is not None:
            bindings = json.loads(str(row["app_bindings_json"] or "{}"))
            app_inputs = json.loads(str(row["app_contract_json"] or "{}")).get("inputs", {})
            profile_inputs = json.loads(str(row["input_contract_json"] or "{}")).get("input_slots", {})
            roles = [role for role in ("REFERENCE_IMAGE_1", "REFERENCE_IMAGE_2", "REFERENCE_IMAGE_3") if role in bindings]
            if not roles and "REFERENCE_IMAGE" in bindings:
                roles = ["REFERENCE_IMAGE"]
            selected = set(roles[:identity_reference_count])
            required = {role for role in roles if app_inputs.get(role, {}).get("required", True) or int(profile_inputs.get(role, {}).get("min", 0)) > 0}
            if len(selected) != identity_reference_count or not required.issubset(selected) or not selected.issubset(profile_inputs):
                continue
        return dict(row)
    return None


def _object_list(raw: object) -> list[dict[str, Any]]:
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []

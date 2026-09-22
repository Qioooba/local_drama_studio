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

#: Effective semantic bindings that let the runtime own an image route's scale.
#: A route that binds all three can be executed at any production size/step
#: count, so its baked graph defaults are only authoring placeholders.
PRODUCTION_IMAGE_SCALE_ROLES: tuple[str, ...] = ("STEPS", "WIDTH", "HEIGHT")

#: Baked graph values at or below these limits are verification/smoke scale.
SMOKE_SAMPLER_STEP_LIMIT = 8
SMOKE_LATENT_EDGE_LIMIT = 512

#: Baked graph values that are self-evidently production scale even when the
#: route cannot be re-parameterised.
PRODUCTION_SAMPLER_STEP_FLOOR = 12
PRODUCTION_LATENT_EDGE_FLOOR = 768


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


def _numeric(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def image_workflow_scale_facts(workflow_content: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract the checkable render-scale facts from a frozen image graph.

    Only immutable graph content is inspected: the sampler step count and the
    latent size a text-to-image graph starts from.  A verification smoke graph
    is exactly the pair of a tiny latent and a single sampler step.
    """

    steps: float | None = None
    edges: list[float] = []
    content = workflow_content if isinstance(workflow_content, Mapping) else {}
    for node in content.values():
        if not isinstance(node, Mapping):
            continue
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        if "Sampler" in class_type:
            node_steps = _numeric(inputs.get("steps"))
            if node_steps is not None:
                steps = node_steps if steps is None else max(steps, node_steps)
        if "Latent" in class_type:
            for axis in ("width", "height"):
                edge = _numeric(inputs.get(axis))
                if edge is not None:
                    edges.append(edge)
    return {"sampler_steps": steps, "latent_edges": edges}


def image_workflow_production_scale(
    connection: Any,
    workflow_version_id: str | None,
    workflow_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the production-scale verdict for a bound image workflow route.

    The verdict is derived from checkable facts only - the published status of
    the bound workflow, its effective semantic bindings and the scale baked into
    the frozen graph - never from the workflow code string.  A route named
    ``...-smoke`` that binds STEPS/WIDTH/HEIGHT and renders 848x480 for 20 steps
    is usable; a route that hard-codes a 256x256 / 1-step verification graph is
    not, whatever it is called.
    """

    normalized = str(workflow_version_id or "").strip()
    report: dict[str, Any] = {
        "workflow_version_id": normalized or None,
        "production_grade": False,
        "reason": "WORKFLOW_UNAVAILABLE",
        "bound_scale_roles": [],
        "missing_scale_roles": list(PRODUCTION_IMAGE_SCALE_ROLES),
        "sampler_steps": None,
        "latent_max_edge": None,
    }
    if not normalized:
        return report
    try:
        contract = effective_workflow_contract(connection, normalized, workflow_row)
    except sqlite3.OperationalError:
        return report
    if str(contract.get("workflow_status") or "") != "PUBLISHED":
        return report
    bindings = contract.get("workflow_bindings")
    bound_roles = set(bindings) if isinstance(bindings, Mapping) else set()
    report["bound_scale_roles"] = sorted(bound_roles.intersection(PRODUCTION_IMAGE_SCALE_ROLES))
    report["missing_scale_roles"] = sorted(set(PRODUCTION_IMAGE_SCALE_ROLES) - bound_roles)
    facts = image_workflow_scale_facts(contract.get("workflow_content"))
    steps = facts["sampler_steps"]
    edges = facts["latent_edges"]
    report["sampler_steps"] = int(steps) if steps is not None else None
    report["latent_max_edge"] = int(max(edges)) if edges else None
    if not report["missing_scale_roles"]:
        # The runtime owns step count and output size; the baked values are
        # authoring placeholders (a 4-step SDXL Turbo preset is still real).
        report["production_grade"] = True
        report["reason"] = "BOUND_PRODUCTION_SCALE"
        return report
    if steps is None:
        report["reason"] = "SCALE_EVIDENCE_MISSING"
        return report
    if steps <= SMOKE_SAMPLER_STEP_LIMIT and (not edges or max(edges) <= SMOKE_LATENT_EDGE_LIMIT):
        report["reason"] = "SMOKE_SCALE"
        return report
    if steps >= PRODUCTION_SAMPLER_STEP_FLOOR or (bool(edges) and max(edges) >= PRODUCTION_LATENT_EDGE_FLOOR):
        report["production_grade"] = True
        report["reason"] = "GRAPH_PRODUCTION_SCALE"
    return report


def image_workflow_is_production_grade(
    connection: Any,
    workflow_version_id: str | None,
    workflow_row: Mapping[str, Any] | None = None,
) -> bool:
    """Whether a published image route can render at production scale.

    Kept as the single decision used by both profile auto-selection and
    explicit profile selection so the two can never drift apart again.
    """

    return bool(image_workflow_production_scale(connection, workflow_version_id, workflow_row)["production_grade"])


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

"""Shared readiness transitions for capability-scoped model validation."""

from __future__ import annotations

import sqlite3


_TEXT_CAPABILITIES = frozenset(
    {
        "LLM_STORY_PARSE",
        "LLM_EPISODE_PLAN",
        "LLM_STORYBOARD",
        "LLM_PROMPT_REWRITE",
    }
)


def supports_installed_smoke(runtime_kind: str, capability_code: str) -> bool:
    """Return whether this build has a real smoke implementation for the pair.

    Native discovery may expose more capabilities than the current application
    can validate. Those forward-looking offerings remain visible and fail
    closed at the capability level, but they must not deadlock unrelated,
    validated capabilities on the same model installation.
    """

    normalized_kind = runtime_kind.strip().upper()
    normalized_capability = capability_code.strip().upper()
    if normalized_kind in {"OLLAMA", "LLAMA_CPP_MANAGED"}:
        return normalized_capability in _TEXT_CAPABILITIES
    if normalized_kind == "PYTORCH_PROCESS":
        return normalized_capability == "EMBEDDING_TEXT"
    if normalized_kind == "COMFYUI":
        return True
    return False


def reconcile_offering_readiness(
    connection: sqlite3.Connection,
    *,
    runtime_model_installation_id: str,
    runtime_installation_version_id: str,
    runtime_kind: str,
    latest_status: str,
    updated_at: str,
) -> tuple[bool, bool]:
    """Project capability smoke results onto installation/runtime state.

    Only capabilities with an installed validator participate in the aggregate.
    An advertised-but-unimplemented capability stays blocked in its own row
    without preventing a fully validated capability from receiving a Profile.
    """

    installation_rows = connection.execute(
        """SELECT capability.code,offering.validation_status
           FROM mp_capability_offerings offering
           JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
           WHERE offering.runtime_model_installation_id=?""",
        (runtime_model_installation_id,),
    ).fetchall()
    supported_installation_rows = [
        row for row in installation_rows
        if supports_installed_smoke(runtime_kind, str(row["code"]))
    ]
    installation_ready = bool(supported_installation_rows) and all(
        str(row["validation_status"]) == "SMOKE_PASSED" for row in supported_installation_rows
    )
    if installation_ready:
        connection.execute(
            "UPDATE mp_runtime_model_installations SET install_state='READY',updated_at=? WHERE id=?",
            (updated_at, runtime_model_installation_id),
        )
    elif latest_status == "FAILED":
        connection.execute(
            "UPDATE mp_runtime_model_installations SET install_state='VALIDATION_FAILED',updated_at=? WHERE id=?",
            (updated_at, runtime_model_installation_id),
        )

    runtime_rows = connection.execute(
        """SELECT capability.code,offering.validation_status
           FROM mp_capability_offerings offering
           JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
           JOIN mp_runtime_model_installations installation ON installation.id=offering.runtime_model_installation_id
           WHERE installation.runtime_installation_version_id=?""",
        (runtime_installation_version_id,),
    ).fetchall()
    supported_runtime_rows = [
        row for row in runtime_rows
        if supports_installed_smoke(runtime_kind, str(row["code"]))
    ]
    runtime_active = bool(supported_runtime_rows) and all(
        str(row["validation_status"]) == "SMOKE_PASSED" for row in supported_runtime_rows
    )
    if runtime_active:
        connection.execute(
            "UPDATE mp_runtime_installation_versions SET status='ACTIVE',updated_at=? WHERE id=?",
            (updated_at, runtime_installation_version_id),
        )
    elif latest_status == "FAILED":
        connection.execute(
            "UPDATE mp_runtime_installation_versions SET status='DEGRADED',updated_at=? WHERE id=?",
            (updated_at, runtime_installation_version_id),
        )
    return installation_ready, runtime_active

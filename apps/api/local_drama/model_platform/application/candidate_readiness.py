"""Read-safe, fail-closed readiness projection for registered V2 candidates."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.offering_readiness import supports_installed_smoke


@dataclass(frozen=True, slots=True)
class CandidateCapabilityReadiness:
    code: str
    title: str
    offering_validation_status: str
    profile_version_count: int
    published_profile_count: int
    workflow_binding_count: int
    workflow_schema_validated_count: int
    readiness_status: str
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegisteredCandidateReadiness:
    runtime_model_installation_id: str
    model_release_id: str
    model_release_code: str
    model_title: str
    runtime_kind: str
    install_state: str
    integrity_status: str
    readiness_status: str
    assignable_capability_count: int
    blockers: tuple[str, ...]
    capabilities: tuple[CandidateCapabilityReadiness, ...]


class CandidateReadinessService:
    """Projects only registered V2 candidates; discovery evidence stays separate.

    A candidate is never treated as executable merely because it was found on
    disk or in Ollama.  Every capability needs a published Profile, and the
    concrete installation must be marked READY by a future verification
    workflow.  This makes the control-plane UI useful without leaking library
    roots, endpoints, or native runtime locators.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self, *, limit: int = 100) -> tuple[RegisteredCandidateReadiness, ...]:
        with self.database.connect() as connection:
            installations = connection.execute(
                """SELECT installation.id,release.id AS release_id,release.code AS release_code,
                          family.title AS model_title,runtime.kind AS runtime_kind,
                          installation.install_state,installation.runtime_installation_version_id
                   FROM mp_runtime_model_installations installation
                   JOIN mp_model_releases release ON release.id=installation.release_id
                   JOIN mp_model_families family ON family.id=release.family_id
                   JOIN mp_runtime_installation_versions runtime_version
                     ON runtime_version.id=installation.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime ON runtime.id=runtime_version.runtime_installation_id
                   ORDER BY installation.created_at DESC,installation.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return tuple(self._read_installation(connection, row) for row in installations)

    def _read_installation(self, connection: sqlite3.Connection, installation: sqlite3.Row) -> RegisteredCandidateReadiness:
        offerings = connection.execute(
            """SELECT capability.code,capability.title,offering.validation_status,
                      COUNT(DISTINCT profile.id) AS profile_version_count,
                      COUNT(DISTINCT CASE WHEN publication.status='PUBLISHED' THEN profile.id END)
                        AS published_profile_count,
                      COUNT(DISTINCT workflow_binding.id) AS workflow_binding_count,
                      COUNT(DISTINCT CASE WHEN workflow_binding.binding_status='SCHEMA_VALIDATED' THEN workflow_binding.id END)
                        AS workflow_schema_validated_count
               FROM mp_capability_offerings offering
               JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
               LEFT JOIN mp_execution_profile_versions profile
                 ON profile.runtime_installation_version_id=?
                AND profile.capability_definition_id=offering.capability_definition_id
               LEFT JOIN mp_profile_publications publication
                 ON publication.execution_profile_version_id=profile.id
               LEFT JOIN mp_runtime_model_workflow_bindings workflow_binding
                 ON workflow_binding.runtime_model_installation_id=offering.runtime_model_installation_id
                AND workflow_binding.capability_definition_id=offering.capability_definition_id
               WHERE offering.runtime_model_installation_id=?
               GROUP BY capability.id,capability.code,capability.title,offering.validation_status
               ORDER BY capability.family,capability.code""",
            (installation["runtime_installation_version_id"], installation["id"]),
        ).fetchall()
        integrity = connection.execute(
            """SELECT status FROM mp_validation_runs
               WHERE target_kind='RUNTIME_MODEL_INSTALLATION' AND target_id=? AND validation_kind='INSTALLATION_INTEGRITY'
               ORDER BY finished_at DESC,created_at DESC,id DESC LIMIT 1""",
            (installation["id"],),
        ).fetchone()
        capabilities = tuple(
            _capability_readiness(
                code=str(row["code"]),
                title=str(row["title"]),
                offering_validation_status=str(row["validation_status"]),
                profile_version_count=int(row["profile_version_count"]),
                published_profile_count=int(row["published_profile_count"]),
                workflow_binding_count=int(row["workflow_binding_count"]),
                workflow_schema_validated_count=int(row["workflow_schema_validated_count"]),
                installation_ready=str(installation["install_state"]) == "READY",
            )
            for row in offerings
        )
        managed_capabilities = tuple(
            item for item in capabilities
            if supports_installed_smoke(str(installation["runtime_kind"]), item.code)
        )
        aggregate_capabilities = managed_capabilities or capabilities
        blockers = _dedupe(blocker for item in aggregate_capabilities for blocker in item.blockers)
        if not capabilities:
            blockers = ("CAPABILITY_MAPPING_REQUIRED",)
        assignable_count = sum(item.readiness_status == "ASSIGNABLE" for item in capabilities)
        readiness_status = _candidate_status(aggregate_capabilities, blockers)
        return RegisteredCandidateReadiness(
            runtime_model_installation_id=str(installation["id"]),
            model_release_id=str(installation["release_id"]),
            model_release_code=str(installation["release_code"]),
            model_title=str(installation["model_title"]),
            runtime_kind=str(installation["runtime_kind"]),
            install_state=str(installation["install_state"]),
            integrity_status=_integrity_status(integrity["status"] if integrity is not None else None),
            readiness_status=readiness_status,
            assignable_capability_count=assignable_count,
            blockers=blockers,
            capabilities=capabilities,
        )


def _capability_readiness(
    *,
    code: str,
    title: str,
    offering_validation_status: str,
    profile_version_count: int,
    published_profile_count: int,
    workflow_binding_count: int,
    workflow_schema_validated_count: int,
    installation_ready: bool,
) -> CandidateCapabilityReadiness:
    status: str
    blockers: tuple[str, ...]
    if published_profile_count and installation_ready:
        status, blockers = "ASSIGNABLE", ()
    elif published_profile_count:
        status, blockers = "INSTALLATION_VERIFICATION_REQUIRED", ("INSTALLATION_NOT_READY",)
    elif profile_version_count:
        status, blockers = "PROFILE_PUBLICATION_REQUIRED", ("PROFILE_SMOKE_AND_PUBLICATION_REQUIRED",)
    elif offering_validation_status != "SMOKE_PASSED":
        status, blockers = "VALIDATION_REQUIRED", ("CAPABILITY_SMOKE_NOT_PASSED", "PROFILE_REQUIRED")
    else:
        status, blockers = "PROFILE_REQUIRED", ("PROFILE_REQUIRED",)
    return CandidateCapabilityReadiness(
        code=code,
        title=title,
        offering_validation_status=offering_validation_status,
        profile_version_count=profile_version_count,
        published_profile_count=published_profile_count,
        workflow_binding_count=workflow_binding_count,
        workflow_schema_validated_count=workflow_schema_validated_count,
        readiness_status=status,
        blockers=blockers,
    )


def _candidate_status(capabilities: tuple[CandidateCapabilityReadiness, ...], blockers: tuple[str, ...]) -> str:
    if not capabilities:
        return "CAPABILITY_MAPPING_REQUIRED"
    ready = sum(item.readiness_status == "ASSIGNABLE" for item in capabilities)
    if ready == len(capabilities):
        return "ASSIGNABLE"
    if ready:
        return "PARTIALLY_ASSIGNABLE"
    if "INSTALLATION_NOT_READY" in blockers:
        return "INSTALLATION_VERIFICATION_REQUIRED"
    if "PROFILE_SMOKE_AND_PUBLICATION_REQUIRED" in blockers:
        return "PROFILE_PUBLICATION_REQUIRED"
    if "PROFILE_REQUIRED" in blockers and "CAPABILITY_SMOKE_NOT_PASSED" not in blockers:
        return "PROFILE_REQUIRED"
    return "VALIDATION_REQUIRED"


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _integrity_status(value: object) -> str:
    if value == "INTEGRITY_PASSED":
        return "PASSED"
    if value == "FAILED":
        return "FAILED"
    return "NOT_RUN"

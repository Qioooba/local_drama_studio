"""Read-safe V2 Profile lifecycle projection for recovery after a page reload."""

from __future__ import annotations

import json
from dataclasses import dataclass

from local_drama.infrastructure.database.sqlite import Database


@dataclass(frozen=True, slots=True)
class ProfileLifecycleItem:
    profile_version_id: str
    profile_code: str
    profile_title: str
    version_no: int
    capability_code: str
    runtime_model_installation_ids: tuple[str, ...]
    lifecycle_status: str
    latest_validation_run_id: str | None
    latest_validation_status: str | None


class ProfileCatalogService:
    """Projects immutable V2 Profile state without runtime wiring or evidence payloads.

    A Profile draft must be recoverable after navigation or a browser refresh.
    The catalog deliberately exposes only IDs needed for the next declared
    lifecycle action; it never returns native locators, paths, endpoints or
    validation evidence.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def list(
        self,
        *,
        runtime_model_installation_id: str | None = None,
        capability_code: str | None = None,
        limit: int = 200,
    ) -> tuple[ProfileLifecycleItem, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT version.id AS profile_version_id,profile.code AS profile_code,profile.title AS profile_title,
                          version.version_no,capability.code AS capability_code,version.payload_json,
                          publication.status AS publication_status,
                          validation.id AS validation_run_id,validation.status AS validation_status
                   FROM mp_execution_profile_versions version
                   JOIN mp_execution_profiles profile ON profile.id=version.profile_id
                   JOIN mp_capability_definitions capability ON capability.id=version.capability_definition_id
                   LEFT JOIN mp_profile_publications publication ON publication.execution_profile_version_id=version.id
                   LEFT JOIN mp_validation_runs validation ON validation.id=(
                     SELECT candidate.id FROM mp_validation_runs candidate
                     WHERE candidate.target_kind='EXECUTION_PROFILE_VERSION' AND candidate.target_id=version.id
                     ORDER BY candidate.finished_at DESC,candidate.created_at DESC,candidate.id DESC LIMIT 1
                   )
                   ORDER BY version.created_at DESC,version.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        wanted_capability = capability_code.strip().upper() if capability_code else None
        items: list[ProfileLifecycleItem] = []
        for row in rows:
            installation_ids = _installation_ids(row["payload_json"])
            if runtime_model_installation_id and runtime_model_installation_id not in installation_ids:
                continue
            if wanted_capability and str(row["capability_code"]) != wanted_capability:
                continue
            validation_status = str(row["validation_status"]) if row["validation_status"] is not None else None
            publication_status = str(row["publication_status"]) if row["publication_status"] is not None else None
            items.append(
                ProfileLifecycleItem(
                    profile_version_id=str(row["profile_version_id"]),
                    profile_code=str(row["profile_code"]),
                    profile_title=str(row["profile_title"]),
                    version_no=int(row["version_no"]),
                    capability_code=str(row["capability_code"]),
                    runtime_model_installation_ids=installation_ids,
                    lifecycle_status=_lifecycle_status(publication_status, validation_status),
                    latest_validation_run_id=str(row["validation_run_id"]) if row["validation_run_id"] is not None else None,
                    latest_validation_status=validation_status,
                )
            )
        return tuple(items)


def _installation_ids(payload_json: object) -> tuple[str, ...]:
    try:
        payload = json.loads(str(payload_json))
    except (TypeError, ValueError):
        return ()
    raw_ids = payload.get("runtime_model_installation_ids") if isinstance(payload, dict) else None
    if not isinstance(raw_ids, list):
        return ()
    return tuple(dict.fromkeys(item.strip() for item in raw_ids if isinstance(item, str) and item.strip()))


def _lifecycle_status(publication_status: str | None, validation_status: str | None) -> str:
    if publication_status == "PUBLISHED":
        return "PUBLISHED"
    if validation_status == "SMOKE_PASSED":
        return "PROFILE_SMOKE_PASSED"
    if validation_status == "FAILED":
        return "PROFILE_SMOKE_FAILED"
    return "DRAFT"

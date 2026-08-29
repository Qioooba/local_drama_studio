"""Persist immutable discovery runs without changing catalog or publication state."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.runtime_adapters import DiscoveryReport, RuntimeAdapter
from local_drama.model_platform.domain.states import RuntimeStatus


@dataclass(frozen=True, slots=True)
class PersistedDiscoveryRun:
    id: str
    status: str
    observation_count: int


class DiscoveryService:
    """The only V2 writer for adapter discovery evidence.

    This service intentionally writes only ``mp_discovery_*``.  A later review
    and validation flow promotes observations into model releases, runtime model
    installations, offerings, and immutable Profiles.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def discover_and_record(
        self,
        adapter: RuntimeAdapter,
        *,
        runtime_installation_version_id: str,
        source: str,
    ) -> PersistedDiscoveryRun:
        report = adapter.discover(
            known_native_digests=self.latest_native_digests(runtime_installation_version_id)
        )
        return self.record(
            report,
            runtime_installation_version_id=runtime_installation_version_id,
            source=source,
        )

    def latest_native_digests(self, runtime_installation_version_id: str) -> dict[str, str]:
        """Read the newest successful observation for each native locator."""

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT observation.native_id, observation.observed_json
                FROM mp_discovery_observations observation
                JOIN mp_discovery_runs run ON run.id = observation.discovery_run_id
                WHERE run.runtime_installation_version_id=? AND run.status='SUCCEEDED'
                ORDER BY run.finished_at DESC, run.created_at DESC
                """,
                (runtime_installation_version_id,),
            )
            digests: dict[str, str] = {}
            for row in rows:
                native_id = str(row["native_id"])
                if native_id in digests:
                    continue
                try:
                    payload = json.loads(str(row["observed_json"]))
                except (TypeError, ValueError):
                    continue
                digest = payload.get("digest") if isinstance(payload, dict) else None
                if isinstance(digest, str) and digest.strip():
                    digests[native_id] = digest.strip()
            return digests

    def record(
        self,
        report: DiscoveryReport,
        *,
        runtime_installation_version_id: str,
        source: str,
    ) -> PersistedDiscoveryRun:
        now = _utc_now()
        run_id = str(uuid.uuid4())
        status = "SUCCEEDED" if report.runtime_status is not RuntimeStatus.UNREACHABLE else "FAILED"
        summary = {
            "runtime_kind": report.runtime_kind.value,
            "runtime_status": report.runtime_status.value,
            "observation_count": len(report.observations),
            "evidence": [_evidence_json(item.code, item.message, item.details) for item in report.evidence],
            "read_only_discovery": report.read_only,
        }
        with self.database.transaction() as connection:
            _assert_runtime_version_exists(connection, runtime_installation_version_id)
            connection.execute(
                """INSERT INTO mp_discovery_runs
                (id,library_id,runtime_installation_version_id,source,status,summary_json,started_at,finished_at,created_at,updated_at)
                VALUES (?,NULL,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    runtime_installation_version_id,
                    source,
                    status,
                    _json(summary),
                    now,
                    now,
                    now,
                    now,
                ),
            )
            for observation in report.observations:
                connection.execute(
                    """INSERT INTO mp_discovery_observations
                    (id,discovery_run_id,native_id,kind,observed_json,content_hint,status,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()),
                        run_id,
                        observation.native_locator,
                        observation.runtime_kind.value,
                        _json(
                            {
                                "digest": observation.digest,
                                "size_bytes": observation.size_bytes,
                                "modified_at": observation.modified_at,
                                "presence": observation.presence.value,
                                "metadata": dict(observation.metadata),
                                "candidate_capabilities": [
                                    {"capability": candidate.capability, "reason": candidate.reason}
                                    for candidate in observation.candidate_capabilities
                                ],
                                "detail_refreshed": observation.detail_refreshed,
                            }
                        ),
                        observation.digest,
                        observation.presence.value,
                        now,
                        now,
                    ),
                )
        return PersistedDiscoveryRun(run_id, status, len(report.observations))


def _assert_runtime_version_exists(connection: sqlite3.Connection, runtime_installation_version_id: str) -> None:
    exists = connection.execute(
        "SELECT 1 FROM mp_runtime_installation_versions WHERE id=?", (runtime_installation_version_id,)
    ).fetchone()
    if exists is None:
        raise ValueError(f"Unknown runtime installation version: {runtime_installation_version_id}")


def _evidence_json(code: str, message: str, details: Mapping[str, object]) -> dict[str, object]:
    return {"code": code, "message": message, "details": dict(details)}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

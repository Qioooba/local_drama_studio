"""Read-only inventory of the pre-V2 model configuration tables.

The V2 importer must not infer a production-ready model from a legacy row.  It
first consumes this report, where every lossy or ambiguous value is explicit as
an audit warning.  Keeping the reader write-free also makes migration dry runs
safe against the database used by a running Windows deployment.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterator

from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.domain.capabilities import normalize_capability


@dataclass(frozen=True, slots=True)
class LegacyInventoryWarning:
    code: str
    source_table: str
    source_id: str | None
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class LegacyRuntimeObservation:
    source_table: str
    id: str
    code: str
    title: str
    status: str
    transport: str | None
    executable_ref: str | None
    runtime_version: str | None
    fingerprint: str | None


@dataclass(frozen=True, slots=True)
class LegacyArtifactObservation:
    id: str
    code: str
    runtime_id: str | None
    kind: str
    machine_path_ref: str
    status: str
    sha256: str | None
    size_bytes: int | None
    manifest_sha256: str | None


@dataclass(frozen=True, slots=True)
class LegacyProfileObservation:
    profile_id: str
    profile_code: str
    profile_title: str
    profile_version_id: str
    version_no: int
    status: str
    capability_raw: str
    capability: str | None
    runtime_reference: str | None
    artifact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyModelPlatformInventory:
    """The sole input accepted by the later one-time V2 importer."""

    local_runtimes: tuple[LegacyRuntimeObservation, ...]
    runtime_environment_versions: tuple[LegacyRuntimeObservation, ...]
    artifacts: tuple[LegacyArtifactObservation, ...]
    profile_versions: tuple[LegacyProfileObservation, ...]
    warnings: tuple[LegacyInventoryWarning, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "local_runtimes": len(self.local_runtimes),
            "runtime_environment_versions": len(self.runtime_environment_versions),
            "artifacts": len(self.artifacts),
            "profile_versions": len(self.profile_versions),
            "warnings": len(self.warnings),
        }


class LegacyModelPlatformInventoryReader:
    """Build an auditable snapshot without mutating either legacy or V2 data."""

    def read(self, database: Database) -> LegacyModelPlatformInventory:
        connection = database.connect()
        try:
            return self.read_connection(connection)
        finally:
            connection.close()

    def read_connection(self, connection: sqlite3.Connection) -> LegacyModelPlatformInventory:
        warnings: list[LegacyInventoryWarning] = []
        local_runtimes = tuple(self._read_local_runtimes(connection, warnings))
        environment_versions = tuple(self._read_runtime_environment_versions(connection, warnings))
        artifacts = tuple(self._read_artifacts(connection, warnings))
        profiles = tuple(self._read_profile_versions(connection, warnings))
        return LegacyModelPlatformInventory(
            local_runtimes=local_runtimes,
            runtime_environment_versions=environment_versions,
            artifacts=artifacts,
            profile_versions=profiles,
            warnings=tuple(warnings),
        )

    def _read_local_runtimes(
        self, connection: sqlite3.Connection, warnings: list[LegacyInventoryWarning]
    ) -> Iterator[LegacyRuntimeObservation]:
        if not _table_exists(connection, "local_runtimes"):
            return
        for row in connection.execute("SELECT * FROM local_runtimes ORDER BY code, id"):
            data = dict(row)
            _parse_json(data.get("details_json"), "local_runtimes", data.get("id"), "details_json", warnings)
            yield LegacyRuntimeObservation(
                source_table="local_runtimes",
                id=str(data["id"]),
                code=str(data["code"]),
                title=str(data["title"]),
                status=str(data["status"]),
                transport=_optional_text(data.get("transport")),
                executable_ref=_optional_text(data.get("executable_ref")),
                runtime_version=_optional_text(data.get("runtime_version")),
                fingerprint=None,
            )

    def _read_runtime_environment_versions(
        self, connection: sqlite3.Connection, warnings: list[LegacyInventoryWarning]
    ) -> Iterator[LegacyRuntimeObservation]:
        if not (_table_exists(connection, "runtime_environments") and _table_exists(connection, "runtime_environment_versions")):
            return
        query = """
            SELECT rev.*, env.code AS environment_code, env.title AS environment_title
            FROM runtime_environment_versions rev
            JOIN runtime_environments env ON env.id = rev.runtime_environment_id
            ORDER BY env.code, rev.version_no, rev.id
        """
        for row in connection.execute(query):
            data = dict(row)
            _parse_json(data.get("manifest_json"), "runtime_environment_versions", data.get("id"), "manifest_json", warnings)
            _parse_json(data.get("validation_json"), "runtime_environment_versions", data.get("id"), "validation_json", warnings)
            yield LegacyRuntimeObservation(
                source_table="runtime_environment_versions",
                id=str(data["id"]),
                code=f"{data['environment_code']}@{data['version_no']}",
                title=str(data["environment_title"]),
                status=str(data["status"]),
                transport=None,
                executable_ref=None,
                runtime_version=str(data["version_no"]),
                fingerprint=_optional_text(data.get("environment_fingerprint")),
            )

    def _read_artifacts(
        self, connection: sqlite3.Connection, warnings: list[LegacyInventoryWarning]
    ) -> Iterator[LegacyArtifactObservation]:
        if not _table_exists(connection, "model_artifacts"):
            return
        for row in connection.execute("SELECT * FROM model_artifacts ORDER BY code, id"):
            data = dict(row)
            _parse_json(data.get("compatibility_json"), "model_artifacts", data.get("id"), "compatibility_json", warnings)
            size_value = data.get("size_bytes")
            if size_value is not None and not isinstance(size_value, int):
                warnings.append(
                    LegacyInventoryWarning(
                        "LEGACY_ARTIFACT_SIZE_INVALID", "model_artifacts", str(data["id"]), "size_bytes",
                        "模型大小不是整数；V2 导入前需人工确认。",
                    )
                )
            yield LegacyArtifactObservation(
                id=str(data["id"]),
                code=str(data["code"]),
                runtime_id=_optional_text(data.get("runtime_id")),
                kind=str(data["kind"]),
                machine_path_ref=str(data["machine_path_ref"]),
                status=str(data["status"]),
                sha256=_optional_text(data.get("sha256")),
                size_bytes=size_value if isinstance(size_value, int) else None,
                manifest_sha256=_optional_text(data.get("manifest_sha256")),
            )

    def _read_profile_versions(
        self, connection: sqlite3.Connection, warnings: list[LegacyInventoryWarning]
    ) -> Iterator[LegacyProfileObservation]:
        if not (_table_exists(connection, "execution_profiles") and _table_exists(connection, "execution_profile_versions")):
            return
        query = """
            SELECT version.*, profile.code AS profile_code, profile.title AS profile_title
            FROM execution_profile_versions version
            JOIN execution_profiles profile ON profile.id = version.execution_profile_id
            ORDER BY profile.code, version.version_no, version.id
        """
        for row in connection.execute(query):
            data = dict(row)
            source_id = str(data["id"])
            raw_capability = str(data.get("capability") or "")
            try:
                capability = normalize_capability(raw_capability)
            except ValueError:
                capability = None
                warnings.append(
                    LegacyInventoryWarning(
                        "LEGACY_PROFILE_CAPABILITY_UNKNOWN", "execution_profile_versions", source_id, "capability",
                        f"无法无损映射 legacy capability '{raw_capability}'；该 Profile 不会自动发布到 V2。",
                    )
                )
            bundle = _parse_json(data.get("model_bundle_json"), "execution_profile_versions", source_id, "model_bundle_json", warnings)
            artifact_ids = tuple(_artifact_ids_from_bundle(bundle)) if bundle is not None else ()
            runtime_reference = _optional_text(data.get("runtime_version_id"))
            if isinstance(bundle, dict):
                runtime_reference = _optional_text(bundle.get("runtime_id")) or runtime_reference
            yield LegacyProfileObservation(
                profile_id=str(data["execution_profile_id"]),
                profile_code=str(data["profile_code"]),
                profile_title=str(data["profile_title"]),
                profile_version_id=source_id,
                version_no=int(data["version_no"]),
                status=str(data["status"]),
                capability_raw=raw_capability,
                capability=capability,
                runtime_reference=runtime_reference,
                artifact_ids=artifact_ids,
            )


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_json(
    raw_value: Any,
    source_table: str,
    source_id: str | None,
    field: str,
    warnings: list[LegacyInventoryWarning],
) -> Any | None:
    if raw_value is None:
        return None
    try:
        return json.loads(str(raw_value))
    except (TypeError, ValueError):
        warnings.append(
            LegacyInventoryWarning(
                "LEGACY_JSON_INVALID", source_table, _optional_text(source_id), field,
                "JSON 无法解析；V2 导入不会猜测其语义。",
            )
        )
        return None


def _artifact_ids_from_bundle(bundle: Any) -> Iterator[str]:
    """Extract only explicit artifact references, never arbitrary string values."""

    if isinstance(bundle, dict):
        artifact_id = bundle.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id.strip():
            yield artifact_id.strip()
        artifact_ids = bundle.get("artifact_ids")
        if isinstance(artifact_ids, list):
            for item in artifact_ids:
                if isinstance(item, str) and item.strip():
                    yield item.strip()
        for value in bundle.values():
            yield from _artifact_ids_from_bundle(value)
    elif isinstance(bundle, list):
        for item in bundle:
            yield from _artifact_ids_from_bundle(item)

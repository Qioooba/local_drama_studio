"""Server-identity integrity validation for controlled ComfyUI/PyTorch bundles."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.runtime_adapters import ModelLockRuntimeAdapter, lock_path_prefix_for_library
from local_drama.model_platform.domain.models import RuntimeKind
from local_drama.model_platform.domain.states import PresenceStatus


@dataclass(frozen=True, slots=True)
class InstallationIntegrityResult:
    validation_run_id: str
    runtime_model_installation_id: str
    status: str
    install_state: str


class InstallationIntegrityService:
    """Re-read a model-lock bundle without trusting previous discovery rows.

    This validates only the controlled component manifest: each declared
    relative path remains inside the service-owned library and its expected
    byte count still matches.  It does not load the model, prove an Adapter,
    or make a capability executable.
    """

    def __init__(self, database: Database, settings: Settings, *, lock_path: Path | None = None) -> None:
        self.database = database
        self.settings = settings
        self.lock_path = lock_path or settings.release_root / "config" / "model-lock.json"

    def verify(self, runtime_model_installation_id: str) -> InstallationIntegrityResult:
        candidate = self._candidate(runtime_model_installation_id)
        kind = RuntimeKind(str(candidate["runtime_kind"]))
        if kind not in {RuntimeKind.COMFYUI, RuntimeKind.PYTORCH_PROCESS}:
            raise DomainRuleError(
                "MP_INSTALLATION_INTEGRITY_IMPLEMENTATION_UNAVAILABLE",
                "该运行时没有受控 model-lock 完整性验证实现。",
                {"runtime_kind": kind.value},
            )
        root = self._configured_library_root(str(candidate["root_path_local"]))
        if not self.lock_path.is_file():
            raise DomainRuleError("MP_MODEL_LOCK_NOT_FOUND", "受控 model-lock 不存在，不能验证模型安装。")
        report = ModelLockRuntimeAdapter(kind, self.lock_path, root, lock_path_prefix=lock_path_prefix_for_library(kind, root)).discover()
        observation = next((item for item in report.observations if item.native_locator == str(candidate["native_locator"])), None)
        status = "INTEGRITY_PASSED" if observation is not None and observation.presence is PresenceStatus.PRESENT else "FAILED"
        result = _safe_result(kind, observation)
        return self._record(candidate, status=status, result=result)

    def _candidate(self, runtime_model_installation_id: str):
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT installation.id AS runtime_model_installation_id,installation.native_locator,
                          runtime.kind AS runtime_kind,runtime_version.configuration_json
                   FROM mp_runtime_model_installations installation
                   JOIN mp_runtime_installation_versions runtime_version
                     ON runtime_version.id=installation.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime ON runtime.id=runtime_version.runtime_installation_id
                   WHERE installation.id=?""",
                (runtime_model_installation_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_RUNTIME_MODEL_INSTALLATION_NOT_FOUND", "指定的 V2 模型安装不存在。")
        try:
            configuration = json.loads(str(row["configuration_json"]))
        except (TypeError, ValueError) as error:
            raise DomainRuleError("MP_RUNTIME_CONFIGURATION_INVALID", "模型运行时配置不是合法 JSON。") from error
        if not isinstance(configuration, dict) or not isinstance(configuration.get("library_id"), str):
            raise DomainRuleError("MP_RUNTIME_CONFIGURATION_INVALID", "该运行时没有受控模型库绑定。")
        with self.database.connect() as connection:
            library = connection.execute(
                "SELECT root_path_local FROM mp_model_libraries WHERE id=?",
                (configuration["library_id"],),
            ).fetchone()
        if library is None:
            raise DomainRuleError("MP_MODEL_LIBRARY_NOT_FOUND", "该运行时引用的模型库不存在。")
        return {
            "runtime_model_installation_id": str(row["runtime_model_installation_id"]),
            "native_locator": str(row["native_locator"]),
            "runtime_kind": str(row["runtime_kind"]),
            "root_path_local": str(library["root_path_local"]),
        }

    def _configured_library_root(self, stored_root: str) -> Path:
        try:
            requested = Path(stored_root).resolve()
        except OSError as error:
            raise DomainRuleError("MP_MODEL_LIBRARY_PATH_INVALID", "受控模型库路径无法解析。") from error
        configured = {root.resolve() for root in self.settings.model_library_roots}
        if requested not in configured:
            raise DomainRuleError(
                "MP_MODEL_LIBRARY_CONFIGURATION_STALE",
                "候选模型库不属于当前 Windows 服务身份配置；请重新配置并扫描。",
            )
        return requested

    def _record(self, candidate: dict[str, str], *, status: str, result: dict[str, object]) -> InstallationIntegrityResult:
        now = _utc_now()
        validation_run_id = str(uuid.uuid4())
        install_state = "INTEGRITY_VERIFIED" if status == "INTEGRITY_PASSED" else "INTEGRITY_FAILED"
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO mp_validation_runs
                (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
                VALUES (?, 'RUNTIME_MODEL_INSTALLATION', ?, 'INSTALLATION_INTEGRITY', ?, ?, ?, ?, ?, ?)""",
                (validation_run_id, candidate["runtime_model_installation_id"], status, _json(result), now, now, now, now),
            )
            connection.execute(
                """INSERT INTO mp_validation_evidence
                (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), validation_run_id, "INSTALLATION_INTEGRITY", _hash(result), _json(result), None, now, now),
            )
            connection.execute(
                "UPDATE mp_runtime_model_installations SET install_state=?,updated_at=? WHERE id=?",
                (install_state, now, candidate["runtime_model_installation_id"]),
            )
        return InstallationIntegrityResult(validation_run_id, candidate["runtime_model_installation_id"], status, install_state)


def _safe_result(kind: RuntimeKind, observation) -> dict[str, object]:
    files = observation.metadata.get("files") if observation is not None else ()
    components = [
        {
            "relative_path": item.get("relative_path"),
            "present": item.get("present"),
            "size_matches": item.get("size_matches"),
        }
        for item in files
        if isinstance(item, dict)
    ] if isinstance(files, (list, tuple)) else []
    return {
        "runtime_kind": kind.value,
        "presence": observation.presence.value if observation is not None else "MISSING",
        "component_count": len(components),
        "components": components,
    }


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

"""V2 discovery orchestration for ComfyUI and PyTorch model-lock bundles."""

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
from local_drama.model_platform.application.discovery import DiscoveryService, PersistedDiscoveryRun
from local_drama.model_platform.application.runtime_adapters import ModelLockRuntimeAdapter, lock_path_prefix_for_library
from local_drama.model_platform.domain.models import RuntimeKind


@dataclass(frozen=True, slots=True)
class ModelLockDiscoveryResult:
    runs: tuple[PersistedDiscoveryRun, ...]


class ModelLockDiscoveryOrchestrator:
    """Scan configured model libraries without treating their paths as identity.

    The release-owned lock controls which relative components are inspected.
    Each configured model library receives an independent V2 Library and a
    ComfyUI/PyTorch runtime binding, allowing a future Windows Server to add a
    second data disk without rewriting model release identity.
    """

    def __init__(self, database: Database, settings: Settings, *, lock_path: Path | None = None) -> None:
        self.database = database
        self.settings = settings
        self.lock_path = lock_path or settings.release_root / "config" / "model-lock.json"

    def scan(self) -> ModelLockDiscoveryResult:
        if not self.settings.model_library_roots:
            raise DomainRuleError("MP_MODEL_LIBRARY_NOT_CONFIGURED", "尚未配置 V2 模型库根目录，不能扫描 ComfyUI 或 PyTorch 模型。")
        if not self.lock_path.is_file():
            raise DomainRuleError("MP_MODEL_LOCK_NOT_FOUND", "受控 model-lock 不存在，不能扫描 ComfyUI 或 PyTorch 模型。")
        runs: list[PersistedDiscoveryRun] = []
        for index, root in enumerate(self.settings.model_library_roots, start=1):
            library_id = self._ensure_library(root, index)
            for kind in _runtime_bindings_for_library(root):
                runtime_version_id = self._ensure_runtime_version(library_id, kind, index)
                runs.append(
                    DiscoveryService(self.database).discover_and_record(
                        ModelLockRuntimeAdapter(kind, self.lock_path, root, lock_path_prefix=lock_path_prefix_for_library(kind, root)),
                        runtime_installation_version_id=runtime_version_id,
                        source="MODEL_LOCK",
                    )
                )
        return ModelLockDiscoveryResult(tuple(runs))

    def _ensure_library(self, root: Path, index: int) -> str:
        node_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:node:{self.settings.instance_id}"))
        library_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:library:{self.settings.instance_id}:{index}"))
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO mp_compute_nodes
                (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (node_id, f"local-{self.settings.instance_id}", "本机服务节点", f"service:{self.settings.instance_id}", "{}", now, now, now),
            )
            connection.execute(
                """INSERT OR IGNORE INTO mp_model_libraries
                (id,node_id,code,kind,root_path_local,managed,read_only,scan_policy_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (library_id, node_id, f"model-library-{index}", "MODEL_ROOT", str(root.resolve()), False, False, "{}", now, now),
            )
        return library_id

    def _ensure_runtime_version(self, library_id: str, kind: RuntimeKind, index: int) -> str:
        runtime_name = "comfyui" if kind is RuntimeKind.COMFYUI else "pytorch"
        runtime_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:{runtime_name}:{self.settings.instance_id}:{index}"))
        configuration = {"library_id": library_id, "discovery_source": "MODEL_LOCK"}
        fingerprint = hashlib.sha256(_json({"kind": kind.value, **configuration}).encode("utf-8")).hexdigest()
        now = _utc_now()
        with self.database.transaction() as connection:
            node_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:node:{self.settings.instance_id}"))
            connection.execute(
                """INSERT OR IGNORE INTO mp_runtime_installations
                (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (runtime_id, node_id, f"{runtime_name}.model-library-{index}", kind.value, "SERVICE_MANAGED", f"{runtime_name.title()}（模型库 {index}）", now, now),
            )
            existing = connection.execute(
                "SELECT id FROM mp_runtime_installation_versions WHERE runtime_installation_id=? AND fingerprint=?",
                (runtime_id, fingerprint),
            ).fetchone()
            if existing is not None:
                return str(existing["id"])
            version_no = int(connection.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM mp_runtime_installation_versions WHERE runtime_installation_id=?",
                (runtime_id,),
            ).fetchone()[0])
            version_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO mp_runtime_installation_versions
                (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (version_id, runtime_id, version_no, f"{runtime_name}.model-lock.v1", "v1", "LOCAL_FILESYSTEM", _json(configuration), fingerprint, "DRAFT", now, now),
            )
        return version_id


def _runtime_bindings_for_library(root: Path) -> tuple[RuntimeKind, ...]:
    """Map canonical library roles to legacy lock prefixes in one Adapter boundary."""
    name = root.name.casefold()
    if name == "comfyui":
        return (RuntimeKind.COMFYUI,)
    if name == "pytorch":
        return (RuntimeKind.PYTORCH_PROCESS,)
    if name in {"ollama", "audio"}:
        # These canonical libraries use their own service/runtime discovery.
        # A release model-lock has no authority to infer their contents.
        return ()
    # Existing administrator-owned aggregate libraries stay readable without
    # guessing a new layout. They may contain both legacy path families.
    return (RuntimeKind.COMFYUI, RuntimeKind.PYTORCH_PROCESS)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

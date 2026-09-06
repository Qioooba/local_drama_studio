"""Service-identity managed llama.cpp bootstrap and V2 discovery orchestration."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.discovery import DiscoveryService, PersistedDiscoveryRun
from local_drama.model_platform.application.gguf_discovery import GgufDirectoryRuntimeAdapter


class LlamaCppDiscoveryOrchestrator:
    """Record GGUF directory scanner evidence under the final application identity.

    Like the Ollama orchestrator, this intentionally does not promote files to
    releases or Profiles.  The resulting runtime version stays DRAFT until an
    explicit validation/publish workflow runs, so discovery cannot change
    creator-facing behavior.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def scan(self, *, root: Path | None = None) -> PersistedDiscoveryRun:
        runtime_version_id = self.ensure_runtime_version()
        scan_root = root or self._default_root()
        return DiscoveryService(self.database).discover_and_record(
            GgufDirectoryRuntimeAdapter(scan_root),
            runtime_installation_version_id=runtime_version_id,
            source="GGUF_DIRECTORY",
        )

    def ensure_runtime_version(self) -> str:
        if self.settings.llm_provider.strip().upper() != "LLAMA_CPP_MANAGED":
            raise DomainRuleError(
                "MP_LLAMA_CPP_DISCOVERY_NOT_CONFIGURED",
                "当前实例未配置托管 llama.cpp 本机运行时，不能执行 GGUF 目录扫描。",
            )
        node_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:node:{self.settings.instance_id}"))
        runtime_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:llama-cpp:{self.settings.instance_id}"))
        # Freeze the non-secret launch policy so a Profile cannot silently
        # run under a different context/KV/MTP configuration. Raw paths stay
        # in machine configuration; only the model-root hash is catalogued.
        configuration = managed_llama_runtime_configuration(self.settings)
        fingerprint = hashlib.sha256(_json(configuration).encode("utf-8")).hexdigest()
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO mp_compute_nodes
                (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (node_id, f"local-{self.settings.instance_id}", "本机服务节点", f"service:{self.settings.instance_id}", "{}", now, now, now),
            )
            connection.execute(
                """INSERT OR IGNORE INTO mp_runtime_installations
                (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (runtime_id, node_id, "llama-cpp.managed", "LLAMA_CPP_MANAGED", "SERVICE_MANAGED", "托管 llama.cpp（本机进程）", now, now),
            )
            existing = connection.execute(
                "SELECT id FROM mp_runtime_installation_versions WHERE runtime_installation_id=? AND fingerprint=?",
                (runtime_id, fingerprint),
            ).fetchone()
            if existing is not None:
                return str(existing["id"])
            next_version = int(connection.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM mp_runtime_installation_versions WHERE runtime_installation_id=?",
                (runtime_id,),
            ).fetchone()[0])
            version_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO mp_runtime_installation_versions
                (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (version_id, runtime_id, next_version, "llama.chat.v1", "v1", "LOCAL_HTTP", _json(configuration), fingerprint, "DRAFT", now, now),
            )
        return version_id

    def _default_root(self) -> Path:
        if self.settings.llama_model_path is None:
            raise DomainRuleError(
                "MP_LLAMA_CPP_DISCOVERY_ROOT_MISSING",
                "未配置 GGUF 模型路径，无法推断扫描目录（LOCAL_DRAMA_LLAMA_MODEL_PATH）。",
            )
        return self.settings.llama_model_path.parent


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def managed_llama_runtime_configuration(settings: Settings) -> dict[str, object]:
    """Return non-secret launch identity frozen into V2 RuntimeVersions."""

    model_root = settings.llama_model_path.parent if settings.llama_model_path is not None else None
    return {
        "provider": "LLAMA_CPP_MANAGED",
        "base_url": settings.llm_base_url.rstrip("/"),
        "gateway_enabled": settings.llama_gateway_enabled,
        "gateway_port": settings.llama_gateway_port if settings.llama_gateway_enabled else None,
        "idle_timeout_seconds": settings.llama_idle_timeout_seconds if settings.llama_gateway_enabled else None,
        "server_port": settings.llama_server_port,
        "ctx_size": settings.llama_ctx_size,
        "gpu_layers": settings.llama_gpu_layers,
        "flash_attn": settings.llama_flash_attn,
        "kv_cache_type": settings.llama_kv_cache_type,
        "mtp_enabled": settings.llama_mtp_enabled,
        "mtp_draft_tokens": settings.llama_mtp_draft_tokens,
        "extra_args_sha256": hashlib.sha256(_json(list(settings.llama_server_args)).encode("utf-8")).hexdigest(),
        "model_root_sha256": (
            hashlib.sha256(str(model_root).casefold().encode("utf-8")).hexdigest()
            if model_root is not None
            else None
        ),
    }


def verify_managed_llama_runtime_configuration(settings: Settings, configuration_json: str) -> str:
    """Reject a frozen RuntimeVersion after its machine launch policy changes."""

    try:
        configured = json.loads(configuration_json)
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_RUNTIME_CONFIGURATION_INVALID", "托管 llama.cpp Runtime 配置不是合法 JSON。") from error
    expected = managed_llama_runtime_configuration(settings)
    if not isinstance(configured, dict) or configured != expected:
        raise DomainRuleError(
            "MP_RUNTIME_CONFIGURATION_STALE",
            "托管 llama.cpp Runtime 启动配置已变化；请重新扫描、验证并发布 Profile。",
        )
    return str(expected["base_url"])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

"""Service-identity Ollama bootstrap and V2 discovery orchestration."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.discovery import DiscoveryService, PersistedDiscoveryRun
from local_drama.model_platform.application.runtime_adapters import OllamaCatalogClient, OllamaRuntimeAdapter


class OllamaDiscoveryOrchestrator:
    """Record Ollama scanner evidence under the final application identity.

    It intentionally does not promote tags to releases or Profiles.  The
    resulting runtime version is DRAFT until an explicit validation/publish
    workflow exists, so discovery cannot change creator-facing behavior.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def scan(self, catalog: OllamaCatalogClient) -> PersistedDiscoveryRun:
        runtime_version_id = self.ensure_runtime_version()
        return DiscoveryService(self.database).discover_and_record(
            OllamaRuntimeAdapter(catalog),
            runtime_installation_version_id=runtime_version_id,
            source="OLLAMA_API",
        )

    def ensure_runtime_version(self) -> str:
        if self.settings.llm_provider.strip().upper() != "OLLAMA_LOOPBACK":
            raise DomainRuleError("MP_OLLAMA_DISCOVERY_NOT_CONFIGURED", "当前实例未配置 Ollama 本机运行时，不能执行 Ollama 扫描。")
        node_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:node:{self.settings.instance_id}"))
        runtime_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:ollama:{self.settings.instance_id}"))
        configuration = {
            "provider": "OLLAMA_LOOPBACK",
            "base_url": self.settings.llm_base_url.rstrip("/"),
            "selected_model": self.settings.llm_model,
        }
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
                (runtime_id, node_id, "ollama.loopback", "OLLAMA", "SERVICE_MANAGED", "Ollama（本机服务）", now, now),
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
                (version_id, runtime_id, next_version, "ollama.chat.v1", "v1", "LOCAL_HTTP", _json(configuration), fingerprint, "DRAFT", now, now),
            )
        return version_id


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

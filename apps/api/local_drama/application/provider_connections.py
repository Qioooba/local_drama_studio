"""Local provider connection metadata and controlled secret access."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.network_policy import parse_runtime_endpoint
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.platform import create_platform_services
from local_drama.platform.contracts import SecretRef, SecretStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mask(secret: str | None) -> str | None:
    if not secret:
        return None
    value = secret.strip()
    if len(value) <= 6:
        return "••••••"
    return f"{value[:3]}{'•' * max(4, min(12, len(value) - 6))}{value[-3:]}"


def _safe_code(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")[:120]
    if not normalized:
        raise DomainRuleError("PROVIDER_CONNECTION_CODE_REQUIRED", "Provider Connection code 不能为空")
    return normalized


def _secret_value(row: Any, secret_store: SecretStore) -> str | None:
    source = str(row["credential_source"] or "NONE").upper()
    if source in {"OS_SECRET_STORE", "WINDOWS_CREDENTIAL_MANAGER"}:
        return secret_store.get(SecretRef("ProviderConnection", str(row["id"])))
    if source == "ENVIRONMENT":
        variable = str(row["environment_variable_name"] or "").strip()
        return os.environ.get(variable) if variable else None
    return None


class ProviderConnectionService:
    def __init__(
        self,
        database: Database,
        settings: Settings | None = None,
        secret_store: SecretStore | None = None,
    ) -> None:
        self.database = database
        self.settings = settings or Settings()
        self.secret_store = secret_store or create_platform_services(self.settings).secret_store

    def _read_secret(self, row: Any) -> str | None:
        return _secret_value(row, self.secret_store)

    def _put_secret(self, connection_id: str, secret: str) -> None:
        self.secret_store.put(SecretRef("ProviderConnection", connection_id), secret)

    def _delete_secret(self, connection_id: str) -> bool:
        return self.secret_store.delete(SecretRef("ProviderConnection", connection_id))

    def _validate_url(self, provider_kind: str, base_url: str) -> str:
        value = base_url.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DomainRuleError("PROVIDER_BASE_URL_INVALID", "Provider Base URL 必须是 http 或 https 地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise DomainRuleError("PROVIDER_BASE_URL_AMBIGUOUS", "Provider Base URL 不得携带凭据、query 或 fragment")
        if provider_kind.upper() in {"OLLAMA", "OLLAMA_LOOPBACK"} and parse_runtime_endpoint(
            value,
            allow_private_network=self.settings.allows_private_network,
            schemes=frozenset({"http"}),
        ) is None:
            raise DomainRuleError("LOCAL_ONLY_ENDPOINT_REQUIRED", "Ollama 连接只允许 loopback 或受控私网地址")
        return value

    @staticmethod
    def _provider_kind(value: str) -> tuple[str, str]:
        provider = value.strip().upper()
        if provider in {"OLLAMA", "OLLAMA_LOOPBACK"}:
            return "OLLAMA", "OLLAMA"
        if provider in {"DEEPSEEK", "OPENAI_COMPAT", "OPENAI_COMPATIBLE", "CUSTOM"}:
            return ("DEEPSEEK" if provider == "DEEPSEEK" else provider), "OPENAI_COMPAT"
        raise DomainRuleError("PROVIDER_KIND_UNSUPPORTED", f"不支持的 Provider 类型：{value}")

    def _ensure_legacy_connection(self, connection: Any) -> None:
        """Make an existing fixed DeepSeek credential visible as a connection."""

        exists = connection.execute("SELECT id FROM provider_connections WHERE code='deepseek-main' LIMIT 1").fetchone()
        if exists is not None:
            return
        legacy_secret = self.secret_store.get(SecretRef("DeepSeekAPI", "default"))
        legacy_env_name = next((name for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY") if os.environ.get(name)), None)
        if not legacy_secret and not legacy_env_name:
            return
        connection_id = "pc_deepseek_main"
        now = _now()
        credential_source = "ENVIRONMENT" if legacy_env_name else (
            "OS_SECRET_STORE"
        )
        connection.execute(
            """INSERT INTO provider_connections
            (id, code, title, provider_kind, protocol, base_url, model, credential_source,
             credential_ref, environment_variable_name, status, created_at, updated_at, created_by, updated_by, revision, schema_version)
            VALUES (?, 'deepseek-main', 'DeepSeek 主连接', 'DEEPSEEK', 'OPENAI_COMPAT',
             'https://api.deepseek.com/v1', NULL, ?, ?, ?, 'ACTIVE', ?, ?, 'migration', 'migration', 1, 'v1')""",
            (
                connection_id,
                credential_source,
                f"LocalDramaStudio/ProviderConnection/{connection_id}" if legacy_secret else None,
                legacy_env_name,
                now,
                now,
            ),
        )
        if legacy_secret:
            try:
                self._put_secret(connection_id, legacy_secret)
            except (OSError, ValueError):
                # Keep the old target readable; the UI will show the new row as
                # configured only after the operator explicitly replaces it.
                connection.execute(
                    "UPDATE provider_connections SET credential_source='NONE', credential_ref=NULL WHERE id=?",
                    (connection_id,),
                )

    def _public(self, row: Any, secret: str | None = None) -> dict[str, Any]:
        value = secret if secret is not None else None
        if secret is None:
            try:
                value = self._read_secret(row)
            except (OSError, ValueError):
                value = None
        return {
            "id": str(row["id"]),
            "code": str(row["code"]),
            "title": str(row["title"]),
            "provider_kind": str(row["provider_kind"]),
            "protocol": str(row["protocol"]),
            "base_url": str(row["base_url"]),
            "model": row["model"],
            "credential_source": str(row["credential_source"]),
            "credential_ref": row["credential_ref"],
            "environment_variable_name": row["environment_variable_name"],
            "has_secret": bool(value),
            "masked_secret": _mask(value),
            "status": str(row["status"]),
            "last_probe": {
                "status": row["last_probe_status"],
                "at": row["last_probe_at"],
                "summary": json.loads(str(row["last_probe_summary_json"] or "{}")),
            },
            "revision": int(row["revision"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_connections(self) -> list[dict[str, Any]]:
        with self.database.transaction() as connection:
            self._ensure_legacy_connection(connection)
            rows = connection.execute("SELECT * FROM provider_connections ORDER BY status, title, code").fetchall()
        return [self._public(row) for row in rows]

    def get_connection(self, connection_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROVIDER_CONNECTION_NOT_FOUND", "Provider Connection 不存在")
        return self._public(row)

    def resolve_for_execution(self, connection_id: str) -> dict[str, Any]:
        """Resolve the exact connection selected by an immutable Profile.

        The secret crosses only this internal execution boundary. It is never
        included in API responses or audit metadata.
        """
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROVIDER_CONNECTION_NOT_FOUND", "Profile 引用的 Provider Connection 不存在")
        if str(row["status"]).upper() != "ACTIVE":
            raise DomainRuleError("PROVIDER_CONNECTION_INACTIVE", "Profile 引用的 Provider Connection 当前未启用")
        try:
            secret = self._read_secret(row)
        except (OSError, ValueError) as error:
            raise DomainRuleError("PROVIDER_SECRET_READ_FAILED", "无法读取 Profile 所选 Provider 的密钥") from error
        return {
            "id": str(row["id"]),
            "provider_kind": str(row["provider_kind"]),
            "protocol": str(row["protocol"]),
            "base_url": str(row["base_url"]),
            "model": row["model"],
            "secret": secret,
        }

    def create(
        self,
        *,
        code: str,
        title: str,
        provider_kind: str,
        base_url: str,
        model: str | None = None,
        credential_source: str = "NONE",
        environment_variable_name: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        normalized_code = _safe_code(code)
        normalized_kind, protocol = self._provider_kind(provider_kind)
        normalized_url = self._validate_url(normalized_kind, base_url)
        source = credential_source.strip().upper()
        if source not in {"NONE", "OS_SECRET_STORE", "WINDOWS_CREDENTIAL_MANAGER", "ENVIRONMENT"}:
            raise DomainRuleError("PROVIDER_CREDENTIAL_SOURCE_INVALID", "不支持的凭据来源")
        if source == "ENVIRONMENT" and not environment_variable_name:
            raise DomainRuleError("PROVIDER_ENVIRONMENT_VARIABLE_REQUIRED", "环境变量凭据来源必须提供变量名")
        connection_id = f"pc_{normalized_code}"
        now = _now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO provider_connections
                    (id, code, title, provider_kind, protocol, base_url, model, credential_source,
                     credential_ref, environment_variable_name, status, last_probe_summary_json,
                     created_at, updated_at, created_by, updated_by, revision, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', '{}', ?, ?, ?, ?, 1, 'v1')""",
                    (
                        connection_id,
                        normalized_code,
                        title.strip(),
                        normalized_kind,
                        protocol,
                        normalized_url,
                        model.strip() if model and model.strip() else None,
                        source,
                        f"LocalDramaStudio/ProviderConnection/{connection_id}"
                        if source in {"OS_SECRET_STORE", "WINDOWS_CREDENTIAL_MANAGER"}
                        else None,
                        environment_variable_name.strip() if environment_variable_name else None,
                        now,
                        now,
                        actor,
                        actor,
                    ),
                )
                connection.execute(
                    "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'PROVIDER_CONNECTION_CREATED', 'provider_connection', ?, ?, ?)",
                    (actor, connection_id, "创建 Provider Connection", _json({"code": normalized_code, "provider_kind": normalized_kind})),
                )
                row = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise DomainRuleError("PROVIDER_CONNECTION_CODE_CONFLICT", "Provider Connection code 已存在") from error
            raise
        return self._public(row)

    def update(self, connection_id: str, *, expected_revision: int, title: str, base_url: str, model: str | None, actor: str = "local-user") -> dict[str, Any]:
        normalized_url = self._validate_url("CUSTOM", base_url)
        now = _now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
            if row is None:
                raise DomainRuleError("PROVIDER_CONNECTION_NOT_FOUND", "Provider Connection 不存在")
            if int(row["revision"]) != expected_revision:
                raise DomainRuleError("PROVIDER_CONNECTION_REVISION_CONFLICT", "Provider Connection 已被其他操作更新，请刷新后重试")
            connection.execute(
                "UPDATE provider_connections SET title=?, base_url=?, model=?, updated_at=?, updated_by=?, revision=revision+1 WHERE id=?",
                (title.strip(), normalized_url, model.strip() if model and model.strip() else None, now, actor, connection_id),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'PROVIDER_CONNECTION_UPDATED', 'provider_connection', ?, ?, ?)",
                (actor, connection_id, "更新 Provider Connection 元数据", _json({"base_url_changed": str(row["base_url"]) != normalized_url})),
            )
            updated = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
        return self._public(updated)

    def reveal(self, connection_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROVIDER_CONNECTION_NOT_FOUND", "Provider Connection 不存在")
        try:
            secret = self._read_secret(row)
        except (OSError, ValueError) as error:
            raise DomainRuleError("PROVIDER_SECRET_READ_FAILED", "无法读取 Provider 密钥") from error
        if not secret:
            raise DomainRuleError("PROVIDER_SECRET_NOT_FOUND", "当前连接没有可查看的密钥")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'PROVIDER_SECRET_REVEALED', 'provider_connection', ?, ?, ?)",
                (actor, connection_id, "查看 Provider 密钥（仅记录元数据）", _json({"credential_source": row["credential_source"], "value_recorded": False})),
            )
        return {"secret": secret, "expires_in_seconds": 60}

    def replace_secret(self, connection_id: str, secret: str, *, actor: str = "local-user") -> dict[str, Any]:
        if not secret.strip():
            raise DomainRuleError("PROVIDER_SECRET_REQUIRED", "Provider 密钥不能为空")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
            if row is None:
                raise DomainRuleError("PROVIDER_CONNECTION_NOT_FOUND", "Provider Connection 不存在")
            if str(row["credential_source"]).upper() not in {"OS_SECRET_STORE", "WINDOWS_CREDENTIAL_MANAGER"}:
                raise DomainRuleError("PROVIDER_SECRET_SOURCE_READ_ONLY", "只有操作系统安全凭据库来源支持在应用内替换")
            try:
                self._put_secret(connection_id, secret)
            except (OSError, ValueError) as error:
                raise DomainRuleError("PROVIDER_SECRET_STORE_FAILED", "无法安全保存 Provider 密钥") from error
            now = _now()
            connection.execute("UPDATE provider_connections SET updated_at=?, updated_by=?, revision=revision+1 WHERE id=?", (now, actor, connection_id))
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'PROVIDER_SECRET_REPLACED', 'provider_connection', ?, ?, ?)",
                (actor, connection_id, "替换 Provider 密钥", _json({"value_recorded": False})),
            )
            updated = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
        return self._public(updated, secret)

    def delete_secret(self, connection_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
            if row is None:
                raise DomainRuleError("PROVIDER_CONNECTION_NOT_FOUND", "Provider Connection 不存在")
            if str(row["credential_source"]).upper() not in {"OS_SECRET_STORE", "WINDOWS_CREDENTIAL_MANAGER"}:
                raise DomainRuleError("PROVIDER_SECRET_SOURCE_READ_ONLY", "环境变量来源不能由应用删除")
            try:
                self._delete_secret(connection_id)
            except (OSError, ValueError) as error:
                raise DomainRuleError("PROVIDER_SECRET_DELETE_FAILED", "无法从 Windows Credential Manager 删除密钥") from error
            now = _now()
            connection.execute("UPDATE provider_connections SET updated_at=?, updated_by=?, revision=revision+1 WHERE id=?", (now, actor, connection_id))
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'PROVIDER_SECRET_DELETED', 'provider_connection', ?, ?, ?)",
                (actor, connection_id, "删除 Provider 密钥", _json({"value_recorded": False})),
            )
            updated = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
        return self._public(updated)

    def delete_connection(self, connection_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
            if row is None:
                raise DomainRuleError("PROVIDER_CONNECTION_NOT_FOUND", "Provider Connection 不存在")
            reference = connection.execute(
                "SELECT id FROM execution_profile_versions WHERE model_bundle_json LIKE ? LIMIT 1",
                (f"%{connection_id}%",),
            ).fetchone()
            if reference is not None:
                raise DomainRuleError("PROVIDER_CONNECTION_IN_USE", "该连接仍被 Profile 引用，请先停用引用或改绑其他连接")
            if str(row["credential_source"]).upper() in {"OS_SECRET_STORE", "WINDOWS_CREDENTIAL_MANAGER"}:
                try:
                    self._delete_secret(connection_id)
                except (OSError, ValueError) as error:
                    raise DomainRuleError("PROVIDER_SECRET_DELETE_FAILED", "无法从 Windows Credential Manager 删除密钥") from error
            connection.execute("DELETE FROM provider_connections WHERE id=?", (connection_id,))
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'PROVIDER_CONNECTION_DELETED', 'provider_connection', ?, ?, ?)",
                (actor, connection_id, "删除 Provider Connection", _json({"value_recorded": False})),
            )
        return {"id": connection_id, "deleted": True}

    def probe(self, connection_id: str, *, model: str | None = None, load_test: bool = False, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM provider_connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROVIDER_CONNECTION_NOT_FOUND", "Provider Connection 不存在")
        resolved_model = (model or row["model"] or "").strip()
        if not resolved_model:
            raise DomainRuleError("PROVIDER_MODEL_REQUIRED", "连通性测试需要模型名称")
        secret = self._read_secret(row)
        provider = "OLLAMA_LOOPBACK" if str(row["protocol"]).upper() == "OLLAMA" else "OPENAI_COMPAT"
        client = LocalLLMClient(
            str(row["base_url"]),
            resolved_model,
            provider=provider,
            api_key=secret,
            allow_private_network=self.settings.allows_private_network,
        )
        try:
            result = client.probe(load_test=load_test)
            status = "OK" if result.get("status") == "PASS" else "FAILED"
            summary = {
                "status": result.get("status"),
                "probe_level_passed": result.get("probe_level_passed"),
                "error_code": result.get("error_code"),
                "model_present": result.get("model_present"),
            }
        except DomainRuleError as error:
            status = "FAILED"
            summary = {"status": "FAIL", "error_code": error.code}
            result = {"status": "FAIL", "error_code": error.code, "message": error.message, "probe_level_passed": 0}
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE provider_connections SET model=?, last_probe_status=?, last_probe_at=?, last_probe_summary_json=?, updated_at=?, revision=revision+1 WHERE id=?",
                (resolved_model, status, now, _json(summary), now, connection_id),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'PROVIDER_CONNECTION_PROBED', 'provider_connection', ?, ?, ?)",
                (actor, connection_id, "探测 Provider Connection", _json({"status": status, "model": resolved_model, "secret_recorded": False})),
            )
        return {"connection": self.get_connection(connection_id), "probe": result}

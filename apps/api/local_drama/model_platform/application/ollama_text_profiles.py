"""First production Profile path: verified Ollama text Offerings to V2 Profiles."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.model_platform.application.profile_publication import ProfilePublicationService, ProfileVersionDraft

_TEXT_CAPABILITIES = frozenset({"LLM_STORY_PARSE", "LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE"})
_TEMPLATE_VERSION = "v1"


class OllamaProfileClient(Protocol):
    def chat_json(
        self,
        system: str,
        user: str,
        images: list[str] | None = None,
        *,
        json_schema: dict[str, object] | None = None,
        inference_options: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class ProvisionedOllamaProfile:
    profile_version_id: str
    profile_code: str
    created: bool


@dataclass(frozen=True, slots=True)
class OllamaProfileSmokeResult:
    validation_run_id: str
    profile_version_id: str
    status: str


class OllamaTextProfileService:
    """Composes and validates V2 Profile candidates from a proven Offering.

    This is intentionally not a generic profile wizard.  Its contracts,
    adapter code and smoke request are versioned in this service; adding a
    different runtime or capability requires another explicit implementation.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        ollama_client_factory: Callable[[str, str], OllamaProfileClient] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.ollama_client_factory = ollama_client_factory or self._default_client

    def provision(self, runtime_model_installation_id: str, capability_code: str) -> ProvisionedOllamaProfile:
        candidate = self._candidate(runtime_model_installation_id, capability_code)
        contracts = self._ensure_contracts(str(candidate["capability_id"]))
        profile_code = _profile_code(str(candidate["release_code"]), str(candidate["capability_code"]))
        payload = {
            "template": "ollama.text.profile.v1",
            "runtime_model_installation_ids": [str(candidate["runtime_model_installation_id"])],
            "defaults": {"temperature": 0.2, "max_tokens": 2048, "num_ctx": 8192},
            "allowed_override_fields": ["temperature", "max_tokens", "num_ctx"],
        }
        service = ProfilePublicationService(self.database)
        try:
            created = service.create_candidate(
                ProfileVersionDraft(
                    profile_code=profile_code,
                    profile_title=f"{candidate['model_title']} · {candidate['capability_title']}",
                    capability_definition_id=str(candidate["capability_id"]),
                    runtime_installation_version_id=str(candidate["runtime_version_id"]),
                    parameter_contract_version_id=contracts["parameter_contract_version_id"],
                    adapter_binding_contract_version_id=contracts["adapter_binding_contract_version_id"],
                    resource_policy_version_id=contracts["resource_policy_version_id"],
                    payload=payload,
                )
            )
            return ProvisionedOllamaProfile(created.profile_version_id, profile_code, True)
        except DomainRuleError as error:
            if error.code != "MP_PROFILE_PAYLOAD_ALREADY_EXISTS":
                raise
            existing_id = str(error.details["profile_version_id"])
            return ProvisionedOllamaProfile(existing_id, profile_code, False)

    def smoke(self, profile_version_id: str) -> OllamaProfileSmokeResult:
        profile = self._profile(profile_version_id)
        base_url = self._verified_base_url(str(profile["configuration_json"]))
        defaults = _profile_defaults(str(profile["payload_json"]))
        source_smoke_id = self._source_smoke(profile)
        try:
            response = self.ollama_client_factory(base_url, str(profile["native_locator"])).chat_json(
                "You are a LocalDramaStudio profile smoke verifier. Return only the requested JSON object.",
                "Return exactly {\"ready\": true}.",
                json_schema={
                    "type": "object",
                    "properties": {"ready": {"type": "boolean"}},
                    "required": ["ready"],
                    "additionalProperties": False,
                },
                inference_options=defaults,
            )
            passed = response.get("ready") is True
            result = {
                "payload_hash": str(profile["payload_hash"]),
                "source_capability_validation_run_id": source_smoke_id,
                "output_contract_verified": passed,
            }
            evidence = {"validator": "ollama.text.profile.v1", "ready": passed, "parameter_names": sorted(defaults)}
        except DomainRuleError as error:
            passed = False
            result = {
                "payload_hash": str(profile["payload_hash"]),
                "source_capability_validation_run_id": source_smoke_id,
                "error_code": error.code,
            }
            evidence = {"validator": "ollama.text.profile.v1", "error_code": error.code}
        validation = ProfilePublicationService(self.database).record_validation(
            profile_version_id,
            validation_kind="PROFILE_SMOKE",
            status="SMOKE_PASSED" if passed else "FAILED",
            result=result,
            evidence=evidence,
        )
        return OllamaProfileSmokeResult(validation.validation_run_id, profile_version_id, validation.status)

    def publish(self, profile_version_id: str, validation_run_id: str, reason: str) -> None:
        ProfilePublicationService(self.database).publish(profile_version_id, validation_run_id=validation_run_id, reason=reason)

    def _candidate(self, runtime_model_installation_id: str, capability_code: str) -> sqlite3.Row:
        with self.database.connect() as connection:
            row: sqlite3.Row | None = connection.execute(
                """SELECT installation.id AS runtime_model_installation_id,release.code AS release_code,family.title AS model_title,
                          runtime_version.id AS runtime_version_id,runtime.kind AS runtime_kind,runtime_version.status AS runtime_status,
                          offering.validation_status,capability.id AS capability_id,capability.code AS capability_code,capability.title AS capability_title
                   FROM mp_runtime_model_installations installation
                   JOIN mp_model_releases release ON release.id=installation.release_id
                   JOIN mp_model_families family ON family.id=release.family_id
                   JOIN mp_runtime_installation_versions runtime_version ON runtime_version.id=installation.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime ON runtime.id=runtime_version.runtime_installation_id
                   JOIN mp_capability_offerings offering ON offering.runtime_model_installation_id=installation.id
                   JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
                   WHERE installation.id=? AND capability.code=? AND installation.install_state='READY'""",
                (runtime_model_installation_id, capability_code.strip().upper()),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_PROFILE_OFFERING_NOT_READY", "该模型安装或能力尚未完成真实 smoke，不能创建 Profile 草稿。")
        if str(row["runtime_kind"]) != "OLLAMA" or str(row["capability_code"]) not in _TEXT_CAPABILITIES:
            raise DomainRuleError("MP_PROFILE_TEMPLATE_UNAVAILABLE", "该运行时/能力没有已安装的 V2 Profile 模板。")
        if str(row["runtime_status"]) != "ACTIVE" or str(row["validation_status"]) != "SMOKE_PASSED":
            raise DomainRuleError("MP_PROFILE_OFFERING_NOT_READY", "当前 Runtime 或 Offering 未处于已验证状态，不能创建 Profile 草稿。")
        return row

    def _ensure_contracts(self, capability_id: str) -> dict[str, str]:
        parameter_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:ollama-text:parameter:{capability_id}:{_TEMPLATE_VERSION}"))
        binding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:ollama-text:binding:{_TEMPLATE_VERSION}"))
        resource_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:ollama-text:resource:{_TEMPLATE_VERSION}"))
        parameter_schema = {
            "type": "object",
            "properties": {
                "temperature": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.2},
                "max_tokens": {"type": "integer", "minimum": 1, "maximum": 8192, "default": 2048},
                "num_ctx": {"type": "integer", "minimum": 256, "maximum": 32768, "default": 8192},
            },
        }
        ui_schema = {"properties": {name: {"scopes": ["RUN"]} for name in parameter_schema["properties"]}}
        binding = {"adapter_code": "ollama.chat.v1", "template": _TEMPLATE_VERSION, "transport": "LOCAL_HTTP"}
        resource = {"network_policy": {"mode": "LOCAL_ONLY"}, "gpu_runtime": "OLLAMA", "exclusive_gpu": True}
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO mp_parameter_contract_versions
                (id,capability_definition_id,version_no,schema_json,ui_schema_json,content_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (parameter_id, capability_id, 1, _json(parameter_schema), _json(ui_schema), _hash(parameter_schema), now, now),
            )
            connection.execute(
                """INSERT OR IGNORE INTO mp_adapter_binding_contract_versions
                (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (binding_id, "OLLAMA", "ollama.chat.v1", 1, _json(binding), _hash(binding), now, now),
            )
            connection.execute(
                """INSERT OR IGNORE INTO mp_resource_policy_versions
                (id,code,version_no,policy_json,content_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (resource_id, "ollama-text-local", 1, _json(resource), _hash(resource), now, now),
            )
        return {
            "parameter_contract_version_id": parameter_id,
            "adapter_binding_contract_version_id": binding_id,
            "resource_policy_version_id": resource_id,
        }

    def _profile(self, profile_version_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row: sqlite3.Row | None = connection.execute(
                """SELECT profile.id,profile.payload_json,profile.payload_hash,capability.code AS capability_code,
                          runtime_version.id AS runtime_version_id,runtime_version.configuration_json,
                          runtime_version.status AS runtime_status,runtime.kind AS runtime_kind
                   FROM mp_execution_profile_versions profile
                   JOIN mp_capability_definitions capability ON capability.id=profile.capability_definition_id
                   JOIN mp_runtime_installation_versions runtime_version ON runtime_version.id=profile.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime ON runtime.id=runtime_version.runtime_installation_id
                   WHERE profile.id=?""",
                (profile_version_id,),
            ).fetchone()
            if row is None:
                installation = None
            else:
                payload = _profile_payload(str(row["payload_json"]))
                installation = connection.execute(
                    """SELECT native_locator FROM mp_runtime_model_installations
                       WHERE id=? AND runtime_installation_version_id=? AND install_state='READY'""",
                    (payload["runtime_model_installation_ids"][0], row["runtime_version_id"]),
                ).fetchone()
        if row is None or installation is None or str(row["runtime_kind"]) != "OLLAMA" or str(row["capability_code"]) not in _TEXT_CAPABILITIES:
            raise DomainRuleError("MP_PROFILE_SMOKE_IMPLEMENTATION_UNAVAILABLE", "该 Profile 没有已安装的真实 Ollama 文本 smoke 实现。")
        if str(row["runtime_status"]) != "ACTIVE":
            raise DomainRuleError("MP_PROFILE_RUNTIME_NOT_ACTIVE", "Profile 所属 RuntimeVersion 未激活，不能运行 Profile smoke。")
        return {**dict(row), "native_locator": str(installation["native_locator"])}

    def _source_smoke(self, profile: dict[str, object]) -> str:
        payload = _profile_payload(str(profile["payload_json"]))
        installation_ids = payload["runtime_model_installation_ids"]
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT run.id FROM mp_validation_runs run
                   JOIN mp_capability_offerings offering ON offering.id=run.target_id
                   JOIN mp_runtime_model_installations installation ON installation.id=offering.runtime_model_installation_id
                   JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
                   WHERE run.target_kind='CAPABILITY_OFFERING' AND run.validation_kind='CAPABILITY_SMOKE'
                     AND run.status='SMOKE_PASSED' AND capability.code=? AND installation.id=?
                     AND EXISTS (SELECT 1 FROM mp_validation_evidence evidence WHERE evidence.validation_run_id=run.id)
                   ORDER BY run.finished_at DESC,run.created_at DESC LIMIT 1""",
                (profile["capability_code"], installation_ids[0]),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_PROFILE_VALIDATION_SOURCE_INVALID", "Profile 绑定的模型安装没有可用的真实能力 smoke 证据。")
        return str(row["id"])

    def _verified_base_url(self, configuration_json: str) -> str:
        try:
            config = json.loads(configuration_json)
        except (TypeError, ValueError) as error:
            raise DomainRuleError("MP_RUNTIME_CONFIGURATION_INVALID", "Ollama Runtime 配置不是合法 JSON。") from error
        service_url = self.settings.llm_base_url.rstrip("/")
        if not isinstance(config, dict) or config.get("provider") != "OLLAMA_LOOPBACK" or str(config.get("base_url") or "").rstrip("/") != service_url:
            raise DomainRuleError("MP_RUNTIME_CONFIGURATION_STALE", "Profile Runtime 与当前服务身份配置不一致；请重新扫描和验证。")
        return service_url

    def _default_client(self, base_url: str, model: str) -> LocalLLMClient:
        return LocalLLMClient(base_url, model, provider="OLLAMA_LOOPBACK", allow_private_network=self.settings.network_mode.value == "LAN_SERVICE")


def _profile_payload(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Profile payload 不是合法 JSON。") from error
    ids = payload.get("runtime_model_installation_ids") if isinstance(payload, dict) else None
    if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0].strip():
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Ollama 文本 Profile 必须且只能绑定一个模型安装。")
    assert isinstance(payload, dict)
    return payload


def _profile_defaults(payload_json: str) -> dict[str, object]:
    payload = _profile_payload(payload_json)
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        raise DomainRuleError("MP_PROFILE_PARAMETER_POLICY_INVALID", "Profile 缺少参数默认值。")
    return {str(key): value for key, value in defaults.items()}


def _profile_code(release_code: str, capability_code: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in release_code.lower()).strip("-")
    return f"ollama-{cleaned[:100]}-{capability_code.lower()}"[:180]


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

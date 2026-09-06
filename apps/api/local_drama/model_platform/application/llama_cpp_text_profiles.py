"""Managed llama.cpp text Profile path: verified GGUF Offerings to V2 Profiles."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.application.gpu_runtime import GpuRuntimeCoordinator
from local_drama.application.job_resources import GpuRuntime
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.model_platform.application.llama_cpp_discovery import verify_managed_llama_runtime_configuration
from local_drama.model_platform.application.profile_publication import ProfilePublicationService, ProfileVersionDraft

_TEXT_CAPABILITIES = frozenset({"LLM_STORY_PARSE", "LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE"})
_TEMPLATE_VERSION = "v1"


class LlamaCppProfileClient(Protocol):
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
class ProvisionedLlamaCppProfile:
    profile_version_id: str
    profile_code: str
    created: bool


@dataclass(frozen=True, slots=True)
class LlamaCppProfileSmokeResult:
    validation_run_id: str
    profile_version_id: str
    status: str


class LlamaCppTextProfileService:
    """Composes and validates V2 Profile candidates from a proven GGUF Offering.

    Unlike the Ollama template, there is no ``num_ctx`` parameter: the context
    window is fixed at llama-server launch time and lives in the machine
    configuration, not in per-call overrides.  The smoke runs under the single
    GPU lease so the managed child is started for the probe and released (or
    retained for a queued same-runtime job) afterwards.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        client_factory: Callable[[str, str], LlamaCppProfileClient] | None = None,
        gpu_coordinator: GpuRuntimeCoordinator | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.client_factory = client_factory or self._default_client
        self.gpu_coordinator = gpu_coordinator

    def provision(self, runtime_model_installation_id: str, capability_code: str) -> ProvisionedLlamaCppProfile:
        candidate = self._candidate(runtime_model_installation_id, capability_code)
        contracts = self._ensure_contracts(str(candidate["capability_id"]))
        profile_code = _profile_code(str(candidate["release_code"]), str(candidate["capability_code"]))
        payload = {
            "template": "llama_cpp.text.profile.v1",
            "runtime_model_installation_ids": [str(candidate["runtime_model_installation_id"])],
            # Machine launch policy (including the bound port) is versioned
            # separately from the model installation. Including that version
            # here lets a safe endpoint migration create a new immutable
            # ProfileVersion instead of incorrectly reusing a stale one.
            "runtime_installation_version_id": str(candidate["runtime_version_id"]),
            "defaults": {"temperature": 0.2, "max_tokens": 2048},
            "allowed_override_fields": ["temperature", "max_tokens"],
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
            return ProvisionedLlamaCppProfile(created.profile_version_id, profile_code, True)
        except DomainRuleError as error:
            if error.code != "MP_PROFILE_PAYLOAD_ALREADY_EXISTS":
                raise
            existing_id = str(error.details["profile_version_id"])
            return ProvisionedLlamaCppProfile(existing_id, profile_code, False)

    def smoke(self, profile_version_id: str) -> LlamaCppProfileSmokeResult:
        profile = self._profile(profile_version_id)
        base_url = self._verified_base_url(str(profile["configuration_json"]))
        defaults = _profile_defaults(str(profile["payload_json"]))
        source_smoke_id = self._source_smoke(profile)
        model_locator = str(profile["native_locator"])
        model_alias = Path(model_locator).stem

        def run_probe() -> dict[str, object]:
            return self.client_factory(base_url, model_alias).chat_json(
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

        try:
            response = self._under_lease(run_probe, model_locator=model_locator)
            passed = response.get("ready") is True
            result = {
                "payload_hash": str(profile["payload_hash"]),
                "source_capability_validation_run_id": source_smoke_id,
                "output_contract_verified": passed,
            }
            evidence = {"validator": "llama_cpp.text.profile.v1", "ready": passed, "parameter_names": sorted(defaults)}
        except DomainRuleError as error:
            passed = False
            result = {
                "payload_hash": str(profile["payload_hash"]),
                "source_capability_validation_run_id": source_smoke_id,
                "error_code": error.code,
            }
            evidence = {"validator": "llama_cpp.text.profile.v1", "error_code": error.code}
        validation = ProfilePublicationService(self.database).record_validation(
            profile_version_id,
            validation_kind="PROFILE_SMOKE",
            status="SMOKE_PASSED" if passed else "FAILED",
            result=result,
            evidence=evidence,
        )
        return LlamaCppProfileSmokeResult(validation.validation_run_id, profile_version_id, validation.status)

    def publish(self, profile_version_id: str, validation_run_id: str, reason: str) -> None:
        ProfilePublicationService(self.database).publish(profile_version_id, validation_run_id=validation_run_id, reason=reason)

    def _under_lease(
        self,
        action: Callable[[], dict[str, object]],
        *,
        model_locator: str,
    ) -> dict[str, object]:
        """Run the probe inside the single-GPU lease so the managed child serves it."""

        coordinator = self.gpu_coordinator or GpuRuntimeCoordinator(self.database, self.settings)
        with coordinator.session(
            GpuRuntime.LLAMA_CPP,
            owner_kind="MP_LLAMA_PROFILE_SMOKE",
            owner_ref="profile-smoke",
            retain_if_same_runtime_waiting=True,
            activation_context={"model_locator": model_locator},
        ):
            return action()

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
        if str(row["runtime_kind"]) != "LLAMA_CPP_MANAGED" or str(row["capability_code"]) not in _TEXT_CAPABILITIES:
            raise DomainRuleError("MP_PROFILE_TEMPLATE_UNAVAILABLE", "该运行时/能力没有已安装的 V2 Profile 模板。")
        if str(row["runtime_status"]) != "ACTIVE" or str(row["validation_status"]) != "SMOKE_PASSED":
            raise DomainRuleError("MP_PROFILE_OFFERING_NOT_READY", "当前 Runtime 或 Offering 未处于已验证状态，不能创建 Profile 草稿。")
        return row

    def _ensure_contracts(self, capability_id: str) -> dict[str, str]:
        parameter_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:llama-cpp-text:parameter:{capability_id}:{_TEMPLATE_VERSION}"))
        binding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:llama-cpp-text:binding:{_TEMPLATE_VERSION}"))
        resource_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:llama-cpp-text:resource:{_TEMPLATE_VERSION}"))
        parameter_schema = {
            "type": "object",
            "properties": {
                "temperature": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.2},
                "max_tokens": {"type": "integer", "minimum": 1, "maximum": 8192, "default": 2048},
            },
        }
        ui_schema = {"properties": {name: {"scopes": ["RUN"]} for name in parameter_schema["properties"]}}
        binding = {"adapter_code": "llama.chat.v1", "template": _TEMPLATE_VERSION, "transport": "LOCAL_HTTP"}
        resource = {"network_policy": {"mode": "LOCAL_ONLY"}, "gpu_runtime": "LLAMA_CPP", "exclusive_gpu": True}
        now = _utc_now()
        with self.database.transaction() as connection:
            parameter_hash = _hash(parameter_schema)
            existing_parameter = connection.execute(
                """SELECT id FROM mp_parameter_contract_versions
                   WHERE capability_definition_id=? AND content_hash=?""",
                (capability_id, parameter_hash),
            ).fetchone()
            if existing_parameter is not None:
                parameter_id = str(existing_parameter["id"])
            else:
                parameter_version = int(
                    connection.execute(
                        """SELECT COALESCE(MAX(version_no), 0) + 1
                           FROM mp_parameter_contract_versions
                           WHERE capability_definition_id=?""",
                        (capability_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """INSERT INTO mp_parameter_contract_versions
                    (id,capability_definition_id,version_no,schema_json,ui_schema_json,content_hash,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (parameter_id, capability_id, parameter_version, _json(parameter_schema), _json(ui_schema), parameter_hash, now, now),
                )
            connection.execute(
                """INSERT OR IGNORE INTO mp_adapter_binding_contract_versions
                (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (binding_id, "LLAMA_CPP_MANAGED", "llama.chat.v1", 1, _json(binding), _hash(binding), now, now),
            )
            connection.execute(
                """INSERT OR IGNORE INTO mp_resource_policy_versions
                (id,code,version_no,policy_json,content_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)""",
                (resource_id, "llama-cpp-text-local", 1, _json(resource), _hash(resource), now, now),
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
        if row is None or installation is None or str(row["runtime_kind"]) != "LLAMA_CPP_MANAGED" or str(row["capability_code"]) not in _TEXT_CAPABILITIES:
            raise DomainRuleError("MP_PROFILE_SMOKE_IMPLEMENTATION_UNAVAILABLE", "该 Profile 没有已安装的真实托管 llama.cpp 文本 smoke 实现。")
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
        return verify_managed_llama_runtime_configuration(self.settings, configuration_json)

    def _default_client(self, base_url: str, model: str) -> LocalLLMClient:
        return LocalLLMClient(
            base_url,
            model,
            provider="LLAMA_CPP_MANAGED",
            allow_private_network=self.settings.network_mode.value == "LAN_SERVICE",
        )


def _profile_code(release_code: str, capability_code: str) -> str:
    return f"llama-cpp-text-{release_code}-{capability_code.lower().replace('_', '-')}"


def _profile_defaults(payload_json: str) -> dict[str, Any]:
    payload = _profile_payload(payload_json)
    defaults = payload.get("defaults")
    value = defaults if isinstance(defaults, dict) else {}
    return {str(key): item for key, item in value.items()}


def _profile_payload(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Profile payload 不是合法 JSON。") from error
    if not isinstance(parsed, dict) or not isinstance(parsed.get("runtime_model_installation_ids"), list) or not parsed["runtime_model_installation_ids"]:
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Profile payload 缺少模型安装绑定。")
    return parsed


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

"""Production Profile lifecycle for the controlled Qwen3 PyTorch embedding adapter."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_ai_subprocess import LocalAiSubprocessRuntime
from local_drama.model_platform.application.profile_publication import ProfilePublicationService, ProfileVersionDraft

_NATIVE_LOCATOR = "qwen3-embedding-8b"
_CAPABILITY = "EMBEDDING_TEXT"
_TEMPLATE = "pytorch.embedding.qwen3.profile.v1"


@dataclass(frozen=True, slots=True)
class ProvisionedPyTorchEmbeddingProfile:
    profile_version_id: str
    profile_code: str
    created: bool


@dataclass(frozen=True, slots=True)
class PyTorchEmbeddingProfileSmokeResult:
    validation_run_id: str
    profile_version_id: str
    status: str


class PyTorchEmbeddingProfileService:
    """Creates and validates only the declared local Qwen3 embedding Profile.

    The V2 endpoint never accepts an arbitrary executable, path, model name or
    subprocess argument.  This service binds the immutable Profile to the
    model-lock candidate that has already completed the real capability smoke.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        embedding_smoke_factory: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.embedding_smoke_factory = embedding_smoke_factory or self._default_embedding_smoke

    def provision(self, runtime_model_installation_id: str, capability_code: str) -> ProvisionedPyTorchEmbeddingProfile:
        candidate = self._candidate(runtime_model_installation_id, capability_code)
        contracts = self._ensure_contracts(str(candidate["capability_id"]))
        profile_code = "pytorch-qwen3-embedding-8b-embedding-text"
        payload = {
            "template": _TEMPLATE,
            "runtime_model_installation_ids": [str(candidate["runtime_model_installation_id"])],
            "defaults": {"instruction": "给定中文小说检索问题，找出能回答问题的世界观资料"},
            "allowed_override_fields": ["instruction"],
        }
        service = ProfilePublicationService(self.database)
        try:
            created = service.create_candidate(
                ProfileVersionDraft(
                    profile_code=profile_code,
                    profile_title="Qwen3 Embedding 8B · 文本向量化",
                    capability_definition_id=str(candidate["capability_id"]),
                    runtime_installation_version_id=str(candidate["runtime_version_id"]),
                    parameter_contract_version_id=contracts["parameter_contract_version_id"],
                    adapter_binding_contract_version_id=contracts["adapter_binding_contract_version_id"],
                    resource_policy_version_id=contracts["resource_policy_version_id"],
                    payload=payload,
                )
            )
            return ProvisionedPyTorchEmbeddingProfile(created.profile_version_id, profile_code, True)
        except DomainRuleError as error:
            if error.code != "MP_PROFILE_PAYLOAD_ALREADY_EXISTS":
                raise
            return ProvisionedPyTorchEmbeddingProfile(str(error.details["profile_version_id"]), profile_code, False)

    def smoke(self, profile_version_id: str) -> PyTorchEmbeddingProfileSmokeResult:
        profile = self._profile(profile_version_id)
        try:
            result = _redacted_receipt(self.embedding_smoke_factory())
        except (OSError, RuntimeError, ValueError):
            result = {"adapter": "pytorch.embedding.qwen3", "passed": False, "error_code": "PYTORCH_EMBEDDING_SMOKE_FAILED"}
        validation = ProfilePublicationService(self.database).record_validation(
            profile_version_id,
            validation_kind="PROFILE_SMOKE",
            status="SMOKE_PASSED" if result["passed"] else "FAILED",
            result={
                "payload_hash": str(profile["payload_hash"]),
                "source_capability_validation_run_id": self._source_smoke(profile),
                "output_contract_verified": result["passed"],
            },
            evidence=result,
        )
        return PyTorchEmbeddingProfileSmokeResult(validation.validation_run_id, profile_version_id, validation.status)

    def _candidate(self, runtime_model_installation_id: str, capability_code: str):
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT installation.id AS runtime_model_installation_id,installation.native_locator,
                          runtime.kind AS runtime_kind,runtime_version.id AS runtime_version_id,runtime_version.status AS runtime_status,
                          offering.validation_status,capability.id AS capability_id,capability.code AS capability_code
                   FROM mp_runtime_model_installations installation
                   JOIN mp_runtime_installation_versions runtime_version ON runtime_version.id=installation.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime ON runtime.id=runtime_version.runtime_installation_id
                   JOIN mp_capability_offerings offering ON offering.runtime_model_installation_id=installation.id
                   JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
                   WHERE installation.id=? AND capability.code=? AND installation.install_state='READY'""",
                (runtime_model_installation_id, capability_code.strip().upper()),
            ).fetchone()
        if (
            row is None
            or str(row["runtime_kind"]) != "PYTORCH_PROCESS"
            or str(row["native_locator"]) != _NATIVE_LOCATOR
            or str(row["capability_code"]) != _CAPABILITY
            or str(row["runtime_status"]) != "ACTIVE"
            or str(row["validation_status"]) != "SMOKE_PASSED"
        ):
            raise DomainRuleError("MP_PROFILE_OFFERING_NOT_READY", "Qwen3 Embedding Offering 尚未完成真实 smoke 或 Runtime 未激活。")
        return row

    def _profile(self, profile_version_id: str):
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT profile.id,profile.payload_json,profile.payload_hash,capability.code AS capability_code,
                          runtime_version.id AS runtime_version_id,runtime_version.status AS runtime_status,runtime.kind AS runtime_kind
                   FROM mp_execution_profile_versions profile
                   JOIN mp_capability_definitions capability ON capability.id=profile.capability_definition_id
                   JOIN mp_runtime_installation_versions runtime_version ON runtime_version.id=profile.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime ON runtime.id=runtime_version.runtime_installation_id
                   WHERE profile.id=?""",
                (profile_version_id,),
            ).fetchone()
            payload = _payload(row["payload_json"]) if row is not None else None
            installation = connection.execute(
                """SELECT native_locator FROM mp_runtime_model_installations
                   WHERE id=? AND runtime_installation_version_id=? AND install_state='READY'""",
                (payload["runtime_model_installation_ids"][0], row["runtime_version_id"]),
            ).fetchone() if row is not None and payload is not None else None
        if (
            row is None
            or installation is None
            or str(row["runtime_kind"]) != "PYTORCH_PROCESS"
            or str(row["runtime_status"]) != "ACTIVE"
            or str(row["capability_code"]) != _CAPABILITY
            or str(installation["native_locator"]) != _NATIVE_LOCATOR
            or payload is None
            or payload.get("template") != _TEMPLATE
        ):
            raise DomainRuleError("MP_PROFILE_SMOKE_IMPLEMENTATION_UNAVAILABLE", "该 Profile 没有已安装的 Qwen3 Embedding smoke 实现。")
        return dict(row)

    def _source_smoke(self, profile) -> str:
        payload = _payload(profile["payload_json"])
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT run.id FROM mp_validation_runs run
                   JOIN mp_capability_offerings offering ON offering.id=run.target_id
                   JOIN mp_runtime_model_installations installation ON installation.id=offering.runtime_model_installation_id
                   JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
                   WHERE run.target_kind='CAPABILITY_OFFERING' AND run.validation_kind='CAPABILITY_SMOKE'
                     AND run.status='SMOKE_PASSED' AND capability.code=? AND installation.id=?
                     AND EXISTS (SELECT 1 FROM mp_validation_evidence evidence WHERE evidence.validation_run_id=run.id)
                   ORDER BY run.finished_at DESC,run.created_at DESC,run.id DESC LIMIT 1""",
                (_CAPABILITY, payload["runtime_model_installation_ids"][0]),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_PROFILE_VALIDATION_SOURCE_INVALID", "Profile 绑定的 Embedding Offering 没有可用的真实 smoke 证据。")
        return str(row["id"])

    def _ensure_contracts(self, capability_id: str) -> dict[str, str]:
        parameter_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:pytorch-embedding:parameter:{capability_id}:v1"))
        binding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "localdramastudio:pytorch-embedding:binding:v1"))
        resource_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "localdramastudio:pytorch-embedding:resource:v1"))
        schema = {"type": "object", "properties": {"instruction": {"type": "string", "minLength": 1, "maxLength": 512}}}
        ui_schema = {"properties": {"instruction": {"scopes": ["RUN"]}}}
        binding = {"adapter_code": "pytorch.embedding.qwen3", "template": "v1", "transport": "LOCAL_PROCESS"}
        resource = {"network_policy": {"mode": "LOCAL_ONLY"}, "gpu_runtime": "PYTORCH", "exclusive_gpu": True}
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO mp_parameter_contract_versions (id,capability_definition_id,version_no,schema_json,ui_schema_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (parameter_id, capability_id, 1, _json(schema), _json(ui_schema), _hash(schema), now, now))
            connection.execute("INSERT OR IGNORE INTO mp_adapter_binding_contract_versions (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)", (binding_id, "PYTORCH_PROCESS", "pytorch.embedding.qwen3", 1, _json(binding), _hash(binding), now, now))
            connection.execute("INSERT OR IGNORE INTO mp_resource_policy_versions (id,code,version_no,policy_json,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", (resource_id, "pytorch-embedding-local", 1, _json(resource), _hash(resource), now, now))
        return {"parameter_contract_version_id": parameter_id, "adapter_binding_contract_version_id": binding_id, "resource_policy_version_id": resource_id}

    def _default_embedding_smoke(self) -> dict[str, object]:
        return LocalAiSubprocessRuntime(self.settings).run_task("embedding").payload


def _payload(value: object) -> dict[str, object]:
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Profile payload 不是合法 JSON。") from error
    identifiers = payload.get("runtime_model_installation_ids") if isinstance(payload, dict) else None
    if not isinstance(identifiers, list) or len(identifiers) != 1 or not isinstance(identifiers[0], str) or not identifiers[0].strip():
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Embedding Profile 必须且只能绑定一个模型安装。")
    return payload


def _redacted_receipt(receipt: dict[str, object]) -> dict[str, object]:
    semantic, unrelated = receipt.get("semantic_score"), receipt.get("unrelated_score")
    ordering = isinstance(semantic, (int, float)) and isinstance(unrelated, (int, float)) and float(semantic) > float(unrelated)
    passed = receipt.get("task") == "embedding" and receipt.get("status") == "PASS" and receipt.get("network_used") is False and receipt.get("dimension") == 4096 and isinstance(receipt.get("count"), int) and int(receipt["count"]) >= 3 and ordering
    return {"adapter": "pytorch.embedding.qwen3", "passed": passed, "dimension": receipt.get("dimension") if isinstance(receipt.get("dimension"), int) else None, "count": receipt.get("count") if isinstance(receipt.get("count"), int) else None, "semantic_ordering": ordering, "network_used": receipt.get("network_used") is False}


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

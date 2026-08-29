"""Capability-specific smoke validation with immutable, redacted evidence."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Protocol

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_ai_subprocess import LocalAiSubprocessRuntime
from local_drama.infrastructure.local_llm import LocalLLMClient

_OLLAMA_TEXT_CAPABILITIES = frozenset({
    "LLM_STORY_PARSE",
    "LLM_EPISODE_PLAN",
    "LLM_STORYBOARD",
    "LLM_PROMPT_REWRITE",
})
_PYTORCH_EMBEDDING_NATIVE_LOCATOR = "qwen3-embedding-8b"


class OllamaProbeClient(Protocol):
    def probe(self, *, load_test: bool = False) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class CapabilitySmokeResult:
    validation_run_id: str
    runtime_model_installation_id: str
    capability_code: str
    status: str
    installation_ready: bool
    runtime_active: bool


class CapabilitySmokeService:
    """Run only a declared, concrete smoke implementation.

    This is deliberately a small registry rather than a generic "test model"
    endpoint. A validator owns its runtime and capability set. Unsupported
    offerings fail closed, which prevents a file check or a chat probe from
    falsely validating ComfyUI, PyTorch, embedding, ASR, or media capability.
    The currently installed non-Ollama implementation is intentionally one
    narrow contract: the controlled Qwen3 Embedding 8B subprocess adapter.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        ollama_client_factory: Callable[[str, str], OllamaProbeClient] | None = None,
        embedding_smoke_factory: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.ollama_client_factory = ollama_client_factory or self._default_ollama_client
        self.embedding_smoke_factory = embedding_smoke_factory or self._default_embedding_smoke

    def smoke(self, runtime_model_installation_id: str, capability_code: str) -> CapabilitySmokeResult:
        candidate = self._candidate(runtime_model_installation_id, capability_code)
        if candidate["runtime_kind"] == "OLLAMA" and candidate["capability_code"] in _OLLAMA_TEXT_CAPABILITIES:
            return self._smoke_ollama(candidate)
        if (
            candidate["runtime_kind"] == "PYTORCH_PROCESS"
            and candidate["capability_code"] == "EMBEDDING_TEXT"
            and candidate["native_locator"] == _PYTORCH_EMBEDDING_NATIVE_LOCATOR
        ):
            return self._smoke_pytorch_embedding(candidate)
        raise DomainRuleError(
            "MP_CAPABILITY_SMOKE_IMPLEMENTATION_UNAVAILABLE",
            "该运行时/能力尚未安装真实 smoke 实现，不能用通用探针代替。",
            {"runtime_kind": str(candidate["runtime_kind"]), "capability_code": str(candidate["capability_code"])},
        )

    def _smoke_ollama(self, candidate) -> CapabilitySmokeResult:
        base_url = self._verified_ollama_base_url(str(candidate["configuration_json"]))
        try:
            probe = self.ollama_client_factory(base_url, str(candidate["native_locator"])).probe(load_test=True)
        except DomainRuleError as error:
            return self._record(
                candidate,
                status="FAILED",
                result={"error_code": error.code, "probe": {"network": False, "model": False, "inference": False}},
            )
        status = "SMOKE_PASSED" if probe.get("status") == "PASS" else "FAILED"
        return self._record(candidate, status=status, result=_redacted_probe(probe))

    def _smoke_pytorch_embedding(self, candidate) -> CapabilitySmokeResult:
        """Load the controlled local Qwen embedding adapter under service identity."""
        try:
            receipt = self.embedding_smoke_factory()
            result = _redacted_embedding_probe(receipt)
            status = "SMOKE_PASSED" if result["passed"] else "FAILED"
        except (OSError, RuntimeError, ValueError):
            status = "FAILED"
            result = {"adapter": "pytorch.embedding.qwen3", "passed": False, "error_code": "PYTORCH_EMBEDDING_SMOKE_FAILED"}
        return self._record(candidate, status=status, result=result)

    def _candidate(self, runtime_model_installation_id: str, capability_code: str):
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT installation.id AS runtime_model_installation_id,installation.native_locator,
                          runtime.kind AS runtime_kind,runtime_version.id AS runtime_installation_version_id,runtime_version.configuration_json,
                          offering.id AS offering_id,capability.code AS capability_code
                   FROM mp_runtime_model_installations installation
                   JOIN mp_runtime_installation_versions runtime_version
                     ON runtime_version.id=installation.runtime_installation_version_id
                   JOIN mp_runtime_installations runtime ON runtime.id=runtime_version.runtime_installation_id
                   JOIN mp_capability_offerings offering ON offering.runtime_model_installation_id=installation.id
                   JOIN mp_capability_definitions capability ON capability.id=offering.capability_definition_id
                   WHERE installation.id=? AND capability.code=?""",
                (runtime_model_installation_id, capability_code.strip().upper()),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_CAPABILITY_OFFERING_NOT_FOUND", "该已登记模型没有声明请求的能力。")
        return row

    def _verified_ollama_base_url(self, configuration_json: str) -> str:
        try:
            configuration = json.loads(configuration_json)
        except (TypeError, ValueError) as error:
            raise DomainRuleError("MP_RUNTIME_CONFIGURATION_INVALID", "Ollama Runtime 配置不是合法 JSON。") from error
        if not isinstance(configuration, dict) or configuration.get("provider") != "OLLAMA_LOOPBACK":
            raise DomainRuleError("MP_RUNTIME_CONFIGURATION_INVALID", "该 Runtime 不是受控 Ollama loopback 配置。")
        configured_url = str(configuration.get("base_url") or "").rstrip("/")
        service_url = self.settings.llm_base_url.rstrip("/")
        if not configured_url or configured_url != service_url:
            raise DomainRuleError(
                "MP_RUNTIME_CONFIGURATION_STALE",
                "候选 Runtime 与当前 Windows 服务身份配置不一致；请重新扫描后再验证。",
            )
        return service_url

    def _record(self, candidate, *, status: str, result: dict[str, object]) -> CapabilitySmokeResult:
        now = _utc_now()
        validation_run_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO mp_validation_runs
                (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
                VALUES (?, 'CAPABILITY_OFFERING', ?, 'CAPABILITY_SMOKE', ?, ?, ?, ?, ?, ?)""",
                (validation_run_id, candidate["offering_id"], status, _json(result), now, now, now, now),
            )
            connection.execute(
                """INSERT INTO mp_validation_evidence
                (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), validation_run_id, "CAPABILITY_SMOKE", _hash(result), _json(result), None, now, now),
            )
            connection.execute(
                "UPDATE mp_capability_offerings SET validation_status=?,updated_at=? WHERE id=?",
                (status, now, candidate["offering_id"]),
            )
            remaining = connection.execute(
                """SELECT COUNT(*) FROM mp_capability_offerings
                   WHERE runtime_model_installation_id=? AND validation_status!='SMOKE_PASSED'""",
                (candidate["runtime_model_installation_id"],),
            ).fetchone()[0]
            installation_ready = status == "SMOKE_PASSED" and int(remaining) == 0
            runtime_active = False
            if installation_ready:
                connection.execute(
                    "UPDATE mp_runtime_model_installations SET install_state='READY',updated_at=? WHERE id=?",
                    (now, candidate["runtime_model_installation_id"]),
                )
                runtime_remaining = connection.execute(
                    """SELECT COUNT(*) FROM mp_capability_offerings offering
                       JOIN mp_runtime_model_installations installation ON installation.id=offering.runtime_model_installation_id
                       WHERE installation.runtime_installation_version_id=? AND offering.validation_status!='SMOKE_PASSED'""",
                    (candidate["runtime_installation_version_id"],),
                ).fetchone()[0]
                runtime_active = int(runtime_remaining) == 0
                if runtime_active:
                    connection.execute(
                        "UPDATE mp_runtime_installation_versions SET status='ACTIVE',updated_at=? WHERE id=?",
                        (now, candidate["runtime_installation_version_id"]),
                    )
            elif status == "FAILED":
                connection.execute(
                    "UPDATE mp_runtime_model_installations SET install_state='VALIDATION_FAILED',updated_at=? WHERE id=?",
                    (now, candidate["runtime_model_installation_id"]),
                )
                connection.execute(
                    "UPDATE mp_runtime_installation_versions SET status='DEGRADED',updated_at=? WHERE id=?",
                    (now, candidate["runtime_installation_version_id"]),
                )
        return CapabilitySmokeResult(
            validation_run_id=validation_run_id,
            runtime_model_installation_id=str(candidate["runtime_model_installation_id"]),
            capability_code=str(candidate["capability_code"]),
            status=status,
            installation_ready=installation_ready,
            runtime_active=runtime_active,
        )

    def _default_ollama_client(self, base_url: str, model: str) -> LocalLLMClient:
        return LocalLLMClient(
            base_url,
            model,
            provider="OLLAMA_LOOPBACK",
            allow_private_network=self.settings.network_mode.value == "LAN_SERVICE",
        )

    def _default_embedding_smoke(self) -> dict[str, object]:
        return LocalAiSubprocessRuntime(self.settings).run_task("embedding").payload


def _redacted_probe(probe: dict[str, object]) -> dict[str, object]:
    levels = probe.get("probe_levels")
    safe_levels = {
        key: bool(value.get("passed"))
        for key, value in levels.items()
        if isinstance(key, str) and isinstance(value, dict)
    } if isinstance(levels, dict) else {}
    return {
        "probe": safe_levels,
        "model_present": bool(probe.get("model_present")),
        "load_test": bool(probe.get("load_test")),
        "error_code": probe.get("error_code") if isinstance(probe.get("error_code"), str) else None,
    }


def _redacted_embedding_probe(receipt: dict[str, object]) -> dict[str, object]:
    """Retain semantic smoke facts while excluding model paths and vectors."""
    dimension = receipt.get("dimension")
    count = receipt.get("count")
    semantic = receipt.get("semantic_score")
    unrelated = receipt.get("unrelated_score")
    semantic_ordering = (
        isinstance(semantic, (int, float))
        and isinstance(unrelated, (int, float))
        and float(semantic) > float(unrelated)
    )
    passed = (
        receipt.get("task") == "embedding"
        and receipt.get("status") == "PASS"
        and receipt.get("network_used") is False
        and dimension == 4096
        and isinstance(count, int)
        and count >= 3
        and semantic_ordering
    )
    return {
        "adapter": "pytorch.embedding.qwen3",
        "passed": passed,
        "dimension": dimension if isinstance(dimension, int) else None,
        "count": count if isinstance(count, int) else None,
        "semantic_ordering": semantic_ordering,
        "network_used": receipt.get("network_used") is False,
    }


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

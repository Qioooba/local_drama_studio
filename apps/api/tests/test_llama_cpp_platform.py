from __future__ import annotations

import json
import struct

import pytest

from local_drama.application.gpu_runtime import GpuRuntimeCoordinator
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.llama_server_manager import LlamaServerLaunchSpec
from local_drama.model_platform.application.capability_smoke import CapabilitySmokeService
from local_drama.model_platform.application.discovery_registration import DiscoveryRegistrationService
from local_drama.model_platform.application.llama_cpp_discovery import LlamaCppDiscoveryOrchestrator
from local_drama.model_platform.application.llama_cpp_text_profiles import LlamaCppTextProfileService
from local_drama.model_platform.application.profile_templates import ProfileTemplateService


class _PassingProbe:
    def probe(self, *, load_test: bool = False) -> dict[str, object]:
        return {
            "status": "PASS",
            "probe_levels": {
                "level_1_network": {"passed": True, "detail": "ok"},
                "level_2_auth": {"passed": True, "detail": "ok"},
                "level_3_model": {"passed": True, "detail": "ok"},
                "level_4_inference": {"passed": True, "detail": "ok"},
            },
            "model_present": True,
            "load_test": load_test,
        }


class _PassingProfileClient:
    def chat_json(self, _system: str, _user: str, images=None, *, json_schema=None, inference_options=None):
        return {"ready": True}


class _StubLlamaManager:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.running = False
        self.last_spec: LlamaServerLaunchSpec | None = None

    def is_running(self) -> bool:
        return self.running

    def start(self, spec: LlamaServerLaunchSpec) -> str:
        self.start_calls += 1
        self.last_spec = spec
        self.running = True
        return "http://127.0.0.1:8101"

    def stop(self) -> bool:
        self.stop_calls += 1
        self.running = False
        return True


class _StubComfy:
    """Loopback double so tests never contact the operator's real ComfyUI."""

    def queue(self) -> dict[str, object]:
        return {"queue_running": [], "queue_pending": []}

    def free_memory(self, **_kwargs: object) -> dict[str, object]:
        return {}

    def system_stats(self) -> dict[str, object]:
        total = 24 * 1024**3
        return {"devices": [{"vram_total": total, "vram_free": int(total * 0.95)}]}


class _UnavailableSystemProbe:
    """Keep platform tests independent from the host GPU's current load."""

    def snapshot(self) -> None:
        return None


def _managed_settings(workspace: Settings) -> Settings:
    """Managed settings whose derived endpoint has been computed by the validator.

    The launch-spec validation runs at adapter activation, so a valid binary
    and GGUF path must exist even though the manager itself is a stub.
    """

    llama_dir = workspace.data_root / "llama"
    llama_dir.mkdir(parents=True, exist_ok=True)
    bin_path = llama_dir / "llama-server.exe"
    bin_path.write_bytes(b"")
    model_path = llama_dir / "qwen3.8-27b.gguf"
    model_path.write_bytes(b"")
    return Settings(
        data_root=workspace.data_root,
        projects_root=workspace.projects_root,
        work_root=workspace.work_root,
        cache_root=workspace.cache_root,
        logs_root=workspace.logs_root,
        backups_root=workspace.backups_root,
        llm_provider="LLAMA_CPP_MANAGED",
        llm_model="qwen3.8-27b",
        llama_server_bin=bin_path,
        llama_model_path=model_path,
    )


def _managed_manager(database: Database, settings: Settings) -> tuple[GpuRuntimeCoordinator, _StubLlamaManager]:
    manager = _StubLlamaManager()
    coordinator = GpuRuntimeCoordinator(
        database,
        settings,
        comfy=_StubComfy(),
        ollama=None,
        llama_manager=manager,
        system_probe=_UnavailableSystemProbe(),
        sleep=lambda _seconds: None,
    )
    return coordinator, manager


def _gguf_kv_string(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _minimal_gguf(*, architecture: str = "Qwen3.8", name: str = "Qwen3.8 27B Instruct", file_type: int = 15) -> bytes:
    parts = [b"GGUF", struct.pack("<I", 3), struct.pack("<Q", 0)]
    entries = [
        ("general.architecture", 8, architecture),
        ("general.name", 8, name),
        ("general.file_type", 4, file_type),
    ]
    parts.append(struct.pack("<Q", len(entries)))
    for key, value_type, value in entries:
        parts.append(_gguf_kv_string(key))
        parts.append(struct.pack("<I", value_type))
        if value_type == 8:
            parts.append(_gguf_kv_string(str(value)))
        elif value_type == 4:
            parts.append(struct.pack("<I", int(value)))  # type: ignore[arg-type]
    return b"".join(parts)


def _seed_verified_llama_offering(database: Database, workspace: Settings) -> str:
    """Scan a fake GGUF, register it, and smoke all four text capabilities."""

    settings = _managed_settings(workspace)
    assert settings.llama_model_path is not None
    settings.llama_model_path.write_bytes(_minimal_gguf())
    orchestrator = LlamaCppDiscoveryOrchestrator(database, settings)
    run = orchestrator.scan()
    with database.connect() as connection:
        observation = connection.execute(
            "SELECT id FROM mp_discovery_observations WHERE discovery_run_id=?",
            (run.id,),
        ).fetchone()
    registered = DiscoveryRegistrationService(database).register(str(observation["id"]))
    coordinator, _manager = _managed_manager(database, settings)
    smoke = CapabilitySmokeService(
        database,
        settings,
        llama_client_factory=lambda _url, _model: _PassingProbe(),
        gpu_coordinator=coordinator,
    )
    for capability in ("LLM_STORY_PARSE", "LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE"):
        result = smoke.smoke(registered.runtime_model_installation_id, capability)
        assert result.status == "SMOKE_PASSED"
    return registered.runtime_model_installation_id


def test_llama_cpp_capability_smoke_activates_installation_under_gpu_lease(workspace, database) -> None:
    installation_id = _seed_verified_llama_offering(database, workspace)
    with database.connect() as connection:
        installation_state = connection.execute(
            "SELECT install_state FROM mp_runtime_model_installations WHERE id=?",
            (installation_id,),
        ).fetchone()["install_state"]
        runtime_state = connection.execute(
            """SELECT status FROM mp_runtime_installation_versions
               WHERE id=(SELECT runtime_installation_version_id FROM mp_runtime_model_installations WHERE id=?)""",
            (installation_id,),
        ).fetchone()["status"]
        evidence = connection.execute(
            """SELECT evidence.payload_json FROM mp_validation_evidence evidence
               JOIN mp_validation_runs run ON run.id=evidence.validation_run_id
               WHERE run.validation_kind='CAPABILITY_SMOKE' LIMIT 1""",
        ).fetchone()["payload_json"]
    assert installation_state == "READY"
    assert runtime_state == "ACTIVE"
    assert "127.0.0.1" not in evidence
    assert '"level_4_inference":true' in evidence


def test_verified_llama_cpp_offering_becomes_publishable_profile(workspace, database) -> None:
    installation_id = _seed_verified_llama_offering(database, workspace)
    settings = _managed_settings(workspace)
    coordinator, manager = _managed_manager(database, settings)

    service = LlamaCppTextProfileService(
        database,
        settings,
        client_factory=lambda _url, _model: _PassingProfileClient(),
        gpu_coordinator=coordinator,
    )
    provisioned = service.provision(installation_id, "LLM_STORY_PARSE")
    replay = service.provision(installation_id, "LLM_STORY_PARSE")
    assert provisioned.created is True
    assert replay.created is False
    assert replay.profile_version_id == provisioned.profile_version_id

    validation = service.smoke(provisioned.profile_version_id)
    assert validation.status == "SMOKE_PASSED"
    # The managed child stayed resident for the probe and was released after.
    assert manager.start_calls >= 1
    assert manager.running is False
    assert manager.last_spec is not None
    assert manager.last_spec.model_path == settings.llama_model_path
    assert manager.last_spec.alias == "qwen3.8-27b"

    service.publish(provisioned.profile_version_id, validation.validation_run_id, reason="llama test publish")
    with database.connect() as connection:
        publication = connection.execute(
            "SELECT status FROM mp_profile_publications WHERE execution_profile_version_id=?",
            (provisioned.profile_version_id,),
        ).fetchone()
        parameter_schema = connection.execute(
            """SELECT schema_json FROM mp_parameter_contract_versions
               WHERE id=(SELECT parameter_contract_version_id FROM mp_execution_profile_versions WHERE id=?)""",
            (provisioned.profile_version_id,),
        ).fetchone()["schema_json"]
    assert publication["status"] == "PUBLISHED"
    properties = json.loads(str(parameter_schema))["properties"]
    # The context window is fixed at launch; num_ctx must not be an override.
    assert set(properties) == {"temperature", "max_tokens"}


def test_profile_template_service_dispatches_llama_cpp_template(workspace, database) -> None:
    installation_id = _seed_verified_llama_offering(database, workspace)
    settings = _managed_settings(workspace)
    coordinator, _manager = _managed_manager(database, settings)

    template = ProfileTemplateService(database, settings)
    provisioned = template.provision(installation_id, "LLM_STORYBOARD")

    with database.connect() as connection:
        payload = connection.execute(
            "SELECT payload_json FROM mp_execution_profile_versions WHERE id=?",
            (provisioned.profile_version_id,),
        ).fetchone()["payload_json"]
    assert json.loads(str(payload))["template"] == "llama_cpp.text.profile.v1"

    # The smoke dispatch goes through the same public boundary.
    smoke_service = LlamaCppTextProfileService(
        database,
        settings,
        client_factory=lambda _url, _model: _PassingProfileClient(),
        gpu_coordinator=coordinator,
    )
    validation = smoke_service.smoke(provisioned.profile_version_id)
    assert validation.status == "SMOKE_PASSED"


def test_llama_cpp_profile_service_rejects_stale_endpoint(workspace, database, monkeypatch: pytest.MonkeyPatch) -> None:
    installation_id = _seed_verified_llama_offering(database, workspace)
    settings = _managed_settings(workspace)
    coordinator, _manager = _managed_manager(database, settings)
    service = LlamaCppTextProfileService(
        database,
        settings,
        client_factory=lambda _url, _model: _PassingProfileClient(),
        gpu_coordinator=coordinator,
    )
    provisioned = service.provision(installation_id, "LLM_STORY_PARSE")

    # Rewrite the frozen runtime configuration so its endpoint no longer
    # matches the current service identity.
    with database.transaction() as connection:
        connection.execute(
            """UPDATE mp_runtime_installation_versions SET configuration_json=?
               WHERE id=(SELECT runtime_installation_version_id FROM mp_execution_profile_versions WHERE id=?)""",
            (
                json.dumps({"provider": "LLAMA_CPP_MANAGED", "base_url": "http://127.0.0.1:9999"}),
                provisioned.profile_version_id,
            ),
        )
    with pytest.raises(Exception) as captured:
        service.smoke(provisioned.profile_version_id)
    assert "MP_RUNTIME_CONFIGURATION_STALE" in str(captured.value) or getattr(captured.value, "code", "") == "MP_RUNTIME_CONFIGURATION_STALE"

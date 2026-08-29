from __future__ import annotations

import json

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.candidate_readiness import CandidateReadinessService
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.capability_smoke import CapabilitySmokeService
from local_drama.model_platform.application.discovery import DiscoveryService
from local_drama.model_platform.application.discovery_registration import DiscoveryRegistrationService
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.installation_integrity import InstallationIntegrityService
from local_drama.model_platform.application.model_lock_discovery import ModelLockDiscoveryOrchestrator
from local_drama.model_platform.application.ollama_discovery import OllamaDiscoveryOrchestrator
from local_drama.model_platform.application.ollama_text_profiles import OllamaTextProfileService
from local_drama.model_platform.application.profile_catalog import ProfileCatalogService
from local_drama.model_platform.application.profile_publication import ProfilePublicationService
from local_drama.model_platform.application.pytorch_embedding_profiles import PyTorchEmbeddingProfileService
from local_drama.model_platform.application.runtime_adapters import ModelLockRuntimeAdapter, OllamaRuntimeAdapter
from local_drama.model_platform.domain.models import RuntimeKind
from local_drama.model_platform.domain.states import PresenceStatus, RuntimeStatus


class _Catalog:
    def __init__(self) -> None:
        self.show_calls: list[str] = []

    def tags(self):
        return [
            {"name": "qwen3.8:27b", "digest": "same", "size": 1, "details": {"family": "qwen"}},
            {"name": "nomic-embed-text", "digest": "new", "size": 2},
        ]

    def show(self, model: str):
        self.show_calls.append(model)
        return {"capabilities": ["embedding"], "details": {"parameter_size": "8B"}}


class _TextCatalog:
    def tags(self):
        return [{"name": "qwen3.8:27b", "digest": "text-digest", "size": 1, "details": {"family": "qwen"}}]

    def show(self, model: str):
        assert model == "qwen3.8:27b"
        return {"capabilities": ["completion"], "details": {"family": "qwen"}}


class _PassingOllamaProbe:
    def probe(self, *, load_test: bool = False):
        assert load_test is True
        return {
            "status": "PASS",
            "base_url": "http://127.0.0.1:11434",
            "model_present": True,
            "load_test": True,
            "probe_levels": {
                "level_1_network": {"passed": True},
                "level_2_auth": {"passed": True},
                "level_3_model": {"passed": True},
                "level_4_inference": {"passed": True},
            },
        }


class _PassingProfileClient:
    def chat_json(self, system: str, user: str, images=None, *, json_schema=None, inference_options=None):
        assert "profile smoke verifier" in system
        assert user == 'Return exactly {"ready": true}.'
        assert json_schema == {
            "type": "object",
            "properties": {"ready": {"type": "boolean"}},
            "required": ["ready"],
            "additionalProperties": False,
        }
        assert inference_options == {"temperature": 0.2, "max_tokens": 2048, "num_ctx": 8192}
        return {"ready": True}


def test_ollama_discovery_refreshes_only_new_or_changed_tags_and_never_publishes() -> None:
    catalog = _Catalog()

    report = OllamaRuntimeAdapter(catalog).discover(known_native_digests={"qwen3.8:27b": "same"})

    assert report.read_only is True
    assert report.runtime_status is RuntimeStatus.READY
    assert catalog.show_calls == ["nomic-embed-text"]
    observations = {item.native_locator: item for item in report.observations}
    qwen = observations["qwen3.8:27b"]
    embedding = observations["nomic-embed-text"]
    assert qwen.detail_refreshed is False
    assert embedding.detail_refreshed is True
    assert [candidate.capability for candidate in embedding.candidate_capabilities] == ["EMBEDDING_TEXT"]


def test_ollama_discovery_returns_structured_unreachable_evidence() -> None:
    class UnreachableCatalog:
        def tags(self):
            raise DomainRuleError("LOCAL_LLM_LOOPBACK_UNAVAILABLE", "Ollama 无法连接", {"reason": "URLError"})

        def show(self, model: str):  # pragma: no cover - discovery stops at tags
            raise AssertionError(model)

    report = OllamaRuntimeAdapter(UnreachableCatalog()).discover()

    assert report.runtime_status is RuntimeStatus.UNREACHABLE
    assert report.observations == ()
    assert report.evidence[0].code == "LOCAL_LLM_LOOPBACK_UNAVAILABLE"


def test_discovery_service_persists_only_immutable_discovery_evidence(database) -> None:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO mp_compute_nodes (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
            VALUES ('node-1','node-1','Node 1','node-fingerprint','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_runtime_installations (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at)
            VALUES ('runtime-1','node-1','ollama','OLLAMA','SERVICE_MANAGED','Ollama',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_runtime_installation_versions
            (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at)
            VALUES ('runtime-v1','runtime-1',1,'ollama.native','v1','LOOPBACK_HTTP','{}','runtime-fingerprint','ACTIVE',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )

    persisted = DiscoveryService(database).discover_and_record(
        OllamaRuntimeAdapter(_Catalog()), runtime_installation_version_id="runtime-v1", source="OLLAMA_API"
    )

    with database.connect() as connection:
        observations = connection.execute(
            "SELECT native_id, observed_json FROM mp_discovery_observations WHERE discovery_run_id=? ORDER BY native_id",
            (persisted.id,),
        ).fetchall()
        assert connection.execute("SELECT COUNT(*) FROM mp_runtime_model_installations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM mp_capability_offerings").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM mp_execution_profiles").fetchone()[0] == 0

    assert persisted.status == "SUCCEEDED"
    assert persisted.observation_count == 2
    assert [row["native_id"] for row in observations] == ["nomic-embed-text", "qwen3.8:27b"]


def test_ollama_orchestrator_registers_service_runtime_and_records_only_discovery(workspace, database) -> None:
    orchestrator = OllamaDiscoveryOrchestrator(database, workspace)

    first = orchestrator.scan(_Catalog())
    second = orchestrator.scan(_Catalog())

    with database.connect() as connection:
        versions = connection.execute(
            "SELECT adapter_code,status,configuration_json FROM mp_runtime_installation_versions ORDER BY created_at"
        ).fetchall()
        releases = connection.execute("SELECT COUNT(*) FROM mp_model_releases").fetchone()[0]
        profiles = connection.execute("SELECT COUNT(*) FROM mp_execution_profiles").fetchone()[0]
    assert first.status == "SUCCEEDED"
    assert second.observation_count == 2
    assert len(versions) == 1
    assert versions[0]["adapter_code"] == "ollama.chat.v1"
    assert versions[0]["status"] == "DRAFT"
    assert json.loads(versions[0]["configuration_json"])["provider"] == "OLLAMA_LOOPBACK"
    assert releases == 0
    assert profiles == 0


def test_discovery_registration_creates_only_an_unvalidated_candidate(workspace, database) -> None:
    run = OllamaDiscoveryOrchestrator(database, workspace).scan(_Catalog())
    with database.connect() as connection:
        observation = connection.execute(
            "SELECT id FROM mp_discovery_observations WHERE discovery_run_id=? AND native_id='qwen3.8:27b'",
            (run.id,),
        ).fetchone()

    registered = DiscoveryRegistrationService(database).register(str(observation["id"]))
    replay = DiscoveryRegistrationService(database).register(str(observation["id"]))

    with database.connect() as connection:
        release = connection.execute("SELECT code,format FROM mp_model_releases WHERE id=?", (registered.model_release_id,)).fetchone()
        offering_count = connection.execute(
            "SELECT COUNT(*) FROM mp_capability_offerings WHERE runtime_model_installation_id=? AND validation_status='NOT_RUN'",
            (registered.runtime_model_installation_id,),
        ).fetchone()[0]
        profile_count = connection.execute("SELECT COUNT(*) FROM mp_execution_profiles").fetchone()[0]
    assert registered.created is True
    assert replay.created is False
    assert replay.model_release_id == registered.model_release_id
    assert release["format"] == "OLLAMA_TAG"
    assert offering_count > 0
    assert profile_count == 0


def test_registered_candidate_readiness_is_capability_scoped_and_fails_closed(workspace, database) -> None:
    run = OllamaDiscoveryOrchestrator(database, workspace).scan(_Catalog())
    with database.connect() as connection:
        observation = connection.execute(
            "SELECT id FROM mp_discovery_observations WHERE discovery_run_id=? AND native_id='nomic-embed-text'",
            (run.id,),
        ).fetchone()

    DiscoveryRegistrationService(database).register(str(observation["id"]))
    candidates = CandidateReadinessService(database).list()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.model_release_code.startswith("ollama-")
    assert candidate.runtime_kind == "OLLAMA"
    assert candidate.install_state == "DISCOVERED"
    assert candidate.readiness_status == "VALIDATION_REQUIRED"
    assert candidate.assignable_capability_count == 0
    assert len(candidate.capabilities) == 1
    capability = candidate.capabilities[0]
    assert capability.code == "EMBEDDING_TEXT"
    assert capability.readiness_status == "VALIDATION_REQUIRED"
    assert capability.blockers == ("CAPABILITY_SMOKE_NOT_PASSED", "PROFILE_REQUIRED")


def test_ollama_text_capability_smoke_records_redacted_evidence_and_only_marks_complete_installation_ready(workspace, database) -> None:
    run = OllamaDiscoveryOrchestrator(database, workspace).scan(_TextCatalog())
    with database.connect() as connection:
        observation = connection.execute("SELECT id FROM mp_discovery_observations WHERE discovery_run_id=?", (run.id,)).fetchone()
    registered = DiscoveryRegistrationService(database).register(str(observation["id"]))
    service = CapabilitySmokeService(database, workspace, ollama_client_factory=lambda _url, _model: _PassingOllamaProbe())

    first = service.smoke(registered.runtime_model_installation_id, "LLM_STORY_PARSE")
    assert first.status == "SMOKE_PASSED"
    assert first.installation_ready is False
    for capability in ("LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE"):
        result = service.smoke(registered.runtime_model_installation_id, capability)

    with database.connect() as connection:
        offering_states = connection.execute(
            "SELECT DISTINCT validation_status FROM mp_capability_offerings WHERE runtime_model_installation_id=?",
            (registered.runtime_model_installation_id,),
        ).fetchall()
        installation_state = connection.execute(
            "SELECT install_state FROM mp_runtime_model_installations WHERE id=?",
            (registered.runtime_model_installation_id,),
        ).fetchone()["install_state"]
        runtime_state = connection.execute(
            "SELECT status FROM mp_runtime_installation_versions WHERE id=(SELECT runtime_installation_version_id FROM mp_runtime_model_installations WHERE id=?)",
            (registered.runtime_model_installation_id,),
        ).fetchone()["status"]
        evidence = connection.execute(
            """SELECT evidence.payload_json FROM mp_validation_evidence evidence
               JOIN mp_validation_runs run ON run.id=evidence.validation_run_id WHERE run.id=?""",
            (first.validation_run_id,),
        ).fetchone()["payload_json"]
    assert result.status == "SMOKE_PASSED"
    assert result.installation_ready is True
    assert result.runtime_active is True
    assert [row["validation_status"] for row in offering_states] == ["SMOKE_PASSED"]
    assert installation_state == "READY"
    assert runtime_state == "ACTIVE"
    assert "127.0.0.1" not in evidence
    assert '"level_4_inference":true' in evidence


def test_verified_ollama_offering_becomes_publishable_profile_and_executable_preview(workspace, database) -> None:
    run = OllamaDiscoveryOrchestrator(database, workspace).scan(_TextCatalog())
    with database.connect() as connection:
        observation = connection.execute("SELECT id FROM mp_discovery_observations WHERE discovery_run_id=?", (run.id,)).fetchone()
    registered = DiscoveryRegistrationService(database).register(str(observation["id"]))
    capability_smoke = CapabilitySmokeService(database, workspace, ollama_client_factory=lambda _url, _model: _PassingOllamaProbe())
    for capability in ("LLM_STORY_PARSE", "LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE"):
        capability_smoke.smoke(registered.runtime_model_installation_id, capability)

    profiles = OllamaTextProfileService(database, workspace, ollama_client_factory=lambda _url, _model: _PassingProfileClient())
    provisioned = profiles.provision(registered.runtime_model_installation_id, "LLM_STORY_PARSE")
    replay = profiles.provision(registered.runtime_model_installation_id, "LLM_STORY_PARSE")
    validation = profiles.smoke(provisioned.profile_version_id)
    smoke_catalog = ProfileCatalogService(database).list(runtime_model_installation_id=registered.runtime_model_installation_id)
    profiles.publish(provisioned.profile_version_id, validation.validation_run_id, "本机 Ollama 真实 Profile smoke 已通过")
    published_catalog = ProfileCatalogService(database).list(runtime_model_installation_id=registered.runtime_model_installation_id)

    preview = ExecutionPlanningService(database).preview(
        ExecutionPreviewRequest(
            capability_code="LLM_STORY_PARSE",
            scope=CapabilityScopeContext(),
            semantic_inputs={"project_id": "project-1"},
            run_overrides={"temperature": 0.3},
        )
    )
    with database.connect() as connection:
        publication = connection.execute(
            "SELECT status FROM mp_profile_publications WHERE execution_profile_version_id=?",
            (provisioned.profile_version_id,),
        ).fetchone()["status"]

    assert provisioned.created is True
    assert replay == type(replay)(provisioned.profile_version_id, provisioned.profile_code, False)
    assert validation.status == "SMOKE_PASSED"
    assert smoke_catalog[0].lifecycle_status == "PROFILE_SMOKE_PASSED"
    assert smoke_catalog[0].latest_validation_run_id == validation.validation_run_id
    assert smoke_catalog[0].runtime_model_installation_ids == (registered.runtime_model_installation_id,)
    assert published_catalog[0].lifecycle_status == "PUBLISHED"
    assert publication == "PUBLISHED"
    assert preview.executable is True
    assert preview.execution_profile_version_id == provisioned.profile_version_id
    assert preview.resolved_parameters["temperature"].value == 0.3


def test_capability_smoke_refuses_to_relabel_embedding_as_text_inference(workspace, database) -> None:
    run = OllamaDiscoveryOrchestrator(database, workspace).scan(_Catalog())
    with database.connect() as connection:
        observation = connection.execute(
            "SELECT id FROM mp_discovery_observations WHERE discovery_run_id=? AND native_id='nomic-embed-text'",
            (run.id,),
        ).fetchone()
    registered = DiscoveryRegistrationService(database).register(str(observation["id"]))

    try:
        CapabilitySmokeService(database, workspace, ollama_client_factory=lambda _url, _model: _PassingOllamaProbe()).smoke(
            registered.runtime_model_installation_id,
            "EMBEDDING_TEXT",
        )
    except DomainRuleError as error:
        assert error.code == "MP_CAPABILITY_SMOKE_IMPLEMENTATION_UNAVAILABLE"
    else:  # pragma: no cover - test needs a positive failure assertion
        raise AssertionError("Embedding must not pass using a text-only smoke implementation")


def test_model_lock_adapter_discovers_pytorch_bundles_without_exposing_absolute_paths(tmp_path) -> None:
    model_root = tmp_path / "model-root"
    artifact = model_root / "Services" / "Embedding" / "model.safetensors"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"1234")
    lock = tmp_path / "model-lock.json"
    lock.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "code": "qwen3-embedding-8b",
                        "runtime": "PYTORCH",
                        "files": [{"path": "Services/Embedding/model.safetensors", "bytes": 4, "format": "SAFETENSORS"}],
                    },
                    {
                        "code": "missing-asr",
                        "runtime": "PYTORCH",
                        "files": [{"path": "Services/ASR/model.safetensors", "bytes": 12, "format": "SAFETENSORS"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = ModelLockRuntimeAdapter(RuntimeKind.PYTORCH_PROCESS, lock, model_root).discover()

    assert report.runtime_status is RuntimeStatus.UNKNOWN
    embedding = next(item for item in report.observations if item.native_locator == "qwen3-embedding-8b")
    assert embedding.presence is PresenceStatus.PRESENT
    assert [candidate.capability for candidate in embedding.candidate_capabilities] == ["EMBEDDING_TEXT"]
    assert embedding.metadata["files"][0]["relative_path"] == "Services/Embedding/model.safetensors"
    assert str(model_root) not in json.dumps(embedding.metadata)
    assert next(item for item in report.observations if item.native_locator == "missing-asr").presence is PresenceStatus.MISSING


def test_model_lock_adapter_rejects_path_escape(tmp_path) -> None:
    lock = tmp_path / "model-lock.json"
    lock.write_text(
        json.dumps(
            {"models": [{"code": "unsafe", "runtime": "COMFY", "files": [{"path": "../secret", "bytes": 1, "format": "BIN"}]}]}
        ),
        encoding="utf-8",
    )

    report = ModelLockRuntimeAdapter(RuntimeKind.COMFYUI, lock, tmp_path / "models").discover()

    assert report.observations[0].presence is PresenceStatus.MISSING
    assert report.evidence[0].code == "MODEL_LOCK_PATH_INVALID"


def test_model_lock_adapter_maps_legacy_prefix_into_the_canonical_split_library(tmp_path) -> None:
    library_root = tmp_path / "models" / "libraries" / "pytorch"
    artifact = library_root / "Embedding" / "model.safetensors"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"1234")
    lock = tmp_path / "model-lock.json"
    lock.write_text(json.dumps({"models": [
        {"code": "qwen3-embedding-8b", "runtime": "PYTORCH", "files": [{"path": "Services/Embedding/model.safetensors", "bytes": 4, "format": "SAFETENSORS"}]},
    ]}), encoding="utf-8")

    report = ModelLockRuntimeAdapter(
        RuntimeKind.PYTORCH_PROCESS,
        lock,
        library_root,
        lock_path_prefix="Services",
    ).discover()

    observation = report.observations[0]
    assert observation.presence is PresenceStatus.PRESENT
    assert observation.metadata["files"][0]["relative_path"] == "Embedding/model.safetensors"
    assert str(library_root) not in json.dumps(observation.metadata)


def test_model_lock_orchestrator_maps_the_default_split_windows_libraries(workspace, database, tmp_path) -> None:
    libraries_root = tmp_path / "models" / "libraries"
    comfy_root = libraries_root / "comfyui"
    pytorch_root = libraries_root / "pytorch"
    ollama_root = libraries_root / "ollama"
    audio_root = libraries_root / "audio"
    (comfy_root / "models").mkdir(parents=True)
    (comfy_root / "models" / "concept.safetensors").write_bytes(b"1234")
    (pytorch_root / "Embedding").mkdir(parents=True)
    (pytorch_root / "Embedding" / "model.safetensors").write_bytes(b"1234")
    ollama_root.mkdir(parents=True)
    audio_root.mkdir(parents=True)
    lock = tmp_path / "model-lock.json"
    lock.write_text(json.dumps({"models": [
        {"code": "concept", "runtime": "COMFY", "files": [{"path": "ComfyUI/models/concept.safetensors", "bytes": 4, "format": "SAFETENSORS"}]},
        {"code": "embedding", "runtime": "PYTORCH", "files": [{"path": "Services/Embedding/model.safetensors", "bytes": 4, "format": "SAFETENSORS"}]},
    ]}), encoding="utf-8")
    settings = workspace.model_copy(update={"model_library_roots": (comfy_root, pytorch_root, ollama_root, audio_root)})

    result = ModelLockDiscoveryOrchestrator(database, settings, lock_path=lock).scan()

    with database.connect() as connection:
        observations = connection.execute(
            "SELECT kind,native_id FROM mp_discovery_observations ORDER BY kind,native_id"
        ).fetchall()
        libraries = connection.execute("SELECT root_path_local FROM mp_model_libraries ORDER BY code").fetchall()
    assert len(result.runs) == 2
    assert [(row["kind"], row["native_id"]) for row in observations] == [
        ("COMFYUI", "concept"),
        ("PYTORCH_PROCESS", "embedding"),
    ]
    assert {row["root_path_local"] for row in libraries} == {
        str(comfy_root.resolve()), str(pytorch_root.resolve()), str(ollama_root.resolve()), str(audio_root.resolve())
    }


def test_model_lock_orchestrator_scans_each_runtime_with_a_library_binding(workspace, database, tmp_path) -> None:
    library_root = tmp_path / "models"
    (library_root / "ComfyUI" / "models").mkdir(parents=True)
    (library_root / "ComfyUI" / "models" / "concept.safetensors").write_bytes(b"1234")
    (library_root / "Services" / "Embedding").mkdir(parents=True)
    (library_root / "Services" / "Embedding" / "model.safetensors").write_bytes(b"1234")
    lock = tmp_path / "model-lock.json"
    lock.write_text(json.dumps({"models": [
        {"code": "concept", "runtime": "COMFY", "files": [{"path": "ComfyUI/models/concept.safetensors", "bytes": 4, "format": "SAFETENSORS"}]},
        {"code": "embedding", "runtime": "PYTORCH", "files": [{"path": "Services/Embedding/model.safetensors", "bytes": 4, "format": "SAFETENSORS"}]},
    ]}), encoding="utf-8")
    settings = workspace.model_copy(update={"model_library_roots": (library_root,)})

    result = ModelLockDiscoveryOrchestrator(database, settings, lock_path=lock).scan()

    with database.connect() as connection:
        libraries = connection.execute("SELECT root_path_local FROM mp_model_libraries").fetchall()
        runtimes = connection.execute(
            """SELECT installation.kind,version.status
               FROM mp_runtime_installation_versions version
               JOIN mp_runtime_installations installation ON installation.id=version.runtime_installation_id
               ORDER BY version.adapter_code"""
        ).fetchall()
        observations = connection.execute("SELECT kind,native_id FROM mp_discovery_observations ORDER BY kind,native_id").fetchall()
    assert len(result.runs) == 2
    assert [row["root_path_local"] for row in libraries] == [str(library_root.resolve())]
    assert [(row["kind"], row["status"]) for row in runtimes] == [("COMFYUI", "DRAFT"), ("PYTORCH_PROCESS", "DRAFT")]
    assert [(row["kind"], row["native_id"]) for row in observations] == [("COMFYUI", "concept"), ("PYTORCH_PROCESS", "embedding")]


def test_model_lock_integrity_verification_rechecks_files_under_the_configured_service_library(workspace, database, tmp_path) -> None:
    library_root = tmp_path / "models"
    artifact = library_root / "Services" / "Embedding" / "model.safetensors"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"1234")
    lock = tmp_path / "model-lock.json"
    lock.write_text(json.dumps({"models": [
        {"code": "embedding", "runtime": "PYTORCH", "files": [{"path": "Services/Embedding/model.safetensors", "bytes": 4, "format": "SAFETENSORS"}]},
    ]}), encoding="utf-8")
    settings = workspace.model_copy(update={"model_library_roots": (library_root,)})
    result = ModelLockDiscoveryOrchestrator(database, settings, lock_path=lock).scan()
    with database.connect() as connection:
        observation = connection.execute(
            "SELECT id FROM mp_discovery_observations WHERE discovery_run_id=? AND native_id='embedding'",
            (result.runs[1].id,),
        ).fetchone()
    registered = DiscoveryRegistrationService(database).register(str(observation["id"]))

    verified = InstallationIntegrityService(database, settings, lock_path=lock).verify(registered.runtime_model_installation_id)

    with database.connect() as connection:
        installation = connection.execute(
            "SELECT install_state FROM mp_runtime_model_installations WHERE id=?",
            (registered.runtime_model_installation_id,),
        ).fetchone()["install_state"]
        evidence = connection.execute(
            """SELECT payload_json FROM mp_validation_evidence WHERE validation_run_id=?""",
            (verified.validation_run_id,),
        ).fetchone()["payload_json"]
    assert verified.status == "INTEGRITY_PASSED"
    assert verified.install_state == "INTEGRITY_VERIFIED"
    assert installation == "INTEGRITY_VERIFIED"
    assert "Services/Embedding/model.safetensors" in evidence
    assert str(library_root) not in evidence


def test_model_lock_integrity_uses_the_same_prefix_mapping_as_split_library_discovery(workspace, database, tmp_path) -> None:
    library_root = tmp_path / "models" / "libraries" / "pytorch"
    artifact = library_root / "Embedding" / "model.safetensors"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"1234")
    lock = tmp_path / "model-lock.json"
    lock.write_text(json.dumps({"models": [
        {"code": "embedding", "runtime": "PYTORCH", "files": [{"path": "Services/Embedding/model.safetensors", "bytes": 4, "format": "SAFETENSORS"}]},
    ]}), encoding="utf-8")
    settings = workspace.model_copy(update={"model_library_roots": (library_root,)})
    result = ModelLockDiscoveryOrchestrator(database, settings, lock_path=lock).scan()
    with database.connect() as connection:
        observation = connection.execute(
            "SELECT id FROM mp_discovery_observations WHERE discovery_run_id=? AND native_id='embedding'",
            (result.runs[0].id,),
        ).fetchone()
    registered = DiscoveryRegistrationService(database).register(str(observation["id"]))

    verified = InstallationIntegrityService(database, settings, lock_path=lock).verify(registered.runtime_model_installation_id)

    assert verified.status == "INTEGRITY_PASSED"
    assert verified.install_state == "INTEGRITY_VERIFIED"


def test_pytorch_embedding_capability_smoke_uses_offline_adapter_receipt_and_activates_runtime(workspace, database, tmp_path) -> None:
    library_root = tmp_path / "models"
    artifact = library_root / "Services" / "Embedding" / "model.safetensors"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"1234")
    lock = tmp_path / "model-lock.json"
    lock.write_text(json.dumps({"models": [
        {"code": "qwen3-embedding-8b", "runtime": "PYTORCH", "files": [{"path": "Services/Embedding/model.safetensors", "bytes": 4, "format": "SAFETENSORS"}]},
    ]}), encoding="utf-8")
    settings = workspace.model_copy(update={"model_library_roots": (library_root,)})
    result = ModelLockDiscoveryOrchestrator(database, settings, lock_path=lock).scan()
    with database.connect() as connection:
        observation = connection.execute(
            "SELECT id FROM mp_discovery_observations WHERE discovery_run_id=? AND native_id='qwen3-embedding-8b'",
            (result.runs[1].id,),
        ).fetchone()
    registered = DiscoveryRegistrationService(database).register(str(observation["id"]))
    InstallationIntegrityService(database, settings, lock_path=lock).verify(registered.runtime_model_installation_id)
    receipt = {
        "task": "embedding",
        "status": "PASS",
        "model": "F:/private/Services/Qwen3-Embedding-8B",
        "dimension": 4096,
        "count": 3,
        "semantic_score": 0.91,
        "unrelated_score": 0.12,
        "vectors": [[0.01, 0.02]],
        "network_used": False,
    }

    smoke = CapabilitySmokeService(database, settings, embedding_smoke_factory=lambda: receipt).smoke(
        registered.runtime_model_installation_id,
        "EMBEDDING_TEXT",
    )
    profiles = PyTorchEmbeddingProfileService(database, settings, embedding_smoke_factory=lambda: receipt)
    provisioned = profiles.provision(registered.runtime_model_installation_id, "EMBEDDING_TEXT")
    profile_smoke = profiles.smoke(provisioned.profile_version_id)
    ProfilePublicationService(database).publish(
        provisioned.profile_version_id,
        validation_run_id=profile_smoke.validation_run_id,
        reason="Embedding Profile smoke passed",
    )
    preview = ExecutionPlanningService(database).preview(
        ExecutionPreviewRequest(
            capability_code="EMBEDDING_TEXT",
            scope=CapabilityScopeContext(),
            semantic_inputs={"document_version_id": "document-1"},
            run_overrides={"instruction": "为本项目检索故事设定"},
        )
    )

    with database.connect() as connection:
        installation_state = connection.execute(
            "SELECT install_state FROM mp_runtime_model_installations WHERE id=?",
            (registered.runtime_model_installation_id,),
        ).fetchone()["install_state"]
        runtime_state = connection.execute(
            "SELECT status FROM mp_runtime_installation_versions WHERE id=(SELECT runtime_installation_version_id FROM mp_runtime_model_installations WHERE id=?)",
            (registered.runtime_model_installation_id,),
        ).fetchone()["status"]
        evidence = connection.execute(
            "SELECT payload_json FROM mp_validation_evidence WHERE validation_run_id=?",
            (smoke.validation_run_id,),
        ).fetchone()["payload_json"]
    assert smoke.status == "SMOKE_PASSED"
    assert smoke.installation_ready is True
    assert smoke.runtime_active is True
    assert installation_state == "READY"
    assert runtime_state == "ACTIVE"
    assert profile_smoke.status == "SMOKE_PASSED"
    assert preview.executable is True
    assert preview.execution_profile_version_id == provisioned.profile_version_id
    assert preview.resolved_parameters["instruction"].value == "为本项目检索故事设定"
    assert '"dimension":4096' in evidence
    assert "F:/private" not in evidence
    assert "vectors" not in evidence

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.gguf_discovery import GgufDirectoryRuntimeAdapter, parse_gguf_header
from local_drama.model_platform.domain.states import RuntimeStatus


def _gguf_kv_string(value: str) -> bytes:
    raw = value.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _minimal_gguf(
    *,
    architecture: str = "Qwen3.8",
    name: str = "Qwen3.8 27B Instruct",
    file_type: int = 15,
    include_tokenizer: bool = False,
) -> bytes:
    """Build a byte-exact minimal GGUF v3 container for parser tests."""

    parts = [b"GGUF", struct.pack("<I", 3), struct.pack("<Q", 0)]
    entries: list[tuple[str, int, object]] = [
        ("general.architecture", 8, architecture),
        ("general.name", 8, name),
        ("general.file_type", 4, file_type),
    ]
    if include_tokenizer:
        entries.append(("tokenizer.ggml.tokens", 9, ["a", "b", "c"]))
    parts.append(struct.pack("<Q", len(entries)))
    for key, value_type, value in entries:
        parts.append(_gguf_kv_string(key))
        parts.append(struct.pack("<I", value_type))
        if value_type == 8:
            parts.append(_gguf_kv_string(str(value)))
        elif value_type == 4:
            parts.append(struct.pack("<I", int(value)))  # type: ignore[arg-type]
        elif value_type == 9:
            items = value if isinstance(value, list) else []
            parts.append(struct.pack("<I", 8))
            parts.append(struct.pack("<Q", len(items)))
            for item in items:
                parts.append(_gguf_kv_string(str(item)))
    return b"".join(parts)


def test_parse_gguf_header_extracts_general_metadata(tmp_path: Path) -> None:
    model = tmp_path / "Qwen3.8-27B-UD-Q4_K_XL.gguf"
    model.write_bytes(_minimal_gguf() + b"\x00" * 1024)  # trailing weight bytes are ignored

    info = parse_gguf_header(model)
    assert info.architecture == "Qwen3.8"
    assert info.model_name == "Qwen3.8 27B Instruct"
    assert info.quantization == "Q4_K_M"
    assert info.is_vision_encoder is False


def test_parse_gguf_header_skips_large_tokenizer_arrays_without_buffering(tmp_path: Path) -> None:
    model = tmp_path / "with-vocab.gguf"
    payload = _minimal_gguf(include_tokenizer=True)
    model.write_bytes(payload)

    info = parse_gguf_header(model)
    assert info.architecture == "Qwen3.8"
    assert info.quantization == "Q4_K_M"


def test_parse_gguf_header_flags_clip_vision_encoders(tmp_path: Path) -> None:
    model = tmp_path / "mmproj.gguf"
    model.write_bytes(_minimal_gguf(architecture="clip", name="Qwen2.5-VL vision", file_type=32))

    info = parse_gguf_header(model)
    assert info.is_vision_encoder is True


def test_parse_gguf_header_rejects_non_gguf_and_truncated_files(tmp_path: Path) -> None:
    not_gguf = tmp_path / "fake.gguf"
    not_gguf.write_bytes(b"SDFL" + b"\x00" * 64)
    with pytest.raises(ValueError):
        parse_gguf_header(not_gguf)

    truncated = tmp_path / "truncated.gguf"
    truncated.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00")
    with pytest.raises(ValueError):
        parse_gguf_header(truncated)


def test_discover_reports_text_models_with_digest_and_skips_vision(tmp_path: Path) -> None:
    text_model = tmp_path / "Qwen3.8-27B-UD-Q4_K_XL.gguf"
    text_payload = _minimal_gguf() + b"\x01" * 2048
    text_model.write_bytes(text_payload)
    (tmp_path / "subdir").mkdir()
    vision_model = tmp_path / "subdir" / "mmproj-f16.gguf"
    vision_model.write_bytes(_minimal_gguf(architecture="clip", name="vision", file_type=1))
    broken = tmp_path / "broken.gguf"
    broken.write_bytes(b"NOPE" + b"\x00" * 32)

    report = GgufDirectoryRuntimeAdapter(tmp_path).discover()
    assert report.runtime_kind.value == "LLAMA_CPP_MANAGED"
    assert report.runtime_status is RuntimeStatus.READY
    assert [observation.native_locator for observation in report.observations] == ["Qwen3.8-27B-UD-Q4_K_XL.gguf"]
    observation = report.observations[0]
    assert observation.presence.value == "PRESENT"
    assert observation.digest == hashlib.sha256(text_payload).hexdigest()
    assert observation.metadata["family"] == "Qwen3.8"
    assert observation.metadata["quantization_level"] == "Q4_K_M"
    assert {item.capability for item in observation.candidate_capabilities} == {
        "LLM_STORY_PARSE", "LLM_EPISODE_PLAN", "LLM_STORYBOARD", "LLM_PROMPT_REWRITE",
    }
    codes = {item.code for item in report.evidence}
    assert "GGUF_VISION_ENCODER_SKIPPED" in codes
    assert "GGUF_FILE_UNREADABLE" in codes


def test_discover_reports_unreachable_root(tmp_path: Path) -> None:
    report = GgufDirectoryRuntimeAdapter(tmp_path / "missing").discover()
    assert report.runtime_status is RuntimeStatus.UNREACHABLE
    assert report.observations == ()
    assert report.evidence[0].code == "GGUF_ROOT_UNAVAILABLE"


def test_discover_empty_directory_is_unknown_not_ready(tmp_path: Path) -> None:
    report = GgufDirectoryRuntimeAdapter(tmp_path).discover()
    assert report.runtime_status is RuntimeStatus.UNKNOWN
    assert report.observations == ()


# -- orchestrator ------------------------------------------------------------


def test_orchestrator_requires_managed_provider(workspace: Settings) -> None:
    from local_drama.infrastructure.database.sqlite import Database
    from local_drama.model_platform.application.llama_cpp_discovery import LlamaCppDiscoveryOrchestrator

    database = Database(workspace.database_path)
    unmanaged = workspace.model_copy(update={"llm_provider": "OPENAI_COMPAT"})
    with pytest.raises(DomainRuleError) as caught:
        LlamaCppDiscoveryOrchestrator(database, unmanaged).ensure_runtime_version()
    assert caught.value.code == "MP_LLAMA_CPP_DISCOVERY_NOT_CONFIGURED"


def test_orchestrator_scans_gguf_directory_and_binds_runtime_version(workspace: Settings, tmp_path: Path) -> None:
    from scripts.migrate import migrate

    workspace.ensure_roots()
    migrate(workspace.database_path)
    from local_drama.infrastructure.database.sqlite import Database
    from local_drama.model_platform.application.llama_cpp_discovery import LlamaCppDiscoveryOrchestrator

    gguf_dir = tmp_path / "gguf"
    gguf_dir.mkdir()
    (gguf_dir / "qwen.gguf").write_bytes(_minimal_gguf())
    # Direct construction (not model_copy) so the dependent-settings validator
    # derives llm_base_url from the managed endpoint.
    managed = Settings(
        data_root=workspace.data_root,
        projects_root=workspace.projects_root,
        work_root=workspace.work_root,
        cache_root=workspace.cache_root,
        logs_root=workspace.logs_root,
        backups_root=workspace.backups_root,
        llm_provider="LLAMA_CPP_MANAGED",
        llama_model_path=gguf_dir / "qwen.gguf",
    )

    database = Database(workspace.database_path)
    orchestrator = LlamaCppDiscoveryOrchestrator(database, managed)
    run = orchestrator.scan()
    assert run.status == "SUCCEEDED"
    assert run.observation_count == 1

    # A second scan reuses the same runtime version (fingerprint identity).
    again = orchestrator.scan()
    assert again.id != run.id

    with database.connect() as connection:
        runtime = connection.execute("SELECT id,kind,code FROM mp_runtime_installations WHERE kind='LLAMA_CPP_MANAGED'").fetchone()
        version = connection.execute(
            """SELECT configuration_json,status FROM mp_runtime_installation_versions
               WHERE runtime_installation_id=?""",
            (runtime["id"],),
        ).fetchone()
        observation = connection.execute(
            "SELECT native_id,observed_json FROM mp_discovery_observations WHERE discovery_run_id=?",
            (run.id,),
        ).fetchone()
    assert runtime["code"] == "llama-cpp.managed"
    configuration = json.loads(str(version["configuration_json"]))
    assert configuration["provider"] == "LLAMA_CPP_MANAGED"
    assert configuration["base_url"] == "http://127.0.0.1:8101"
    assert version["status"] == "DRAFT"
    assert str(observation["native_id"]).endswith("qwen.gguf")
    observed = json.loads(str(observation["observed_json"]))
    assert observed["presence"] == "PRESENT"


# -- execution handler --------------------------------------------------------


class _Snapshot:
    def __init__(self, settings: Settings, **overrides: object) -> None:
        from local_drama.model_platform.application.llama_cpp_discovery import managed_llama_runtime_configuration

        values: dict[str, object] = {
            "capability_code": "LLM_STORYBOARD",
            "adapter_code": "llama.chat.v1",
            "network_policy": {"mode": "LOCAL_ONLY"},
            "runtime_configuration": managed_llama_runtime_configuration(settings),
            "model_bindings": [{"native_locator": "qwen3.8-27b.gguf"}],
            "semantic_inputs": {"system_prompt": "system", "user_prompt": "user"},
            "execution_binding": {"json_schema": {"type": "object"}},
            "resolved_parameters": {"temperature": 0.2, "max_tokens": 64},
        }
        values.update(overrides)
        self.__dict__.update(values)


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def chat_json(self, system: str, user: str, images=None, *, json_schema=None, inference_options=None):
        self.calls.append({"system": system, "user": user, "json_schema": json_schema, "options": inference_options})
        return {"ready": True}


def test_llama_text_handler_executes_frozen_snapshot(workspace: Settings, tmp_path: Path) -> None:
    from local_drama.model_platform.application.llama_cpp_text_execution import make_llama_cpp_text_handler

    output_root = workspace.work_root / "jobs" / "llama-test"
    output_root.mkdir(parents=True, exist_ok=True)
    client = _StubClient()
    settings = workspace.model_copy(update={"llm_provider": "LLAMA_CPP_MANAGED", "llm_base_url": "http://127.0.0.1:8101"})
    handler = make_llama_cpp_text_handler(settings, client_factory=lambda _model: client)
    kind, relative = handler(_Snapshot(settings), output_root)
    assert kind == "LLAMA_TEXT_RESULT"
    assert relative == "jobs/llama-test/llama/result.json"
    assert client.calls[0]["json_schema"] == {"type": "object"}
    artifact = json.loads((output_root / "llama" / "result.json").read_text(encoding="utf-8"))
    assert artifact["schema"] == "localdramastudio.llama-text-result.v1"


def test_llama_text_handler_rejects_stale_endpoint(workspace: Settings, tmp_path: Path) -> None:
    from local_drama.model_platform.application.llama_cpp_text_execution import make_llama_cpp_text_handler

    output_root = workspace.work_root / "jobs" / "llama-stale"
    output_root.mkdir(parents=True, exist_ok=True)
    frozen_settings = workspace.model_copy(update={"llm_provider": "LLAMA_CPP_MANAGED", "llm_base_url": "http://127.0.0.1:8101"})
    current_settings = frozen_settings.model_copy(update={"llm_base_url": "http://127.0.0.1:9999"})
    handler = make_llama_cpp_text_handler(current_settings, client_factory=lambda _model: _StubClient())
    with pytest.raises(DomainRuleError) as caught:
        handler(_Snapshot(frozen_settings), output_root)
    assert caught.value.code == "MP_LLAMA_TEXT_RUNTIME_STALE"


def test_llama_text_handler_rejects_wrong_adapter(workspace: Settings, tmp_path: Path) -> None:
    from local_drama.model_platform.application.llama_cpp_text_execution import make_llama_cpp_text_handler

    output_root = workspace.work_root / "jobs" / "llama-wrong"
    output_root.mkdir(parents=True, exist_ok=True)
    handler = make_llama_cpp_text_handler(workspace, client_factory=lambda _model: _StubClient())
    with pytest.raises(DomainRuleError) as caught:
        handler(_Snapshot(workspace, adapter_code="ollama.chat.v1"), output_root)
    assert caught.value.code == "MP_LLAMA_TEXT_SNAPSHOT_MISMATCH"


def test_worker_resolves_profile_model_locator_before_gpu_activation(workspace: Settings) -> None:
    from local_drama.application.worker import llama_cpp_activation_context

    snapshot = _Snapshot(
        workspace,
        model_bindings=[{"native_locator": "nested/Qwen3.8-27B-UD-Q4_K_XL.gguf"}],
    )

    assert llama_cpp_activation_context(snapshot) == {
        "model_locator": "nested/Qwen3.8-27B-UD-Q4_K_XL.gguf"
    }


def test_worker_rejects_ambiguous_llama_model_bindings(workspace: Settings) -> None:
    from local_drama.application.worker import llama_cpp_activation_context

    snapshot = _Snapshot(
        workspace,
        model_bindings=[{"native_locator": "a.gguf"}, {"native_locator": "b.gguf"}],
    )

    with pytest.raises(DomainRuleError) as caught:
        llama_cpp_activation_context(snapshot)

    assert caught.value.code == "MP_LLAMA_TEXT_MODEL_BINDING_INVALID"

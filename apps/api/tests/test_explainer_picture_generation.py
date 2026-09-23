"""Explainer picture generation: the real-generation path and its honest fallbacks.

These tests never call a GPU.  They pin the two things that decide whether a
delivered frame is a *generated picture* or a typeset card: the geometry the model
is asked for, the semantic inputs the workflow receives, and the coded failure that
makes the caller degrade instead of claiming a generated shot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from local_drama.application.explainers.picture_generation import (
    ExplainerPictureGenerationError,
    ExplainerPictureGenerationRuntime,
    generation_geometry,
)
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database


def _seed_binding(database: Database, *, published: bool = True) -> str:
    """Insert one real image-generation profile version binding, returning its id."""

    profile_id = "mp-profile-1"
    runtime_id = "mp-runtime-1"
    workflow_id = "wf-image-1"
    profile_version_id = "mp-profile-version-1"
    capability_id = "cap-image-concept"
    with database.transaction() as connection:
        # The capability vocabulary is seeded by the migrations, so reuse the real
        # IMAGE_CONCEPT definition instead of inventing a second one.
        existing = connection.execute(
            "SELECT id FROM mp_capability_definitions WHERE code='IMAGE_CONCEPT'"
        ).fetchone()
        if existing is not None:
            capability_id = str(existing["id"])
        else:
            connection.execute(
                "INSERT INTO mp_capability_definitions (id, code, title, family, input_modalities_json, "
                "output_modalities_json, business_surfaces_json, background_only, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (capability_id, "IMAGE_CONCEPT", "概念图", "IMAGE", '["TEXT"]', '["IMAGE"]', "[]", 0, "2026-01-01", "2026-01-01"),
            )
        connection.execute(
            "INSERT INTO mp_execution_profiles (id, code, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (profile_id, "comfy-qwen-image", "Qwen-Image", "2026-01-01", "2026-01-01"),
        )
        # The three contract rows the profile version points at.  Real installs get
        # them from the model-platform installer; the contract under test only needs
        # them to exist so the binding is well formed.
        connection.execute(
            "INSERT INTO mp_parameter_contract_versions (id, capability_definition_id, version_no, schema_json, "
            "ui_schema_json, content_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("param-1", capability_id, 1, "{}", "{}", "hash-param", "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO mp_adapter_binding_contract_versions (id, runtime_kind, adapter_code, version_no, "
            "binding_json, content_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("adapter-1", "COMFYUI", "comfy.workflow.v1", 1, "{}", "hash-adapter", "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO mp_resource_policy_versions (id, code, version_no, policy_json, content_hash, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?)",
            ("resource-1", "EXPLAINER_PICTURE", 1, "{}", "hash-resource", "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO mp_compute_nodes (id, code, display_name, fingerprint, host_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("node-1", "local", "本机", "fp-node", "{}", "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO mp_runtime_installations (id, node_id, code, kind, owner_mode, display_name, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?)",
            ("runtime-install-1", "node-1", "comfyui-qwen21", "COMFYUI", "SERVICE", "Qwen-Image 运行时", "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO mp_runtime_installation_versions (id, runtime_installation_id, version_no, adapter_code, "
            "adapter_version, transport, configuration_json, fingerprint, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                runtime_id,
                "runtime-install-1",
                1,
                "comfy.workflow.v1",
                "v1",
                "LOOPBACK_HTTP",
                '{"base_url":"http://127.0.0.1:8189","output_root":"work/comfy/output","runtime_code":"comfyui-qwen21","model_code":"qwen-image"}',
                "fp",
                "ACTIVE",
                "2026-01-01",
                "2026-01-01",
            ),
        )
        connection.execute(
            "INSERT INTO mp_execution_profile_versions (id, profile_id, version_no, capability_definition_id, "
            "runtime_installation_version_id, parameter_contract_version_id, adapter_binding_contract_version_id, "
            "resource_policy_version_id, workflow_version_id, payload_json, payload_hash, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                profile_version_id,
                profile_id,
                1,
                capability_id,
                runtime_id,
                "param-1",
                "adapter-1",
                "resource-1",
                workflow_id,
                '{"model_code":"qwen-image"}',
                "hash",
                "2026-01-01",
                "2026-01-01",
            ),
        )
        if published:
            connection.execute(
                "INSERT INTO mp_capability_assignments (id, scope_type, scope_id, capability_definition_id, "
                "resolution_mode, execution_profile_version_id, override_json, revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("assign-1", "SYSTEM", "", capability_id, "EXPLICIT", profile_version_id, "{}", 1, "2026-01-01", "2026-01-01"),
            )
    return profile_version_id


class _FakeComposer:
    def __init__(self) -> None:
        self.compiled: list[dict[str, Any]] = []

    def get_version(self, version_id: str) -> dict[str, Any]:
        return {"workflow": {"1": {"class_type": "UNETLoader", "inputs": {}}}, "id": version_id}

    def compile_semantic_inputs(self, version_id: str, semantic_inputs: dict[str, Any]) -> dict[str, Any]:
        self.compiled.append(dict(semantic_inputs))
        return {"workflow": {"4": {"inputs": dict(semantic_inputs)}}, "compiled_hash": "compiled"}


class _FakeComfy:
    def __init__(self, *, output: Path | None, fail_queue: bool = False, status: str = "success") -> None:
        self.output = output
        self.fail_queue = fail_queue
        self.status = status
        self.queued: list[dict[str, Any]] = []
        self.waited: list[str] = []

    def object_info(self) -> dict[str, Any]:
        return {name: {} for name in ("UNETLoader", "CLIPLoader", "VAELoader", "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage")}

    def queue_prompt(self, workflow: dict[str, Any], *, client_id: str | None = None) -> dict[str, Any]:
        if self.fail_queue:
            raise RuntimeError("loopback refused")
        self.queued.append({"workflow": workflow, "client_id": client_id})
        return {"prompt_id": "prompt-1"}

    def wait_history(self, prompt_id: str, *, timeout_seconds: float = 30.0, poll_seconds: float = 0.5) -> dict[str, Any]:
        self.waited.append(prompt_id)
        return {"status": self.status, "history": {"outputs": {}}}

    def collect_outputs(self, history_item: dict[str, Any]) -> list[Path]:
        if self.output is None:
            raise RuntimeError("no output file")
        return [self.output]


def _runtime(
    database: Database, settings: Settings, comfy: _FakeComfy, composer: _FakeComposer | None = None
) -> ExplainerPictureGenerationRuntime:
    return ExplainerPictureGenerationRuntime(
        database,
        settings,
        composer=composer or _FakeComposer(),
        comfy_client_factory=lambda binding: comfy,
    )


def test_generation_geometry_rounds_onto_the_model_grid() -> None:
    """The model is asked for a grid-aligned canvas of the same aspect ratio."""

    assert generation_geometry(854, 480) == (864, 480)
    assert generation_geometry(480, 854) == (480, 864)
    assert generation_geometry(1920, 1080) == (1920, 1088)


def test_resolve_binding_prefers_the_frozen_snapshot_profile(
    database: Database, workspace: Settings
) -> None:
    """The profile the preflight promised is the profile that executes."""

    _seed_binding(database)
    runtime = _runtime(database, workspace, _FakeComfy(output=None))
    snapshot = {
        "capabilities": [
            {"capability": "image.text_to_image", "available": True, "profile_version_id": "mp-profile-version-1"}
        ]
    }
    binding = runtime.resolve_binding(snapshot)
    assert binding["profile_version_id"] == "mp-profile-version-1"
    assert binding["workflow_version_id"] == "wf-image-1"
    assert binding["base_url"] == "http://127.0.0.1:8189"
    assert binding["model_code"] == "qwen-image"


def test_resolve_binding_reports_unavailable_without_a_published_profile(
    database: Database, workspace: Settings
) -> None:
    runtime = _runtime(database, workspace, _FakeComfy(output=None))
    probe = runtime.probe(None)
    assert probe["available"] is False
    assert probe["reason"] == "PICTURE_PROFILE_UNAVAILABLE"


def test_probe_reports_missing_nodes_before_any_generation(
    database: Database, workspace: Settings
) -> None:
    _seed_binding(database)

    class _EmptyNodes(_FakeComfy):
        def object_info(self) -> dict[str, Any]:
            return {}

    probe = _runtime(database, workspace, _EmptyNodes(output=None)).probe(None)
    assert probe["available"] is False
    assert probe["reason"] == "PICTURE_NODES_MISSING"
    assert "UNETLoader" in probe["detail"]["missing"]


def test_generate_image_passes_the_declared_semantic_inputs(
    database: Database, workspace: Settings, tmp_path: Path
) -> None:
    """PROMPT/SEED/WIDTH/HEIGHT/STEPS reach the workflow; the file is hashed."""

    _seed_binding(database)
    image = tmp_path / "beat.png"
    image.write_bytes(b"fake-png-bytes")
    comfy = _FakeComfy(output=image)
    composer = _FakeComposer()
    runtime = _runtime(database, workspace, comfy, composer)
    binding = runtime.resolve_binding(None)

    result = runtime.generate_image(
        binding=binding,
        prompt="a deep sea cross section",
        width=854,
        height=480,
        seed=42,
        negative_prompt="text, watermark",
        steps=17,
    )

    sent = composer.compiled[0]
    assert sent["PROMPT"] == "a deep sea cross section"
    assert sent["SEED"] == 42
    assert (sent["WIDTH"], sent["HEIGHT"]) == (864, 480)
    assert sent["STEPS"] == 17
    assert sent["NEGATIVE_PROMPT"] == "text, watermark"
    assert result["path"] == image
    assert result["generation_width"] == 864
    assert result["model_code"] == "qwen-image"
    assert len(str(result["sha256"])) == 64
    assert comfy.waited == ["prompt-1"]


def test_generate_image_fails_with_a_code_the_caller_can_record(
    database: Database, workspace: Settings
) -> None:
    """A rejected prompt, a timeout and a missing file are distinct diagnoses."""

    _seed_binding(database)
    binding = _runtime(database, workspace, _FakeComfy(output=None)).resolve_binding(None)

    with pytest.raises(ExplainerPictureGenerationError) as rejected:
        _runtime(database, workspace, _FakeComfy(output=None, fail_queue=True)).generate_image(
            binding=binding, prompt="x", width=854, height=480, seed=1
        )
    assert rejected.value.code == "PICTURE_PROMPT_REJECTED"

    with pytest.raises(ExplainerPictureGenerationError) as failed:
        _runtime(database, workspace, _FakeComfy(output=None, status="error")).generate_image(
            binding=binding, prompt="x", width=854, height=480, seed=1
        )
    assert failed.value.code == "PICTURE_GENERATION_FAILED"

    with pytest.raises(ExplainerPictureGenerationError) as invalid:
        _runtime(database, workspace, _FakeComfy(output=None)).generate_image(
            binding=binding, prompt="x", width=854, height=480, seed=1
        )
    assert invalid.value.code == "PICTURE_OUTPUT_INVALID"

    with pytest.raises(ExplainerPictureGenerationError) as empty:
        _runtime(database, workspace, _FakeComfy(output=None)).generate_image(
            binding=binding, prompt="   ", width=854, height=480, seed=1
        )
    assert empty.value.code == "PICTURE_PROMPT_EMPTY"

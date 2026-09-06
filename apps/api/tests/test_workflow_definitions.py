from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.comfy_lab import ComfyLabService
from local_drama.application.workflow_definitions import WorkflowDefinitionService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def test_runtime_model_options_preserve_exact_runtime_filenames(workspace, monkeypatch):
    from local_drama.infrastructure.comfy import ComfyClient
    monkeypatch.setattr(ComfyClient, "object_info", lambda self: {
        "UnetLoaderGGUF": {"input": {"required": {"unet_name": [["Qwen\\edit.gguf"]]}}},
        "CLIPLoader": {"input": {"required": {"clip_name": [["Qwen\\clip.safetensors"]]}}},
        "VAELoader": {"input": {"required": {"vae_name": [["Qwen\\vae.safetensors"]]}}},
    })
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/workflow-definitions/QWEN_IDENTITY_3/runtime-options")
        assert response.status_code == 200, response.text
        fields = response.json()["fields"]
        assert fields["model"]["options"] == [{"value": "Qwen\\edit.gguf", "label": "Qwen\\edit.gguf"}]
        assert fields["text_encoder"]["options"][0]["value"] == "Qwen\\clip.safetensors"
        assert fields["vae"]["options"][0]["value"] == "Qwen\\vae.safetensors"
        assert "prompt" not in fields


class _SuccessfulDesigner:
    def queue_prompt(self, workflow, *, client_id):
        assert workflow
        assert client_id.startswith("local-drama-designer-")
        return {"prompt_id": "designer-prompt-1"}

    def wait_history(self, prompt_id, timeout_seconds):
        assert prompt_id == "designer-prompt-1"
        assert timeout_seconds == 240.0
        return {"prompt_id": prompt_id, "status": "success", "history": {}}


def test_workflow_definition_compiles_sdxl_on_server_with_distinct_negative_conditioning(workspace, database) -> None:
    service = WorkflowDefinitionService(workspace)
    compiled = service.instantiate("SDXL_T2I", {"prompt": "portrait", "negative_prompt": "blur", "seed": 9})
    workflow = compiled["workflow"]
    assert workflow["2"]["inputs"]["text"] == "portrait"
    assert workflow["3"]["inputs"]["text"] == "blur"
    assert workflow["5"]["inputs"]["positive"] == ["2", 0]
    assert workflow["5"]["inputs"]["negative"] == ["3", 0]
    assert compiled["node_bindings"]["NEGATIVE_PROMPT"] == {"node_id": "3", "input": "text"}

    with TestClient(create_app(workspace)) as client:
        definitions = client.get("/api/v1/workflow-definitions")
        assert definitions.status_code == 200
        assert {item["code"] for item in definitions.json()["items"]} >= {"H3_T2V", "H3_I2V", "SDXL_T2I"}
        created = client.post(
            "/api/v1/workflow-definitions/SDXL_T2I:instantiate",
            json={"code": "server-sdxl", "title": "Server SDXL", "parameters": {"prompt": "portrait", "seed": 9}},
        )
        assert created.status_code == 201, created.text
        version = created.json()["workflow_version"]
        assert version["contract"]["definition"] == {"code": "SDXL_T2I", "revision": 1}
        assert version["workflow"]["5"]["inputs"]["negative"] == ["3", 0]


def test_workflow_definition_rejects_unknown_page_fields(workspace) -> None:
    with pytest.raises(DomainRuleError) as error:
        WorkflowDefinitionService(workspace).instantiate("SDXL_T2I", {"made_up_node_option": True})
    assert error.value.code == "WORKFLOW_DEFINITION_FIELD_UNKNOWN"


def test_h3_definition_preserves_freeform_duration_and_sampling(workspace) -> None:
    compiled = WorkflowDefinitionService(workspace).instantiate(
        "H3_T2V",
        {
            "prompt": "freeform",
            "seed": 3,
            "aspect_ratio": "9:16",
            "use_production_tier": False,
            "duration_seconds": 4.0,
            "sigma_points": 7,
        },
    )
    assert compiled["workflow"]["7"]["inputs"]["steps"] == 7
    assert compiled["workflow"]["8"]["inputs"]["length"] == 107
    assert compiled["contract"]["production_tier"] is None
    assert compiled["contract"]["sampling_mode"] == "FREEFORM"
    assert compiled["contract"]["parameter_effects"]["tier"] == "INACTIVE_FREEFORM_MODE"

    tiered = WorkflowDefinitionService(workspace).instantiate("H3_T2V", {"prompt": "tiered", "seed": 4})
    assert tiered["contract"]["parameter_effects"]["duration_seconds"] == "INACTIVE_TIER_CONTROLS_FRAMES"
    assert tiered["contract"]["parameter_effects"]["sigma_points"] == "INACTIVE_TIER_CONTROLS_STEPS"


def test_comfy_lab_capture_requires_matching_pass_execution_before_promotion(workspace) -> None:
    service = ComfyLabService(workspace)
    workflow = {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "lab"}}}
    captured = service.capture(workflow, "tested capture")
    with pytest.raises(DomainRuleError) as error:
        service.promotable_capture(str(captured["capture_id"]))
    assert error.value.code == "COMFY_LAB_EXECUTION_TEST_REQUIRED"

    service._status = lambda: {"status": "RUNNING", "endpoint": "http://127.0.0.1:8188"}  # type: ignore[method-assign]
    evidence = service.test_run(None, capture_id=str(captured["capture_id"]), execute=True, client=_SuccessfulDesigner())
    assert evidence["status"] == "PASS"
    promotable = service.promotable_capture(str(captured["capture_id"]))
    assert promotable["content_hash"] == captured["content_hash"]
    test_file = workspace.work_root / "comfy-lab" / "captures" / f"{captured['capture_id']}.test.json"
    assert json.loads(test_file.read_text(encoding="utf-8"))["content_hash"] == captured["content_hash"]


def test_comfy_lab_rejects_a_capture_modified_after_freezing(workspace) -> None:
    service = ComfyLabService(workspace)
    captured = service.capture({"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "lab"}}}, "frozen")
    path = workspace.work_root / "comfy-lab" / "captures" / f"{captured['capture_id']}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["workflow"]["1"]["inputs"]["filename_prefix"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DomainRuleError) as error:
        service.get_capture(str(captured["capture_id"]))
    assert error.value.code == "COMFY_LAB_CAPTURE_HASH_MISMATCH"

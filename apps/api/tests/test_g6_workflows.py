from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.h3_workflows import H3WorkflowFactory
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation_contracts import CameraPlan, PerformanceBinding, TimedDirection, resolve_camera_plan
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.main import create_app


def _workflow() -> tuple[dict[str, object], dict[str, object]]:
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "fixture.png"}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "g6_fixture"}},
    }
    bindings = {"OUTPUT_PREFIX": {"node_id": "2", "input": "filename_prefix"}}
    return workflow, bindings


class _OfflineComfyContract:
    base_url = "http://127.0.0.1:8188"

    def object_info(self) -> dict[str, object]:
        return {"LoadImage": {}, "SaveImage": {}}


@pytest.mark.comfyui
def test_real_loopback_comfy_workflow_capture_compile_and_publish(workspace, database) -> None:
    workflow, bindings = _workflow()
    service = WorkflowService(database)
    version = service.register_package("g6_comfy_fixture", "G6 Comfy fixture", workflow, {"output": "IMAGE"}, bindings, {"transport": "LOOPBACK_HTTP"})
    package_path = workspace.work_root / str(version["package_rel_path"])
    assert package_path.is_file()
    package = json.loads(package_path.read_text(encoding="utf-8"))
    assert package["content_hash"] == version["content_hash"]
    assert not list(package_path.parent.glob(".partial-*"))
    compiled = service.compile_semantic_inputs(str(version["id"]), {"OUTPUT_PREFIX": "g6_compiled"})
    assert compiled["workflow"]["2"]["inputs"]["filename_prefix"] == "g6_compiled"
    validation = service.validate_against_comfy(str(version["id"]), ComfyClient())
    assert validation["status"] == "PASS"
    published = service.publish(str(version["id"]), str(validation["validation_id"]))
    assert published["status"] == "PUBLISHED"
    assert published["content_hash"] == version["content_hash"]

    with pytest.raises(DomainRuleError, match="semantic input slot"):
        service.compile_semantic_inputs(str(version["id"]), {"NODE_ID": "2"})


def test_workflow_registration_rejects_untrusted_custom_node(workspace, database) -> None:
    service = WorkflowService(database, workspace)
    with pytest.raises(DomainRuleError) as raised:
        service.register_package(
            "untrusted_custom_node",
            "Untrusted custom node",
            {"1": {"class_type": "ArbitraryInternetDownloaderNode", "inputs": {}}},
            {"output": "VIDEO"},
            {},
        )
    assert raised.value.code == "WORKFLOW_NODE_SUPPLY_CHAIN_UNTRUSTED"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM workflows WHERE code='untrusted_custom_node'").fetchone()[0] == 0


@pytest.mark.comfyui
def test_workflow_rejects_absolute_path_and_api_reads_real_comfy_stats(workspace, database) -> None:
    service = WorkflowService(database)
    with pytest.raises(DomainRuleError, match="绝对路径"):
        service.register_package(
            "g6_reject_path",
            "Reject path",
            {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "F:\\secret\\out"}}},
            {},
            {},
        )
    with TestClient(create_app(workspace)) as client:
        stats = client.get("/api/v1/comfy/system-stats")
        assert stats.status_code == 200
        version = str(stats.json()["system"]["system"]["comfyui_version"])
        assert version.count(".") >= 2 and version[0].isdigit()
        queue = client.get("/api/v1/comfy/queue")
        assert queue.status_code == 200


@pytest.mark.comfyui
def test_h3_factory_reads_manifest_and_compiles_real_candidate_workflow(workspace, database) -> None:
    factory = H3WorkflowFactory(workspace)
    assets = factory.candidate_assets()
    assert assets["model_root"] == "MiniMax-H3"
    assert assets["fl2va_unet_name"] == "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    assert assets["text_encoder_name"] == "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    layout = factory.runtime_layout()
    assert layout["status"] == "PASS"
    assert not layout["missing_model_files"]
    workflow = factory.build_t2va("a local test shot", seed=42, duration_seconds=4.0, sigma_points=2)
    assert workflow["1"]["class_type"] == "UNETLoader"
    assert workflow["8"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert workflow["14"]["inputs"]["format"] == "mp4"

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/workflow-packages:h3-candidate",
            json={"code": "g6_api_h3_candidate", "title": "H3 API candidate", "prompt": "A local test shot", "seed": 42},
        )
        assert response.status_code == 201
        assert response.json()["workflow_version"]["status"] == "DRAFT"

        i2v_response = client.post(
            "/api/v1/workflow-packages:h3-i2v-candidate",
            json={"code": "g6_api_h3_i2v", "title": "H3 I2V", "prompt": "A local test shot", "seed": 43},
        )
        assert i2v_response.status_code == 201
        i2v = i2v_response.json()["workflow_version"]
        assert i2v["workflow"]["5"]["class_type"] == "LoadImage"
        assert i2v["workflow"]["7"]["class_type"] == "MiniMaxH3ImageToVideo"
        assert i2v["node_bindings"]["FIRST_FRAME"] == {"node_id": "5", "input": "image"}

    fl2va = factory.build_fl2va("approved keyframe motion", first_frame="keyframe.png", seed=9, duration_seconds=4.0, sigma_points=2)
    assert fl2va["6"]["inputs"]["image"] == ["5", 0]
    assert fl2va["7"]["inputs"]["vae"] == ["3", 0]
    assert fl2va["16"]["inputs"]["format"] == "mp4"

    h3_service = WorkflowService(database, workspace)
    h3 = h3_service.register_package(
        "g6_h3_runtime_gate",
        "H3 runtime gate",
        workflow,
        {"capability": "H3_T2VA_CANDIDATE"},
        {},
    )
    validation = h3_service.validate_against_comfy(str(h3["id"]), ComfyClient())
    assert validation["status"] == "PASS"
    assert validation["runtime_layout"]["status"] == "PASS"
    published = h3_service.publish(str(h3["id"]), str(validation["validation_id"]))
    assert published["status"] == "PUBLISHED"


def test_local_llm_is_loopback_only_and_never_fakes_breakdown(workspace, database) -> None:
    with pytest.raises(DomainRuleError, match="loopback"):
        LocalLLMClient("http://example.com", "qwen3:8b")
    blocked = LocalLLMService(database, workspace).status()
    assert blocked["status"] == "BLOCKED"
    assert blocked["error_code"] == "LOCAL_LLM_MODEL_REQUIRED"

    configured = workspace.model_copy(update={"llm_model": "qwen3:8b"})
    candidate = LocalLLMService(database, configured).sync_candidate()
    assert candidate["status"] in {"CANDIDATE_BLOCKED", "CANDIDATE_UNVERIFIED"}
    assert candidate["probe"]["status"] in {"BLOCKED", "PASS"}
    with database.connect() as connection:
        row = connection.execute("SELECT status, capability FROM execution_profile_versions WHERE id=?", (candidate["profile_version_id"],)).fetchone()
    assert row["status"] == candidate["status"]
    assert row["capability"] == "SCRIPT_BREAKDOWN_LLM"


def test_local_llm_parser_accepts_real_reasoning_fence_and_array_shape() -> None:
    payload = LocalLLMClient._parse_json_content(
        "<think>private chain of thought</think>\n"
        "```json\n"
        '[{"scene_no":1,"title":"开场","summary":"相遇","characters":[],"shots":[]}]\n'
        "```"
    )
    assert isinstance(payload, list)
    assert payload[0]["scene_no"] == 1

    embedded = LocalLLMClient._parse_json_content('说明文字 {"scenes": []} trailing text')
    assert embedded == {"scenes": []}

    with pytest.raises(DomainRuleError, match="可提取"):
        LocalLLMClient._parse_json_content("<think>done</think>not-json")


def test_g6_camera_motion_contracts_and_workflow_rollback(workspace, database) -> None:
    native = resolve_camera_plan(native_supported=True, prompt_fallback_supported=True, shot_type="DOLLY", movement="slow push")
    assert native.mode == "NATIVE"
    fallback = resolve_camera_plan(native_supported=False, prompt_fallback_supported=True, shot_type="PAN", movement="left to right")
    assert fallback.mode == "PROMPT_FALLBACK"
    unsupported = resolve_camera_plan(native_supported=False, prompt_fallback_supported=False, shot_type="ORBIT", movement="orbit")
    assert unsupported.mode == "UNSUPPORTED"
    TimedDirection(0, "forward", 0.5).validate()
    PerformanceBinding("actor-1", "turn", 0, 1_000_000).validate()
    with pytest.raises(DomainRuleError, match="prompt fallback"):
        CameraPlan("PROMPT_FALLBACK", "PAN", "left", "").validate()

    service = WorkflowService(database)
    workflow, bindings = _workflow()
    first = service.register_package("g6_rollback_fixture", "Rollback fixture", workflow, {}, bindings)
    second = service.register_package("g6_rollback_fixture", "Rollback fixture", workflow, {}, bindings)
    second_validation = service.validate_against_comfy(str(second["id"]), _OfflineComfyContract())  # type: ignore[arg-type]
    first_validation = service.validate_against_comfy(str(first["id"]), _OfflineComfyContract())  # type: ignore[arg-type]
    service.publish(str(second["id"]), str(second_validation["validation_id"]))
    service.rollback(str(first["id"]), str(first_validation["validation_id"]))
    assert service.get_version(str(first["id"]))["status"] == "PUBLISHED"
    assert service.get_version(str(second["id"]))["status"] == "RETIRED"


def test_workflow_history_list_is_read_only_and_preserves_versions(workspace, database) -> None:
    service = WorkflowService(database)
    workflow, bindings = _workflow()
    first = service.register_package("g6_history_fixture", "History fixture", workflow, {"capability": "IMAGE"}, bindings)
    second = service.register_package("g6_history_fixture", "History fixture", workflow, {"capability": "IMAGE"}, bindings)
    validation = service.validate_against_comfy(str(second["id"]), _OfflineComfyContract())  # type: ignore[arg-type]
    service.publish(str(second["id"]), str(validation["validation_id"]))

    before = database.path.read_bytes()
    items = service.list_versions()
    after = database.path.read_bytes()
    history = [item for item in items if item["code"] == "g6_history_fixture"]

    assert [item["version_no"] for item in history] == [2, 1]
    assert history[0]["status"] == "PUBLISHED"
    assert history[1]["id"] == first["id"]
    assert history[0]["contract"]["capability"] == "IMAGE"
    assert before == after


def test_workflow_publish_rejects_client_forged_pass(workspace, database) -> None:
    service = WorkflowService(database)
    workflow, bindings = _workflow()
    version = service.register_package("g6_attestation_fixture", "Attestation fixture", workflow, {}, bindings)

    with pytest.raises(DomainRuleError, match="attestation"):
        service.publish(str(version["id"]), "forged-pass")

    validation = service.validate_against_comfy(str(version["id"]), _OfflineComfyContract())  # type: ignore[arg-type]
    assert validation["validation_id"]
    assert service.publish(str(version["id"]), str(validation["validation_id"]))["status"] == "PUBLISHED"

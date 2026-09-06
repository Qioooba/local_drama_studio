from local_drama.domain.comfy_schema_validation import validate_comfy_inputs
from local_drama.infrastructure.comfy_diagnostics import summarize_node_errors


def test_runtime_model_names_are_exact_but_bound_media_is_deferred():
    graph = {
        "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "Qwen/model.safetensors"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "runtime/identity-1.png"}},
    }
    schema = {
        "CLIPLoader": {"input": {"required": {"clip_name": [["Qwen\\model.safetensors"]]}}},
        "LoadImage": {"input": {"required": {"image": [["existing.png"]]}}},
    }
    bindings = {"REFERENCE_IMAGE_1": {"node_id": "2", "input": "image"}}
    errors = validate_comfy_inputs(graph, schema, bindings)
    assert errors == [{"node_id": "1", "class_type": "CLIPLoader", "input": "clip_name", "error": "VALUE_NOT_IN_LIST", "value": "Qwen/model.safetensors", "allowed_values": ["Qwen\\model.safetensors"]}]
    graph["1"]["inputs"]["clip_name"] = "Qwen\\model.safetensors"
    assert validate_comfy_inputs(graph, schema, bindings) == []
    assert validate_comfy_inputs(graph, schema, {})[0]["node_id"] == "2"


def test_missing_required_and_v3_combo_literals_are_checked():
    graph = {"1": {"class_type": "Sampler", "inputs": {"mode": "wrong"}}}
    schema = {"Sampler": {"input": {"required": {"seed": ["INT"], "mode": ["COMBO", {"options": ["valid"]}]}}}}
    assert [error["error"] for error in validate_comfy_inputs(graph, schema, {})] == ["REQUIRED_INPUT_MISSING", "VALUE_NOT_IN_LIST"]


def test_runtime_error_retains_model_choices_but_redacts_prompt_and_absolute_paths():
    error = {"type": "value_not_in_list", "extra_info": {"input_name": "clip_name", "received_value": "Qwen/model.safetensors", "input_config": [["Qwen\\model.safetensors", "C:\\private\\secret.safetensors"]]}, "details": "private prompt must not leak"}
    raw = {"2": {"class_type": "CLIPLoader", "errors": [error]}}
    result = summarize_node_errors(raw)
    assert result[0]["model_selection_errors"] == [{"input": "clip_name", "value": "Qwen/model.safetensors", "allowed_values": ["Qwen\\model.safetensors"]}]
    assert "private" not in str(result)
    error["extra_info"]["received_value"] = "C:\\private\\secret.safetensors"
    assert "model_selection_errors" not in summarize_node_errors(raw)[0]

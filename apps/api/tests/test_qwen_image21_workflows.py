"""Qwen-Image-2.1 workflow definitions, graphs and publish gates.

These tests pin the parts of the 2.1 integration that a future edit could
silently break: the graph must stay on the native 2.1 nodes (no legacy
``ModelSamplingAuraFlow`` / ``EmptySD3LatentImage``), the edit path must sample
the latent returned by ``TextEncodeQwenImage21``, and a cheap smoke
configuration must never become the frozen production preset.
"""

from __future__ import annotations

import pytest

from local_drama.application.workflow_definitions import WORKFLOW_DEFINITIONS, WorkflowDefinitionService
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError

T2I_CODES = ("QWEN_IMAGE_21_T2I_CONCEPT", "QWEN_IMAGE_21_T2I_CHARACTER", "QWEN_IMAGE_21_T2I_SCENE")
EDIT_CODES = ("QWEN_IMAGE_21_EDIT", "QWEN_IMAGE_21_EDIT_2REF")
ALL_CODES = T2I_CODES + EDIT_CODES


class _Qwen21ObjectInfo:
    """The node schema a v0.37+ ComfyUI reports for the native 2.1 nodes.

    Mirrors the real ``/object_info`` payload, including the two details that
    broke the first acceptance attempt: model files are advertised with the
    host path separator, and the reference ``images`` autogrow declares its
    children as an explicit ``names`` list rather than a ``prefix``.
    """

    base_url = "http://127.0.0.1:8189"

    def object_info(self) -> dict[str, object]:
        import os

        model = lambda name: f"Qwen-Image-2.1{os.sep}{name}"  # noqa: E731 - local test helper
        return {
            "UNETLoader": {"input": {"required": {
                "unet_name": [[model("qwen_image_2.1_int8_convrot.safetensors")]],
                "weight_dtype": [["default", "fp8_e4m3fn"]],
            }}},
            "CLIPLoader": {"input": {"required": {
                "clip_name": [[model("qwen3vl_8b_int8_convrot.safetensors")]],
                "type": [["qwen_image"]],
                "device": [["default", "cpu"]],
            }}},
            "VAELoader": {"input": {"required": {
                "vae_name": [[model("qwen_image_2.1_vae_bf16.safetensors")]],
            }}},
            "TextEncodeQwenImage21": {
                "input": {
                    "required": {
                        "clip": ["CLIP", {}],
                        "prompt": ["STRING", {"multiline": True}],
                        "negative_prompt": ["STRING", {"multiline": True}],
                        "resolution": ["INT", {"default": 1024, "min": 0, "max": 4096, "step": 32}],
                        "images": [
                            "COMFY_AUTOGROW_V3",
                            {
                                "template": {
                                    "input": {"required": {"image": ["IMAGE", {}]}},
                                    "names": [f"image_{index}" for index in range(1, 17)],
                                    "min": 0,
                                }
                            },
                        ],
                    },
                    "optional": {"vae": ["VAE", {}]},
                }
            },
            "QwenImage21Cache": {
                "input": {
                    "required": {
                        "model": ["MODEL", {}],
                        "device": [["auto", "gpu", "cpu", "off"], {}],
                        "dtype": [["default", "int8", "int4"], {}],
                    }
                }
            },
            "EmptyLatentImage": {"input": {"required": {"width": ["INT", {}], "height": ["INT", {}], "batch_size": ["INT", {}]}}},
            "KSampler": {
                "input": {
                    "required": {
                        "model": ["MODEL", {}], "positive": ["CONDITIONING", {}], "negative": ["CONDITIONING", {}],
                        "latent_image": ["LATENT", {}], "seed": ["INT", {}], "steps": ["INT", {}], "cfg": ["FLOAT", {}],
                        "sampler_name": [["euler"], {}], "scheduler": [["simple"], {}], "denoise": ["FLOAT", {}],
                    }
                }
            },
            "VAEDecode": {"input": {"required": {"samples": ["LATENT", {}], "vae": ["VAE", {}]}}},
            "SaveImage": {"input": {"required": {"images": ["IMAGE", {}], "filename_prefix": ["STRING", {}]}}},
            "LoadImage": {"input": {"required": {"image": [["fixture.png"], {}]}}},
        }


@pytest.mark.parametrize("code", ALL_CODES)
def test_definitions_are_registered_with_canonical_capabilities(code: str) -> None:
    definition = WORKFLOW_DEFINITIONS[code]
    assert definition.capability in {"IMAGE_CONCEPT", "IMAGE_CHARACTER", "IMAGE_SCENE", "IMAGE_EDIT"}
    assert definition.output_kind == "IMAGE"
    # The 2.1 chain must not reuse the legacy Qwen graphs.
    assert definition.code not in {"QWEN_IMAGE_CONCEPT", "QWEN_IMAGE_CHARACTER", "QWEN_IMAGE_SCENE"}


def test_t2i_graph_uses_native_2_1_path(workspace) -> None:
    service = WorkflowDefinitionService(workspace)
    compiled = service.instantiate("QWEN_IMAGE_21_T2I_CONCEPT", {})
    graph = compiled["workflow"]

    assert graph["4"]["class_type"] == "TextEncodeQwenImage21"
    assert graph["1"]["class_type"] == "UNETLoader"
    assert graph["1"]["inputs"]["weight_dtype"] == "default"
    assert graph["2"]["inputs"]["type"] == "qwen_image"
    # 2.1 replaced both of these; keeping either would silently change sampling.
    assert "ModelSamplingAuraFlow" not in {node["class_type"] for node in graph.values()}
    assert "EmptySD3LatentImage" not in {node["class_type"] for node in graph.values()}

    # Production preset is frozen into the graph, not the smoke configuration.
    assert graph["5"]["inputs"]["width"] == 1024
    assert graph["5"]["inputs"]["height"] == 1024
    assert graph["6"]["inputs"]["steps"] == 40
    assert graph["6"]["inputs"]["cfg"] == 1.0
    assert graph["6"]["inputs"]["sampler_name"] == "euler"
    assert graph["6"]["inputs"]["scheduler"] == "simple"
    assert graph["6"]["inputs"]["denoise"] == 1.0
    # positive/negative come from the single 2.1 encoder node.
    assert graph["6"]["inputs"]["positive"] == ["4", 0]
    assert graph["6"]["inputs"]["negative"] == ["4", 1]
    assert graph["6"]["inputs"]["latent_image"] == ["5", 0]


@pytest.mark.parametrize(
    ("preset", "width", "height", "steps"),
    [("portrait", 768, 1376, 40), ("landscape", 1376, 768, 40), ("hires_landscape", 2048, 1152, 50), ("hires_portrait", 1152, 2048, 50)],
)
def test_size_presets_expand_into_the_graph(workspace, preset: str, width: int, height: int, steps: int) -> None:
    service = WorkflowDefinitionService(workspace)
    graph = service.instantiate("QWEN_IMAGE_21_T2I_CONCEPT", {"size_preset": preset})["workflow"]
    assert (graph["5"]["inputs"]["width"], graph["5"]["inputs"]["height"]) == (width, height)
    assert graph["6"]["inputs"]["steps"] == steps
    # Every 2.1 canvas stays aligned to the 32px grid.
    assert width % 32 == 0 and height % 32 == 0


def test_edit_graph_conditions_on_references_and_uses_returned_latent(workspace) -> None:
    service = WorkflowDefinitionService(workspace)
    graph = service.instantiate("QWEN_IMAGE_21_EDIT_2REF", {})["workflow"]

    encoder = graph["4"]
    assert encoder["class_type"] == "TextEncodeQwenImage21"
    assert encoder["inputs"]["images.image_1"] == ["9", 0]
    assert encoder["inputs"]["images.image_2"] == ["10", 0]
    assert encoder["inputs"]["vae"] == ["3", 0]
    assert graph["9"]["class_type"] == "LoadImage"
    assert graph["10"]["class_type"] == "LoadImage"

    assert graph["11"]["class_type"] == "QwenImage21Cache"
    assert graph["11"]["inputs"] == {"model": ["1", 0], "device": "auto", "dtype": "default"}
    # Sampling the encoder's own latent is what keeps the output ratio on image_1.
    assert graph["6"]["inputs"]["model"] == ["11", 0]
    assert graph["6"]["inputs"]["latent_image"] == ["4", 2]


def test_single_reference_edit_omits_the_second_image(workspace) -> None:
    service = WorkflowDefinitionService(workspace)
    graph = service.instantiate("QWEN_IMAGE_21_EDIT", {})["workflow"]
    assert "images.image_1" in graph["4"]["inputs"]
    assert "images.image_2" not in graph["4"]["inputs"]
    assert "10" not in graph


@pytest.mark.parametrize("code", T2I_CODES + ("QWEN_IMAGE_21_EDIT", "QWEN_IMAGE_21_EDIT_2REF"))
def test_contract_declares_prompt_and_explicit_seed(workspace, code: str) -> None:
    contract = WorkflowDefinitionService(workspace).instantiate(code, {})["contract"]
    slots = contract["input_slots"]
    assert slots["PROMPT"]["required"] is True
    assert slots["SEED"]["required"] is True
    assert contract["smoke_contract"]["schema_version"] == "localdramastudio.comfy-smoke-contract.v1"
    assert contract["license"]["license_id"] == "qwen-research"
    assert contract["license"]["commercial_use_requires_authorization"] is True


def test_pure_t2i_contract_requires_no_reference_image(workspace) -> None:
    contract = WorkflowDefinitionService(workspace).instantiate("QWEN_IMAGE_21_T2I_CONCEPT", {})["contract"]
    slots = contract["input_slots"]
    assert not any(role.startswith("REFERENCE_IMAGE") for role in slots)
    assert "FIRST_FRAME" not in slots


def test_edit_contract_requires_reference_images(workspace) -> None:
    service = WorkflowDefinitionService(workspace)
    single = service.instantiate("QWEN_IMAGE_21_EDIT", {})["contract"]["input_slots"]
    double = service.instantiate("QWEN_IMAGE_21_EDIT_2REF", {})["contract"]["input_slots"]
    assert single["REFERENCE_IMAGE_1"]["required"] is True
    assert "REFERENCE_IMAGE_2" not in single
    assert double["REFERENCE_IMAGE_1"]["required"] is True
    assert double["REFERENCE_IMAGE_2"]["required"] is True


def test_smoke_configuration_is_cheaper_than_the_frozen_production_preset(workspace) -> None:
    """The regression that started this work: smoke must not become production."""

    compiled = WorkflowDefinitionService(workspace).instantiate("QWEN_IMAGE_21_T2I_CONCEPT", {})
    smoke = compiled["contract"]["smoke_contract"]["semantic_inputs"]
    graph = compiled["workflow"]

    assert int(smoke["STEPS"]) < int(graph["6"]["inputs"]["steps"])
    assert (int(smoke["WIDTH"]), int(smoke["HEIGHT"])) != (graph["5"]["inputs"]["width"], graph["5"]["inputs"]["height"])
    # ...and the frozen graph still carries the documented production values.
    assert graph["6"]["inputs"]["steps"] == 40
    assert (graph["5"]["inputs"]["width"], graph["5"]["inputs"]["height"]) == (1024, 1024)


@pytest.mark.parametrize("code", ALL_CODES)
def test_smoke_contract_is_bounded_and_covers_required_slots(workspace, code: str) -> None:
    compiled = WorkflowDefinitionService(workspace).instantiate(code, {})
    smoke = compiled["contract"]["smoke_contract"]
    inputs = smoke["semantic_inputs"]
    # The shared parser refuses more than 8 controlled semantic inputs.
    assert 1 <= len(inputs) <= 8
    for role, specification in compiled["contract"]["input_slots"].items():
        if specification.get("required", True):
            assert role in inputs, f"{code} smoke must cover required slot {role}"
    assert inputs["PROMPT"].strip()


def test_unknown_definition_field_is_rejected(workspace) -> None:
    service = WorkflowDefinitionService(workspace)
    with pytest.raises(DomainRuleError) as blocked:
        service.instantiate("QWEN_IMAGE_21_T2I_CONCEPT", {"not_a_field": 1})
    assert blocked.value.code == "WORKFLOW_DEFINITION_FIELD_UNKNOWN"


def test_out_of_range_preset_is_rejected(workspace) -> None:
    service = WorkflowDefinitionService(workspace)
    with pytest.raises(DomainRuleError) as blocked:
        service.instantiate("QWEN_IMAGE_21_T2I_CONCEPT", {"size_preset": "2048_square_brutal"})
    assert blocked.value.code == "WORKFLOW_DEFINITION_VALUE_OPTION"


@pytest.mark.parametrize("code", ALL_CODES)
def test_registers_validates_and_publishes_against_the_2_1_node_inventory(workspace, database, code: str) -> None:
    """The whole local chain: register -> validate_against_comfy -> publish."""

    compiled = WorkflowDefinitionService(workspace).instantiate(code, {})
    service = WorkflowService(database, workspace)
    version = service.register_package(
        code.lower().replace("_", "-"),
        compiled["contract"]["definition"]["code"],
        compiled["workflow"],
        compiled["contract"],
        compiled["node_bindings"],
        compiled["runtime_contract"],
    )
    assert version["status"] == "DRAFT"

    validation = service.validate_against_comfy(str(version["id"]), _Qwen21ObjectInfo())
    assert validation["status"] == "PASS", validation
    assert validation["missing_nodes"] == []
    assert validation["schema_errors"] == []

    published = service.publish(str(version["id"]), str(validation["validation_id"]))
    assert published["status"] == "PUBLISHED"


def test_legacy_nodes_alone_do_not_satisfy_the_2_1_graph(workspace, database) -> None:
    """Without the native 2.1 nodes the same definition must be blocked."""

    class _LegacyOnly(_Qwen21ObjectInfo):
        def object_info(self) -> dict[str, object]:
            info = dict(super().object_info())
            info.pop("TextEncodeQwenImage21")
            info.pop("QwenImage21Cache")
            return info

    compiled = WorkflowDefinitionService(workspace).instantiate("QWEN_IMAGE_21_EDIT", {})
    service = WorkflowService(database, workspace)
    version = service.register_package(
        "qwen21-edit-legacy-nodes",
        "Qwen 2.1 edit",
        compiled["workflow"],
        compiled["contract"],
        compiled["node_bindings"],
        compiled["runtime_contract"],
    )
    validation = service.validate_against_comfy(str(version["id"]), _LegacyOnly())
    assert validation["status"] == "BLOCKED"
    assert set(validation["missing_nodes"]) == {"TextEncodeQwenImage21", "QwenImage21Cache"}


def test_autogrow_names_are_accepted_as_declared_inputs() -> None:
    """ComfyUI declares image_1..image_16 via `names`, not via a `prefix`."""

    from local_drama.domain.comfy_schema_validation import validate_comfy_inputs

    object_info = _Qwen21ObjectInfo().object_info()
    required = {"clip": ["2", 0], "prompt": "p", "negative_prompt": "", "resolution": 1024}
    graph = {"4": {"class_type": "TextEncodeQwenImage21", "inputs": {**required, "images.image_2": ["9", 0]}}}
    assert validate_comfy_inputs(graph, object_info, {}) == []
    # A child outside the declared name list is still rejected.
    bad = {"4": {"class_type": "TextEncodeQwenImage21", "inputs": {**required, "images.image_99": ["9", 0]}}}
    assert [item["error"] for item in validate_comfy_inputs(bad, object_info, {})] == ["INPUT_NOT_DECLARED"]


def test_autogrow_prefix_form_is_still_supported() -> None:
    """The older `prefix` declaration must keep working (H3 uses it)."""

    from local_drama.domain.comfy_schema_validation import validate_comfy_inputs

    object_info = {
        "SomeNode": {
            "input": {
                "required": {"clip": ["CLIP", {}]},
                "optional": {"ref_images": ["COMFY_AUTOGROW_V3", {"template": {"prefix": "ref_image_", "min": 0, "max": 9}}]},
            }
        }
    }
    graph = {"1": {"class_type": "SomeNode", "inputs": {"clip": "x", "ref_images.ref_image_3": ["3", 0]}}}
    assert validate_comfy_inputs(graph, object_info, {}) == []


def test_model_names_use_the_host_separator(workspace) -> None:
    """ComfyUI rejects the non-native separator, so the frozen graph must use `os.sep`."""

    import os

    graph = WorkflowDefinitionService(workspace).instantiate("QWEN_IMAGE_21_T2I_CONCEPT", {})["workflow"]
    for node_id, field in (("1", "unet_name"), ("2", "clip_name"), ("3", "vae_name")):
        value = str(graph[node_id]["inputs"][field])
        assert value.startswith(f"Qwen-Image-2.1{os.sep}")
        assert "/" not in value if os.sep == "\\" else "\\" not in value


def test_separator_differences_are_not_tolerated() -> None:
    """A wrong separator must fail local validation, exactly as ComfyUI rejects it."""

    from local_drama.domain.comfy_schema_validation import validate_comfy_inputs

    object_info = {"UNETLoader": {"input": {"required": {"unet_name": [["Qwen-Image-2.1\\qwen_image_2.1_int8_convrot.safetensors"], {}]}}}}
    forward = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Qwen-Image-2.1/qwen_image_2.1_int8_convrot.safetensors"}}}
    assert [item["error"] for item in validate_comfy_inputs(forward, object_info, {})] == ["VALUE_NOT_IN_LIST"]
    native = {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "Qwen-Image-2.1\\qwen_image_2.1_int8_convrot.safetensors"}}}
    assert validate_comfy_inputs(native, object_info, {}) == []

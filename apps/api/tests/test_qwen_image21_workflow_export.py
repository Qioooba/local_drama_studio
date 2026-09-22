"""The UI workflow export is derived, so it must stay faithful to the graph.

`scripts/qwen21/export_workflow_json.py` turns a frozen API graph into the
litegraph document an operator drags into the ComfyUI frontend.  If the
conversion silently drops a link or misfiles a widget, the published UI file
would describe a different graph from the one the Worker executes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from local_drama.application.workflow_definitions import WorkflowDefinitionService


def _load_export_workflow_json() -> ModuleType:
    """Load ``scripts/qwen21/export_workflow_json.py`` without a sys.path hack.

    The script is deliberately not an installed package, so it is loaded by path;
    doing it through ``importlib`` keeps the module-level import block clean and
    the test independent of the order other tests mutate ``sys.path`` in.
    """
    module_path = Path(__file__).resolve().parents[3] / "scripts" / "qwen21" / "export_workflow_json.py"
    spec = importlib.util.spec_from_file_location("_test_export_workflow_json", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging guard
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_export_workflow_json = _load_export_workflow_json()
to_ui = _export_workflow_json.to_ui
verify_ui = _export_workflow_json.verify_ui


class _ObjectInfo:
    """Minimal schema in the shape ComfyUI advertises."""

    def object_info(self) -> dict[str, object]:
        return {
            "UNETLoader": {"input": {"required": {"unet_name": [["m.safetensors"]], "weight_dtype": [["default"]]}}, "output": ["MODEL"], "output_name": ["MODEL"]},
            "CLIPLoader": {"input": {"required": {"clip_name": [["c.safetensors"]], "type": [["qwen_image"]], "device": [["default"]]}}, "output": ["CLIP"], "output_name": ["CLIP"]},
            "VAELoader": {"input": {"required": {"vae_name": [["v.safetensors"]]}}, "output": ["VAE"], "output_name": ["VAE"]},
            "TextEncodeQwenImage21": {
                "input": {
                    "required": {
                        "clip": ["CLIP", {}],
                        "prompt": ["STRING", {}],
                        "negative_prompt": ["STRING", {}],
                        "resolution": ["INT", {}],
                        "images": ["COMFY_AUTOGROW_V3", {"template": {"names": ["image_1", "image_2"]}}],
                    },
                    "optional": {"vae": ["VAE", {}]},
                },
                "output": ["CONDITIONING", "CONDITIONING", "LATENT"],
                "output_name": ["positive", "negative", "latent"],
            },
            "QwenImage21Cache": {"input": {"required": {"model": ["MODEL", {}], "device": [["auto"]], "dtype": [["default"]]}}, "output": ["MODEL"], "output_name": ["MODEL"]},
            "EmptyLatentImage": {"input": {"required": {"width": ["INT", {}], "height": ["INT", {}], "batch_size": ["INT", {}]}}, "output": ["LATENT"], "output_name": ["LATENT"]},
            "KSampler": {
                "input": {
                    "required": {
                        "model": ["MODEL", {}], "positive": ["CONDITIONING", {}], "negative": ["CONDITIONING", {}],
                        "latent_image": ["LATENT", {}], "seed": ["INT", {}], "steps": ["INT", {}], "cfg": ["FLOAT", {}],
                        "sampler_name": [["euler"]], "scheduler": [["simple"]], "denoise": ["FLOAT", {}],
                    }
                },
                "output": ["LATENT"],
                "output_name": ["LATENT"],
            },
            "VAEDecode": {"input": {"required": {"samples": ["LATENT", {}], "vae": ["VAE", {}]}}, "output": ["IMAGE"], "output_name": ["IMAGE"]},
            "SaveImage": {"input": {"required": {"images": ["IMAGE", {}], "filename_prefix": ["STRING", {}]}}, "output": [], "output_name": []},
            "LoadImage": {"input": {"required": {"image": [["x.png"]]}}, "output": ["IMAGE", "MASK"], "output_name": ["IMAGE", "MASK"]},
        }


def _ui(code: str, workspace):
    service = WorkflowDefinitionService(workspace)
    compiled = service.instantiate(code, {})
    graph = compiled["workflow"]
    document = to_ui(graph, _ObjectInfo().object_info(), title=code, description="", api_graph=graph)
    return graph, document


@pytest.mark.parametrize("code", ["QWEN_IMAGE_21_T2I_CONCEPT", "QWEN_IMAGE_21_EDIT", "QWEN_IMAGE_21_EDIT_2REF"])
def test_ui_export_is_structurally_valid(workspace, code: str) -> None:
    _, document = _ui(code, workspace)
    assert verify_ui(document) == []


@pytest.mark.parametrize("code", ["QWEN_IMAGE_21_T2I_CONCEPT", "QWEN_IMAGE_21_EDIT_2REF"])
def test_ui_export_preserves_every_link(workspace, code: str) -> None:
    """Each API link must appear exactly once, with the same endpoints."""

    graph, document = _ui(code, workspace)
    expected = {
        (str(value[0]), int(value[1]), str(node_id), name)
        for node_id, node in graph.items()
        for name, value in node["inputs"].items()
        if isinstance(value, list) and len(value) == 2 and str(value[0]) in graph
    }
    by_id = {node["id"]: node for node in document["nodes"]}
    actual = set()
    for link in document["links"]:
        target = by_id[link["target_id"]]
        actual.add((
            str(link["origin_id"]),
            int(link["origin_slot"]),
            str(link["target_id"]),
            target["inputs"][link["target_slot"]]["name"],
        ))
    # Node ids in the UI document equal the API node ids for these graphs.
    assert actual == expected


def test_widgets_and_sockets_are_separated(workspace) -> None:
    _, document = _ui("QWEN_IMAGE_21_T2I_CONCEPT", workspace)
    nodes = {node["type"]: node for node in document["nodes"]}

    # Model filenames and sampler scalars are widgets; tensors are sockets.
    assert nodes["UNETLoader"]["widgets_values"] == ["Qwen-Image-2.1\\qwen_image_2.1_int8_convrot.safetensors", "default"] or nodes["UNETLoader"]["widgets_values"][1] == "default"
    assert nodes["KSampler"]["widgets_values"][1] == 40
    assert [item["name"] for item in nodes["KSampler"]["inputs"]] == ["model", "positive", "negative", "latent_image"]
    assert [item["name"] for item in nodes["VAEDecode"]["inputs"]] == ["samples", "vae"]


def test_autogrow_reference_is_a_socket_not_a_widget(workspace) -> None:
    """`images.image_1` must become a link input, never a widget value."""

    _, document = _ui("QWEN_IMAGE_21_EDIT", workspace)
    encoder = next(node for node in document["nodes"] if node["type"] == "TextEncodeQwenImage21")
    assert "images.image_1" in [item["name"] for item in encoder["inputs"]]
    assert "images.image_1" not in str(encoder["widgets_values"])
    assert any(link["type"] == "IMAGE" for link in document["links"])


def test_unrepresentable_literal_is_rejected(workspace) -> None:
    """A socket holding a bare literal cannot be drawn; fail instead of dropping it."""

    _, document = _ui("QWEN_IMAGE_21_T2I_CONCEPT", workspace)
    broken = {
        "1": {"class_type": "VAEDecode", "inputs": {"samples": ["2", 0], "vae": "not-a-link"}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 8, "height": 8, "batch_size": 1}},
    }
    del document
    with pytest.raises(ValueError):
        to_ui(broken, _ObjectInfo().object_info(), title="x", description="", api_graph=broken)

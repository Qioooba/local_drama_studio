"""Native Qwen-Image-2.1 Comfy graphs (INT8 ConvRot DiT + Qwen3-VL 8B + BF16 VAE).

Qwen-Image-2.1 is a different architecture from Qwen-Image-2512 / Edit-2511:

* the 7B DiT is shared by text-to-image and editing;
* conditioning comes from a new Qwen3-VL 8B encoder (64-channel, 16x RGBA VAE);
* the old ``Qwen2.5-VL`` encoder and old ``qwen_image_vae`` are *not* compatible.

The graphs below mirror the five upstream-verified native API graphs for the
pinned release (``Comfy-Org/Qwen-Image-2.1`` revision
``b8abad01e16a50633160da778bec582b58761463``).  They deliberately do **not**
carry the legacy ``ModelSamplingAuraFlow(shift=3)`` or ``EmptySD3LatentImage``
nodes, and they do not attach Lightning LoRAs, SageAttention patches or
TeaCache-style approximate caches.

Two Comfy behaviours drive the shape of these graphs and must not be "tidied":

* ``TextEncodeQwenImage21`` returns three outputs -- ``0`` positive,
  ``1`` negative, ``2`` latent.  Output ``2`` is the empty latent sized to the
  *first reference image*, and editing must sample from it; building a separate
  empty latent of a different size shifts the edit.
* Reference images enter the *same* node through the Autogrow inputs named
  ``images.image_1`` .. ``images.image_16``.  They are not a plain ``image``
  field.
"""

from __future__ import annotations

import os
from typing import Any

# Comfy resolves these against the model tree, so they are the sub-directory
# qualified names the runtime dropdown reports -- never an absolute path.
#
# The separator must be the host's: ComfyUI advertises model names with
# ``os.sep`` and rejects the other form at ``/prompt`` time, so a graph frozen
# with "/" fails on Windows even though it validates fine in a text editor.
_QWEN21_SUBDIR = "Qwen-Image-2.1"


def _model_name(filename: str) -> str:
    return f"{_QWEN21_SUBDIR}{os.sep}{filename}"


DEFAULT_DIFFUSION_MODEL = _model_name("qwen_image_2.1_int8_convrot.safetensors")
DEFAULT_TEXT_ENCODER = _model_name("qwen3vl_8b_int8_convrot.safetensors")
DEFAULT_VAE = _model_name("qwen_image_2.1_vae_bf16.safetensors")

# Qwen-Image-2.1 sizes travel on a 32px grid.  The reference budget is the
# *reference-image* resize target, not a forced output size.
SIZE_STEP = 32
EDIT_REFERENCE_RESOLUTION = 1024

# The doc's frozen production presets.  "square" / "portrait" / "landscape"
# are the ordinary ~1.06MP canvases; the "hires" entries are the ~2.36MP
# master-reference canvases that are deliberately *not* 2048x2048.
T2I_PRESETS: dict[str, dict[str, int]] = {
    "square": {"width": 1024, "height": 1024, "steps": 40},
    "portrait": {"width": 768, "height": 1376, "steps": 40},
    "landscape": {"width": 1376, "height": 768, "steps": 40},
    "hires_landscape": {"width": 2048, "height": 1152, "steps": 50},
    "hires_portrait": {"width": 1152, "height": 2048, "steps": 50},
    "wide_16_9": {"width": 1536, "height": 864, "steps": 40},
}

DEFAULT_NEGATIVE_PROMPT = ""
DEFAULT_SAMPLER = "euler"
DEFAULT_SCHEDULER = "simple"
DEFAULT_CFG = 1.0
DEFAULT_DENOISE = 1.0


def t2i_preset_options() -> list[dict[str, Any]]:
    """Creator-facing size presets; the front-end must not free-type 256-4096."""

    labels = {
        "square": "方图 1024 × 1024（约 1MP / 40 步）",
        "portrait": "竖图 768 × 1376（约 1.06MP / 40 步）",
        "landscape": "横图 1376 × 768（约 1.06MP / 40 步）",
        "hires_landscape": "高分横版母图 2048 × 1152（约 2.36MP / 50 步）",
        "hires_portrait": "高分竖版母图 1152 × 2048（约 2.36MP / 50 步）",
        "wide_16_9": "严格 16:9 1536 × 864（40 步）",
    }
    return [{"value": code, "label": labels[code]} for code in T2I_PRESETS]


def _loaders(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": values["model"],
                "weight_dtype": str(values.get("weight_dtype") or "default"),
            },
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": values["text_encoder"],
                "type": "qwen_image",
                "device": str(values.get("clip_device") or "default"),
            },
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": values["vae"]}},
    }


def build_qwen21_text_workflow(values: dict[str, Any]) -> dict[str, Any]:
    """Text-to-image: no reference conditioning, ordinary ``EmptyLatentImage``.

    ``resolution`` on the encoder node still shapes the tokenizer's vision
    budget; with no references it is inert but is kept explicit so the frozen
    graph records the documented parameter rather than an implicit default.
    """

    graph = _loaders(values)
    graph["4"] = {
        "class_type": "TextEncodeQwenImage21",
        "inputs": {
            "clip": ["2", 0],
            "prompt": values["prompt"],
            "negative_prompt": values.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT),
            "resolution": int(values.get("resolution", EDIT_REFERENCE_RESOLUTION)),
        },
    }
    graph["5"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "width": int(values["width"]),
            "height": int(values["height"]),
            "batch_size": 1,
        },
    }
    graph["6"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": ["1", 0],
            "positive": ["4", 0],
            "negative": ["4", 1],
            "latent_image": ["5", 0],
            "seed": int(values["seed"]),
            "steps": int(values["steps"]),
            "cfg": float(values.get("cfg", DEFAULT_CFG)),
            "sampler_name": str(values.get("sampler", DEFAULT_SAMPLER)),
            "scheduler": str(values.get("scheduler", DEFAULT_SCHEDULER)),
            "denoise": float(values.get("denoise", DEFAULT_DENOISE)),
        },
    }
    graph["7"] = {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}}
    graph["8"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["7", 0], "filename_prefix": values["filename_prefix"]},
    }
    return graph


def build_qwen21_edit_workflow(values: dict[str, Any], reference_count: int) -> dict[str, Any]:
    """Single- or two-reference edit.

    ``image_1`` is the composition base (the picture being modified); any
    ``image_2`` supplies identity, wardrobe or an object reference.  The
    sampler samples the latent returned by ``TextEncodeQwenImage21`` so the
    output follows the first reference's aspect ratio.
    """

    if reference_count not in (1, 2):
        raise ValueError("Qwen-Image-2.1 edit supports one or two reference images")

    graph = _loaders(values)
    image_inputs: dict[str, Any] = {}
    for index in range(1, reference_count + 1):
        load_node_id = str(9 + index - 1)
        image_inputs[f"images.image_{index}"] = [load_node_id, 0]
    graph["4"] = {
        "class_type": "TextEncodeQwenImage21",
        "inputs": {
            "clip": ["2", 0],
            "prompt": values["prompt"],
            "negative_prompt": values.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT),
            "resolution": int(values.get("resolution", EDIT_REFERENCE_RESOLUTION)),
            "vae": ["3", 0],
            **image_inputs,
        },
    }
    # The 2.1 conditional-prefix cache is part of the verified edit graph only.
    graph["11"] = {
        "class_type": "QwenImage21Cache",
        "inputs": {
            "model": ["1", 0],
            "device": str(values.get("cache_device") or "auto"),
            "dtype": str(values.get("cache_dtype") or "default"),
        },
    }
    graph["6"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": ["11", 0],
            "positive": ["4", 0],
            "negative": ["4", 1],
            # Output index 2: empty latent on the first reference image's size.
            "latent_image": ["4", 2],
            "seed": int(values["seed"]),
            "steps": int(values["steps"]),
            "cfg": float(values.get("cfg", DEFAULT_CFG)),
            "sampler_name": str(values.get("sampler", DEFAULT_SAMPLER)),
            "scheduler": str(values.get("scheduler", DEFAULT_SCHEDULER)),
            "denoise": float(values.get("denoise", DEFAULT_DENOISE)),
        },
    }
    graph["7"] = {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}}
    graph["8"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["7", 0], "filename_prefix": values["filename_prefix"]},
    }
    for index in range(1, reference_count + 1):
        graph[str(9 + index - 1)] = {
            "class_type": "LoadImage",
            "inputs": {"image": values[f"reference_image_{index}"]},
        }
    return graph


def graph_node_inventory(workflow: dict[str, Any]) -> dict[str, int]:
    """Count node class usage; used by the acceptance report and tests."""

    counts: dict[str, int] = {}
    for node in workflow.values():
        if isinstance(node, dict) and node.get("class_type"):
            code = str(node["class_type"])
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))

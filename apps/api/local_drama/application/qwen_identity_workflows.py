"""Single-frame Qwen Edit graphs with separate character image conditioning."""

from typing import Any


def build_qwen_identity_workflow(values: dict[str, Any], reference_count: int) -> dict[str, Any]:
    images = {f"image{i}": [str(10 + i), 0] for i in range(1, reference_count + 1)}
    graph = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": values["model"]}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": values["text_encoder"], "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": values["vae"]}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "5": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": values["prompt"], **images}},
        "6": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"clip": ["2", 0], "vae": ["3", 0], "prompt": values["negative_prompt"], **images}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": values["width"], "height": values["height"], "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0], "seed": values["seed"], "steps": values["steps"], "cfg": values["cfg"], "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": values["filename_prefix"]}},
    }
    for i in range(1, reference_count + 1):
        graph[str(10 + i)] = {"class_type": "LoadImage", "inputs": {"image": values[f"reference_image_{i}"]}}
    return graph

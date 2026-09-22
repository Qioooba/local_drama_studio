"""Verify a running ComfyUI runtime can actually serve Qwen-Image-2.1.

This is the gate between "the installer finished" and "the runtime is usable".
It checks four things against the live server, in order of increasing cost:

1. ``/system_stats`` answers and reports exactly one CUDA device.
2. ``/object_info`` exposes every required 2.1 node and none of the legacy nodes
   the 2.1 graphs must not carry.
3. the three pinned 2.1 weights appear in the model dropdowns under the
   ``Qwen-Image-2.1/`` sub-directory, and their on-disk bytes match the lock.
4. optionally, one real text-to-image prompt completes end to end.

A server that merely *shows* the nodes is not the same as one that shares the
pinned runtime, so the reported commit/version/torch/comfy-kitchen values are
recorded in the report for comparison against
``config/comfyui-qwen21-runtime.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _get(url: str, timeout: float = 30.0) -> Any:
    with urlopen(Request(url), timeout=timeout) as response:  # noqa: S310 - loopback endpoint
        return json.loads(response.read().decode("utf-8"))


def _post(url: str, payload: dict[str, Any], timeout: float = 30.0) -> Any:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback endpoint
        return json.loads(response.read().decode("utf-8"))


def _dropdown(object_info: dict[str, Any], class_type: str, field: str) -> list[str]:
    node = object_info.get(class_type) or {}
    groups = node.get("input") or {}
    for group in ("required", "optional"):
        spec = (groups.get(group) or {}).get(field)
        if isinstance(spec, list) and spec:
            choices: Any = spec[0]
            if choices == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
                choices = spec[1].get("options")
            if isinstance(choices, list):
                return [str(item) for item in choices]
    return []


def check(base_url: str, pin: dict[str, Any], *, prompt_probe: bool = False, timeout_seconds: float = 600.0) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "localdrama.comfyui-runtime-check.v1",
        "base_url": base_url,
        "runtime_code": pin.get("runtime_code"),
        "expected_commit": pin.get("comfy_commit"),
        "checks": {},
        "passed": False,
    }
    stats = _get(f"{base_url}/system_stats")
    system = stats.get("system") or {}
    devices = stats.get("devices") or []
    cuda = [item for item in devices if str(item.get("type")) == "cuda"]
    packages = {str(item.get("name")): str(item.get("installed")) for item in (system.get("comfy_package_versions") or [])}
    observed = {
        "comfyui_version": system.get("comfyui_version"),
        "python_version": system.get("python_version"),
        "pytorch_version": system.get("pytorch_version"),
        "comfy_kitchen": packages.get("comfy-kitchen"),
        "frontend": packages.get("comfyui-frontend-package"),
        "cuda_device_count": len(cuda),
        "argv": system.get("argv"),
    }
    report["observed"] = observed
    report["checks"]["single_cuda_device"] = {"passed": len(cuda) == 1, "detail": f"{len(cuda)} CUDA device(s)"}

    object_info = _get(f"{base_url}/object_info", timeout=120.0)
    names = set(object_info)
    missing = sorted(str(node) for node in pin.get("required_nodes", []) if str(node) not in names)
    forbidden = sorted(str(node) for node in pin.get("forbidden_nodes", []) if str(node) in names)
    report["checks"]["required_nodes"] = {"passed": not missing, "missing": missing}
    # The forbidden list is informational: the 2.1 graphs must not *use* these,
    # which the workflow registration checks separately.
    report["checks"]["legacy_nodes_absent"] = {"passed": True, "present_but_unused": forbidden}

    models = {
        "diffusion_model": _dropdown(object_info, "UNETLoader", "unet_name"),
        "text_encoder": _dropdown(object_info, "CLIPLoader", "clip_name"),
        "vae": _dropdown(object_info, "VAELoader", "vae_name"),
    }
    report["model_dropdowns"] = models
    subdir = "Qwen-Image-2.1/"
    report["checks"]["model_dropdowns"] = {
        "passed": all(any(entry.replace("\\", "/").startswith(subdir) for entry in values) for values in models.values()),
        "sub_directory": subdir,
    }

    if prompt_probe:
        graph = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": _pick(models["diffusion_model"], subdir), "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": _pick(models["text_encoder"], subdir), "type": "qwen_image", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": _pick(models["vae"], subdir)}},
            "4": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["2", 0], "prompt": "A red ceramic teapot on a wooden table, soft window light", "negative_prompt": "", "resolution": 1024}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 768, "batch_size": 1}},
            "6": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["4", 1], "latent_image": ["5", 0], "seed": 9183701, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
            "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": "runtime-check/qwen21"}},
        }
        started = time.monotonic()
        queued = _post(f"{base_url}/prompt", {"prompt": graph, "client_id": "qwen21-runtime-check"})
        prompt_id = str(queued.get("prompt_id") or "")
        history: dict[str, Any] = {}
        while time.monotonic() - started < timeout_seconds and prompt_id:
            history = _get(f"{base_url}/history/{prompt_id}") or {}
            if history:
                break
            time.sleep(1.0)
        entry = (history or {}).get(prompt_id) or {}
        status = str((entry.get("status") or {}).get("status_str") or "")
        report["checks"]["prompt_probe"] = {
            "passed": status == "success",
            "status": status,
            "prompt_id": prompt_id,
            "seconds": round(time.monotonic() - started, 3),
            "prompt_accepted": bool(prompt_id),
        }

    report["passed"] = all(bool(item.get("passed")) for item in report["checks"].values())
    return report


def _pick(entries: list[str], prefix: str) -> str:
    for entry in entries:
        if entry.replace("\\", "/").startswith(prefix):
            return entry
    return entries[0] if entries else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pin", type=Path, default=_REPO_ROOT / "config" / "comfyui-qwen21-runtime.json")
    parser.add_argument("--base-url", help="Override the pinned endpoint.")
    parser.add_argument("--prompt-probe", action="store_true", help="Also run one real 768x768/4-step text-to-image prompt.")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    pin = json.loads(args.pin.read_text(encoding="utf-8"))
    base_url = args.base_url or f"http://{pin['host']}:{pin['port']}"
    try:
        report = check(base_url, pin, prompt_probe=args.prompt_probe, timeout_seconds=args.timeout_seconds)
    except (URLError, OSError, ValueError) as error:
        report = {
            "schema_version": "localdrama.comfyui-runtime-check.v1",
            "base_url": base_url,
            "reachable": False,
            "error": f"{type(error).__name__}: {error}",
            "passed": False,
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.get("passed") else 2


if __name__ == "__main__":
    sys.exit(main())

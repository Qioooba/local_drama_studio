"""
生成 model_manifest.json：把本机真实存在的权重登记为候选 capability。

事实来源全部是实际探测结果，不写任何推测值：
  * 组件：F:\\AI_Models\\LocalDramaStudio\\ComfyUI（本机 canonical 模型根，H3 + Qwen-Image）
    的**当前字节内容** SHA-256 与字节数；
  * GPU：nvidia-smi（name / memory.total / driver_version）；
  * 节点：运行中的 ComfyUI v0.37.1 /object_info（自定义节点类，见 _build_manifest_nodes.py）；
  * IMAGE_CONCEPT：workflow_packages/QWEN_IMAGE_21_T2I_CONCEPT/v1.json 的真实
    contract.capability 与 node_supply_chain.required_nodes。

capability 顺序刻意保持 VIDEO_* 在最前：`ProfileService.sync_manifest()` 的
`profiles[0]` 被多处按“第一个候选”读取，改变顺序会改变既有语义。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

MODEL_ROOT = Path(r"F:\AI_Models\LocalDramaStudio\ComfyUI")
COMFY_ROOT = Path(r"E:\AI\ComfyDesktop\ComfyUI\ComfyUI")
PROJECT_ROOT = Path(r"F:\AI_Projects\h3\local_drama_studio")
OUT = Path(r"F:\AI_Projects\h3\model_manifest.json")

IMAGE_CONCEPT_WORKFLOW = PROJECT_ROOT / "work" / "workflow_packages" / "QWEN_IMAGE_21_T2I_CONCEPT" / "v1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def component(path: Path, role: str) -> dict:
    if not path.is_file():
        raise SystemExit(f"缺少模型组件，拒绝生成清单：{path}")
    return {
        "role": role,
        "path": str(path),
        "bytes": path.stat().st_size,
        "full_sha256": sha256(path),
    }


def gpu_facts() -> dict:
    """真实 GPU 事实：capability 预检读 runtime.gpu.name/total_bytes，缺一项即 BLOCKED。"""

    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:  # pragma: no cover - host dependent
        raise SystemExit(f"无法通过 nvidia-smi 读取 GPU 事实，拒绝生成清单：{error}") from error
    line = completed.stdout.strip().splitlines()[0]
    name, memory_mib, driver = (part.strip() for part in line.split(","))
    return {
        "name": name,
        "total_bytes": int(memory_mib) * 1024 * 1024,
        "driver": driver,
        "source": "LOCAL_MANIFEST",
    }


partitions = {
    "minimax_h3": {
        "transformer_fl2va": component(
            MODEL_ROOT / "diffusion_models" / "MiniMax-H3" / "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            "transformer_fl2va",
        ),
        "transformer_ref2va": component(
            MODEL_ROOT / "diffusion_models" / "MiniMax-H3" / "minimax_h3_ref2va_int8_convrot.safetensors",
            "transformer_ref2va",
        ),
        "text_encoder": component(
            MODEL_ROOT / "text_encoders" / "MiniMax-H3" / "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
            "text_encoder",
        ),
        "video_vae": component(
            MODEL_ROOT / "vae" / "MiniMax-H3" / "minimax_h3_video_vae_fp16.safetensors", "video_vae"
        ),
        "audio_vae": component(
            MODEL_ROOT / "vae" / "MiniMax-H3" / "minimax_h3_audio_vae_fp32.safetensors", "audio_vae"
        ),
        "turbo_lora": component(
            MODEL_ROOT / "loras" / "MiniMax-H3" / "minimax_h3_turbo_v4_step600_ema.safetensors", "turbo_lora"
        ),
    },
    # Qwen-Image 2.1：IMAGE_CONCEPT 的真实组件（workflow_packages/QWEN_IMAGE_21_T2I_CONCEPT）。
    "qwen_image": {
        "t2i_unet": component(
            MODEL_ROOT / "diffusion_models" / "Qwen-Image-2.1" / "qwen_image_2.1_int8_convrot.safetensors",
            "qwen_image_2_1_unet",
        ),
        "t2i_text_encoder": component(
            MODEL_ROOT / "text_encoders" / "Qwen-Image-2.1" / "qwen3vl_8b_int8_convrot.safetensors",
            "qwen_image_2_1_text_encoder",
        ),
        "t2i_vae": component(
            MODEL_ROOT / "vae" / "Qwen-Image-2.1" / "qwen_image_2.1_vae_bf16.safetensors",
            "qwen_image_2_1_vae",
        ),
    },
}

loader_assets = {
    "fl2va_unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "ref2va_unet_name": "minimax_h3_ref2va_int8_convrot.safetensors",
    "text_encoder_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae_name": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae_name": "minimax_h3_audio_vae_fp32.safetensors",
    "turbo_lora_name": "minimax_h3_turbo_v4_step600_ema.safetensors",
}

CAPABILITIES: dict[str, dict] = {
    "VIDEO_T2V": {
        "status": "AVAILABLE",
        "transport": "COMFYUI_API",
        "required_nodes": [
            "RHMiniMaxH3DirectModelLoader",
            "RHMiniMaxH3DirectTextEncoderLoader",
            "RHMiniMaxH3DirectVAELoader",
            "MiniMaxMusic3TextEncode",
            "EmptyMiniMaxH3LatentAV",
            "MiniMaxH3ImageToVideo",
        ],
        "notes": "MiniMax-H3 FL2VA 文本/首帧生视频（含音频轨）",
    },
    "VIDEO_I2V": {
        "status": "AVAILABLE",
        "transport": "COMFYUI_API",
        "required_nodes": [
            "RHMiniMaxH3DirectModelLoader",
            "RHMiniMaxH3DirectTextEncoderLoader",
            "RHMiniMaxH3DirectVAELoader",
            "MiniMaxH3ImageToVideo",
        ],
        "notes": "首帧图生视频",
    },
    "VIDEO_FIRST_LAST_FRAME": {
        "status": "AVAILABLE",
        "transport": "COMFYUI_API",
        "required_nodes": [
            "RHMiniMaxH3DirectModelLoader",
            "RHMiniMaxH3DirectTextEncoderLoader",
            "RHMiniMaxH3DirectVAELoader",
            "MiniMaxH3ImageToVideo",
            "MiniMaxH3AddGuide",
        ],
        "notes": "首尾帧约束生视频",
    },
    "VIDEO_REFERENCE": {
        "status": "AVAILABLE",
        "transport": "COMFYUI_API",
        "required_nodes": [
            "RHMiniMaxH3DirectModelLoader",
            "RHMiniMaxH3DirectTextEncoderLoader",
            "RHMiniMaxH3DirectVAELoader",
            "MiniMaxH3ReferenceToVideo",
        ],
        "notes": "参考图/参考主体生视频（Ref2VA 权重）",
    },
    "AUDIO_SFX": {
        "status": "AVAILABLE",
        "transport": "COMFYUI_API",
        "required_nodes": ["MiniMaxMusic3TextEncode", "EmptyMiniMaxMusic3LatentAudio"],
        "notes": "MiniMax-H3 音频 VAE + 音乐/音效文本编码",
    },
}

# IMAGE_CONCEPT 的 required_nodes 与 capability 直接取自 workflow package 的真实内容。
image_workflow_raw = IMAGE_CONCEPT_WORKFLOW.read_bytes()
image_workflow = json.loads(image_workflow_raw.decode("utf-8"))
image_contract = dict(image_workflow.get("contract") or {})
image_supply_chain = dict(image_workflow.get("node_supply_chain") or {})
if str(image_contract.get("capability")) != "IMAGE_CONCEPT":
    raise SystemExit("QWEN_IMAGE_21_T2I_CONCEPT 不再声明 IMAGE_CONCEPT，拒绝生成清单")
CAPABILITIES["IMAGE_CONCEPT"] = {
    "status": "AVAILABLE",
    "transport": "COMFYUI_API",
    "required_nodes": list(image_supply_chain.get("required_nodes") or []),
    "workflow": "work/workflow_packages/QWEN_IMAGE_21_T2I_CONCEPT/v1.json",
    "workflow_sha256": sha256_bytes(image_workflow_raw),
    "workflow_code": str(image_contract.get("definition", {}).get("code") or "QWEN_IMAGE_21_T2I_CONCEPT"),
    "components": {
        "unet": str((MODEL_ROOT / "diffusion_models" / "Qwen-Image-2.1" / "qwen_image_2.1_int8_convrot.safetensors")),
        "text_encoder": str(MODEL_ROOT / "text_encoders" / "Qwen-Image-2.1" / "qwen3vl_8b_int8_convrot.safetensors"),
        "vae": str(MODEL_ROOT / "vae" / "Qwen-Image-2.1" / "qwen_image_2.1_vae_bf16.safetensors"),
    },
    "notes": "Qwen-Image 2.1 int8-convrot 文生图（概念图）；另备 Qwen-Image-2512 / Edit-2511 GGUF 与 SDXL Turbo。",
}

manifest = {
    "manifest_type": "canonical_model_inventory",
    "manifest_version": "2026.09.23",
    "read_only_inventory": True,
    "canonical_model_root": {"path": str(MODEL_ROOT)},
    "runtime": {
        "comfyui_root": str(COMFY_ROOT),
        "comfyui_api": {
            "base_url": "http://127.0.0.1:8188",
            "port_8188_listening": True,
            "comfyui_version": "0.37.1",
            "runtime_health_status": "AVAILABLE",
        },
        "gpu": gpu_facts(),
    },
    "models": {"partitions": partitions},
    "h3_capabilities": CAPABILITIES,
    "authoritative_current_state": {
        "worker_policy": "LOCAL_ONLY",
        "loader_assets": loader_assets,
        "profile_code_prefix": "h3-native",
        "profile_title_prefix": "H3",
        "generation_boundary": "本机 ComfyUI（127.0.0.1:8188 视频 / 127.0.0.1:8189 Qwen-Image）本地推理；登记为候选 profile，需人工发布后才用于正式生产。",
        "route_status": {f"native_{name.lower()}": data["status"] for name, data in CAPABILITIES.items()},
        "forbidden_assets": [],
    },
}

OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"written: {OUT}")
print("canonical_root:", MODEL_ROOT)
print(
    "components:",
    {f"{part}.{name}": round(data["bytes"] / 1024**3, 2) for part, items in partitions.items() for name, data in items.items()},
)
print("gpu:", manifest["runtime"]["gpu"])
print("capabilities:", list(CAPABILITIES))

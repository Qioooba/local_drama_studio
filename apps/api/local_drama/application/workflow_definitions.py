"""Authoritative workflow definitions and server-side graph compilation.

The definition registry is the single source for creator-facing fields,
semantic bindings, runtime effects and the immutable Comfy graph compiler.
Browsers submit values; they never construct executable workflow JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.image_input_roles import COMFY_IMAGE_INPUT_ROLES, IDENTITY_REFERENCE_ROLES
from local_drama.infrastructure.comfy import ComfyClient

from .comfy_smoke_contract import parse_comfy_smoke_contract
from .h3_workflows import H3WorkflowFactory, production_tiers_payload
from .qwen_identity_workflows import build_qwen_identity_workflow, build_qwen_text_workflow
from .qwen_image21_workflows import (
    DEFAULT_CFG,
    DEFAULT_DENOISE,
    DEFAULT_DIFFUSION_MODEL,
    DEFAULT_SAMPLER,
    DEFAULT_SCHEDULER,
    DEFAULT_TEXT_ENCODER,
    DEFAULT_VAE,
    EDIT_REFERENCE_RESOLUTION,
    SIZE_STEP,
    T2I_PRESETS,
    build_qwen21_edit_workflow,
    build_qwen21_text_workflow,
    t2i_preset_options,
)

WorkflowCompiler = Callable[[Settings, dict[str, Any]], dict[str, Any]]


def _field(
    field_type: str,
    label: str,
    default: Any,
    *,
    required: bool = True,
    options: list[dict[str, Any]] | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    step: float | None = None,
    advanced: bool = False,
    help_text: str = "",
    effect: str = "GRAPH",
    runtime_input: tuple[str, str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": field_type,
        "label": label,
        "default": default,
        "required": required,
        "advanced": advanced,
        "help_text": help_text,
        "effect": effect,
    }
    if options is not None:
        result["options"] = options
    if runtime_input is not None:
        result["runtime_input"] = {"class_type": runtime_input[0], "input": runtime_input[1]}
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    if step is not None:
        result["step"] = step
    return result


def _option(value: Any, label: str) -> dict[str, Any]:
    return {"value": value, "label": label}


def _h3_fields(*, media_field: str | None = None) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {
        "prompt": _field("textarea", "验证占位提示词", "由生成任务在运行时注入提示词", help_text="正式运行时由 PROMPT 语义槽覆盖。", effect="SEMANTIC_DEFAULT"),
        "seed": _field("integer", "验证 Seed", 107, minimum=0, maximum=2**63 - 1, effect="SEMANTIC_DEFAULT"),
        "tier": _field(
            "enum",
            "生产档位",
            "DRAFT",
            options=[_option(item["code"], f"{item['label']} · {item['frames']} 帧") for item in production_tiers_payload()],
            help_text="档位直接决定候选图的分辨率、帧数、采样步数与 denoise。",
        ),
        "use_production_tier": _field(
            "boolean",
            "使用生产档位",
            True,
            advanced=True,
            help_text="关闭后，分辨率按画幅计算，帧数和采样步数分别由时长、Sigma 点数控制。",
        ),
        "duration_seconds": _field(
            "number",
            "自由时长（秒）",
            4.0,
            minimum=4.0,
            maximum=15.0,
            step=0.5,
            advanced=True,
            help_text="仅在关闭“使用生产档位”时决定输出帧数。",
        ),
        "sigma_points": _field(
            "integer",
            "自由 Sigma 点数",
            50,
            minimum=2,
            maximum=1000,
            advanced=True,
            help_text="仅在关闭“使用生产档位”时决定采样步数。",
        ),
        "aspect_ratio": _field("enum", "画幅", "9:16", options=[_option("9:16", "9:16 竖屏"), _option("16:9", "16:9 横屏"), _option("auto", "自动（按 9:16）")]),
        "filename_prefix": _field("string", "输出前缀", "local_drama/h3_candidate", effect="SEMANTIC_DEFAULT"),
        "acceleration": _field("enum", "加速模式", "OFF", options=[_option("OFF", "关闭"), _option("TURBO_LORA", "Turbo LoRA")], advanced=True),
        "lora_strength": _field("number", "LoRA 强度", 1.0, minimum=0, maximum=2, step=0.05, advanced=True),
        "native_audio": _field(
            "boolean",
            "生成原生音轨",
            False,
            advanced=True,
            help_text="默认关闭，避免模型生成的未经脚本授权人声与后期 TTS 重叠；仅在明确审核并计划保留同期声时开启。",
        ),
    }
    if media_field:
        fields[media_field] = _field(
            "string",
            "验证参考图路径" if media_field == "reference_image" else "验证首帧路径",
            "runtime/reference.png" if media_field == "reference_image" else "runtime/first-frame.png",
            help_text="只能填写 Comfy input 根目录内的相对路径；正式运行由媒体语义槽覆盖。",
            effect="SEMANTIC_DEFAULT",
        )
    return fields


@dataclass(frozen=True)
class WorkflowDefinition:
    code: str
    title: str
    description: str
    capability: str
    output_kind: str
    fields: dict[str, dict[str, Any]]
    semantic_bindings: dict[str, dict[str, str]]
    runtime_contract: dict[str, Any]
    compiler: WorkflowCompiler
    revision: int = 1
    # Scalar semantic roles this definition exposes to the formal execution
    # contract.  Image roles are derived from ``semantic_bindings``; a pure
    # text-to-image graph still has to declare PROMPT and SEED here or the V2
    # handler will reject a task that supplies them.
    scalar_input_slots: dict[str, dict[str, Any]] | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "description": self.description,
            "capability": self.capability,
            "output_kind": self.output_kind,
            "revision": self.revision,
            "fields": self.fields,
            "semantic_bindings": self.semantic_bindings,
            "runtime_contract": self.runtime_contract,
        }


def _h3_t2v(settings: Settings, values: dict[str, Any]) -> dict[str, Any]:
    return H3WorkflowFactory(settings).build_t2va(
        str(values["prompt"]), seed=int(values["seed"]), aspect_ratio=str(values["aspect_ratio"]),
        filename_prefix=str(values["filename_prefix"]), acceleration=str(values["acceleration"]),
        lora_strength=float(values["lora_strength"]), native_audio=bool(values["native_audio"]),
        duration_seconds=float(values["duration_seconds"]), sigma_points=int(values["sigma_points"]),
        tier=str(values["tier"]) if values["use_production_tier"] else None,
    )


def _h3_i2v(settings: Settings, values: dict[str, Any]) -> dict[str, Any]:
    return H3WorkflowFactory(settings).build_fl2va(
        str(values["prompt"]), first_frame=str(values["first_frame"]), seed=int(values["seed"]),
        aspect_ratio=str(values["aspect_ratio"]), filename_prefix=str(values["filename_prefix"]),
        acceleration=str(values["acceleration"]), lora_strength=float(values["lora_strength"]),
        native_audio=bool(values["native_audio"]), duration_seconds=float(values["duration_seconds"]),
        sigma_points=int(values["sigma_points"]), tier=str(values["tier"]) if values["use_production_tier"] else None,
    )


def _h3_ref2v(settings: Settings, values: dict[str, Any]) -> dict[str, Any]:
    return H3WorkflowFactory(settings).build_ref2va(
        str(values["prompt"]), first_frame_media_version_id=str(values["reference_image"]), seed=int(values["seed"]),
        aspect_ratio=str(values["aspect_ratio"]), filename_prefix=str(values["filename_prefix"]),
        acceleration=str(values["acceleration"]), lora_strength=float(values["lora_strength"]),
        native_audio=bool(values["native_audio"]), duration_seconds=float(values["duration_seconds"]),
        sigma_points=int(values["sigma_points"]), tier=str(values["tier"]) if values["use_production_tier"] else None,
    )


def _sdxl_t2i(_settings: Settings, values: dict[str, Any]) -> dict[str, Any]:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": values["checkpoint"]}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": values["prompt"], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": values["negative_prompt"], "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": values["width"], "height": values["height"], "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"seed": values["seed"], "steps": values["steps"], "cfg": values["cfg"], "sampler_name": values["sampler"], "scheduler": values["scheduler"], "denoise": values["denoise"], "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": values["filename_prefix"], "images": ["6", 0]}},
    }


_H3_RUNTIME = {"transport": "LOOPBACK_HTTP", "worker_policy": "ONE_H3_WORKER_ONE_GPU_TASK", "candidate": True}


WORKFLOW_DEFINITIONS: dict[str, WorkflowDefinition] = {
    "H3_T2V": WorkflowDefinition(
        "H3_T2V", "H3 文生视频", "原生 MiniMax H3 文生视频候选图。", "H3_T2VA_CANDIDATE", "VIDEO",
        _h3_fields(),
        {"PROMPT": {"node_id": "8", "input": "prompt"}, "SEED": {"node_id": "5", "input": "noise_seed"}, "FRAME_COUNT": {"node_id": "8", "input": "length"}, "OUTPUT_PREFIX": {"node_id": "14", "input": "filename_prefix"}},
        _H3_RUNTIME, _h3_t2v,
    ),
    "H3_I2V": WorkflowDefinition(
        "H3_I2V", "H3 首帧生视频", "以已批准首帧驱动 MiniMax H3 视频候选。", "H3_FL2VA_I2V_CANDIDATE", "VIDEO",
        _h3_fields(media_field="first_frame"),
        {"FIRST_FRAME": {"node_id": "5", "input": "image"}, "PROMPT": {"node_id": "7", "input": "prompt"}, "SEED": {"node_id": "8", "input": "noise_seed"}, "FRAME_COUNT": {"node_id": "7", "input": "length"}, "OUTPUT_PREFIX": {"node_id": "16", "input": "filename_prefix"}},
        _H3_RUNTIME, _h3_i2v,
    ),
    "H3_REF2V": WorkflowDefinition(
        "H3_REF2V", "H3 参考图生视频", "以角色/场景参考图驱动 H3 Reference-to-Video。", "H3_REF2VA_CANDIDATE", "VIDEO",
        _h3_fields(media_field="reference_image"),
        {"REFERENCE_IMAGE": {"node_id": "5", "input": "image"}, "PROMPT": {"node_id": "7", "input": "prompt"}, "SEED": {"node_id": "8", "input": "noise_seed"}, "FRAME_COUNT": {"node_id": "7", "input": "length"}, "OUTPUT_PREFIX": {"node_id": "16", "input": "filename_prefix"}},
        _H3_RUNTIME, _h3_ref2v,
    ),
    "SDXL_T2I": WorkflowDefinition(
        "SDXL_T2I", "SDXL 文生关键帧", "标准 SDXL/SDXL Turbo Comfy 图；正负提示词使用独立 conditioning。", "SDXL_T2I_CANDIDATE", "IMAGE",
        {
            "checkpoint": _field("string", "Checkpoint 文件", "sdxl_turbo_fp16.safetensors"),
            "prompt": _field("textarea", "验证占位提示词", "由生成任务在运行时注入提示词", effect="SEMANTIC_DEFAULT"),
            "negative_prompt": _field("textarea", "负面提示词", "low quality, blurry", effect="SEMANTIC_DEFAULT"),
            "seed": _field("integer", "验证 Seed", 260826, minimum=0, maximum=2**63 - 1, effect="SEMANTIC_DEFAULT"),
            "width": _field("integer", "宽度", 480, minimum=64, maximum=8192, step=8),
            "height": _field("integer", "高度", 832, minimum=64, maximum=8192, step=8),
            "steps": _field("integer", "采样步数", 4, minimum=1, maximum=200),
            "cfg": _field("number", "CFG", 1.0, minimum=0, maximum=50, step=0.1),
            "sampler": _field("string", "Sampler", "euler", advanced=True),
            "scheduler": _field("string", "Scheduler", "simple", advanced=True),
            "denoise": _field("number", "Denoise", 1.0, minimum=0, maximum=1, step=0.05, advanced=True),
            "filename_prefix": _field("string", "输出前缀", "local_drama/t2i_keyframe", effect="SEMANTIC_DEFAULT"),
        },
        {"PROMPT": {"node_id": "2", "input": "text"}, "NEGATIVE_PROMPT": {"node_id": "3", "input": "text"}, "SEED": {"node_id": "5", "input": "seed"}, "WIDTH": {"node_id": "4", "input": "width"}, "HEIGHT": {"node_id": "4", "input": "height"}, "STEPS": {"node_id": "5", "input": "steps"}, "CFG": {"node_id": "5", "input": "cfg"}, "DENOISE": {"node_id": "5", "input": "denoise"}, "OUTPUT_PREFIX": {"node_id": "7", "input": "filename_prefix"}},
        {"transport": "LOOPBACK_HTTP", "worker_policy": "ONE_GPU_TASK"}, _sdxl_t2i,
    ),
}


def _qwen_identity_definition(count: int) -> WorkflowDefinition:
    fields = {
        "model": _field("string", "Qwen Edit GGUF 模型文件", "Qwen-Image-Edit-2511/qwen-image-edit-2511-Q5_K_M.gguf", runtime_input=("UnetLoaderGGUF", "unet_name")),
        "text_encoder": _field("string", "文本编码器文件", "Qwen-Image/qwen_2.5_vl_7b_fp8_scaled.safetensors", runtime_input=("CLIPLoader", "clip_name")),
        "vae": _field("string", "VAE 文件", "Qwen-Image/qwen_image_vae.safetensors", runtime_input=("VAELoader", "vae_name")),
        "prompt": _field("textarea", "验证占位提示词", "根据人物参考图生成单镜头电影画面", effect="SEMANTIC_DEFAULT"),
        "negative_prompt": _field("textarea", "负面提示词", "拼贴，多画幅，变形，多余肢体，文字，水印", effect="SEMANTIC_DEFAULT"),
        "seed": _field("integer", "验证 Seed", 260906, minimum=0, maximum=2**63 - 1, effect="SEMANTIC_DEFAULT"),
        "width": _field("integer", "宽度", 480, minimum=256, maximum=2048, step=16),
        "height": _field("integer", "高度", 832, minimum=256, maximum=2048, step=16),
        "steps": _field("integer", "采样步数", 20, minimum=1, maximum=100),
        "cfg": _field("number", "CFG", 4.0, minimum=1, maximum=10, step=0.1),
        "filename_prefix": _field("string", "输出前缀", "local_drama/identity_keyframe", effect="SEMANTIC_DEFAULT"),
    }
    bindings = {
        "PROMPT": {"node_id": "5", "input": "prompt"},
        "NEGATIVE_PROMPT": {"node_id": "6", "input": "prompt"},
        "SEED": {"node_id": "8", "input": "seed"},
        "WIDTH": {"node_id": "7", "input": "width"}, "HEIGHT": {"node_id": "7", "input": "height"},
        "STEPS": {"node_id": "8", "input": "steps"}, "CFG": {"node_id": "8", "input": "cfg"},
    }
    for i, role in enumerate(IDENTITY_REFERENCE_ROLES[:count], 1):
        fields[f"reference_image_{i}"] = _field("string", f"验证人物参考图 {i}", f"runtime/identity-{i}.png",
            help_text="Comfy input 内相对文件名；正式镜头生成会替换为已批准身份包的正面图。", effect="SEMANTIC_DEFAULT")
        bindings[role] = {"node_id": str(10 + i), "input": "image"}
    return WorkflowDefinition(f"QWEN_IDENTITY_{count}", f"Qwen {count} 人物参考关键帧",
        "将人物参考图分别接入 Qwen Edit 图像条件，重新构图为一个镜头；不使用多视角 LoRA 或拼接。",
        "IMAGE_CONCEPT", "IMAGE", fields, bindings,
        {"transport": "LOOPBACK_HTTP", "worker_policy": "ONE_GPU_TASK"},
        lambda _settings, values: build_qwen_identity_workflow(values, count))


for _reference_count in (1, 2, 3):
    _definition = _qwen_identity_definition(_reference_count)
    WORKFLOW_DEFINITIONS[_definition.code] = _definition


def _qwen_text_definition(capability: str, title: str) -> WorkflowDefinition:
    base = _qwen_identity_definition(0)
    fields = {
        **base.fields,
        "model": _field("string", "Qwen Image GGUF 模型文件", "Qwen-Image-2512/qwen-image-2512-Q5_K_M.gguf", runtime_input=("UnetLoaderGGUF", "unet_name")),
        "prompt": _field("textarea", "验证占位提示词", "电影画面，雨后的旧街，暖色灯光，细腻光影", effect="SEMANTIC_DEFAULT"),
    }
    bindings = {
        **base.semantic_bindings,
        "PROMPT": {"node_id": "5", "input": "text"},
        "NEGATIVE_PROMPT": {"node_id": "6", "input": "text"},
        "OUTPUT_PREFIX": {"node_id": "10", "input": "filename_prefix"},
    }
    return WorkflowDefinition(
        f"QWEN_{capability}", title, "使用 Qwen Image 从文字生成图片，无需先上传参考图；模型文件从本机运行时选择。",
        capability, "IMAGE", fields, bindings, base.runtime_contract,
        lambda _settings, values: build_qwen_text_workflow(values), revision=2,
    )


for _capability, _title in (
    ("IMAGE_CONCEPT", "Qwen 文生关键帧"),
    ("IMAGE_CHARACTER", "Qwen 人物设定图"),
    ("IMAGE_SCENE", "Qwen 场景背景图"),
):
    _definition = _qwen_text_definition(_capability, _title)
    WORKFLOW_DEFINITIONS[_definition.code] = _definition


# --------------------------------------------------------------------------
# Qwen-Image-2.1 (INT8 ConvRot DiT + Qwen3-VL 8B encoder + BF16 VAE)
#
# These are separate definitions rather than a file-name swap on the legacy
# QWEN_* graphs: 2.1 shares one DiT between generation and editing, uses a
# different encoder, returns its own latent from the text-encode node, and
# must not inherit ModelSamplingAuraFlow / EmptySD3LatentImage.
# --------------------------------------------------------------------------

_QWEN21_RUNTIME = {"transport": "LOOPBACK_HTTP", "worker_policy": "ONE_GPU_TASK"}


def _qwen21_loader_fields() -> dict[str, dict[str, Any]]:
    return {
        "model": _field(
            "string", "2.1 DiT（INT8 ConvRot）", DEFAULT_DIFFUSION_MODEL,
            runtime_input=("UNETLoader", "unet_name"),
            help_text="官方 INT8 ConvRot 权重；不与 2512 / Edit-2511 的 GGUF 混用。",
        ),
        "text_encoder": _field(
            "string", "2.1 文本/视觉编码器（INT8 ConvRot）", DEFAULT_TEXT_ENCODER,
            runtime_input=("CLIPLoader", "clip_name"),
            help_text="2.1 必须使用 Qwen3-VL 8B；旧 Qwen2.5-VL 编码器不兼容。",
        ),
        "vae": _field(
            "string", "2.1 VAE（BF16）", DEFAULT_VAE,
            runtime_input=("VAELoader", "vae_name"),
            help_text="新的 64 通道 RGBA VAE；旧 qwen_image_vae 不兼容。",
        ),
    }


def _qwen21_sampler_fields() -> dict[str, dict[str, Any]]:
    return {
        "cfg": _field("number", "CFG", DEFAULT_CFG, minimum=0, maximum=10, step=0.1, advanced=True,
                      help_text="2.1 固定使用 CFG 1（原生条件路径不使用无分类器引导）。"),
        "sampler": _field("string", "Sampler", DEFAULT_SAMPLER, advanced=True),
        "scheduler": _field("string", "Scheduler", DEFAULT_SCHEDULER, advanced=True),
        "denoise": _field("number", "Denoise", DEFAULT_DENOISE, minimum=0, maximum=1, step=0.05, advanced=True),
    }


_QWEN21_T2I_SCALAR_SLOTS = {
    "PROMPT": {"required": True},
    "NEGATIVE_PROMPT": {"required": False},
    "SEED": {"required": True},
    "WIDTH": {"required": False},
    "HEIGHT": {"required": False},
    "STEPS": {"required": False},
    "CFG": {"required": False},
    "OUTPUT_PREFIX": {"required": False},
}

_QWEN21_EDIT_SCALAR_SLOTS = {
    "PROMPT": {"required": True},
    "NEGATIVE_PROMPT": {"required": False},
    "SEED": {"required": True},
    "RESOLUTION": {"required": False},
    "STEPS": {"required": False},
    "CFG": {"required": False},
    "OUTPUT_PREFIX": {"required": False},
}


def _qwen21_t2i_fields() -> dict[str, dict[str, Any]]:
    return {
        **_qwen21_loader_fields(),
        "prompt": _field("textarea", "提示词", "电影画面，雨后的旧街，暖色灯光，细腻光影", effect="SEMANTIC_DEFAULT"),
        "negative_prompt": _field("textarea", "负面提示词", "", required=False, effect="SEMANTIC_DEFAULT",
                                  help_text="2.1 常规任务留空；留空是固定方案的一部分，不是缺失配置。"),
        "seed": _field("integer", "Seed", 9183701, minimum=0, maximum=2**63 - 1, effect="SEMANTIC_DEFAULT",
                       help_text="每个任务显式传入并保存实际值；seed 不是身份一致性保证。"),
        "size_preset": _field(
            "enum", "画布预设", "square", options=t2i_preset_options(),
            help_text="2.1 尺寸按 32 像素网格对齐；预设直接决定宽高与步数，避免在 24GB 卡上误用超大画布。",
        ),
        **_qwen21_sampler_fields(),
        "resolution": _field("integer", "参考图分辨率预算", EDIT_REFERENCE_RESOLUTION, minimum=0, maximum=4096,
                             step=SIZE_STEP, advanced=True,
                             help_text="0 会保留每张参考图自身尺寸，不要无意继承外部模板中的 0。"),
        "filename_prefix": _field("string", "输出前缀", "local_drama/qwen_image_2_1", effect="SEMANTIC_DEFAULT"),
    }


def _qwen21_edit_fields(reference_count: int) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {
        **_qwen21_loader_fields(),
        "prompt": _field("textarea", "编辑指令", "以 image_1 为构图底图，只替换背景，保持人物脸部特征与姿态不变",
                         effect="SEMANTIC_DEFAULT"),
        "negative_prompt": _field("textarea", "负面提示词", "", required=False, effect="SEMANTIC_DEFAULT",
                                  help_text="2.1 常规编辑任务留空。"),
        "seed": _field("integer", "Seed", 9183703, minimum=0, maximum=2**63 - 1, effect="SEMANTIC_DEFAULT"),
        "resolution": _field("integer", "参考图分辨率预算", EDIT_REFERENCE_RESOLUTION, minimum=0, maximum=4096,
                             step=SIZE_STEP,
                             help_text="约 1MP 参考预算。0 会保留原尺寸；编辑输出比例跟随 image_1。"),
        "steps": _field("integer", "采样步数", 40, minimum=1, maximum=200,
                        help_text="2.1 编辑固定采用 40 步；编辑没有独立画布尺寸字段，输出比例跟随 image_1。"),
        **_qwen21_sampler_fields(),
        "cache_device": _field("enum", "前缀缓存设备", "auto", advanced=True,
                               options=[_option(value, value) for value in ("auto", "gpu", "cpu", "off")],
                               help_text="原生条件前缀缓存；off 会每步重算，仅用于排除缓存因素。"),
        "cache_dtype": _field("enum", "前缀缓存精度", "default", advanced=True,
                              options=[_option(value, value) for value in ("default", "int8", "int4")],
                              help_text="default 为无损；int4 会明显增加每步误差。"),
        "filename_prefix": _field("string", "输出前缀", "local_drama/qwen_image_2_1_edit", effect="SEMANTIC_DEFAULT"),
    }
    for index in range(1, reference_count + 1):
        role = "主构图底图" if index == 1 else "人物/对象参考图"
        fields[f"reference_image_{index}"] = _field(
            "string", f"验证{role} {index}", f"runtime/qwen21-edit-{index}.png",
            help_text="Comfy input 内相对文件名；正式运行由媒体语义槽覆盖。", effect="SEMANTIC_DEFAULT",
        )
    return fields


def _qwen21_t2i_compiler(_settings: Settings, values: dict[str, Any]) -> dict[str, Any]:
    preset = dict(T2I_PRESETS[str(values["size_preset"])])
    return build_qwen21_text_workflow({**values, "width": preset["width"], "height": preset["height"], "steps": preset["steps"]})


def _qwen21_edit_compiler(reference_count: int) -> WorkflowCompiler:
    return lambda _settings, values: build_qwen21_edit_workflow(values, reference_count)


def _qwen21_t2i_definition(capability: str, title: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        f"QWEN_IMAGE_21_T2I_{capability.removeprefix('IMAGE_')}",
        title,
        "Qwen-Image-2.1 官方 INT8 ConvRot 文生图：无参考条件，使用 2.1 原生编码与采样路径。",
        capability, "IMAGE",
        _qwen21_t2i_fields(),
        {
            "PROMPT": {"node_id": "4", "input": "prompt"},
            "NEGATIVE_PROMPT": {"node_id": "4", "input": "negative_prompt"},
            "RESOLUTION": {"node_id": "4", "input": "resolution"},
            "SEED": {"node_id": "6", "input": "seed"},
            "WIDTH": {"node_id": "5", "input": "width"},
            "HEIGHT": {"node_id": "5", "input": "height"},
            "STEPS": {"node_id": "6", "input": "steps"},
            "CFG": {"node_id": "6", "input": "cfg"},
            "SAMPLER": {"node_id": "6", "input": "sampler_name"},
            "SCHEDULER": {"node_id": "6", "input": "scheduler"},
            "DENOISE": {"node_id": "6", "input": "denoise"},
            "OUTPUT_PREFIX": {"node_id": "8", "input": "filename_prefix"},
        },
        _QWEN21_RUNTIME, _qwen21_t2i_compiler,
        scalar_input_slots=_QWEN21_T2I_SCALAR_SLOTS,
    )


def _qwen21_edit_definition(reference_count: int) -> WorkflowDefinition:
    code_suffix = "EDIT" if reference_count == 1 else f"EDIT_{reference_count}REF"
    bindings = {
        "PROMPT": {"node_id": "4", "input": "prompt"},
        "NEGATIVE_PROMPT": {"node_id": "4", "input": "negative_prompt"},
        "RESOLUTION": {"node_id": "4", "input": "resolution"},
        "SEED": {"node_id": "6", "input": "seed"},
        "STEPS": {"node_id": "6", "input": "steps"},
        "CFG": {"node_id": "6", "input": "cfg"},
        "SAMPLER": {"node_id": "6", "input": "sampler_name"},
        "SCHEDULER": {"node_id": "6", "input": "scheduler"},
        "DENOISE": {"node_id": "6", "input": "denoise"},
        "OUTPUT_PREFIX": {"node_id": "8", "input": "filename_prefix"},
    }
    for index in range(1, reference_count + 1):
        bindings[f"REFERENCE_IMAGE_{index}"] = {"node_id": str(9 + index - 1), "input": "image"}
    title = "Qwen-Image-2.1 单参考编辑" if reference_count == 1 else "Qwen-Image-2.1 双参考编辑"
    return WorkflowDefinition(
        f"QWEN_IMAGE_21_{code_suffix}", title,
        "Qwen-Image-2.1 原生编辑：采样使用编码节点返回的 latent，输出比例跟随 image_1。",
        "IMAGE_EDIT", "IMAGE",
        _qwen21_edit_fields(reference_count), bindings,
        _QWEN21_RUNTIME, _qwen21_edit_compiler(reference_count),
        scalar_input_slots=_QWEN21_EDIT_SCALAR_SLOTS,
    )


for _capability, _title in (
    ("IMAGE_CONCEPT", "Qwen-Image-2.1 文生概念图"),
    ("IMAGE_CHARACTER", "Qwen-Image-2.1 文生角色图"),
    ("IMAGE_SCENE", "Qwen-Image-2.1 文生场景图"),
):
    _definition = _qwen21_t2i_definition(_capability, _title)
    WORKFLOW_DEFINITIONS[_definition.code] = _definition

for _reference_count in (1, 2):
    _definition = _qwen21_edit_definition(_reference_count)
    WORKFLOW_DEFINITIONS[_definition.code] = _definition

# Smoke is deliberately cheaper than the frozen production preset: the graph
# content keeps 1024x1024 / 40 steps / CFG 1, while the low-cost acceptance
# probe runs at a reduced step count.  Keeping the two separate is what stops a
# smoke configuration from silently becoming the production default again.
_QWEN21_SMOKE_PROMPT = "A small red ceramic teapot beside a green plant on a worn wooden kitchen table, soft morning light, realistic photograph"
_QWEN21_SMOKE_EDIT_PROMPT = "Replace only the background with a plain blue studio wall; keep the subject, viewpoint and lighting unchanged"
QWEN21_SMOKE_REFERENCE_1 = "local_drama_qwen21_smoke_base.png"
QWEN21_SMOKE_REFERENCE_2 = "local_drama_qwen21_smoke_ref.png"


def _qwen21_smoke_contract(definition_code: str) -> dict[str, Any]:
    base = {
        "schema_version": "localdramastudio.comfy-smoke-contract.v1",
        "timeout_seconds": 300,
        "expected_output": {"media_kind": "IMAGE", "min_count": 1, "max_count": 1},
    }
    if "EDIT" in definition_code:
        two_reference = definition_code.endswith("2REF")
        semantic = {
            "PROMPT": _QWEN21_SMOKE_EDIT_PROMPT,
            "SEED": 9183703 if not two_reference else 9183705,
            "REFERENCE_IMAGE_1": QWEN21_SMOKE_REFERENCE_1,
            "RESOLUTION": EDIT_REFERENCE_RESOLUTION,
            "STEPS": 10,
            "CFG": 1.0,
            "OUTPUT_PREFIX": "local_drama/qwen21_edit_smoke",
        }
        if two_reference:
            semantic["REFERENCE_IMAGE_2"] = QWEN21_SMOKE_REFERENCE_2
        return {**base, "semantic_inputs": semantic}
    return {
        **base,
        "semantic_inputs": {
            "PROMPT": _QWEN21_SMOKE_PROMPT,
            "SEED": 9183701,
            "WIDTH": 768,
            "HEIGHT": 768,
            "STEPS": 10,
            "CFG": 1.0,
            "OUTPUT_PREFIX": "local_drama/qwen21_smoke",
        },
    }


class WorkflowDefinitionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def runtime_options(self, code: str, client: ComfyClient) -> dict[str, Any]:
        definition = self.get(code)
        schema = client.object_info()
        fields = {}
        for name, field in definition.fields.items():
            source = field.get("runtime_input")
            if not source:
                continue
            node = schema.get(source["class_type"], {})
            groups = node.get("input", {})
            spec = groups.get("required", {}).get(source["input"]) or groups.get("optional", {}).get(source["input"])
            choices = spec[0] if isinstance(spec, list) and spec else None
            if choices == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
                choices = spec[1].get("options")
            fields[name] = {"options": [_option(value, str(value)) for value in choices] if isinstance(choices, list) else [], "runtime_input": source}
        return {"definition_code": definition.code, "fields": fields, "runtime_contacted": True}

    def list_definitions(self) -> list[dict[str, Any]]:
        result = []
        for definition in WORKFLOW_DEFINITIONS.values():
            payload = definition.payload()
            if definition.code == "H3_REF2V":
                try:
                    capability = H3WorkflowFactory(self.settings).ref2va_capability()
                    payload["available"] = bool(capability.get("supported"))
                    payload["availability"] = capability
                except DomainRuleError as error:
                    payload["available"] = False
                    payload["availability"] = {"supported": False, "reason_code": error.code}
            else:
                payload["available"] = True
            result.append(payload)
        return result

    def get(self, code: str) -> WorkflowDefinition:
        definition = WORKFLOW_DEFINITIONS.get(str(code).strip().upper())
        if definition is None:
            raise DomainRuleError("WORKFLOW_DEFINITION_NOT_FOUND", "工作流定义不存在", {"definition_code": code})
        return definition

    @staticmethod
    def _normalize_value(name: str, spec: dict[str, Any], raw: Any) -> Any:
        value = spec.get("default") if raw is None else raw
        if value is None:
            raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_REQUIRED", "工作流参数不能为空", {"field": name})
        kind = str(spec.get("type"))
        if kind == "integer":
            if isinstance(value, bool):
                raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_INVALID", "工作流参数类型无效", {"field": name})
            try:
                value = int(value)
            except (TypeError, ValueError) as error:
                raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_INVALID", "工作流参数必须是整数", {"field": name}) from error
        elif kind == "number":
            if isinstance(value, bool):
                raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_INVALID", "工作流参数类型无效", {"field": name})
            try:
                value = float(value)
            except (TypeError, ValueError) as error:
                raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_INVALID", "工作流参数必须是数字", {"field": name}) from error
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_INVALID", "工作流参数必须是布尔值", {"field": name})
        else:
            value = str(value).strip()
            if spec.get("required", True) and not value:
                raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_REQUIRED", "工作流参数不能为空", {"field": name})
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if spec.get("minimum") is not None and value < spec["minimum"]:
                raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_RANGE", "工作流参数低于最小值", {"field": name, "minimum": spec["minimum"]})
            if spec.get("maximum") is not None and value > spec["maximum"]:
                raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_RANGE", "工作流参数超过最大值", {"field": name, "maximum": spec["maximum"]})
        options = spec.get("options")
        if isinstance(options, list) and value not in {item.get("value") for item in options if isinstance(item, dict)}:
            if name == "tier":
                raise DomainRuleError("H3_TIER_UNSUPPORTED", "不支持的 H3 生产档位", {"tier_code": value})
            raise DomainRuleError("WORKFLOW_DEFINITION_VALUE_OPTION", "工作流参数不在允许选项中", {"field": name})
        return value

    def instantiate(self, definition_code: str, parameters: dict[str, Any]) -> dict[str, Any]:
        definition = self.get(definition_code)
        unknown = sorted(set(parameters) - set(definition.fields))
        if unknown:
            raise DomainRuleError("WORKFLOW_DEFINITION_FIELD_UNKNOWN", "工作流定义不支持提交的参数", {"fields": unknown})
        values = {name: self._normalize_value(name, spec, parameters.get(name)) for name, spec in definition.fields.items()}
        workflow = definition.compiler(self.settings, values)
        input_slots = {
            role: {"required": True, "min": 1, "max": 1, "allowed_media_kinds": ["IMAGE"]}
            for role in definition.semantic_bindings
            if role in COMFY_IMAGE_INPUT_ROLES
        }
        for role, slot in (definition.scalar_input_slots or {}).items():
            if role not in input_slots and role in definition.semantic_bindings:
                input_slots[role] = dict(slot)
        parameter_effects = {name: spec["effect"] for name, spec in definition.fields.items()}
        if definition.code.startswith("H3_"):
            if values["use_production_tier"]:
                parameter_effects["duration_seconds"] = "INACTIVE_TIER_CONTROLS_FRAMES"
                parameter_effects["sigma_points"] = "INACTIVE_TIER_CONTROLS_STEPS"
            else:
                parameter_effects["tier"] = "INACTIVE_FREEFORM_MODE"
        contract = {
            "capability": definition.capability,
            "definition": {"code": definition.code, "revision": definition.revision},
            "input_slots": input_slots,
            "requires_explicit_validation": True,
            "local_only": True,
            "parameter_effects": parameter_effects,
            "authoring_parameters": values,
        }
        if definition.code in {"QWEN_IMAGE_CONCEPT", "QWEN_IMAGE_CHARACTER", "QWEN_IMAGE_SCENE"}:
            contract["smoke_contract"] = {
                "schema_version": "localdramastudio.comfy-smoke-contract.v1",
                "semantic_inputs": {
                    "PROMPT": "A quiet street after rain, warm light, cinematic composition",
                    "NEGATIVE_PROMPT": "blurry, watermark, text",
                    "SEED": 260914,
                    "WIDTH": 256,
                    "HEIGHT": 256,
                    "STEPS": 4,
                    "CFG": 4.0,
                    "OUTPUT_PREFIX": "local_drama/qwen_capability_smoke",
                },
                "timeout_seconds": 300,
                "expected_output": {"media_kind": "IMAGE", "min_count": 1, "max_count": 1},
            }
            parse_comfy_smoke_contract(contract, definition.semantic_bindings)
        if definition.code.startswith("QWEN_IMAGE_21_"):
            contract["smoke_contract"] = _qwen21_smoke_contract(definition.code)
            contract["license"] = {
                "model_code": "qwen-image-2.1-int8-convrot",
                "license_id": "qwen-research",
                "commercial_use_requires_authorization": True,
            }
            parse_comfy_smoke_contract(contract, definition.semantic_bindings)
        if definition.code.startswith("H3_"):
            contract["production_tier"] = values["tier"] if values["use_production_tier"] else None
            contract["sampling_mode"] = "PRODUCTION_TIER" if values["use_production_tier"] else "FREEFORM"
            contract["runtime_overrides"] = {
                "acceleration": values["acceleration"], "lora_strength": values["lora_strength"], "native_audio": values["native_audio"]
            }
        return {"workflow": workflow, "contract": contract, "node_bindings": definition.semantic_bindings, "runtime_contract": definition.runtime_contract, "parameters": values}

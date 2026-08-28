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

from .h3_workflows import H3WorkflowFactory, production_tiers_payload

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
        "native_audio": _field("boolean", "生成原生音轨", True, advanced=True),
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


class WorkflowDefinitionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
            role: {"required": role in {"FIRST_FRAME", "REFERENCE_IMAGE"}, "min": 1 if role in {"FIRST_FRAME", "REFERENCE_IMAGE"} else 0, "max": 1}
            for role in definition.semantic_bindings
            if role in {"FIRST_FRAME", "REFERENCE_IMAGE"}
        }
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
        if definition.code.startswith("H3_"):
            contract["production_tier"] = values["tier"] if values["use_production_tier"] else None
            contract["sampling_mode"] = "PRODUCTION_TIER" if values["use_production_tier"] else "FREEFORM"
            contract["runtime_overrides"] = {
                "acceleration": values["acceleration"], "lora_strength": values["lora_strength"], "native_audio": values["native_audio"]
            }
        return {"workflow": workflow, "contract": contract, "node_bindings": definition.semantic_bindings, "runtime_contract": definition.runtime_contract, "parameters": values}

"""Keep actionable model selection errors without exposing prompt payloads."""

from typing import Any

_MODEL_INPUTS = {
    "UnetLoaderGGUF": {"unet_name"}, "UNETLoader": {"unet_name"},
    "CLIPLoader": {"clip_name", "type", "device"},
    "DualCLIPLoader": {"clip_name1", "clip_name2", "type", "device"},
    "VAELoader": {"vae_name"}, "CheckpointLoaderSimple": {"ckpt_name"},
    "LoraLoaderModelOnly": {"lora_name"},
}


def _relative_value(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 512:
        return None
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized or ".." in normalized.split("/"):
        return None
    return value


def summarize_node_errors(raw: Any) -> list[dict[str, Any]]:
    result = []
    if not isinstance(raw, dict):
        return result
    for node_id, item in list(raw.items())[:32]:
        if not isinstance(item, dict):
            continue
        class_type = str(item.get("class_type") or "UNKNOWN")[:120]
        errors = [entry for entry in item.get("errors", []) if isinstance(entry, dict)][:8]
        summary = {"node_id": str(node_id)[:80], "class_type": class_type,
                   "error_types": [str(entry["type"])[:120] for entry in errors if entry.get("type")]}
        selections = []
        for entry in errors:
            info = entry.get("extra_info")
            if entry.get("type") != "value_not_in_list" or not isinstance(info, dict):
                continue
            name = info.get("input_name")
            if name not in _MODEL_INPUTS.get(class_type, set()):
                continue
            value = _relative_value(info.get("received_value"))
            if value is None:
                continue
            detail = {"input": name, "value": value}
            config = info.get("input_config")
            if isinstance(config, list) and config and isinstance(config[0], list):
                detail["allowed_values"] = [safe for choice in config[0][:128] if (safe := _relative_value(choice)) is not None]
            selections.append(detail)
        if selections:
            summary["model_selection_errors"] = selections
        result.append(summary)
    return result

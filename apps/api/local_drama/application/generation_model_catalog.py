"""Model-first catalog built from capability-specific execution routes.

ExecutionProfileVersion remains the immutable executable route.  This read
model deliberately groups routes that use the same underlying model so the
product can present one model with several supported generation actions.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def action_for_capability(capability: str) -> str | None:
    canonical = capability.strip().upper()
    if canonical == "LLM_STORY_PARSE":
        return "TEXT_PLANNING"
    if canonical.startswith("IMAGE_"):
        return "TEXT_TO_IMAGE"
    if canonical == "VIDEO_T2V":
        return "TEXT_TO_VIDEO"
    if canonical == "VIDEO_I2V":
        return "IMAGE_TO_VIDEO"
    return None


def category_for_capability(capability: str) -> str | None:
    action = action_for_capability(capability)
    if action == "TEXT_PLANNING":
        return "TEXT"
    if action == "TEXT_TO_IMAGE":
        return "IMAGE"
    if action in {"TEXT_TO_VIDEO", "IMAGE_TO_VIDEO"}:
        return "VIDEO"
    return None


def _identity(profile: dict[str, Any], category: str) -> tuple[str, str]:
    raw_bundle = profile.get("model_bundle")
    bundle: dict[str, Any] = raw_bundle if isinstance(raw_bundle, dict) else {}
    declared = str(bundle.get("model_family") or bundle.get("model") or bundle.get("model_ref") or "").strip()
    provider = str(bundle.get("provider") or "LOCAL").strip().upper()
    artifact_ids = sorted(str(item) for item in bundle.get("artifact_ids", []) if str(item).strip())
    source = {"category": category, "provider": provider, "declared": declared, "artifacts": artifact_ids}
    if not declared and not artifact_ids:
        source["execution_profile_id"] = str(profile.get("id") or "")
    digest = hashlib.sha256(_json(source).encode("utf-8")).hexdigest()[:20]
    return f"generation-model:{digest}", declared


def _display_name(profile: dict[str, Any], declared: str, category: str) -> str:
    if declared:
        return declared
    title = str(profile.get("title") or profile.get("code") or "未命名模型")
    cleaned = re.sub(r"\b(?:LLM_STORY_PARSE|IMAGE_[A-Z0-9_]+|VIDEO_(?:T2V|I2V)|T2V|I2V|PROFILE)\b", "", title, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ·-_/")
    if cleaned:
        return cleaned
    return {"TEXT": "文字模型", "IMAGE": "图片模型", "VIDEO": "视频模型"}[category]


def build_generation_model_catalog(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        capability = str(profile.get("capability") or "").upper()
        action = action_for_capability(capability)
        category = category_for_capability(capability)
        if action is None or category is None:
            continue
        model_id, declared = _identity(profile, category)
        model = grouped.setdefault(
            model_id,
            {
                "id": model_id,
                "name": _display_name(profile, declared, category),
                "category": category,
                "capabilities": [],
                "actions": [],
                "routes": [],
            },
        )
        if capability not in model["capabilities"]:
            model["capabilities"].append(capability)
        if action not in model["actions"]:
            model["actions"].append(action)
        status = str(profile.get("status") or "UNKNOWN").upper()
        workflow_version_id = str(profile.get("workflow_version_id") or "").strip() or None
        model["routes"].append(
            {
                "action": action,
                "capability": capability,
                "profile_version_id": str(profile["version_id"]),
                "profile_title": str(profile.get("title") or model["name"]),
                "version_no": int(profile.get("version_no") or 1),
                "status": status,
                "workflow_version_id": workflow_version_id,
                "executable": status == "PUBLISHED" and workflow_version_id is not None,
            }
        )
    models = list(grouped.values())
    for model in models:
        model["capabilities"].sort()
        model["actions"].sort()
        model["routes"].sort(key=lambda item: (item["action"], not item["executable"], -item["version_no"]))
        model["executable"] = any(route["executable"] for route in model["routes"])
    return sorted(models, key=lambda item: (item["category"], item["name"].casefold(), item["id"]))

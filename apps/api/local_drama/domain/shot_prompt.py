from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mapping(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _dialogue_line(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    text = _text(value.get("text"))
    if not text:
        return ""
    speaker = _text(value.get("speaker"))
    if not speaker or text.startswith((f"{speaker}：", f"{speaker}:")):
        return text
    return f"{speaker}：{text}"


def normalize_prompt_modifiers(value: object) -> list[str]:
    """Return stable, unique prompt modifiers without inventing semantics."""
    if isinstance(value, str):
        candidates: Iterable[object] = value.replace("，", ",").split(",")
    elif isinstance(value, list):
        candidates = value
    else:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = _text(candidate)
        folded = text.casefold()
        if not text or folded in seen:
            continue
        seen.add(folded)
        normalized.append(text)
    return normalized


def compose_shot_prompt(fields: dict[str, Any], *, shot_code: str | None = None) -> str:
    """Compile DirectorIntent facts into the canonical shot-generation prompt.

    The compiler is deliberately deterministic and contains no model calls. It
    keeps batch generation and single/episode generation on the same persisted
    creative facts; prompt modifiers are an explicit final layer rather than a
    destructive rewrite of the shot's creative intent.
    """
    camera = _mapping(fields.get("camera_plan"))
    performance = _mapping(fields.get("performance"))
    composition = _mapping(fields.get("composition"))
    parts: list[str] = []
    if _text(shot_code):
        parts.append(f"镜头 {_text(shot_code)}")
    action = _text(fields.get("subject_action"))
    if action:
        parts.append(action)
    intent = _text(fields.get("creative_intent"))
    if intent:
        parts.append(f"情绪基调：{intent}")
    shot_type = _text(camera.get("shot_type")) or _text(fields.get("shot_type"))
    if shot_type:
        parts.append(f"景别 {shot_type}")
    framing = _text(composition.get("framing")) or _text(composition.get("preset"))
    if framing:
        parts.append(f"构图 {framing}")
    movement = _text(camera.get("movement"))
    if movement and movement != "STATIC":
        parts.append(f"运镜 {movement}")
    emotion = _text(performance.get("emotion"))
    if emotion:
        parts.append(f"情绪 {emotion}")
    environment = _text(fields.get("environment"))
    if environment:
        parts.append(f"环境：{environment}")
    dialogue_value = fields.get("dialogue")
    dialogue_parts: list[str] = []
    if isinstance(dialogue_value, str):
        dialogue_parts.append(_dialogue_line(dialogue_value))
    elif isinstance(dialogue_value, list):
        for line in dialogue_value:
            dialogue_parts.append(_dialogue_line(line))
    dialogue = "；".join(item for item in dialogue_parts if item)
    if dialogue:
        parts.append(f"对白：{dialogue}")
    modifiers = normalize_prompt_modifiers(fields.get("prompt_modifiers"))
    if modifiers:
        parts.append(f"统一视觉修饰：{'，'.join(modifiers)}")
    return "，".join(parts)

"""Pure canonicalization rules shared by timeline application services."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from local_drama.domain.errors import DomainRuleError

CANONICAL_TRACK_TYPES = {"DIALOGUE": "DIALOGUE", "BGM": "BGM", "SFX": "SFX"}
LEGACY_TRACK_TYPES = {"MUSIC": "BGM", "ENVIRONMENT": "SFX"}


def canonical_track_type(track_type: str) -> str | None:
    return CANONICAL_TRACK_TYPES.get(track_type) or LEGACY_TRACK_TYPES.get(track_type)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalized_text_with_offsets(value: str) -> tuple[str, list[int]]:
    """Collapse whitespace while retaining offsets into authoritative text."""

    normalized: list[str] = []
    offsets: list[int] = []
    in_whitespace = False
    for offset, character in enumerate(value):
        if character.isspace():
            if normalized and not in_whitespace:
                normalized.append(" ")
                offsets.append(offset)
            in_whitespace = True
            continue
        normalized.append(character)
        offsets.append(offset)
        in_whitespace = False
    if normalized and normalized[-1] == " ":
        normalized.pop()
        offsets.pop()
    return "".join(normalized), offsets


def subtitle_time(value_us: int, *, decimal_separator: str = ",") -> str:
    if value_us < 0:
        raise DomainRuleError("TIMELINE_TIME_INVALID", "时间戳不能为负数")
    total_ms = value_us // 1000
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{decimal_separator}{millis:03d}"

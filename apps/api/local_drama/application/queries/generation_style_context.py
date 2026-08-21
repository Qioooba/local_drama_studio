"""Read-only, declarative Style/Brand context for generation snapshots.

This module deliberately reads database metadata only.  It never opens a
MediaVersion path and never copies media bytes into a job snapshot.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

_BRAND_SECTIONS = frozenset({"colors", "typography", "spacing", "radii", "motion", "iconography", "logo"})
_STYLE_FIELDS = frozenset(
    {
        "visual_style",
        "style",
        "palette",
        "colors",
        "lighting",
        "composition",
        "texture",
        "motion",
        "typography",
        "keywords",
        "notes",
        "prompt",
        "negative_prompt",
        "negative_style_rules",
    }
)
_NEGATIVE_FIELDS = frozenset({"negative_prompt", "negative_style_rules"})
_FORBIDDEN_KEY_PARTS = (
    "shell",
    "python",
    "command",
    "executor",
    "script",
    "code",
    "runtime",
    "provider",
    "network",
    "path",
    "url",
    "uri",
    "file",
    "media",
)
_MAX_DEPTH = 6
_MAX_ITEMS = 64
_MAX_STRING_LENGTH = 2_000


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return bool(normalized) and not any(part in normalized for part in _FORBIDDEN_KEY_PARTS)


def _declarative(value: object, *, depth: int = 0) -> object | None:
    """Return bounded JSON data with execution/file configuration removed."""
    if depth > _MAX_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.strip()[:_MAX_STRING_LENGTH]
    if isinstance(value, list):
        return [_declarative(item, depth=depth + 1) for item in value[:_MAX_ITEMS]]
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key in sorted(value, key=str)[:_MAX_ITEMS]:
            if not _safe_key(key):
                continue
            sanitized = _declarative(value[key], depth=depth + 1)
            if sanitized is not None:
                result[str(key)] = sanitized
        return result
    return None


def _parsed_object(raw: object) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _prompt_line(prefix: str, code: str, version: str, value: object) -> str:
    return f"{prefix} · {code} {version}: {_canonical(value)}"


def build_generation_style_context(connection: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    """Freeze active project Style/Brand facts into a deterministic snapshot.

    STYLE_REFERENCE rows contribute provenance metadata only (id/version/hash);
    their files are never accessed. Subtitle STYLE templates are excluded from
    visual generation because they use a separate, explicit rendering contract.
    """
    brand_kits: list[dict[str, Any]] = []
    positive_lines: list[str] = []
    negative_lines: list[str] = []
    brand_rows = connection.execute(
        """SELECT id,code,title,version_no,tokens_json
        FROM brand_kits WHERE project_id=? AND status='ACTIVE'
        ORDER BY code,version_no,id""",
        (project_id,),
    ).fetchall()
    for row in brand_rows:
        raw = _parsed_object(row["tokens_json"])
        selected = {key: raw[key] for key in sorted(raw) if key in _BRAND_SECTIONS}
        tokens = _declarative(selected)
        if not isinstance(tokens, dict) or not tokens:
            continue
        item = {
            "id": str(row["id"]),
            "code": str(row["code"]),
            "title": str(row["title"]),
            "version_no": int(row["version_no"]),
            "tokens": tokens,
            "tokens_hash": _digest(tokens),
        }
        brand_kits.append(item)
        positive_lines.append(_prompt_line("BrandKit", item["code"], f"v{item['version_no']}", tokens))

    style_entries: list[dict[str, Any]] = []
    style_rows = connection.execute(
        """SELECT ce.id,ce.code,ce.title,ce.current_revision_id,
        cer.revision_no,cer.content_hash,cer.content_json
        FROM creative_entries ce
        JOIN creative_entry_revisions cer ON cer.id=ce.current_revision_id
        WHERE ce.project_id=? AND ce.kind='STYLE'
        ORDER BY ce.code,cer.revision_no,ce.id""",
        (project_id,),
    ).fetchall()
    for row in style_rows:
        raw = _parsed_object(row["content_json"])
        if raw.get("schema_version") == "localdrama.subtitle-style.v1":
            continue
        selected = {key: raw[key] for key in sorted(raw) if key in _STYLE_FIELDS}
        content = _declarative(selected)
        if not isinstance(content, dict) or not content:
            continue
        positive = {key: value for key, value in content.items() if key not in _NEGATIVE_FIELDS}
        negative = {key: value for key, value in content.items() if key in _NEGATIVE_FIELDS}
        item = {
            "id": str(row["id"]),
            "code": str(row["code"]),
            "title": str(row["title"]),
            "revision_id": str(row["current_revision_id"]),
            "revision_no": int(row["revision_no"]),
            "content_hash": str(row["content_hash"]),
            "declarative_content": content,
            "declarative_content_hash": _digest(content),
        }
        style_entries.append(item)
        if positive:
            positive_lines.append(_prompt_line("Style", item["code"], f"r{item['revision_no']}", positive))
        if negative:
            negative_lines.append(_prompt_line("Style negative", item["code"], f"r{item['revision_no']}", negative))

    reference_rows = connection.execute(
        """SELECT sr.id,sr.story_asset_id,sr.media_version_id,sr.label,sr.priority,sr.is_locked,
        sa.code AS asset_code,sa.name AS asset_name,mv.version_no AS media_version_no,
        mv.sha256,mv.integrity_status
        FROM story_asset_references sr
        JOIN story_assets sa ON sa.id=sr.story_asset_id
        JOIN media_versions mv ON mv.id=sr.media_version_id
        WHERE sr.project_id=? AND sr.reference_kind='STYLE_REFERENCE' AND sr.status='ACTIVE'
        ORDER BY sr.priority,sr.created_at,sr.id""",
        (project_id,),
    ).fetchall()
    style_references = [
        {
            "reference_id": str(row["id"]),
            "story_asset_id": str(row["story_asset_id"]),
            "asset_code": str(row["asset_code"]),
            "asset_name": str(row["asset_name"]),
            "media_version_id": str(row["media_version_id"]),
            "media_version_no": int(row["media_version_no"]),
            "sha256": str(row["sha256"]),
            "integrity_status": str(row["integrity_status"]),
            "label": str(row["label"] or ""),
            "priority": int(row["priority"]),
            "is_locked": bool(row["is_locked"]),
        }
        for row in reference_rows
    ]

    if not brand_kits and not style_entries and not style_references:
        return None
    body: dict[str, Any] = {
        "schema_version": "localdrama.generation-style-context.v1",
        "brand_kits": brand_kits,
        "style_entries": style_entries,
        "style_references": style_references,
    }
    if positive_lines:
        body["prompt_context"] = "\n".join(positive_lines)
    if negative_lines:
        body["negative_prompt_context"] = "\n".join(negative_lines)
    return {**body, "context_hash": _digest(body)}

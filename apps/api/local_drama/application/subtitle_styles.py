"""Project-level subtitle style templates (P1-12).

Persistence decision
--------------------
The constraint "no new migration" rules out a dedicated table.  Subtitle styles
are already carried per-cue in ``subtitle_cues.style_json``, and project-scoped
versioned JSON entries already exist as **creative entries** whose schema even
declares a ``STYLE`` kind (``ck_creative_entries_kind``).  Style templates are
therefore stored as ``kind='STYLE'`` creative entries with
``content = {schema_version, font, size, color, position, outline}``:

* no new table, no migration, no change to ``configuration.py`` (which is out
  of scope for this batch);
* immutable revision history + project uniqueness come for free from
  ``CreativeEntryService``;
* the renderer consumes the same validated style object that gets stored in
  each cue's ``style_json``.

The only template-specific behaviour added here is validation, the
create-or-update-by-code convenience and an audit trail (the generic creative
entry service does not write ``audit_events``).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from local_drama.application.creative_entries import CreativeEntryService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

DEFAULT_SUBTITLE_STYLE: dict[str, Any] = {
    "font": "Microsoft YaHei",
    "size": 48,
    "color": "#FFFFFF",
    "position": "BOTTOM",
    "outline": 2,
}

STYLE_TEMPLATE_KIND = "STYLE"
_STYLE_SCHEMA_VERSION = "localdrama.subtitle-style.v1"
_POSITIONS = frozenset({"TOP", "CENTER", "BOTTOM"})
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def validate_style(style: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a subtitle style object.

    Accepted fields: ``font`` (non-empty string), ``size`` (int 8-160),
    ``color`` (``#RRGGBB``), ``position`` (TOP/CENTER/BOTTOM),
    ``outline`` (int 0-12).  Unknown fields and out-of-range values are
    rejected so a mistyped template can never silently corrupt ASS output.
    """
    if not isinstance(style, dict):
        raise DomainRuleError("SUBTITLE_STYLE_INVALID", "字幕样式必须是对象")
    unknown = set(style) - {"font", "size", "color", "position", "outline"}
    if unknown:
        raise DomainRuleError("SUBTITLE_STYLE_INVALID", "字幕样式包含未知字段", {"unknown": sorted(unknown)})

    font = style.get("font", DEFAULT_SUBTITLE_STYLE["font"])
    if not isinstance(font, str) or not font.strip():
        raise DomainRuleError("SUBTITLE_STYLE_INVALID", "字幕样式 font 必须是非空字符串")

    size = style.get("size", DEFAULT_SUBTITLE_STYLE["size"])
    if isinstance(size, bool) or not isinstance(size, int) or not 8 <= size <= 160:
        raise DomainRuleError("SUBTITLE_STYLE_INVALID", "字幕样式 size 必须是 8—160 的整数", {"size": size})

    color = style.get("color", DEFAULT_SUBTITLE_STYLE["color"])
    if not isinstance(color, str) or not _COLOR_RE.match(color):
        raise DomainRuleError("SUBTITLE_STYLE_INVALID", "字幕样式 color 必须是 #RRGGBB 形式", {"color": color})

    position = style.get("position", DEFAULT_SUBTITLE_STYLE["position"])
    if position not in _POSITIONS:
        raise DomainRuleError("SUBTITLE_STYLE_INVALID", "字幕样式 position 必须是 TOP/CENTER/BOTTOM", {"position": position})

    outline = style.get("outline", DEFAULT_SUBTITLE_STYLE["outline"])
    if isinstance(outline, bool) or not isinstance(outline, int) or not 0 <= outline <= 12:
        raise DomainRuleError("SUBTITLE_STYLE_INVALID", "字幕样式 outline 必须是 0—12 的整数", {"outline": outline})

    return {"font": font.strip(), "size": size, "color": color.upper(), "position": position, "outline": outline}


def _style_content(style: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": _STYLE_SCHEMA_VERSION, **validate_style(style)}


class SubtitleStyleTemplateService:
    """Project-level subtitle style template CRUD on top of STYLE creative entries."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._entries = CreativeEntryService(database)

    def list_templates(self, project_id: str) -> list[dict[str, Any]]:
        return self._entries.list_entries(project_id, kind=STYLE_TEMPLATE_KIND)

    def get_template(self, entry_id: str) -> dict[str, Any]:
        entry = self._entries.get(entry_id)
        if entry["kind"] != STYLE_TEMPLATE_KIND:
            raise DomainRuleError("SUBTITLE_STYLE_TEMPLATE_KIND_INVALID", "该创作资料不是字幕样式模板")
        return entry

    def save_template(
        self,
        project_id: str,
        code: str,
        title: str,
        style: dict[str, Any],
        change_note: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Create or update (by project+code) a style template; returns the entry."""
        content = _style_content(style)
        existing = self._find(project_id, code)
        if existing is None:
            created = self._entries.create(project_id, STYLE_TEMPLATE_KIND, code, title, content, change_note, actor)
            self._audit(actor, "SUBTITLE_STYLE_TEMPLATE_CREATED", created["id"], {"project_id": project_id, "code": code})
            return created
        self._entries.save_revision(existing, content, change_note, actor)
        self._audit(actor, "SUBTITLE_STYLE_TEMPLATE_UPDATED", existing, {"project_id": project_id, "code": code})
        return self._entries.get(existing)

    def delete_template(self, entry_id: str, actor: str = "local-user") -> dict[str, Any]:
        """Delete a style template (physical delete; templates are configuration, not history)."""
        entry = self._entries.get(entry_id)
        if entry["kind"] != STYLE_TEMPLATE_KIND:
            raise DomainRuleError("SUBTITLE_STYLE_TEMPLATE_KIND_INVALID", "该创作资料不是字幕样式模板")
        with self.database.transaction() as connection:
            # creative_entry_revisions.parent_revision_id is RESTRICT-declared,
            # so revisions must be removed newest-first before the entry row.
            revision_ids = [row["id"] for row in connection.execute(
                "SELECT id FROM creative_entry_revisions WHERE entry_id=? ORDER BY revision_no DESC", (entry_id,)
            ).fetchall()]
            for revision_id in revision_ids:
                connection.execute("DELETE FROM creative_entry_revisions WHERE id=?", (revision_id,))
            connection.execute("DELETE FROM creative_entries WHERE id=?", (entry_id,))
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'SUBTITLE_STYLE_TEMPLATE_DELETED', 'creative_entry', ?, ?, ?)",
                (actor, entry_id, "删除字幕样式模板", _json({"project_id": str(entry["project_id"]), "code": str(entry["code"])})),
            )
        return {"id": entry_id, "deleted": True}

    def _find(self, project_id: str, code: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM creative_entries WHERE project_id=? AND kind=? AND code=?",
                (project_id, STYLE_TEMPLATE_KIND, code),
            ).fetchone()
        return str(row["id"]) if row else None

    def _audit(self, actor: str, action: str, subject_id: str, metadata: dict[str, Any]) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', ?, 'creative_entry', ?, ?, ?)",
                (actor, action, subject_id, "保存字幕样式模板", _json(metadata)),
            )

"""Canonical audio/caption requirements for an episode.

The production gate must be driven by facts in the episode, not by a fixed
three-track checklist.  In particular, an episode with no music or effects
cue must not be forced to invent either track.  This module is deliberately
read-only and accepts the caller's open SQLite connection so every read model
uses the same projection.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_AUDIO_TRACKS = ("DIALOGUE", "BGM", "SFX")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _has_value(value: Any) -> bool:
    """Return whether a cue value is materially populated.

    Cue fields in older imported shot revisions have appeared as ``null``, an
    empty string, an empty list or an empty object.  Those are absence, not a
    production requirement.
    """

    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return any(_has_value(item) for item in value) if not isinstance(value, dict) else any(
            _has_value(item) for item in value.values()
        )
    return True


def _field_cue_counts(connection: sqlite3.Connection, episode_id: str) -> dict[str, int]:
    """Read cue-like fields from the canonical shot revision JSON.

    The current schema has no separate music/sfx cue tables.  Keeping this
    compatibility reader means imported projects that already carry cue
    facts in their frozen shot fields still participate in the same policy;
    it never treats the free-form ``sound`` field as a cue by itself.
    """

    counts = {"caption_cues": 0, "music_cues": 0, "sfx_cues": 0}
    rows = connection.execute(
        """SELECT sr.fields_json
           FROM shot_revisions sr
           JOIN shots sh ON sh.id=sr.shot_id
          WHERE sh.episode_id=?""",
        (episode_id,),
    ).fetchall()
    keys = {
        "caption_cues": ("caption_cue", "caption_cues"),
        "music_cues": ("music_cue", "music_cues"),
        "sfx_cues": ("sfx_cue", "sfx_cues"),
    }
    for row in rows:
        try:
            fields = json.loads(str(row["fields_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            fields = {}
        if not isinstance(fields, dict):
            continue
        for result_key, aliases in keys.items():
            for alias in aliases:
                if alias in fields and _has_value(fields[alias]):
                    value = fields[alias]
                    counts[result_key] += len(value) if isinstance(value, list) else 1
                    break
    return counts


def _table_cue_count(connection: sqlite3.Connection, table: str, episode_id: str) -> int:
    """Count rows in an optional episode cue table when one exists.

    This is intentionally conservative: only tables with a direct
    ``episode_id`` column are considered.  Unknown legacy schemas are not
    guessed and therefore cannot accidentally make a gate pass.
    """

    if not _table_exists(connection, table):
        return 0
    columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if "episode_id" not in columns:
        return 0
    row = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE episode_id=?", (episode_id,)).fetchone()
    return int(row[0] or 0) if row is not None else 0


def canonical_audio_requirements(connection: sqlite3.Connection, episode_id: str) -> dict[str, Any]:
    """Return the episode's evidence-based required audio tracks.

    ``DIALOGUE`` is required only when a non-empty canonical dialogue line is
    present.  BGM and SFX are required only when their corresponding cue facts
    exist.  Caption facts are returned separately because subtitle delivery is
    not an audio track, while still allowing the subtitle gate to share the
    same source of truth.
    """

    dialogue_row = connection.execute(
        """SELECT COUNT(*) FROM dialogue_lines
           WHERE episode_id=? AND trim(COALESCE((
             SELECT dtr.text FROM dialogue_text_revisions dtr
             WHERE dtr.dialogue_line_id=dialogue_lines.id
             ORDER BY dtr.revision_no DESC LIMIT 1
           ),''))<>''""",
        (episode_id,),
    ).fetchone()
    dialogue_count = int(dialogue_row[0] or 0) if dialogue_row is not None else 0
    field_counts = _field_cue_counts(connection, episode_id)
    counts = {
        "dialogue_lines": dialogue_count,
        "caption_cues": field_counts["caption_cues"] + _table_cue_count(connection, "caption_cues", episode_id),
        "music_cues": field_counts["music_cues"] + _table_cue_count(connection, "music_cues", episode_id),
        "sfx_cues": field_counts["sfx_cues"] + _table_cue_count(connection, "sfx_cues", episode_id),
    }
    required_tracks = []
    if counts["dialogue_lines"] > 0:
        required_tracks.append("DIALOGUE")
    if counts["music_cues"] > 0:
        required_tracks.append("BGM")
    if counts["sfx_cues"] > 0:
        required_tracks.append("SFX")
    return {
        "required_tracks": [track for track in _AUDIO_TRACKS if track in required_tracks],
        "subtitle_required": bool(counts["dialogue_lines"] or counts["caption_cues"]),
        "counts": counts,
        "source": "CANONICAL_DIALOGUE_AND_CUE_FACTS",
    }


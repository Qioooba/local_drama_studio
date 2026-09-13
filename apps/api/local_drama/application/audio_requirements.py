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

from local_drama.domain.errors import DomainRuleError

_AUDIO_TRACKS = ("DIALOGUE", "BGM", "SFX")
_NARRATOR_SPEAKERS = frozenset({"旁白", "画外音", "NARRATOR"})


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
        return any(_has_value(item) for item in value) if not isinstance(value, dict) else any(_has_value(item) for item in value.values())
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
        """SELECT sh.id AS shot_id,sh.current_revision_id,sr.id AS revision_id,sr.fields_json
           FROM shots sh
           LEFT JOIN shot_revisions sr
             ON sr.id=sh.current_revision_id AND sr.shot_id=sh.id
          WHERE sh.episode_id=? AND sh.archived_at IS NULL""",
        (episode_id,),
    ).fetchall()
    keys = {
        "caption_cues": ("caption_cue", "caption_cues"),
        "music_cues": ("music_cue", "music_cues"),
        "sfx_cues": ("sfx_cue", "sfx_cues"),
    }
    for row in rows:
        if row["current_revision_id"] is None or row["revision_id"] is None:
            raise DomainRuleError(
                "SHOT_CURRENT_REVISION_MISSING",
                "当前有效镜头缺少可读取的 current revision，无法判定音轨需求",
                {
                    "episode_id": episode_id,
                    "shot_id": str(row["shot_id"]),
                    "current_revision_id": row["current_revision_id"],
                },
            )
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


def canonical_tts_requirements(connection: sqlite3.Connection, episode_id: str) -> dict[str, Any]:
    """Resolve only current dialogue lines that still need generated audio.

    This projection intentionally mirrors the execution contract: a unique
    character bound to the shot wins, otherwise an exact normalized speaker
    code/name match is used. Existing current, verified AUDIO selections are
    reusable and therefore need neither a voice nor an external TTS check.
    """

    episode = connection.execute(
        """SELECT se.project_id FROM episodes e JOIN seasons se ON se.id=e.season_id
        WHERE e.id=?""",
        (episode_id,),
    ).fetchone()
    if episode is None:
        raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
    project_id = str(episode["project_id"])
    rows = connection.execute(
        """SELECT dl.id,dl.code,dl.shot_id,dl.speaker,
        (SELECT dtr.id FROM dialogue_text_revisions dtr WHERE dtr.dialogue_line_id=dl.id
         ORDER BY dtr.revision_no DESC,dtr.id DESC LIMIT 1) AS text_revision_id,
        (SELECT dtr.text FROM dialogue_text_revisions dtr WHERE dtr.dialogue_line_id=dl.id
         ORDER BY dtr.revision_no DESC,dtr.id DESC LIMIT 1) AS text,
        dcs.source_text_revision_id,tc.status AS candidate_status,tc.media_version_id,
        ma.media_kind,mv.integrity_status
        FROM dialogue_lines dl
        LEFT JOIN dialogue_candidate_selections dcs ON dcs.id=(
          SELECT latest.id FROM dialogue_candidate_selections latest
          WHERE latest.dialogue_line_id=dl.id ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
        LEFT JOIN tts_candidates tc ON tc.id=dcs.tts_candidate_id
        LEFT JOIN media_versions mv ON mv.id=tc.media_version_id
        LEFT JOIN media_assets ma ON ma.id=mv.media_asset_id
        WHERE dl.episode_id=? ORDER BY dl.code,dl.id""",
        (episode_id,),
    ).fetchall()
    characters = connection.execute(
        """SELECT id,code,name FROM story_assets
        WHERE project_id=? AND kind='CHARACTER' AND status='ACTIVE' ORDER BY code,id""",
        (project_id,),
    ).fetchall()
    bindings = connection.execute(
        """SELECT cvb.character_asset_id,cvb.voice_profile_version_id,
        vpv.voice_ref,vpv.status,epv.id AS provider_profile_version_id,
        epv.status AS provider_status,epv.capability AS provider_capability
        FROM character_voice_bindings cvb
        JOIN voice_profile_versions vpv ON vpv.id=cvb.voice_profile_version_id
        LEFT JOIN execution_profile_versions epv ON epv.id=vpv.provider_profile_version_id
        WHERE cvb.project_id=?""",
        (project_id,),
    ).fetchall()
    shot_ids = sorted({str(row["shot_id"]) for row in rows if row["shot_id"]})
    shot_characters: dict[str, list[str]] = {}
    if shot_ids:
        marks = ",".join("?" for _ in shot_ids)
        for row in connection.execute(
            f"""SELECT sab.shot_id,sa.id AS asset_id FROM shot_asset_bindings sab
            JOIN story_assets sa ON sa.id=sab.asset_id
            WHERE sab.shot_id IN ({marks}) AND sa.kind='CHARACTER' AND sa.status='ACTIVE'
            ORDER BY sab.shot_id,sa.id""",
            shot_ids,
        ).fetchall():
            shot_characters.setdefault(str(row["shot_id"]), []).append(str(row["asset_id"]))

    def normalize(value: object) -> str:
        return str(value or "").strip().replace("\u3000", "").replace(" ", "")

    characters_by_name: dict[str, str] = {}
    for character in characters:
        for value in (character["name"], character["code"]):
            key = normalize(value)
            if key:
                characters_by_name[key] = str(character["id"])
    voice_by_character = {str(row["character_asset_id"]): row for row in bindings}
    items: list[dict[str, Any]] = []
    for row in rows:
        text_revision_id = str(row["text_revision_id"] or "")
        if not text_revision_id or not str(row["text"] or "").strip():
            continue
        reusable = bool(
            str(row["source_text_revision_id"] or "") == text_revision_id
            and str(row["candidate_status"] or "") == "READY"
            and str(row["media_kind"] or "") == "AUDIO"
            and str(row["integrity_status"] or "") == "VERIFIED"
        )
        speaker = str(row["speaker"] or "").strip()
        narrator = speaker.upper() in _NARRATOR_SPEAKERS
        character_asset_id = None
        candidates = shot_characters.get(str(row["shot_id"] or ""), [])
        if not narrator and len(candidates) == 1:
            character_asset_id = candidates[0]
        if character_asset_id is None and not narrator:
            character_asset_id = characters_by_name.get(normalize(speaker))
        voice = voice_by_character.get(str(character_asset_id)) if character_asset_id else None
        eligible = bool(
            voice is not None
            and str(voice["status"]) == "ACTIVE"
            and str(voice["provider_status"] or "") == "PUBLISHED"
            and "TTS" in str(voice["provider_capability"] or "").upper()
            and str(voice["voice_ref"] or "").startswith("sapi:")
        )
        reason = None
        if not reusable and not eligible:
            reason = (
                "NARRATOR_VOICE_REQUIRED"
                if narrator
                else "VOICE_UNRESOLVED" if character_asset_id is None or voice is None
                else "VOICE_NOT_JOB_ELIGIBLE"
            )
        items.append(
            {
                "line_id": str(row["id"]),
                "code": str(row["code"]),
                "shot_id": str(row["shot_id"]) if row["shot_id"] else None,
                "speaker": speaker,
                "text_revision_id": text_revision_id,
                "reusable_media_version_id": str(row["media_version_id"]) if reusable else None,
                "generation_required": not reusable,
                "character_asset_id": character_asset_id,
                "voice_profile_version_id": str(voice["voice_profile_version_id"]) if voice is not None else None,
                "provider_profile_version_id": str(voice["provider_profile_version_id"]) if voice is not None and voice["provider_profile_version_id"] else None,
                "eligible": reusable or eligible,
                "blocked_reason": reason,
            }
        )
    pending = [item for item in items if item["generation_required"]]
    blockers = [item for item in pending if not item["eligible"]]
    return {
        "items": items,
        "pending": pending,
        "blockers": blockers,
        "generation_required_count": len(pending),
        "reusable_count": len(items) - len(pending),
        "status": "PASS" if not blockers else "BLOCKED",
        "resolution_policy": "UNIQUE_SHOT_CHARACTER_THEN_EXACT_SPEAKER",
    }


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

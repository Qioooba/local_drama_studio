"""Read-only canonical dialogue facts used by shot prompt compilers."""

from __future__ import annotations

import sqlite3
from typing import Any

_UNRESOLVED_SPEAKERS = frozenset({"待确认说话人", "UNKNOWN", "UNRESOLVED"})


def current_shot_dialogue(connection: sqlite3.Connection, shot_id: str) -> dict[str, Any]:
    """Return stable line identity plus the latest immutable text revision."""

    rows = connection.execute(
        """SELECT dl.id AS dialogue_line_id,dl.code,dl.speaker,
                  dtr.id AS text_revision_id,dtr.revision_no,dtr.text
           FROM dialogue_lines dl
           JOIN dialogue_text_revisions dtr ON dtr.id=(
             SELECT latest.id FROM dialogue_text_revisions latest
             WHERE latest.dialogue_line_id=dl.id
             ORDER BY latest.revision_no DESC,latest.id DESC LIMIT 1
           )
          WHERE dl.shot_id=?
          ORDER BY dl.code,dl.id""",
        (shot_id,),
    ).fetchall()
    lines = [
        {
            "dialogue_line_id": str(row["dialogue_line_id"]),
            "code": str(row["code"]),
            "speaker": str(row["speaker"] or "").strip(),
            "text_revision_id": str(row["text_revision_id"]),
            "text_revision_no": int(row["revision_no"]),
            "text": str(row["text"]),
        }
        for row in rows
    ]
    unresolved = [
        line["dialogue_line_id"]
        for line in lines
        if not line["speaker"] or str(line["speaker"]).upper() in _UNRESOLVED_SPEAKERS
    ]
    return {"lines": lines, "unresolved_speaker_line_ids": unresolved}


def fields_with_current_dialogue(
    connection: sqlite3.Connection,
    shot_id: str,
    fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    facts = current_shot_dialogue(connection, shot_id)
    resolved = dict(fields)
    resolved["dialogue"] = list(facts["lines"])
    return resolved, facts

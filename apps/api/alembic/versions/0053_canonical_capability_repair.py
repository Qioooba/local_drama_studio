"""Repair canonical project capability bindings without guessing.

This is a forward-only repair for databases which already applied 0049.  The
0049 migration intentionally remains immutable; it did not repair
``project_profile_bindings`` and could therefore leave legacy aliases in that
table.  Known aliases are normalized.  Blank, ambiguous, unknown and duplicate
bindings are made inactive and copied to an auditable quarantine table instead
of being silently coerced.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "0053_canonical_capability_repair"
down_revision = "0052_storage_operations"
branch_labels = None
depends_on = None


# Frozen migration vocabulary.  Never import the live domain module from an
# Alembic revision: future application changes must not change old migrations.
CANONICAL_CAPABILITIES = frozenset(
    {
        "LLM_STORY_PARSE",
        "LLM_EPISODE_PLAN",
        "LLM_STORYBOARD",
        "LLM_PROMPT_REWRITE",
        "IMAGE_CONCEPT",
        "IMAGE_CHARACTER",
        "IMAGE_SCENE",
        "IMAGE_EDIT",
        "IMAGE_MULTI_VIEW",
        "IMAGE_EXPRESSION",
        "VIDEO_T2V",
        "VIDEO_I2V",
        "VIDEO_FIRST_FRAME",
        "VIDEO_FIRST_LAST_FRAME",
        "VIDEO_REFERENCE",
        "VIDEO_MOTION_CONTROL",
        "TTS",
        "VOICE_CLONE",
        "LIPSYNC",
        "AUDIO_SFX",
        "AUDIO_MUSIC",
        "FRAME_EXTRACT",
        "UPSCALE_IMAGE",
        "UPSCALE_VIDEO",
        "POST_PROCESS",
        "QC_VISUAL",
        "QC_FACE",
        "QC_IDENTITY",
        "QC_CONTINUITY",
        "QC_AUDIO",
    }
)

CAPABILITY_ALIASES = {
    "SCRIPT_BREAKDOWN_LLM": "LLM_STORY_PARSE",
    "STORY_PARSE": "LLM_STORY_PARSE",
    "STORY_BREAKDOWN": "LLM_STORY_PARSE",
    "EPISODE_PLAN": "LLM_EPISODE_PLAN",
    "PROMPT_REWRITE": "LLM_PROMPT_REWRITE",
    "I2V": "VIDEO_I2V",
    "IMAGE_TO_VIDEO": "VIDEO_I2V",
    "IMAGE2VIDEO": "VIDEO_I2V",
    "T2V": "VIDEO_T2V",
    "TEXT_TO_VIDEO": "VIDEO_T2V",
    "TEXT2VIDEO": "VIDEO_T2V",
    "FIRST_FRAME": "VIDEO_FIRST_FRAME",
    "FIRST_LAST_FRAME": "VIDEO_FIRST_LAST_FRAME",
    "VIDEO_FIRST_LAST": "VIDEO_FIRST_LAST_FRAME",
    "R2V": "VIDEO_REFERENCE",
    "REF2V": "VIDEO_REFERENCE",
    "REFERENCE_TO_VIDEO": "VIDEO_REFERENCE",
    "V2V": "VIDEO_REFERENCE",
    "VIDEO_TO_VIDEO": "VIDEO_REFERENCE",
    "MOTION_CONTROL": "VIDEO_MOTION_CONTROL",
    "MOTION_BRUSH": "VIDEO_MOTION_CONTROL",
    "AUDIO_TTS": "TTS",
    "TEXT_TO_SPEECH": "TTS",
    "AUDIO_CLONE": "VOICE_CLONE",
    "AUDIO_VOICE_CLONE": "VOICE_CLONE",
    "LIP_SYNC": "LIPSYNC",
    "SFX": "AUDIO_SFX",
    "MUSIC": "AUDIO_MUSIC",
    "BGM": "AUDIO_MUSIC",
    "BGM_GEN": "AUDIO_MUSIC",
    "AUDIO_BGM": "AUDIO_MUSIC",
    "CHARACTER": "IMAGE_CHARACTER",
    "SCENE": "IMAGE_SCENE",
    "CONCEPT": "IMAGE_CONCEPT",
    "MULTI_VIEW": "IMAGE_MULTI_VIEW",
    "EXPRESSION": "IMAGE_EXPRESSION",
    "EDIT": "IMAGE_EDIT",
    "UPSCALE": "UPSCALE_IMAGE",
    "SR_IMAGE": "UPSCALE_IMAGE",
    "SR_VIDEO": "UPSCALE_VIDEO",
}

# These labels describe a family rather than one executable capability.  They
# must never be guessed into a more specific canonical value.
AMBIGUOUS_CAPABILITIES = frozenset(
    {
        "IMAGE",
        "IMAGE_GENERATION",
        "VIDEO",
        "VIDEO_GENERATION",
        "AUDIO",
        "AUDIO_GENERATION",
        "TEXT",
        "LLM",
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _classify_capability(value: Any) -> tuple[str, str | None]:
    if value is None or not str(value).strip():
        return "NULL_OR_BLANK", None
    cleaned = str(value).strip().upper()
    if cleaned in CANONICAL_CAPABILITIES:
        return "CANONICAL", cleaned
    if cleaned in CAPABILITY_ALIASES:
        return "ALIAS", CAPABILITY_ALIASES[cleaned]
    if cleaned in AMBIGUOUS_CAPABILITIES:
        return "AMBIGUOUS", None
    return "UNKNOWN", None


def _row_dict(row: Any) -> dict[str, Any]:
    mapping = getattr(row, "_mapping", row)
    return {str(key): value for key, value in dict(mapping).items()}


def _quarantine_token(source_row_id: str) -> str:
    # The source id is globally unique and keeps the table's existing unique
    # (project_id, capability) constraint useful while freeing the canonical
    # key for the deterministic survivor.
    return f"QUARANTINED:{source_row_id}"[:120]


def _record_quarantine(
    connection: Any,
    *,
    row: dict[str, Any],
    reason_code: str,
    canonical_capability: str | None,
    now: str,
    details: dict[str, Any] | None = None,
) -> None:
    snapshot = dict(row)
    if details:
        snapshot["migration_details"] = details
    connection.execute(
        sa.text(
            """INSERT INTO capability_migration_quarantine
            (id,source_table,source_row_id,project_id,original_capability,canonical_capability,
             reason_code,original_status,row_snapshot_json,quarantined_at,last_seen_at,
             resolved_at,resolution_action)
            VALUES (:id,'project_profile_bindings',:source_row_id,:project_id,:original_capability,
                    :canonical_capability,:reason_code,:original_status,:row_snapshot_json,:now,:now,NULL,NULL)
            ON CONFLICT(source_table,source_row_id) DO UPDATE SET
              canonical_capability=excluded.canonical_capability,
              reason_code=excluded.reason_code,
              row_snapshot_json=excluded.row_snapshot_json,
              last_seen_at=excluded.last_seen_at"""
        ),
        {
            "id": f"project_profile_bindings:{row['id']}",
            "source_row_id": str(row["id"]),
            "project_id": row.get("project_id"),
            "original_capability": row.get("capability"),
            "canonical_capability": canonical_capability,
            "reason_code": reason_code,
            "original_status": row.get("status"),
            "row_snapshot_json": json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "now": now,
        },
    )
    connection.execute(
        sa.text(
            """UPDATE project_profile_bindings
            SET capability=:token,status='QUARANTINED',updated_at=:now,revision=revision+1
            WHERE id=:source_row_id AND (status<>'QUARANTINED' OR capability<>:token)"""
        ),
        {"token": _quarantine_token(str(row["id"])), "now": now, "source_row_id": str(row["id"])},
    )


def _repair_project_profile_bindings(connection: Any, *, now: str | None = None) -> dict[str, int]:
    """Normalize or quarantine bindings; safe to run again after interruption.

    The helper is intentionally callable by recovery tooling/tests.  Rows
    already carrying the migration's ``QUARANTINED`` status are untouched.
    If an operator corrects such a row and explicitly reactivates it, a rerun
    normalizes it and marks the quarantine record resolved.
    """

    repaired_at = now or _now()
    rows = [
        _row_dict(row)
        for row in connection.execute(
            sa.text(
                """SELECT id,project_id,capability,execution_profile_version_id,status,
                created_at,updated_at,created_by,revision,schema_version
                FROM project_profile_bindings ORDER BY project_id,created_at,id"""
            )
        ).fetchall()
    ]
    stats = {
        "scanned_count": len(rows),
        "canonical_count": 0,
        "normalized_count": 0,
        "quarantined_null_count": 0,
        "quarantined_ambiguous_count": 0,
        "quarantined_unknown_count": 0,
        "quarantined_duplicate_count": 0,
        "preexisting_quarantined_count": 0,
    }
    known_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for row in rows:
        if str(row.get("status") or "").upper() == "QUARANTINED":
            stats["preexisting_quarantined_count"] += 1
            continue
        classification, canonical = _classify_capability(row.get("capability"))
        if classification in {"CANONICAL", "ALIAS"} and canonical is not None:
            known_groups.setdefault((str(row["project_id"]), canonical), []).append(row)
            continue
        reason_code = {
            "NULL_OR_BLANK": "CAPABILITY_NULL_OR_BLANK",
            "AMBIGUOUS": "CAPABILITY_AMBIGUOUS",
            "UNKNOWN": "CAPABILITY_UNKNOWN",
        }[classification]
        _record_quarantine(
            connection,
            row=row,
            reason_code=reason_code,
            canonical_capability=None,
            now=repaired_at,
        )
        stats[
            {
                "NULL_OR_BLANK": "quarantined_null_count",
                "AMBIGUOUS": "quarantined_ambiguous_count",
                "UNKNOWN": "quarantined_unknown_count",
            }[classification]
        ] += 1

    for (_project_id, canonical), candidates in known_groups.items():
        # Preserve the effective ACTIVE binding first; within the same status,
        # prefer the row which was already canonical, then stable creation/id
        # order.  Every losing row remains recoverable in quarantine.
        def survivor_key(row: dict[str, Any], canonical_capability: str = canonical) -> tuple[int, int, str, str]:
            status_rank = {"ACTIVE": 0, "SELECTED_CANDIDATE": 1}.get(str(row.get("status") or "").upper(), 2)
            already_canonical = str(row.get("capability") or "").strip().upper() == canonical_capability
            return status_rank, 0 if already_canonical else 1, str(row.get("created_at") or ""), str(row["id"])

        ordered = sorted(candidates, key=survivor_key)
        survivor = ordered[0]
        for duplicate in ordered[1:]:
            _record_quarantine(
                connection,
                row=duplicate,
                reason_code="CAPABILITY_DUPLICATE_CANONICAL_KEY",
                canonical_capability=canonical,
                now=repaired_at,
                details={"survivor_source_row_id": str(survivor["id"])},
            )
            stats["quarantined_duplicate_count"] += 1

        original = str(survivor.get("capability") or "")
        if original != canonical:
            connection.execute(
                sa.text(
                    """UPDATE project_profile_bindings
                    SET capability=:canonical,updated_at=:now,revision=revision+1 WHERE id=:source_row_id"""
                ),
                {"canonical": canonical, "now": repaired_at, "source_row_id": str(survivor["id"])},
            )
            stats["normalized_count"] += 1
        else:
            stats["canonical_count"] += 1
        connection.execute(
            sa.text(
                """UPDATE capability_migration_quarantine
                SET resolved_at=:now,resolution_action='OPERATOR_CORRECTED_AND_REACTIVATED',last_seen_at=:now
                WHERE source_table='project_profile_bindings' AND source_row_id=:source_row_id
                  AND resolved_at IS NULL"""
            ),
            {"now": repaired_at, "source_row_id": str(survivor["id"])},
        )

    return stats


def _backfill_execution_profile_aliases(connection: Any) -> int:
    """Repeat the safe profile-version backfill for writes made after 0049.

    Older application builds could reintroduce aliases (notably
    ``SCRIPT_BREAKDOWN_LLM``) after the 0049 migration had already run.
    ``execution_profile_versions.capability`` has no capability uniqueness
    constraint, so these explicit updates cannot merge unrelated rows.
    """

    normalized_count = 0
    for alias, canonical in CAPABILITY_ALIASES.items():
        result = connection.execute(
            sa.text(
                """UPDATE execution_profile_versions SET capability=:canonical
                WHERE UPPER(TRIM(capability))=:alias AND capability<>:canonical"""
            ),
            {"canonical": canonical, "alias": alias},
        )
        normalized_count += max(0, int(result.rowcount or 0))
    return normalized_count


def upgrade() -> None:
    op.create_table(
        "capability_migration_quarantine",
        sa.Column("id", sa.String(200), primary_key=True),
        sa.Column("source_table", sa.String(80), nullable=False),
        sa.Column("source_row_id", sa.String(120), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("original_capability", sa.String(120), nullable=True),
        sa.Column("canonical_capability", sa.String(120), nullable=True),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("original_status", sa.String(40), nullable=True),
        sa.Column("row_snapshot_json", sa.Text(), nullable=False),
        sa.Column("quarantined_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.Text(), nullable=True),
        sa.Column("resolution_action", sa.String(120), nullable=True),
        sa.UniqueConstraint("source_table", "source_row_id", name="uq_capability_quarantine_source"),
    )
    op.create_index(
        "ix_capability_quarantine_reason",
        "capability_migration_quarantine",
        ["reason_code", "resolved_at"],
    )
    op.create_table(
        "capability_migration_reports",
        sa.Column("migration_revision", sa.String(80), primary_key=True),
        sa.Column("source_table", sa.String(80), nullable=False),
        sa.Column("scanned_count", sa.Integer(), nullable=False),
        sa.Column("canonical_count", sa.Integer(), nullable=False),
        sa.Column("normalized_count", sa.Integer(), nullable=False),
        sa.Column("execution_profile_normalized_count", sa.Integer(), nullable=False),
        sa.Column("quarantined_null_count", sa.Integer(), nullable=False),
        sa.Column("quarantined_ambiguous_count", sa.Integer(), nullable=False),
        sa.Column("quarantined_unknown_count", sa.Integer(), nullable=False),
        sa.Column("quarantined_duplicate_count", sa.Integer(), nullable=False),
        sa.Column("preexisting_quarantined_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )

    connection = op.get_bind()
    repaired_at = _now()
    execution_profile_normalized_count = _backfill_execution_profile_aliases(connection)
    stats = _repair_project_profile_bindings(connection, now=repaired_at)
    connection.execute(
        sa.text(
            """INSERT INTO capability_migration_reports
            (migration_revision,source_table,scanned_count,canonical_count,normalized_count,
             execution_profile_normalized_count,
             quarantined_null_count,quarantined_ambiguous_count,quarantined_unknown_count,
             quarantined_duplicate_count,preexisting_quarantined_count,created_at)
            VALUES (:migration_revision,'project_profile_bindings',:scanned_count,:canonical_count,
                    :normalized_count,:execution_profile_normalized_count,
                    :quarantined_null_count,:quarantined_ambiguous_count,
                    :quarantined_unknown_count,:quarantined_duplicate_count,
                    :preexisting_quarantined_count,:created_at)"""
        ),
        {
            "migration_revision": revision,
            "created_at": repaired_at,
            "execution_profile_normalized_count": execution_profile_normalized_count,
            **stats,
        },
    )


def downgrade() -> None:
    raise RuntimeError(
        "Canonical capability quarantine is forward-only; resolve quarantined rows or restore the pre-upgrade backup"
    )

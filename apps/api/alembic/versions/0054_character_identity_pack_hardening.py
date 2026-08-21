"""Harden Character Identity Pack approval, provenance and generation snapshots.

The original 0050 vertical slice stored the basic pack/version/slot graph, but
did not freeze slot authorization provenance or the exact identity-pack input
used by a GenerationVariant.  This linear migration adds those audit fields and
quarantines legacy approvals that do not satisfy the fail-closed three-view
contract.  It never invents missing FRONT/LEFT/RIGHT media.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "0054_character_identity_pack_hardening"
down_revision = "0053_canonical_capability_repair"
branch_labels = None
depends_on = None

REQUIRED_SLOTS = frozenset({"FRONT", "LEFT", "RIGHT"})


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(row: Any) -> dict[str, Any]:
    return dict(getattr(row, "_mapping", row))


def _backfill_pack_versions(connection: Any) -> None:
    now = _now()
    versions = connection.execute(
        sa.text(
            """SELECT v.id,v.pack_id,v.project_id,v.version_no,v.status,
            p.current_version_id
            FROM character_identity_pack_versions v
            JOIN character_identity_packs p ON p.id=v.pack_id
            WHERE v.status IN ('APPROVED','SUPERSEDED')
            ORDER BY v.pack_id,v.version_no"""
        )
    ).fetchall()
    for raw_version in versions:
        version = _mapping(raw_version)
        raw_slots = connection.execute(
            sa.text(
                """SELECT s.id,s.slot_kind,s.media_version_id,mv.sha256,mv.byte_size,
                mv.integrity_status,ma.media_kind,ma.project_id,
                waa.id AS authorization_id,waa.authorization_status,
                waa.license_status,waa.sha256 AS authorization_sha256,
                waa.byte_size AS authorization_byte_size
                FROM character_identity_pack_slots s
                JOIN media_versions mv ON mv.id=s.media_version_id
                JOIN media_assets ma ON ma.id=mv.media_asset_id
                LEFT JOIN workspace_asset_authorizations waa
                  ON waa.project_id=ma.project_id AND waa.media_version_id=mv.id
                WHERE s.pack_version_id=:version_id
                ORDER BY s.slot_kind,s.id"""
            ),
            {"version_id": version["id"]},
        ).fetchall()
        slots = [_mapping(row) for row in raw_slots]
        kinds = {str(row["slot_kind"]).upper() for row in slots}
        reasons: list[str] = []
        missing = sorted(REQUIRED_SLOTS - kinds)
        if missing:
            reasons.append("MISSING_REQUIRED_SLOTS:" + ",".join(missing))
        required_media_ids = [
            str(row["media_version_id"])
            for row in slots
            if str(row["slot_kind"]).upper() in REQUIRED_SLOTS
        ]
        if len(required_media_ids) != len(set(required_media_ids)):
            reasons.append("DUPLICATE_REQUIRED_MEDIA")
        required_hashes = [
            str(row["sha256"])
            for row in slots
            if str(row["slot_kind"]).upper() in REQUIRED_SLOTS
        ]
        if len(required_hashes) != len(set(required_hashes)):
            reasons.append("DUPLICATE_REQUIRED_CONTENT")
        for slot in slots:
            if str(slot["project_id"]) != str(version["project_id"]):
                reasons.append(f"MEDIA_PROJECT_MISMATCH:{slot['slot_kind']}")
            if str(slot["media_kind"]).upper() != "IMAGE":
                reasons.append(f"MEDIA_KIND_NOT_IMAGE:{slot['slot_kind']}")
            if str(slot["integrity_status"]).upper() != "VERIFIED":
                reasons.append(f"MEDIA_NOT_VERIFIED:{slot['slot_kind']}")
            if slot.get("authorization_id"):
                if (
                    str(slot.get("authorization_status")) != "AUTHORIZED"
                    or str(slot.get("license_status")) != "LOCAL_PROJECT_AUTHORIZED"
                    or str(slot.get("authorization_sha256")) != str(slot.get("sha256"))
                    or int(slot.get("authorization_byte_size") or -1) != int(slot.get("byte_size") or -2)
                ):
                    reasons.append(f"AUTHORIZATION_NOT_USABLE:{slot['slot_kind']}")
                else:
                    connection.execute(
                        sa.text("UPDATE character_identity_pack_slots SET authorization_id=:authorization_id WHERE id=:slot_id"),
                        {"authorization_id": slot["authorization_id"], "slot_id": slot["id"]},
                    )
            else:
                reasons.append(f"AUTHORIZATION_MISSING:{slot['slot_kind']}")

        if reasons:
            snapshot = {
                "pack_version_id": str(version["id"]),
                "pack_id": str(version["pack_id"]),
                "version_no": int(version["version_no"]),
                "previous_status": str(version["status"]),
                "reason_codes": sorted(set(reasons)),
                "slot_media_version_ids": {
                    str(row["slot_kind"]): str(row["media_version_id"]) for row in slots
                },
            }
            connection.execute(
                sa.text(
                    """INSERT INTO identity_pack_migration_quarantine
                    (pack_version_id,reason_codes_json,row_snapshot_json,quarantined_at)
                    VALUES (:version_id,:reasons,:snapshot,:now)"""
                ),
                {
                    "version_id": version["id"],
                    "reasons": _canonical(sorted(set(reasons))),
                    "snapshot": _canonical(snapshot),
                    "now": now,
                },
            )
            connection.execute(
                sa.text(
                    """UPDATE character_identity_pack_versions
                    SET status='REJECTED',retired_at=:now,retired_by='migration-0054',
                    retired_reason='Legacy approval failed fail-closed identity-pack validation',
                    updated_at=:now,revision=revision+1 WHERE id=:version_id"""
                ),
                {"now": now, "version_id": version["id"]},
            )
            if str(version.get("current_version_id") or "") == str(version["id"]):
                connection.execute(
                    sa.text(
                        """UPDATE character_identity_packs SET current_version_id=NULL,
                        updated_at=:now,revision=revision+1 WHERE id=:pack_id"""
                    ),
                    {"now": now, "pack_id": version["pack_id"]},
                )
            continue

        content = {
            "schema_version": "localdrama.identity-pack-content.v1",
            "pack_id": str(version["pack_id"]),
            "version_id": str(version["id"]),
            "version_no": int(version["version_no"]),
            "slots": [
                {
                    "slot_kind": str(row["slot_kind"]),
                    "media_version_id": str(row["media_version_id"]),
                    "sha256": str(row["sha256"]),
                    "byte_size": int(row["byte_size"]),
                    "authorization_id": str(row["authorization_id"]) if row.get("authorization_id") else None,
                }
                for row in slots
            ],
        }
        connection.execute(
            sa.text("UPDATE character_identity_pack_versions SET content_hash=:content_hash WHERE id=:version_id"),
            {"content_hash": _hash(content), "version_id": version["id"]},
        )


def upgrade() -> None:
    op.add_column(
        "character_identity_pack_slots",
        sa.Column("authorization_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "character_identity_pack_versions",
        sa.Column("content_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "character_identity_pack_versions",
        sa.Column("retired_at", sa.Text(), nullable=True),
    )
    op.add_column(
        "character_identity_pack_versions",
        sa.Column("retired_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "character_identity_pack_versions",
        sa.Column("retired_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "generation_variants",
        sa.Column("identity_pack_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_character_identity_pack_slots_authorization",
        "character_identity_pack_slots",
        ["authorization_id"],
    )
    op.create_index(
        "ix_character_identity_pack_versions_content_hash",
        "character_identity_pack_versions",
        ["content_hash"],
    )
    op.create_table(
        "identity_pack_migration_quarantine",
        sa.Column("pack_version_id", sa.String(36), primary_key=True),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("row_snapshot_json", sa.Text(), nullable=False),
        sa.Column("quarantined_at", sa.Text(), nullable=False),
    )
    _backfill_pack_versions(op.get_bind())


def downgrade() -> None:
    op.drop_table("identity_pack_migration_quarantine")
    op.drop_index("ix_character_identity_pack_versions_content_hash", table_name="character_identity_pack_versions")
    op.drop_index("ix_character_identity_pack_slots_authorization", table_name="character_identity_pack_slots")
    op.drop_column("generation_variants", "identity_pack_snapshot_json")
    op.drop_column("character_identity_pack_versions", "retired_reason")
    op.drop_column("character_identity_pack_versions", "retired_by")
    op.drop_column("character_identity_pack_versions", "retired_at")
    op.drop_column("character_identity_pack_versions", "content_hash")
    op.drop_column("character_identity_pack_slots", "authorization_id")

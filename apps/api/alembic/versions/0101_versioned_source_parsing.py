"""Versioned source parsing and scope-aware import receipts.

Adds the parse-generation identity (``parser_version``/``structure_version``)
to ``source_document_versions`` so a new parser or structure index can create a
*new* parse version with its own ``extracted_text_rel`` file while every
already-committed episode keeps referencing the old offsets and hashes.

Also records the committed body range on the import session so NP08 commit
idempotency can compare ranges instead of silently replaying a stale receipt.

The migration is additive.  It never rewrites an existing row's referenced
offsets or hashes.  It only retires ``PREVIEW_READY`` sessions whose stored
preview has zero paragraphs: those rows can never satisfy the response contract
and would otherwise be reused as a broken "ready" session.  Uniqueness of
``(source_document_id, version_no)`` is kept, so more than one immutable parse
generation of the same raw file can coexist as separate version rows.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0101_versioned_source_parsing"
down_revision = "0100_production_session_waiting_user"
branch_labels = None
depends_on = None

_LEGACY_PARSER_VERSION = 1
_LEGACY_STRUCTURE_VERSION = 2


def upgrade() -> None:
    with op.batch_alter_table("source_document_versions") as batch:
        batch.add_column(
            sa.Column("parser_version", sa.Integer(), nullable=False, server_default=str(_LEGACY_PARSER_VERSION))
        )
        batch.add_column(
            sa.Column("structure_version", sa.Integer(), nullable=False, server_default=str(_LEGACY_STRUCTURE_VERSION))
        )
    with op.batch_alter_table("import_sessions") as batch:
        batch.add_column(sa.Column("committed_scope_json", sa.Text()))
        batch.add_column(sa.Column("committed_scope_hash", sa.String(64)))
    op.execute(
        """
        UPDATE import_sessions
        SET status='INVALID',
            error_summary=COALESCE(error_summary, '解析结果没有可用段落，会话已作废；请重新导入以创建新的解析版本')
        WHERE status='PREVIEW_READY'
          AND COALESCE(CAST(json_extract(preview_json, '$.paragraph_count') AS INTEGER), 0) <= 0
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "versioned source parsing is additive; restore the pre-migration backup instead of downgrading"
    )

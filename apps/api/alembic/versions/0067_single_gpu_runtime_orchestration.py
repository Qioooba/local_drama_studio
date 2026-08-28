"""Add cross-process single-GPU runtime orchestration facts.

Revision ID: 0067_single_gpu_runtime_orchestration
Revises: 0066_action_driven_quick_generation
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0067_single_gpu_runtime_orchestration"
down_revision = "0066_action_driven_quick_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gpu_runtime_leases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resource_key", sa.String(80), nullable=False),
        sa.Column("owner_token", sa.String(80), nullable=False, unique=True),
        sa.Column("owner_kind", sa.String(40), nullable=False),
        sa.Column("owner_ref", sa.String(200), nullable=False),
        sa.Column("runtime_kind", sa.String(20), nullable=False),
        sa.Column("acquired_at", sa.Text(), nullable=False),
        sa.Column("heartbeat_at", sa.Text(), nullable=False),
        sa.Column("lease_expires_at", sa.Text(), nullable=False),
        sa.Column("released_at", sa.Text()),
        sa.Column("release_reason", sa.String(80)),
        sa.CheckConstraint("runtime_kind IN ('COMFY','OLLAMA')", name="ck_gpu_runtime_lease_kind"),
    )
    op.create_index(
        "uq_gpu_runtime_leases_active_resource",
        "gpu_runtime_leases",
        ["resource_key"],
        unique=True,
        sqlite_where=sa.text("released_at IS NULL"),
    )
    op.create_index("ix_gpu_runtime_leases_owner_ref", "gpu_runtime_leases", ["owner_ref", "released_at"])
    op.create_table(
        "gpu_runtime_state",
        sa.Column("resource_key", sa.String(80), primary_key=True),
        sa.Column("resident_runtime", sa.String(20)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("active_owner_ref", sa.String(200)),
        sa.Column("last_transition_at", sa.Text()),
        sa.Column("last_release_at", sa.Text()),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("last_error_detail_redacted", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("resident_runtime IS NULL OR resident_runtime IN ('COMFY','OLLAMA')", name="ck_gpu_runtime_state_kind"),
    )
    op.execute(
        """INSERT INTO gpu_runtime_state
        (resource_key,resident_runtime,status,updated_at)
        VALUES ('GPU:0:EXCLUSIVE',NULL,'IDLE',CURRENT_TIMESTAMP)"""
    )


def downgrade() -> None:
    op.drop_table("gpu_runtime_state")
    op.drop_index("ix_gpu_runtime_leases_owner_ref", table_name="gpu_runtime_leases")
    op.drop_index("uq_gpu_runtime_leases_active_resource", table_name="gpu_runtime_leases")
    op.drop_table("gpu_runtime_leases")

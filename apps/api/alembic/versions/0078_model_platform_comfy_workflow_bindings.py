"""Bind V2 Comfy model installations to validated immutable workflow versions.

Revision ID: 0078_model_platform_comfy_workflow_bindings
Revises: 0077_model_platform_snapshot_model_bindings
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0078_model_platform_comfy_workflow_bindings"
down_revision = "0077_model_platform_snapshot_model_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_runtime_model_workflow_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("runtime_model_installation_id", sa.String(36), sa.ForeignKey("mp_runtime_model_installations.id"), nullable=False),
        sa.Column("capability_definition_id", sa.String(36), sa.ForeignKey("mp_capability_definitions.id"), nullable=False),
        sa.Column("workflow_version_id", sa.String(36), nullable=False),
        sa.Column("workflow_content_hash", sa.String(64), nullable=False),
        sa.Column("workflow_validation_id", sa.String(36), nullable=False),
        sa.Column("binding_status", sa.String(32), nullable=False, server_default="SCHEMA_VALIDATED"),
        sa.Column("binding_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "runtime_model_installation_id",
            "capability_definition_id",
            "workflow_version_id",
            name="uq_mp_model_workflow_binding_version",
        ),
    )
    op.create_index(
        "ix_mp_model_workflow_binding_offering",
        "mp_runtime_model_workflow_bindings",
        ["runtime_model_installation_id", "capability_definition_id", "binding_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_mp_model_workflow_binding_offering", table_name="mp_runtime_model_workflow_bindings")
    op.drop_table("mp_runtime_model_workflow_bindings")

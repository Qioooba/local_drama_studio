"""Persist immutable V2 execution snapshots before worker handoff.

Revision ID: 0073_model_platform_execution_snapshots
Revises: 0072_adaptation_analysis_job_stage
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0073_model_platform_execution_snapshots"
down_revision = "0072_adaptation_analysis_job_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_execution_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("capability_definition_id", sa.String(36), sa.ForeignKey("mp_capability_definitions.id"), nullable=False),
        sa.Column("execution_profile_version_id", sa.String(36), sa.ForeignKey("mp_execution_profile_versions.id"), nullable=False),
        sa.Column("profile_payload_hash", sa.String(64), nullable=False),
        sa.Column("runtime_installation_version_id", sa.String(36), sa.ForeignKey("mp_runtime_installation_versions.id"), nullable=False),
        sa.Column("runtime_fingerprint", sa.String(128), nullable=False),
        sa.Column("adapter_code", sa.String(120), nullable=False),
        sa.Column("adapter_version", sa.String(80), nullable=False),
        sa.Column("parameter_contract_version_id", sa.String(36), sa.ForeignKey("mp_parameter_contract_versions.id"), nullable=False),
        sa.Column("parameter_contract_hash", sa.String(64), nullable=False),
        sa.Column("resolved_parameters_json", sa.Text(), nullable=False),
        sa.Column("parameter_provenance_json", sa.Text(), nullable=False),
        sa.Column("semantic_inputs_json", sa.Text(), nullable=False),
        sa.Column("resolution_json", sa.Text(), nullable=False),
        sa.Column("resource_policy_json", sa.Text(), nullable=False),
        sa.Column("network_policy_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_mp_execution_snapshots_profile_created", "mp_execution_snapshots", ["execution_profile_version_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_mp_execution_snapshots_profile_created", table_name="mp_execution_snapshots")
    op.drop_table("mp_execution_snapshots")

"""Persist V2 project-knowledge embedding runs separately from legacy indexes.

Revision ID: 0084_model_platform_project_knowledge_indexes
Revises: 0083_model_platform_scope_override_set_versions
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0084_model_platform_project_knowledge_indexes"
down_revision = "0083_model_platform_scope_override_set_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mp_project_knowledge_index_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_document_version_id", sa.String(36), sa.ForeignKey("source_document_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("capability_definition_id", sa.String(36), sa.ForeignKey("mp_capability_definitions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("execution_profile_version_id", sa.String(36), sa.ForeignKey("mp_execution_profile_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_text_hash", sa.String(64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("completed_batch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("failure_code", sa.String(120), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
        sa.UniqueConstraint(
            "source_document_version_id", "execution_profile_version_id", "source_text_hash",
            name="uq_mp_project_knowledge_index_run_source_profile",
        ),
    )
    op.create_index(
        "ix_mp_project_knowledge_index_runs_project_status",
        "mp_project_knowledge_index_runs", ["project_id", "status", "updated_at"],
    )
    op.create_table(
        "mp_project_knowledge_index_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("index_run_id", sa.String(36), sa.ForeignKey("mp_project_knowledge_index_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("chunk_manifest_json", sa.Text(), nullable=False),
        sa.Column("execution_snapshot_id", sa.String(36), sa.ForeignKey("mp_execution_snapshots.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("index_run_id", "ordinal", name="uq_mp_project_knowledge_index_batch_ordinal"),
        sa.UniqueConstraint("job_id", name="uq_mp_project_knowledge_index_batch_job"),
        sa.UniqueConstraint("execution_snapshot_id", name="uq_mp_project_knowledge_index_batch_snapshot"),
    )
    op.create_index(
        "ix_mp_project_knowledge_index_batches_run_status",
        "mp_project_knowledge_index_batches", ["index_run_id", "status", "ordinal"],
    )
    op.create_table(
        "mp_project_knowledge_vectors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("index_run_id", sa.String(36), sa.ForeignKey("mp_project_knowledge_index_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("mp_project_knowledge_index_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("vector_f32", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("index_run_id", "ordinal", name="uq_mp_project_knowledge_vector_ordinal"),
    )
    op.create_index(
        "ix_mp_project_knowledge_vectors_run_batch",
        "mp_project_knowledge_vectors", ["index_run_id", "batch_id", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index("ix_mp_project_knowledge_vectors_run_batch", table_name="mp_project_knowledge_vectors")
    op.drop_table("mp_project_knowledge_vectors")
    op.drop_index("ix_mp_project_knowledge_index_batches_run_status", table_name="mp_project_knowledge_index_batches")
    op.drop_table("mp_project_knowledge_index_batches")
    op.drop_index("ix_mp_project_knowledge_index_runs_project_status", table_name="mp_project_knowledge_index_runs")
    op.drop_table("mp_project_knowledge_index_runs")

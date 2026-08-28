"""Add local PyTorch runtime, RAG indexes, and speech alignment facts.

Revision ID: 0068_local_ai_model_runtime
Revises: 0067_single_gpu_runtime_orchestration
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0068_local_ai_model_runtime"
down_revision = "0067_single_gpu_runtime_orchestration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gpu_runtime_leases", recreate="always") as batch:
        batch.drop_constraint("ck_gpu_runtime_lease_kind", type_="check")
        batch.create_check_constraint(
            "ck_gpu_runtime_lease_kind",
            "runtime_kind IN ('COMFY','OLLAMA','PYTORCH')",
        )
    with op.batch_alter_table("gpu_runtime_state", recreate="always") as batch:
        batch.drop_constraint("ck_gpu_runtime_state_kind", type_="check")
        batch.create_check_constraint(
            "ck_gpu_runtime_state_kind",
            "resident_runtime IS NULL OR resident_runtime IN ('COMFY','OLLAMA','PYTORCH')",
        )

    op.create_table(
        "embedding_indexes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "source_document_version_id",
            sa.String(36),
            sa.ForeignKey("source_document_versions.id"),
            nullable=False,
        ),
        sa.Column("model_artifact_id", sa.String(36), sa.ForeignKey("model_artifacts.id"), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.UniqueConstraint(
            "source_document_version_id",
            "model_artifact_id",
            "content_hash",
            name="uq_embedding_indexes_source_model_content",
        ),
    )
    op.create_index("ix_embedding_indexes_project_status", "embedding_indexes", ["project_id", "status"])
    op.create_table(
        "embedding_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("embedding_index_id", sa.String(36), sa.ForeignKey("embedding_indexes.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("vector_f32", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("embedding_index_id", "ordinal", name="uq_embedding_chunks_index_ordinal"),
    )
    op.create_index("ix_embedding_chunks_index", "embedding_chunks", ["embedding_index_id", "ordinal"])

    op.create_table(
        "speech_alignment_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("media_version_id", sa.String(36), sa.ForeignKey("media_versions.id"), nullable=False),
        sa.Column(
            "execution_profile_version_id",
            sa.String(36),
            sa.ForeignKey("execution_profile_versions.id"),
            nullable=False,
        ),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id")),
        sa.Column("language", sa.String(40)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("transcript_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("transcript_hash", sa.String(64)),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
    )
    op.create_index("ix_speech_alignment_media_status", "speech_alignment_runs", ["media_version_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_speech_alignment_media_status", table_name="speech_alignment_runs")
    op.drop_table("speech_alignment_runs")
    op.drop_index("ix_embedding_chunks_index", table_name="embedding_chunks")
    op.drop_table("embedding_chunks")
    op.drop_index("ix_embedding_indexes_project_status", table_name="embedding_indexes")
    op.drop_table("embedding_indexes")
    with op.batch_alter_table("gpu_runtime_state", recreate="always") as batch:
        batch.drop_constraint("ck_gpu_runtime_state_kind", type_="check")
        batch.create_check_constraint(
            "ck_gpu_runtime_state_kind",
            "resident_runtime IS NULL OR resident_runtime IN ('COMFY','OLLAMA')",
        )
    with op.batch_alter_table("gpu_runtime_leases", recreate="always") as batch:
        batch.drop_constraint("ck_gpu_runtime_lease_kind", type_="check")
        batch.create_check_constraint("ck_gpu_runtime_lease_kind", "runtime_kind IN ('COMFY','OLLAMA')")

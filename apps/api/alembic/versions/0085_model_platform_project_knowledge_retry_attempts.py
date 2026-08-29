"""Make Project Knowledge retries immutable V2 attempts.

Revision ID: 0085_model_platform_project_knowledge_retry_attempts
Revises: 0084_model_platform_project_knowledge_indexes
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0085_model_platform_project_knowledge_retry_attempts"
down_revision = "0084_model_platform_project_knowledge_indexes"
branch_labels = None
depends_on = None

_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def upgrade() -> None:
    # SQLite needs a table rebuild to change the immutable idempotency key.
    # Existing rows become attempt #1 and retain Job/artifact/vector evidence.
    with op.batch_alter_table(
        "mp_project_knowledge_index_runs", recreate="always", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.add_column(sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(
            sa.Column(
                "retry_of_index_run_id",
                sa.String(36),
                sa.ForeignKey(
                    "mp_project_knowledge_index_runs.id",
                    name="fk_mp_project_knowledge_index_runs_retry_of_index_run_id_mp_project_knowledge_index_runs",
                    ondelete="RESTRICT",
                ),
                nullable=True,
            )
        )
        batch.drop_constraint("uq_mp_project_knowledge_index_run_source_profile", type_="unique")
        batch.create_unique_constraint(
            "uq_mp_project_knowledge_index_run_attempt",
            ["source_document_version_id", "execution_profile_version_id", "source_text_hash", "attempt_no"],
        )
    op.create_index(
        "ix_mp_project_knowledge_index_runs_retry",
        "mp_project_knowledge_index_runs", ["retry_of_index_run_id", "attempt_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_mp_project_knowledge_index_runs_retry", table_name="mp_project_knowledge_index_runs")
    with op.batch_alter_table(
        "mp_project_knowledge_index_runs", recreate="always", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint("uq_mp_project_knowledge_index_run_attempt", type_="unique")
        batch.drop_column("retry_of_index_run_id")
        batch.drop_column("attempt_no")
        batch.create_unique_constraint(
            "uq_mp_project_knowledge_index_run_source_profile",
            ["source_document_version_id", "execution_profile_version_id", "source_text_hash"],
        )

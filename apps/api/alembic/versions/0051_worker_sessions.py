"""Durable local worker sessions and API/worker handshake state (PR-CUR-011)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0051_worker_sessions"
down_revision = "0050_character_identity_packs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("worker_id", sa.String(100), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=True),
        sa.Column("api_version", sa.String(80), nullable=False),
        sa.Column("worker_version", sa.String(80), nullable=False),
        sa.Column("protocol_version", sa.String(80), nullable=False),
        sa.Column("supported_channels_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("heartbeat_at", sa.Text(), nullable=False),
        sa.Column("lease_expires_at", sa.Text(), nullable=False),
        sa.Column("stopped_at", sa.Text(), nullable=True),
        sa.Column("restart_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_restart_at", sa.Text(), nullable=True),
        sa.Column("last_exit_code", sa.Integer(), nullable=True),
        sa.Column("last_error_redacted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="supervisor"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.CheckConstraint(
            "status IN ('STARTING','RUNNING','BACKING_OFF','DRAINING','STOPPED','STALE','INCOMPATIBLE')",
            name="ck_worker_sessions_status",
        ),
    )
    op.create_index("ix_worker_sessions_worker_started", "worker_sessions", ["worker_id", "started_at"])
    op.create_index("ix_worker_sessions_status_lease", "worker_sessions", ["status", "lease_expires_at"])
    op.add_column("job_attempts", sa.Column("worker_session_id", sa.String(36), nullable=True))
    op.create_index("ix_job_attempts_worker_session", "job_attempts", ["worker_session_id"])


def downgrade() -> None:
    op.drop_index("ix_job_attempts_worker_session", table_name="job_attempts")
    op.drop_column("job_attempts", "worker_session_id")
    op.drop_index("ix_worker_sessions_status_lease", table_name="worker_sessions")
    op.drop_index("ix_worker_sessions_worker_started", table_name="worker_sessions")
    op.drop_table("worker_sessions")

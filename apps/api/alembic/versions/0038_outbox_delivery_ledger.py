"""Persist bounded generic outbox delivery attempts and recovery state."""

import sqlalchemy as sa

from alembic import op


revision = "0038_outbox_delivery_ledger"
down_revision = "0037_job_progress_scheduler"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep dispatcher claims durable across API restarts.

    ``outbox_events.delivered_at`` remains the compatibility/read-model flag.
    This ledger adds endpoint-scoped claims so a process that exits between
    POST and acknowledgement can be reclaimed without losing the event or
    creating an unbounded retry loop.
    """

    op.create_table(
        "outbox_delivery_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("outbox_events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until_at", sa.Text()),
        sa.Column("next_attempt_at", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_response_status", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("delivered_at", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.UniqueConstraint("event_id", "endpoint_url", name="uq_outbox_delivery_event_endpoint"),
    )
    op.create_index(
        "ix_outbox_delivery_attempts_endpoint_status_next",
        "outbox_delivery_attempts",
        ["endpoint_url", "status", "next_attempt_at"],
    )
    op.create_index(
        "ix_outbox_delivery_attempts_event_endpoint",
        "outbox_delivery_attempts",
        ["event_id", "endpoint_url"],
    )


def downgrade() -> None:
    raise RuntimeError("Outbox delivery ledger is release history; restore migration preflight backup")

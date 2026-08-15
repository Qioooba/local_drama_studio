"""Persist scoped loopback automation clients and bounded webhook delivery."""

import sqlalchemy as sa

from alembic import op

revision = "0034_local_automation_webhooks"
down_revision = "0033_brand_watermark_compliance_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_clients",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.Text()),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.UniqueConstraint("code", name="uq_automation_clients_code"),
        sa.UniqueConstraint("token_hash", name="uq_automation_clients_token_hash"),
    )
    op.create_index("ix_automation_clients_project_status", "automation_clients", ["project_id", "status"])
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("automation_client_id", sa.String(36), sa.ForeignKey("automation_clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("event_types_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("signing_secret", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
    )
    op.create_index("ix_webhook_subscriptions_client_status", "webhook_subscriptions", ["automation_client_id", "status"])
    op.create_index("ix_webhook_subscriptions_project_status", "webhook_subscriptions", ["project_id", "status"])
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("subscription_id", sa.String(36), sa.ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("outbox_events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_response_status", sa.Integer()),
        sa.Column("signature", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("delivered_at", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.UniqueConstraint("subscription_id", "event_id", name="uq_webhook_deliveries_subscription_event"),
    )
    op.create_index("ix_webhook_deliveries_status_next", "webhook_deliveries", ["status", "next_attempt_at"])
    op.create_index("ix_webhook_deliveries_subscription_status", "webhook_deliveries", ["subscription_id", "status"])


def downgrade() -> None:
    raise RuntimeError("Automation webhook evidence is release history; restore migration preflight backup")

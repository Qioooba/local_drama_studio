"""Add durable production sessions for one-click episode and drama runs."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0096_production_sessions"
down_revision = "0095_video_upscale_delivery"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default=sa.text("'production-session.v1'")),
    )


def upgrade() -> None:
    op.create_table(
        "production_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("production_mode", sa.String(16), nullable=False),
        sa.Column("checkpoint_policy", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_stage", sa.String(40), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("configuration_json", sa.Text(), nullable=False),
        sa.Column("counters_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("started_at", sa.Text()),
        sa.Column("finished_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_production_sessions_project_idempotency"),
        sa.CheckConstraint(
            "scope_type IN ('SINGLE_EPISODE','WHOLE_DRAMA')",
            name="ck_production_sessions_scope",
        ),
        sa.CheckConstraint(
            "production_mode IN ('DRAFT','BALANCED','QUALITY')",
            name="ck_production_sessions_mode",
        ),
        sa.CheckConstraint(
            "checkpoint_policy IN ('AUTO_CONTINUE','AFTER_ASSETS','AFTER_SHOT_PLAN','BEFORE_VIDEO','ON_EXCEPTION')",
            name="ck_production_sessions_checkpoint",
        ),
        sa.CheckConstraint(
            "status IN ('READY','RUNNING','PAUSED','WAITING_REVIEW','COMPLETED','FAILED','CANCELLED')",
            name="ck_production_sessions_status",
        ),
    )
    op.create_table(
        "production_session_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("current_stage", sa.String(40), nullable=False),
        sa.Column("source_episode_revision", sa.Integer(), nullable=False),
        sa.Column("shot_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["session_id"], ["production_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("session_id", "episode_id", name="uq_production_session_items_episode"),
        sa.UniqueConstraint("session_id", "ordinal", name="uq_production_session_items_ordinal"),
        sa.CheckConstraint("ordinal > 0", name="ck_production_session_items_ordinal"),
        sa.CheckConstraint("shot_count >= 0", name="ck_production_session_items_shot_count"),
        sa.CheckConstraint(
            "state IN ('PENDING','RUNNING','WAITING','BLOCKED','FAILED','COMPLETED','SKIPPED','CANCELLED')",
            name="ck_production_session_items_state",
        ),
    )
    op.create_table(
        "production_choices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("session_item_id", sa.String(36)),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(80), nullable=False),
        sa.Column("slot_role", sa.String(40), nullable=False),
        sa.Column("candidate_id", sa.String(80), nullable=False),
        sa.Column("choice_type", sa.String(16), nullable=False),
        sa.Column("selection_state", sa.String(16), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("reason_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("human_review_decision_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["session_id"], ["production_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_item_id"], ["production_session_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["human_review_decision_id"], ["review_decisions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "session_id", "target_kind", "target_id", "slot_role",
            name="uq_production_choices_session_target_role",
        ),
        sa.CheckConstraint("choice_type IN ('MACHINE','HUMAN')", name="ck_production_choices_type"),
        sa.CheckConstraint(
            "selection_state IN ('TEMPORARY','CONFIRMED','REVOKED')",
            name="ck_production_choices_state",
        ),
    )
    op.create_table(
        "production_session_job_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("session_item_id", sa.String(36)),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("stage_code", sa.String(40), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("link_state", sa.String(16), nullable=False, server_default=sa.text("'ACTIVE'")),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["session_id"], ["production_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_item_id"], ["production_session_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("session_id", "job_id", name="uq_production_session_job_links_job"),
        sa.CheckConstraint("link_state IN ('ACTIVE','SUPERSEDED')", name="ck_production_session_job_links_state"),
    )
    op.create_index("ix_production_sessions_project_status", "production_sessions", ["project_id", "status"])
    op.create_index("ix_production_sessions_updated", "production_sessions", ["updated_at"])
    op.create_index("ix_production_session_items_state", "production_session_items", ["session_id", "state"])
    op.create_index("ix_production_choices_episode", "production_choices", ["episode_id", "target_kind"])
    op.create_index("ix_production_session_job_links_item", "production_session_job_links", ["session_item_id", "stage_code"])


def downgrade() -> None:
    op.drop_index("ix_production_session_job_links_item", table_name="production_session_job_links")
    op.drop_index("ix_production_choices_episode", table_name="production_choices")
    op.drop_index("ix_production_session_items_state", table_name="production_session_items")
    op.drop_index("ix_production_sessions_updated", table_name="production_sessions")
    op.drop_index("ix_production_sessions_project_status", table_name="production_sessions")
    op.drop_table("production_session_job_links")
    op.drop_table("production_choices")
    op.drop_table("production_session_items")
    op.drop_table("production_sessions")

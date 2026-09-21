"""Expose production sessions that require a person's intervention."""

from __future__ import annotations

from alembic import op

revision = "0100_production_session_waiting_user"
down_revision = "0099_production_session_asset_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("production_sessions", recreate="always") as batch:
        batch.drop_constraint("ck_production_sessions_status", type_="check")
        batch.create_check_constraint(
            "ck_production_sessions_status",
            "status IN ('READY','RUNNING','PAUSED','WAITING_USER','WAITING_REVIEW',"
            "'COMPLETED','FAILED','CANCELLED')",
        )


def downgrade() -> None:
    raise RuntimeError(
        "production session status migrations are append-only; restore the pre-migration backup"
    )

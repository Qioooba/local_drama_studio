"""Persist a project-level target episode duration.

Episodes keep their own resolved ``target_duration_ms`` value so a later
change to a project's default never rewrites an existing episode or its
production plan.  Projects created before this field existed are backfilled
once from their earliest authored episode; this is a compatibility migration,
not a runtime fallback to the first episode.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0094_project_target_duration"
down_revision = "0093_shot_prompt_bundle_snapshots"
branch_labels = None
depends_on = None

DEFAULT_TARGET_DURATION_MS = 120_000


def upgrade() -> None:
    # Keep the column nullable for SQLite and for projects whose legacy data
    # has no episode yet.  The application resolves that legacy empty case to
    # the documented product default (120 seconds) without mutating it.
    op.add_column(
        "projects",
        sa.Column("target_duration_ms", sa.Integer(), nullable=True),
    )
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE projects
            SET target_duration_ms = (
                SELECT e.target_duration_ms
                FROM episodes e
                JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=projects.id
                ORDER BY s.display_order, s.number, s.id,
                         e.display_order, e.number, e.id
                LIMIT 1
            )
            WHERE target_duration_ms IS NULL
            """
        )
    )
    connection.execute(
        sa.text(
            "UPDATE projects SET target_duration_ms=:default_ms "
            "WHERE target_duration_ms IS NULL OR target_duration_ms <= 0"
        ),
        {"default_ms": DEFAULT_TARGET_DURATION_MS},
    )


def downgrade() -> None:
    op.drop_column("projects", "target_duration_ms")

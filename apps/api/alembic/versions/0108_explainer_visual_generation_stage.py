"""Register the explainer picture stage as its own job stage code.

Revision ID: 0108_explainer_visual_generation_stage
Revises: 0107_retire_still_motion_render_type

What this migration changes and why
-----------------------------------
``VISUAL_GENERATION`` is the stage that turns an adopted keyframe into a real AI
image-to-video clip.  Until now it had no ``job_stage_definitions`` row: the
production graph smuggled it under the storyboard step's stage code, so the stage
could only ever run inside a whole-graph run.  That is exactly why a film whose
graph run had stopped could not get its remaining clips.

``jobs.stage_code`` has a foreign key to ``job_stage_definitions``, so scheduling
the stage as a standalone command is impossible without registering the code.
This migration adds only that dictionary row — it changes no business data, and
the historical jobs that ran under ``EXPLAINER_STORYBOARD`` keep their label.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0108_explainer_visual_generation_stage"
down_revision = "0107_retire_still_motion_render_type"
branch_labels = None
depends_on = None


STAGE_CODE = "VISUAL_GENERATION"


def upgrade() -> None:
    bind = op.get_bind()
    definition = sa.table(
        "job_stage_definitions",
        sa.column("code", sa.String),
        sa.column("title", sa.String),
        sa.column("domain", sa.String),
        sa.column("active", sa.Integer),
    )
    existing = bind.execute(
        sa.text("SELECT COUNT(*) FROM job_stage_definitions WHERE code=:code"),
        {"code": STAGE_CODE},
    ).scalar()
    if not existing:
        op.bulk_insert(
            definition,
            [
                {
                    "code": STAGE_CODE,
                    "title": "解说画面与图生视频片段",
                    "domain": "EXPLAINER",
                    "active": 1,
                }
            ],
        )


def downgrade() -> None:
    """Remove only the dictionary row this migration added.

    Jobs that already ran under this code would keep a dangling foreign key if the
    row disappeared, so the downgrade refuses while any job still references it
    instead of silently orphaning execution history.
    """

    bind = op.get_bind()
    in_use = bind.execute(
        sa.text("SELECT COUNT(*) FROM jobs WHERE stage_code=:code"), {"code": STAGE_CODE}
    ).scalar()
    if in_use:
        raise RuntimeError(
            "0108 downgrade refused: jobs still reference the VISUAL_GENERATION stage code"
        )
    bind.execute(
        sa.text("DELETE FROM job_stage_definitions WHERE code=:code"), {"code": STAGE_CODE}
    )

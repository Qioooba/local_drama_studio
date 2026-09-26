"""Register the explainer subtitle stage as its own job stage code.

Revision ID: 0109_explainer_subtitle_build_stage
Revises: 0108_explainer_visual_generation_stage

Why
---
``SUBTITLE_BUILD`` had a first-party handler and a place in the ``EXPLAINER_TASK``
dispatch family, but no ``job_stage_definitions`` row, and ``jobs.stage_code`` has a
foreign key to that table.  A complete delivery package must contain a subtitle file
for every locale the edition declares, so an edition driven page by page (rather than
by one whole production-graph run) could never build the subtitles the export stage
requires.  This migration adds only the dictionary row; no business data changes.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0109_explainer_subtitle_build_stage"
down_revision = "0108_explainer_visual_generation_stage"
branch_labels = None
depends_on = None


STAGE_CODE = "SUBTITLE_BUILD"


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
                    "title": "解说字幕构建",
                    "domain": "EXPLAINER",
                    "active": 1,
                }
            ],
        )


def downgrade() -> None:
    """Remove the dictionary row, refusing while any job still references it."""

    bind = op.get_bind()
    in_use = bind.execute(
        sa.text("SELECT COUNT(*) FROM jobs WHERE stage_code=:code"), {"code": STAGE_CODE}
    ).scalar()
    if in_use:
        raise RuntimeError(
            "0109 downgrade refused: jobs still reference the SUBTITLE_BUILD stage code"
        )
    bind.execute(
        sa.text("DELETE FROM job_stage_definitions WHERE code=:code"), {"code": STAGE_CODE}
    )

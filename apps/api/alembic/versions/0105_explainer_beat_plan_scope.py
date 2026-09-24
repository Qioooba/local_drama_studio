"""Per-plan attribution for explainer visual beats (audit A11).

Revision ID: 0105_explainer_beat_plan_scope
Revises: 0104_pipeline_apply_watermark
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0105_explainer_beat_plan_scope"
down_revision = "0104_pipeline_apply_watermark"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Scope beat codes to the plan that produced them.

    ``explainer_visual_beats`` had exactly one uniqueness rule, ``(video_id,
    code)``, and every reader (``ExplainerRepository.beats`` / ``beat_links``)
    filtered on ``video_id`` alone.  A second storyboard plan for the same video
    therefore had to either reuse the first plan's codes — silently mixing two
    plans — or fail on the unique constraint, and a worker could not ask for "the
    beats of the plan I was dispatched with".

    The attribution column is nullable and is deliberately **not** backfilled.
    Existing beats carry no trustworthy record of which plan step produced them,
    and the design forbids inventing one, so they keep ``plan_step_binding_id IS
    NULL`` and stay readable through the explicit legacy path.  Their original
    ``(video_id, code)`` uniqueness is preserved as a *partial* unique index so the
    legacy rows remain self-consistent without constraining a new plan.

    SQLite has to rebuild the table to drop the old UNIQUE constraint, and
    ``beat_narration_links`` references this table with ``ON DELETE CASCADE``: with
    foreign keys enforced, the rebuild's ``DROP TABLE`` fires that cascade and
    deletes every beat/narration link.  Following the repository convention
    (0062/0064/0066), the pragma is turned off for the rebuild, its effect is
    proven with ``PRAGMA foreign_key_check``, and enforcement is restored.
    """

    bind = op.get_bind()
    bind.commit()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    try:
        with op.batch_alter_table("explainer_visual_beats", recreate="always") as batch:
            batch.add_column(sa.Column("plan_step_binding_id", sa.String(36), nullable=True))
            batch.drop_constraint("uq_explainer_visual_beats_code", type_="unique")
            batch.create_unique_constraint(
                "uq_explainer_visual_beats_plan_code", ["plan_step_binding_id", "code"]
            )
            batch.create_index(
                "ix_explainer_visual_beats_plan", ["plan_step_binding_id", "ordinal"], unique=False
            )
        op.create_index(
            "uq_explainer_visual_beats_legacy_code",
            "explainer_visual_beats",
            ["video_id", "code"],
            unique=True,
            sqlite_where=sa.text("plan_step_binding_id IS NULL"),
        )
    finally:
        bind.exec_driver_sql("PRAGMA legacy_alter_table=OFF")

    bind.commit()
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(
            f"0105 explainer beat plan scope produced foreign key violations: {violations[:10]}"
        )
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    bind = op.get_bind()
    bind.commit()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    try:
        op.drop_index("uq_explainer_visual_beats_legacy_code", table_name="explainer_visual_beats")
        with op.batch_alter_table("explainer_visual_beats", recreate="always") as batch:
            batch.drop_index("ix_explainer_visual_beats_plan")
            batch.drop_constraint("uq_explainer_visual_beats_plan_code", type_="unique")
            batch.create_unique_constraint("uq_explainer_visual_beats_code", ["video_id", "code"])
            batch.drop_column("plan_step_binding_id")
    finally:
        bind.exec_driver_sql("PRAGMA legacy_alter_table=OFF")

    bind.commit()
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(
            f"0105 downgrade produced foreign key violations: {violations[:10]}"
        )
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")

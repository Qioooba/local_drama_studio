"""Retire the deterministic 静图推拉 (still-motion) render type.

Revision ID: 0107_retire_still_motion_render_type
Revises: 0106_explainer_visual_candidate_owners

What this migration changes and why
-----------------------------------
A 解说 (explainer) film is an AI 图生视频 product.  ``STILL_MOTION`` and
``PARALLAX`` described a *still image plus a deterministic FFmpeg camera move*.
That is not a video model's output, the product no longer offers it, and keeping
the value in the enum would let a caller keep planning it.  The whole
deterministic push/pull picture path was therefore removed from the application,
and this migration removes its last legal representation from the database:

1. Every ``explainer_visual_beats.render_type`` row still holding a retired value
   is rewritten to ``I2V`` — the beat must now be produced by a real image-to-video
   generation.  Rewriting is the honest choice here: the beat is a *plan*, and the
   plan's intent was always "this画面段 needs a moving picture".
2. ``actual_fallback_type`` is cleared where it recorded the retired degradation,
   and ``fallback_reason`` keeps a searchable marker so the history is not lost.
3. The ``ck_explainer_visual_beats_render_type`` check constraint is narrowed to
   ``('I2V','INFOGRAPHIC','LICENSED_MEDIA')`` so the retired values cannot come
   back through a direct write.

Adopted candidate rows are deliberately *not* rewritten.  A clip that really was
a still plus a camera move stays recorded as exactly that in
``explainer_media_candidates`` / ``explainer_beat_selections``; rewriting history
would turn a real, auditable artefact into a false claim of AI generation.  Those
rows simply keep their retired label and surface as planned-vs-actual differences.

SQLite has to rebuild a table to change a CHECK constraint, and
``beat_narration_links`` / ``explainer_beat_selections`` reference
``explainer_visual_beats`` with ``ON DELETE CASCADE``.  Following the repository
convention in 0105/0106, foreign keys are disabled for the rebuild, the result is
proven with ``PRAGMA foreign_key_check``, and enforcement is restored afterwards.
"""

from __future__ import annotations

from alembic import op

revision = "0107_retire_still_motion_render_type"
down_revision = "0106_explainer_visual_candidate_owners"
branch_labels = None
depends_on = None


RETIRED_RENDER_TYPES = "('STILL_MOTION','PARALLAX')"
RENDER_TYPE_CHECK = "render_type IN ('I2V','INFOGRAPHIC','LICENSED_MEDIA')"
LEGACY_RENDER_TYPE_CHECK = "render_type IN ('STILL_MOTION','PARALLAX','I2V','INFOGRAPHIC','LICENSED_MEDIA')"


def upgrade() -> None:
    bind = op.get_bind()
    bind.commit()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    try:
        # The plan's intent was always a moving picture; the retired value only said
        # *how* this build produced movement.  Promote the plan to the real route.
        bind.exec_driver_sql(
            "UPDATE explainer_visual_beats SET render_type='I2V' "
            f"WHERE render_type IN {RETIRED_RENDER_TYPES}"
        )
        bind.exec_driver_sql(
            "UPDATE explainer_visual_beats SET fallback_reason='RETIRED_STILL_MOTION_PICTURE_PATH' "
            f"WHERE actual_fallback_type IN {RETIRED_RENDER_TYPES}"
        )
        bind.exec_driver_sql(
            "UPDATE explainer_visual_beats SET actual_fallback_type=NULL "
            f"WHERE actual_fallback_type IN {RETIRED_RENDER_TYPES}"
        )
        with op.batch_alter_table("explainer_visual_beats", recreate="always") as batch:
            batch.drop_constraint("ck_explainer_visual_beats_render_type", type_="check")
            batch.create_check_constraint(
                "ck_explainer_visual_beats_render_type",
                RENDER_TYPE_CHECK,
            )
    finally:
        bind.exec_driver_sql("PRAGMA legacy_alter_table=OFF")

    bind.commit()
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(
            "0107 retire still motion render type produced foreign key violations: "
            f"{violations[:10]}"
        )
    leftovers = list(
        bind.exec_driver_sql(
            "SELECT COUNT(*) FROM explainer_visual_beats "
            f"WHERE render_type IN {RETIRED_RENDER_TYPES}"
        )
    )
    if leftovers and int(leftovers[0][0]):
        raise RuntimeError("0107 left retired render types on explainer_visual_beats")
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    """Widen the constraint back without re-labelling anything.

    The retired values are gone from the data on purpose; a downgrade restores the
    schema's ability to *store* them, not a claim that any beat is still produced
    that way.  Re-labelling adopters back to a still-motion plan would be a
    fabricated fact, so it is not done here.
    """

    bind = op.get_bind()
    bind.commit()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    try:
        with op.batch_alter_table("explainer_visual_beats", recreate="always") as batch:
            batch.drop_constraint("ck_explainer_visual_beats_render_type", type_="check")
            batch.create_check_constraint(
                "ck_explainer_visual_beats_render_type",
                LEGACY_RENDER_TYPE_CHECK,
            )
    finally:
        bind.exec_driver_sql("PRAGMA legacy_alter_table=OFF")

    bind.commit()
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(
            f"0107 downgrade produced foreign key violations: {violations[:10]}"
        )
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")

"""Link promoted generated media to the exact verified job artifact."""

import sqlalchemy as sa

from alembic import op

revision = "0013_g6_artifact_media_lineage"
down_revision = "0012_g6_frame_anchor_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite can add this nullable lineage column without rebuilding the core
    # table (which is referenced by many immutable-history foreign keys).
    op.add_column("media_versions", sa.Column("source_artifact_id", sa.String(36)))
    op.create_index(
        "uq_media_versions_source_artifact",
        "media_versions",
        ["source_artifact_id"],
        unique=True,
    )


def downgrade() -> None:
    raise RuntimeError("Generated artifact lineage is immutable production history; restore migration preflight backup")

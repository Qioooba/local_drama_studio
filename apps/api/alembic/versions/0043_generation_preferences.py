"""Versioned project/episode/shot generation preferences."""

import sqlalchemy as sa

from alembic import op

revision = "0043_generation_preferences"
down_revision = "0042_asset_bible_states_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_preference_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("owner_type", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("capability", sa.String(120), nullable=False),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["current_version_id"], ["generation_preference_versions.id"],
            ondelete="RESTRICT", use_alter=True, name="fk_generation_preference_current_version",
        ),
        sa.UniqueConstraint(
            "project_id", "owner_type", "owner_id", "capability",
            name="uq_generation_preference_owner_capability",
        ),
        sa.CheckConstraint("owner_type IN ('PROJECT','EPISODE','SHOT')", name="ck_generation_preference_owner_type"),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_generation_preference_status"),
    )
    op.create_index(
        "ix_generation_preference_sets_resolution",
        "generation_preference_sets",
        ["project_id", "capability", "owner_type", "owner_id", "status"],
    )
    op.create_table(
        "generation_preference_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("preference_set_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("execution_profile_version_id", sa.String(36), nullable=True),
        sa.Column("resolution_mode", sa.String(16), nullable=False),
        sa.Column("settings_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_frozen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["preference_set_id"], ["generation_preference_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_profile_version_id"], ["execution_profile_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("preference_set_id", "version_no", name="uq_generation_preference_versions_no"),
        sa.CheckConstraint("resolution_mode IN ('AUTO','EXPLICIT')", name="ck_generation_preference_resolution_mode"),
    )
    op.create_index(
        "ix_generation_preference_versions_profile",
        "generation_preference_versions",
        ["execution_profile_version_id"],
    )

def downgrade() -> None:
    raise RuntimeError("Generation preferences are append-only release history; restore migration preflight backup")

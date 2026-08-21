"""Immutable declarative Director Recipes and explicit project binding."""

import sqlalchemy as sa

from alembic import op

revision = "0046_director_recipes"
down_revision = "0045_qc_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "director_recipes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "code", name="uq_director_recipes_project_code"),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_director_recipes_status"),
    )
    op.create_table(
        "director_recipe_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("recipe_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("recipe_json", sa.Text(), nullable=False),
        sa.Column("recipe_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_frozen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["recipe_id"], ["director_recipes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("recipe_id", "version_no", name="uq_director_recipe_version"),
        sa.UniqueConstraint("recipe_id", "recipe_hash", name="uq_director_recipe_hash"),
        sa.CheckConstraint("is_frozen=1", name="ck_director_recipe_frozen"),
    )
    op.create_index("ix_director_recipe_versions_recipe", "director_recipe_versions", ["recipe_id", "version_no"])
    op.create_table(
        "project_director_recipe_bindings",
        sa.Column("project_id", sa.String(36), primary_key=True),
        sa.Column("recipe_version_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipe_version_id"], ["director_recipe_versions.id"], ondelete="RESTRICT"),
    )
    # Nullable provenance columns preserve all historical Variant rows.  SQLite
    # cannot ALTER-add foreign keys, so command validation owns scope/existence.
    op.add_column("generation_variants", sa.Column("director_recipe_version_id", sa.String(36), nullable=True))
    op.add_column("generation_variants", sa.Column("director_recipe_hash", sa.String(64), nullable=True))
    op.create_index("ix_generation_variants_director_recipe", "generation_variants", ["director_recipe_version_id"])


def downgrade() -> None:
    raise RuntimeError("Director Recipe versions are immutable production provenance; restore pre-migration backup")

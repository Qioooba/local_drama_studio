"""Versioned QC policy inheritance and bounded variant QC dispositions."""

import sqlalchemy as sa

from alembic import op

revision = "0045_qc_policies"
down_revision = "0044_shot_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_qc_policy_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("owner_type", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("current_version_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["current_version_id"], ["generation_qc_policy_versions.id"],
            ondelete="RESTRICT", use_alter=True, name="fk_generation_qc_policy_current_version",
        ),
        sa.UniqueConstraint("project_id", "owner_type", "owner_id", "stage", name="uq_generation_qc_policy_scope"),
        sa.CheckConstraint("owner_type IN ('PROJECT','EPISODE','SHOT')", name="ck_generation_qc_policy_owner"),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_generation_qc_policy_status"),
    )
    op.create_index(
        "ix_generation_qc_policy_resolution", "generation_qc_policy_sets",
        ["project_id", "stage", "owner_type", "owner_id", "status"],
    )
    op.create_table(
        "generation_qc_policy_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("policy_set_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("max_auto_rerolls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_reroll_categories_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("is_frozen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["policy_set_id"], ["generation_qc_policy_sets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("policy_set_id", "version_no", name="uq_generation_qc_policy_version"),
        sa.CheckConstraint("max_auto_rerolls BETWEEN 0 AND 10", name="ck_generation_qc_policy_reroll_bound"),
        sa.CheckConstraint("is_frozen=1", name="ck_generation_qc_policy_frozen"),
    )
    op.create_table(
        "variant_qc_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("variant_id", sa.String(36), nullable=False),
        sa.Column("machine_check_run_id", sa.String(36), nullable=False),
        sa.Column("policy_version_id", sa.String(36), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("retry_ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("child_variant_id", sa.String(36), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["variant_id"], ["generation_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["machine_check_run_id"], ["machine_check_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["policy_version_id"], ["generation_qc_policy_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["child_variant_id"], ["generation_variants.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("variant_id", "machine_check_run_id", name="uq_variant_qc_machine_check"),
        sa.CheckConstraint(
            "disposition IN ('PASS','ATTENTION','AUTO_REROLL_ALLOWED','WAITING_GATE')",
            name="ck_variant_qc_disposition",
        ),
        sa.CheckConstraint("retry_ordinal BETWEEN 0 AND 10", name="ck_variant_qc_retry_ordinal"),
    )
    op.create_index("ix_variant_qc_links_variant_created", "variant_qc_links", ["variant_id", "created_at"])
    op.create_index("ix_variant_qc_links_child", "variant_qc_links", ["child_variant_id"])


def downgrade() -> None:
    raise RuntimeError("QC policy versions and variant dispositions are release evidence; restore pre-migration backup")

"""Persist versioned watermark profiles and local compliance policies."""

import sqlalchemy as sa

from alembic import op

revision = "0033_brand_watermark_compliance_versions"
down_revision = "0032_episode_render_execution_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watermark_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.UniqueConstraint("project_id", "code", "version_no", name="uq_watermark_profiles_project_code_version"),
    )
    op.create_index("ix_watermark_profiles_project_status", "watermark_profiles", ["project_id", "status"])
    op.create_table(
        "compliance_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("rules_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.UniqueConstraint("project_id", "code", "version_no", name="uq_compliance_policies_project_code_version"),
    )
    op.create_index("ix_compliance_policies_project_status", "compliance_policies", ["project_id", "status"])
    op.add_column("delivery_packages", sa.Column("brand_kit_id", sa.String(36)))
    op.add_column("delivery_packages", sa.Column("watermark_profile_id", sa.String(36)))
    op.add_column("delivery_packages", sa.Column("compliance_policy_id", sa.String(36)))
    op.add_column("delivery_packages", sa.Column("machine_preflight_status", sa.String(24), nullable=False, server_default="NOT_RUN"))
    op.add_column("delivery_packages", sa.Column("machine_preflight_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("delivery_packages", sa.Column("human_review_status", sa.String(24), nullable=False, server_default="PENDING"))
    op.add_column("delivery_packages", sa.Column("platform_review_status", sa.String(24), nullable=False, server_default="PENDING"))


def downgrade() -> None:
    raise RuntimeError("Brand/watermark/compliance evidence is release history; restore migration preflight backup")

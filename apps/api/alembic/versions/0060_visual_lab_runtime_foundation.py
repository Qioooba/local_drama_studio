"""Replace the production canvas with Visual Lab and versioned runtimes.

Revision ID: 0060_visual_lab_runtime_foundation
Revises: 0059_automation_workflow_versions
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0060_visual_lab_runtime_foundation"
down_revision = "0059_automation_workflow_versions"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.String(40), nullable=False, server_default="v1"),
    )


def upgrade() -> None:
    op.create_table(
        "visual_lab_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("episode_id", sa.String(36)),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("topology_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("viewport_json", sa.Text(), nullable=False, server_default='{"x":0,"y":0,"zoom":1}'),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "code", name="uq_visual_lab_project_code"),
    )
    op.create_table(
        "visual_lab_nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("node_kind", sa.String(40), nullable=False),
        sa.Column("current_content_revision_id", sa.String(36)),
        sa.Column("position_x", sa.Float(), nullable=False),
        sa.Column("position_y", sa.Float(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False, server_default="260"),
        sa.Column("height", sa.Float(), nullable=False, server_default="180"),
        sa.Column("z_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collapsed", sa.Integer(), nullable=False, server_default="0"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["document_id"], ["visual_lab_documents.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "visual_lab_node_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(36)),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["node_id"], ["visual_lab_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["visual_lab_node_revisions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("node_id", "revision_no", name="uq_visual_lab_node_revision"),
    )
    with op.batch_alter_table("visual_lab_nodes") as batch:
        batch.create_foreign_key(
            "fk_visual_lab_node_current_revision",
            "visual_lab_node_revisions",
            ["current_content_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_table(
        "visual_lab_edges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("source_node_id", sa.String(36), nullable=False),
        sa.Column("source_port", sa.String(80), nullable=False),
        sa.Column("target_node_id", sa.String(36), nullable=False),
        sa.Column("target_port", sa.String(80), nullable=False),
        sa.Column("edge_kind", sa.String(32), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["document_id"], ["visual_lab_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_node_id"], ["visual_lab_nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_node_id"], ["visual_lab_nodes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "source_node_id", "source_port", "target_node_id", "target_port", name="uq_visual_lab_edge"),
        sa.CheckConstraint("source_node_id <> target_node_id", name="ck_visual_lab_no_self_edge"),
    )
    op.create_table(
        "visual_lab_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("snapshot_no", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["document_id"], ["visual_lab_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "snapshot_no", name="uq_visual_lab_snapshot_no"),
    )
    op.create_table(
        "visual_lab_promotions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("node_id", sa.String(36), nullable=False),
        sa.Column("source_media_version_id", sa.String(36), nullable=False),
        sa.Column("target_type", sa.String(40), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("result_subject_type", sa.String(40)),
        sa.Column("result_subject_id", sa.String(36)),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["document_id"], ["visual_lab_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["visual_lab_nodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_media_version_id"], ["media_versions.id"], ondelete="RESTRICT"),
    )

    op.create_table(
        "runtime_environments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        *_audit_columns(),
    )
    op.create_table(
        "runtime_environment_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("runtime_environment_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("environment_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("validation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("published_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["runtime_environment_id"], ["runtime_environments.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("runtime_environment_id", "version_no", name="uq_runtime_environment_version"),
    )
    op.create_table(
        "workflow_app_contract_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_version_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("capability", sa.String(100), nullable=False),
        sa.Column("contract_json", sa.Text(), nullable=False),
        sa.Column("bindings_json", sa.Text(), nullable=False),
        sa.Column("semantic_phases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workflow_version_id", "version_no", name="uq_workflow_app_contract_version"),
    )
    op.create_table(
        "workflow_runtime_bindings",
        sa.Column("workflow_version_id", sa.String(36), primary_key=True),
        sa.Column("contract_version_id", sa.String(36), nullable=False),
        sa.Column("runtime_environment_version_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False, server_default="v1"),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contract_version_id"], ["workflow_app_contract_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_environment_version_id"], ["runtime_environment_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "runtime_instances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("runtime_environment_version_id", sa.String(36), nullable=False),
        sa.Column("instance_kind", sa.String(24), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("port", sa.Integer()),
        sa.Column("process_id", sa.Integer()),
        sa.Column("owned_attempt_id", sa.String(36)),
        sa.Column("observed_fingerprint", sa.String(64)),
        sa.Column("health_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_heartbeat_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["runtime_environment_version_id"], ["runtime_environment_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owned_attempt_id"], ["job_attempts.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "provider_execution_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_attempt_id", sa.String(36), nullable=False),
        sa.Column("provider_prompt_id", sa.String(200), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("semantic_phase", sa.String(80)),
        sa.Column("progress", sa.Float()),
        sa.Column("payload_redacted_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_attempt_id"], ["job_attempts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_attempt_id", "sequence_no", name="uq_provider_execution_event_sequence"),
    )
    op.create_index("ix_provider_execution_events_attempt_time", "provider_execution_events", ["job_attempt_id", "occurred_at"])
    op.create_index("ix_visual_lab_documents_project_updated", "visual_lab_documents", ["project_id", "updated_at"])
    op.create_index("ix_visual_lab_nodes_document_z", "visual_lab_nodes", ["document_id", "z_index"])
    op.create_index("ix_visual_lab_node_revisions_node_no", "visual_lab_node_revisions", ["node_id", "revision_no"])
    op.create_index("ix_visual_lab_edges_document", "visual_lab_edges", ["document_id"])
    op.create_index("ix_visual_lab_snapshots_document_no", "visual_lab_snapshots", ["document_id", "snapshot_no"])
    op.create_index("ix_runtime_environment_versions_environment_no", "runtime_environment_versions", ["runtime_environment_id", "version_no"])
    op.create_index("ix_runtime_instances_environment_created", "runtime_instances", ["runtime_environment_version_id", "created_at"])
    op.create_index("ix_workflow_contract_versions_workflow_no", "workflow_app_contract_versions", ["workflow_version_id", "version_no"])


def downgrade() -> None:
    raise RuntimeError("Visual Lab and runtime versions are production history; restore the pre-migration backup")

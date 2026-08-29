"""Add immutable long-form adaptation planning foundation.

Revision ID: 0070_adaptation_planning_foundation
Revises: 0069_job_history_deletion
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0070_adaptation_planning_foundation"
down_revision = "0069_job_history_deletion"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default=sa.text("'v1'")),
    )


def upgrade() -> None:
    op.create_table(
        "source_document_units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_document_version_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("unit_kind", sa.String(16), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("chapter_ordinal", sa.Integer()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["source_document_version_id"], ["source_document_versions.id"], ondelete="CASCADE"),
        sa.CheckConstraint("ordinal >= 1", name="ck_source_document_units_ordinal"),
        sa.CheckConstraint("source_start >= 0 AND source_end > source_start", name="ck_source_document_units_offsets"),
        sa.CheckConstraint("unit_kind IN ('HEADING','BODY')", name="ck_source_document_units_kind"),
        sa.UniqueConstraint("source_document_version_id", "ordinal", name="uq_source_document_units_ordinal"),
    )
    op.create_index("ix_source_document_units_version_kind", "source_document_units", ["source_document_version_id", "unit_kind"])

    op.create_table(
        "adaptation_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("source_document_version_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("artifact_status", sa.String(32), nullable=False),
        sa.Column("current_revision_id", sa.String(36)),
        sa.Column("archived_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_version_id"], ["source_document_versions.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "mode IN ('COMPLETE_WORK','SERIAL_INCREMENTAL','PRESEGMENTED_SCRIPT','SINGLE_EPISODE')",
            name="ck_adaptation_plans_mode",
        ),
        sa.CheckConstraint(
            "artifact_status IN ('DRAFT','IN_REVIEW','APPROVED','MATERIALIZED','SUPERSEDED','ARCHIVED')",
            name="ck_adaptation_plans_status",
        ),
    )
    op.create_index("ix_adaptation_plans_project_updated", "adaptation_plans", ["project_id", "updated_at"])

    op.create_table(
        "adaptation_plan_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(36)),
        sa.Column("source_scope_json", sa.Text(), nullable=False),
        sa.Column("constraints_json", sa.Text(), nullable=False),
        sa.Column("diagnosis_json", sa.Text(), nullable=False),
        sa.Column("analysis_contract_version", sa.String(64), nullable=False),
        sa.Column("prompt_schema_version", sa.String(64), nullable=False),
        sa.Column("profile_version_id", sa.String(36)),
        sa.Column("runtime_contract_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("validation_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_reason", sa.String(32), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["plan_id"], ["adaptation_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["adaptation_plan_revisions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("plan_id", "revision_no", name="uq_adaptation_plan_revisions_no"),
        sa.CheckConstraint("revision_no >= 1", name="ck_adaptation_plan_revisions_no"),
    )

    op.create_table(
        "adaptation_plan_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("plan_revision_id", sa.String(36), nullable=False),
        sa.Column("run_status", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("total_nodes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_nodes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_nodes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_input_tokens", sa.Integer()),
        sa.Column("actual_input_tokens", sa.Integer()),
        sa.Column("actual_output_tokens", sa.Integer()),
        sa.Column("estimated_cost_microunits", sa.Integer()),
        sa.Column("actual_cost_microunits", sa.Integer()),
        sa.Column("started_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["plan_id"], ["adaptation_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_revision_id"], ["adaptation_plan_revisions.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "run_status IN ('PREPARED','QUEUED','RUNNING','READY_FOR_REVIEW','FAILED','CANCELLED','NEEDS_ATTENTION')",
            name="ck_adaptation_plan_runs_status",
        ),
        sa.UniqueConstraint("plan_id", "request_fingerprint", name="uq_adaptation_plan_runs_request"),
    )
    op.create_index("ix_adaptation_plan_runs_plan_created", "adaptation_plan_runs", ["plan_id", "created_at"])

    op.create_table(
        "adaptation_plan_run_nodes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("node_key", sa.String(160), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("core_source_start", sa.Integer()),
        sa.Column("core_source_end", sa.Integer()),
        sa.Column("context_source_start", sa.Integer()),
        sa.Column("context_source_end", sa.Integer()),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(36)),
        sa.Column("output_sha256", sa.String(64)),
        sa.Column("output_json", sa.Text()),
        sa.Column("quality_flags_json", sa.Text(), nullable=False, server_default="[]"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["adaptation_plan_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "stage IN ('CHUNK_MAP','ARC_REDUCE','SEASON_PLAN','EPISODE_BOUNDARY','VALIDATE')",
            name="ck_adaptation_plan_run_nodes_stage",
        ),
        sa.UniqueConstraint("run_id", "node_key", "input_fingerprint", name="uq_adaptation_plan_run_nodes_key"),
    )

    op.create_table(
        "adaptation_story_arcs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_revision_id", sa.String(36), nullable=False),
        sa.Column("logical_arc_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("dramatic_promise", sa.Text(), nullable=False, server_default=""),
        sa.Column("beginning_state_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("ending_state_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("open_threads_json", sa.Text(), nullable=False, server_default="[]"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["plan_revision_id"], ["adaptation_plan_revisions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("plan_revision_id", "logical_arc_id", name="uq_adaptation_story_arcs_logical"),
        sa.UniqueConstraint("plan_revision_id", "ordinal", name="uq_adaptation_story_arcs_ordinal"),
    )

    op.create_table(
        "adaptation_season_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_revision_id", sa.String(36), nullable=False),
        sa.Column("logical_season_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("release_intent", sa.Text(), nullable=False, server_default=""),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["plan_revision_id"], ["adaptation_plan_revisions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("plan_revision_id", "logical_season_id", name="uq_adaptation_season_groups_logical"),
        sa.UniqueConstraint("plan_revision_id", "ordinal", name="uq_adaptation_season_groups_ordinal"),
    )

    op.create_table(
        "adaptation_episode_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_revision_id", sa.String(36), nullable=False),
        sa.Column("logical_episode_id", sa.String(36), nullable=False),
        sa.Column("display_ordinal", sa.Integer(), nullable=False),
        sa.Column("story_arc_id", sa.String(36)),
        sa.Column("season_group_id", sa.String(36)),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("logline", sa.Text(), nullable=False, server_default=""),
        sa.Column("opening_carry", sa.Text(), nullable=False, server_default=""),
        sa.Column("core_conflict", sa.Text(), nullable=False, server_default=""),
        sa.Column("payoff", sa.Text(), nullable=False, server_default=""),
        sa.Column("ending_hook", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_duration_ms", sa.Integer(), nullable=False),
        sa.Column("estimated_duration_ms", sa.Integer()),
        sa.Column("estimated_dialogue_chars", sa.Integer()),
        sa.Column("estimated_narration_chars", sa.Integer()),
        sa.Column("estimated_shot_count", sa.Integer()),
        sa.Column("canon_delta_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("confidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("review_state", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("lock_state", sa.String(32), nullable=False, server_default="UNLOCKED"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["plan_revision_id"], ["adaptation_plan_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_arc_id"], ["adaptation_story_arcs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["season_group_id"], ["adaptation_season_groups.id"], ondelete="SET NULL"),
        sa.CheckConstraint("display_ordinal >= 1", name="ck_adaptation_episode_items_ordinal"),
        sa.CheckConstraint("target_duration_ms > 0", name="ck_adaptation_episode_items_duration"),
        sa.UniqueConstraint("plan_revision_id", "logical_episode_id", name="uq_adaptation_episode_items_logical"),
        sa.UniqueConstraint("plan_revision_id", "display_ordinal", name="uq_adaptation_episode_items_ordinal"),
    )

    op.create_table(
        "adaptation_episode_source_spans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("plan_episode_id", sa.String(36), nullable=False),
        sa.Column("source_document_version_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("span_role", sa.String(24), nullable=False),
        sa.Column("paragraph_start", sa.Integer()),
        sa.Column("paragraph_end", sa.Integer()),
        sa.Column("unicode_start", sa.Integer(), nullable=False),
        sa.Column("unicode_end", sa.Integer(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["plan_episode_id"], ["adaptation_episode_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_version_id"], ["source_document_versions.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("span_role IN ('PRIMARY','SUPPORTING','BRIDGE')", name="ck_adaptation_episode_source_spans_role"),
        sa.CheckConstraint("unicode_start >= 0 AND unicode_end > unicode_start", name="ck_adaptation_episode_source_spans_offsets"),
        sa.UniqueConstraint("plan_episode_id", "ordinal", name="uq_adaptation_episode_source_spans_ordinal"),
    )

    op.create_table(
        "llm_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_attempt_id", sa.String(36)),
        sa.Column("run_node_id", sa.String(36)),
        sa.Column("profile_version_id", sa.String(36)),
        sa.Column("provider", sa.String(120)),
        sa.Column("model", sa.String(200)),
        sa.Column("prompt_contract_version", sa.String(64), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("finish_reason", sa.String(120)),
        sa.Column("provider_request_id", sa.String(300)),
        sa.Column("estimated_cost_microunits", sa.Integer()),
        sa.Column("actual_cost_microunits", sa.Integer()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("uncertain_side_effect", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["job_attempt_id"], ["job_attempts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_node_id"], ["adaptation_plan_run_nodes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["profile_version_id"], ["execution_profile_versions.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('PENDING','SUCCEEDED','FAILED','UNKNOWN')", name="ck_llm_invocations_status"),
    )
    op.create_index("ix_llm_invocations_run_node", "llm_invocations", ["run_node_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_invocations_run_node", table_name="llm_invocations")
    op.drop_table("llm_invocations")
    op.drop_table("adaptation_episode_source_spans")
    op.drop_table("adaptation_episode_items")
    op.drop_table("adaptation_season_groups")
    op.drop_table("adaptation_story_arcs")
    op.drop_table("adaptation_plan_run_nodes")
    op.drop_index("ix_adaptation_plan_runs_plan_created", table_name="adaptation_plan_runs")
    op.drop_table("adaptation_plan_runs")
    op.drop_table("adaptation_plan_revisions")
    op.drop_index("ix_adaptation_plans_project_updated", table_name="adaptation_plans")
    op.drop_table("adaptation_plans")
    op.drop_index("ix_source_document_units_version_kind", table_name="source_document_units")
    op.drop_table("source_document_units")

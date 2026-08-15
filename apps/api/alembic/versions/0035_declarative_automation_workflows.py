"""Persist bounded declarative automation workflows and HITL run state.

The workflow tables intentionally model a small, auditable state machine rather
than storing executable code.  Definitions contain data-only conditions and a
finite batch; runs persist every counter and human gate so a restart cannot
silently resume past an approval boundary.
"""

import sqlalchemy as sa

from alembic import op


revision = "0035_declarative_automation_workflows"
down_revision = "0034_local_automation_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
        sa.UniqueConstraint("project_id", "code", name="uq_automation_workflows_project_code"),
    )
    op.create_index("ix_automation_workflows_project_status", "automation_workflows", ["project_id", "status"])
    op.create_table(
        "automation_workflow_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("automation_workflows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("iteration_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("task_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disk_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_iterations", sa.Integer(), nullable=False),
        sa.Column("max_tasks", sa.Integer(), nullable=False),
        sa.Column("max_disk_bytes", sa.Integer(), nullable=False),
        sa.Column("pending_gate_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("machine_context_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("ai_scores_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("human_approval_status", sa.String(24), nullable=False, server_default="NOT_REQUIRED"),
        sa.Column("started_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v1"),
    )
    op.create_index("ix_automation_workflow_runs_project_status", "automation_workflow_runs", ["project_id", "status"])
    op.create_index("ix_automation_workflow_runs_workflow_status", "automation_workflow_runs", ["workflow_id", "status"])
    op.create_table(
        "automation_workflow_run_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(200), nullable=False),
        sa.Column("item_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("produced_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("machine_context_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("review_status", sa.String(24), nullable=False, server_default="NOT_REVIEWED"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_automation_workflow_run_task_ordinal"),
    )
    op.create_index("ix_automation_workflow_run_tasks_run_status", "automation_workflow_run_tasks", ["run_id", "status"])
    op.create_table(
        "automation_workflow_run_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("automation_workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="local-user"),
    )
    op.create_index("ix_automation_workflow_run_events_run_created", "automation_workflow_run_events", ["run_id", "created_at"])


def downgrade() -> None:
    raise RuntimeError("Declarative automation evidence is release history; restore migration preflight backup")

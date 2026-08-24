"""Link declarative automation tasks to the durable local job queue.

Automation run state remains the source of truth for the bounded/HITL state
machine.  The nullable link lets old run history remain readable while every
new task gets a durable Job row (and, when claimed, the normal JobAttempt
lineage) in the same transaction as the task and its audit event.
"""

import sqlalchemy as sa

from alembic import op

revision = "0039_automation_task_jobs"
down_revision = "0038_outbox_delivery_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automation_workflow_run_tasks",
        # SQLite cannot ALTER a table with a new FK constraint without a
        # batch copy. The durable ID is still checked at write time by
        # JobService and the index keeps the read path bounded.
        sa.Column("job_id", sa.String(36), nullable=True),
    )
    op.create_index("ix_automation_workflow_run_tasks_job", "automation_workflow_run_tasks", ["job_id"])


def downgrade() -> None:
    raise RuntimeError("Automation task Job lineage is release history; restore migration preflight backup")

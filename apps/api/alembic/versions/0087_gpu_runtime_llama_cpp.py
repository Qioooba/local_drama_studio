"""Allow the LLAMA_CPP runtime kind in single-GPU lease tables.

Revision ID: 0087_gpu_runtime_llama_cpp
Revises: 0086_model_platform_quick_create_v2_runs
"""

from __future__ import annotations

from alembic import op

revision = "0087_gpu_runtime_llama_cpp"
down_revision = "0086_model_platform_quick_create_v2_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gpu_runtime_leases", recreate="always") as batch:
        batch.drop_constraint("ck_gpu_runtime_lease_kind", type_="check")
        batch.create_check_constraint(
            "ck_gpu_runtime_lease_kind",
            "runtime_kind IN ('COMFY','OLLAMA','PYTORCH','LLAMA_CPP')",
        )
    with op.batch_alter_table("gpu_runtime_state", recreate="always") as batch:
        batch.drop_constraint("ck_gpu_runtime_state_kind", type_="check")
        batch.create_check_constraint(
            "ck_gpu_runtime_state_kind",
            "resident_runtime IS NULL OR resident_runtime IN ('COMFY','OLLAMA','PYTORCH','LLAMA_CPP')",
        )


def downgrade() -> None:
    with op.batch_alter_table("gpu_runtime_leases", recreate="always") as batch:
        batch.drop_constraint("ck_gpu_runtime_lease_kind", type_="check")
        batch.create_check_constraint(
            "ck_gpu_runtime_lease_kind",
            "runtime_kind IN ('COMFY','OLLAMA','PYTORCH')",
        )
    with op.batch_alter_table("gpu_runtime_state", recreate="always") as batch:
        batch.drop_constraint("ck_gpu_runtime_state_kind", type_="check")
        batch.create_check_constraint(
            "ck_gpu_runtime_state_kind",
            "resident_runtime IS NULL OR resident_runtime IN ('COMFY','OLLAMA','PYTORCH')",
        )

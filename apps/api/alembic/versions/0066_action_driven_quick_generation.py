"""Make quick generation action-driven and allow image-only results.

Revision ID: 0066_action_driven_quick_generation
Revises: 0065_quick_generation_parameters
"""

from __future__ import annotations

from alembic import op

revision = "0066_action_driven_quick_generation"
down_revision = "0065_quick_generation_parameters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.commit()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    bind.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    op.rename_table("quick_generation_runs", "quick_generation_runs_before_actions")
    op.execute(
        """
        CREATE TABLE quick_generation_runs (
          id VARCHAR(64) NOT NULL PRIMARY KEY,
          idempotency_key VARCHAR(200) NOT NULL UNIQUE,
          mode VARCHAR(32) NOT NULL,
          state VARCHAR(32) NOT NULL,
          stage VARCHAR(32) NOT NULL,
          story_json TEXT NOT NULL,
          story_sha256 VARCHAR(64) NOT NULL,
          language VARCHAR(32) NOT NULL,
          llm_profile_version_id VARCHAR(64) NOT NULL,
          image_profile_version_id VARCHAR(64),
          video_profile_version_id VARCHAR(64),
          image_candidate_count INTEGER NOT NULL DEFAULT 4,
          remote_outbound_confirmed BOOLEAN NOT NULL DEFAULT 0,
          plan_json TEXT NOT NULL DEFAULT '{}',
          plan_hash VARCHAR(64),
          execution_fingerprint VARCHAR(128),
          selected_candidate_id VARCHAR(64),
          selected_image_output_id VARCHAR(64),
          job_id VARCHAR(64),
          output_id VARCHAR(64),
          seed BIGINT,
          retry_count INTEGER NOT NULL DEFAULT 0,
          error_json TEXT NOT NULL DEFAULT '{}',
          model_parameters_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          confirmed_at TEXT,
          completed_at TEXT,
          created_by TEXT NOT NULL DEFAULT 'local-user',
          revision INTEGER NOT NULL DEFAULT 1,
          schema_version TEXT NOT NULL DEFAULT 'v2',
          FOREIGN KEY(job_id) REFERENCES jobs(id),
          CHECK(mode IN ('TEXT_TO_IMAGE','TEXT_TO_VIDEO','TEXT_TO_IMAGE_TO_VIDEO')),
          CHECK(image_candidate_count BETWEEN 1 AND 8),
          CHECK(
            (mode='TEXT_TO_IMAGE' AND image_profile_version_id IS NOT NULL AND video_profile_version_id IS NULL)
            OR (mode='TEXT_TO_VIDEO' AND image_profile_version_id IS NULL AND video_profile_version_id IS NOT NULL)
            OR (mode='TEXT_TO_IMAGE_TO_VIDEO' AND image_profile_version_id IS NOT NULL AND video_profile_version_id IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        """
        INSERT INTO quick_generation_runs (
          id,idempotency_key,mode,state,stage,story_json,story_sha256,language,
          llm_profile_version_id,image_profile_version_id,video_profile_version_id,
          image_candidate_count,remote_outbound_confirmed,plan_json,plan_hash,
          execution_fingerprint,selected_candidate_id,selected_image_output_id,
          job_id,output_id,seed,retry_count,error_json,model_parameters_json,
          created_at,updated_at,confirmed_at,completed_at,created_by,revision,schema_version
        )
        SELECT id,idempotency_key,
          CASE mode WHEN 'DIRECT_T2V' THEN 'TEXT_TO_VIDEO' ELSE 'TEXT_TO_IMAGE_TO_VIDEO' END,
          state,stage,story_json,story_sha256,language,llm_profile_version_id,
          image_profile_version_id,video_profile_version_id,image_candidate_count,
          remote_outbound_confirmed,plan_json,plan_hash,execution_fingerprint,
          selected_candidate_id,selected_image_output_id,job_id,output_id,seed,
          retry_count,error_json,model_parameters_json,created_at,updated_at,
          confirmed_at,completed_at,created_by,revision,schema_version
        FROM quick_generation_runs_before_actions
        """
    )
    op.execute(
        """
        UPDATE quick_generation_runs
        SET plan_json=json_set(plan_json, '$.mode', mode, '$.result_kind', 'VIDEO')
        WHERE json_valid(plan_json) AND json_type(plan_json, '$.video_plan')='object'
        """
    )
    op.drop_table("quick_generation_runs_before_actions")
    op.create_index("ix_quick_generation_runs_updated", "quick_generation_runs", ["updated_at"])
    op.create_index("ix_quick_generation_runs_job", "quick_generation_runs", ["job_id"])
    bind.commit()
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(f"0066 action migration produced foreign key violations: {violations[:10]}")
    bind.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    raise RuntimeError("0066 changes persisted route semantics; restore the pre-upgrade backup instead")

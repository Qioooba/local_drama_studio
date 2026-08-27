"""Add canonical Job subject, production scope and stage identity.

Revision ID: 0062_canonical_job_scope_stage
Revises: 0061_shot_working_media_slots
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0062_canonical_job_scope_stage"
down_revision = "0061_shot_working_media_slots"
branch_labels = None
depends_on = None


STAGES = (
    ("STORY_ANALYSIS", "故事解析", "STORY"),
    ("ASSET_EXTRACTION", "资产提取", "ASSET"),
    ("ASSET_COMPLETION", "资产补全", "ASSET"),
    ("SHOT_PLANNING", "分集与分镜规划", "PLANNING"),
    ("SHOT_IMAGE", "镜头画面", "SHOT"),
    ("VIDEO", "视频", "SHOT"),
    ("AUDIO_SUBTITLE", "声音与字幕", "POST"),
    ("COMPOSE_QC", "合成与质检", "POST"),
    ("MEDIA_MAINTENANCE", "媒体维护", "SYSTEM"),
    ("MODEL_DIAGNOSTIC", "模型诊断", "SYSTEM"),
    ("VISUAL_LAB", "Visual Lab", "LAB"),
    ("AUTOMATION", "自动化", "SYSTEM"),
    ("LEGACY_UNCLASSIFIED", "待迁移旧任务", "LEGACY"),
)


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite cannot rebuild a parent table while child rows reference it with
    # foreign_keys=ON.  This migration is already run under the maintenance
    # lock with a verified backup and rehearsal copy, so perform one explicit
    # offline table switch and validate every FK before re-enabling writes.
    bind.commit()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    op.create_table(
        "job_stage_definitions",
        sa.Column("code", sa.String(40), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("length(trim(code)) > 0", name="ck_job_stage_definition_code"),
    )
    stage_table = sa.table(
        "job_stage_definitions",
        sa.column("code", sa.String),
        sa.column("title", sa.String),
        sa.column("domain", sa.String),
        sa.column("active", sa.Boolean),
    )
    op.bulk_insert(
        stage_table,
        [{"code": code, "title": title, "domain": domain, "active": True} for code, title, domain in STAGES],
    )
    op.execute(
        """
        CREATE TABLE jobs_canonical_new (
          id VARCHAR(36) NOT NULL PRIMARY KEY,
          type VARCHAR(80) NOT NULL,
          project_id VARCHAR(36) NOT NULL,
          subject_type VARCHAR(40) NOT NULL,
          subject_id VARCHAR(36) NOT NULL,
          subject_kind VARCHAR(40) NOT NULL,
          scope_project_id VARCHAR(36) NOT NULL,
          scope_episode_id VARCHAR(36),
          scope_shot_id VARCHAR(36),
          stage_code VARCHAR(40) NOT NULL,
          state VARCHAR(32) NOT NULL,
          channel VARCHAR(40) NOT NULL,
          idempotency_key VARCHAR(200),
          input_snapshot_json TEXT NOT NULL,
          execution_profile_version_id VARCHAR(36),
          created_at TEXT DEFAULT CURRENT_TIMESTAMP NOT NULL,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP NOT NULL,
          created_by TEXT DEFAULT 'system' NOT NULL,
          revision INTEGER DEFAULT 1 NOT NULL,
          schema_version TEXT DEFAULT 'v2' NOT NULL,
          priority INTEGER DEFAULT 100 NOT NULL,
          max_attempts INTEGER DEFAULT 3 NOT NULL,
          next_run_at TEXT,
          cancel_requested_at TEXT,
          last_error_code VARCHAR(80),
          progress_json TEXT DEFAULT '{}' NOT NULL,
          progress_updated_at TEXT,
          started_at TEXT,
          finished_at TEXT,
          last_error_detail_redacted TEXT,
          CONSTRAINT ck_jobs_subject_kind_nonempty CHECK(length(trim(subject_kind)) > 0),
          CONSTRAINT fk_jobs_project FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
          CONSTRAINT fk_jobs_execution_profile FOREIGN KEY(execution_profile_version_id)
            REFERENCES execution_profile_versions(id) ON DELETE RESTRICT,
          CONSTRAINT fk_jobs_scope_project FOREIGN KEY(scope_project_id) REFERENCES projects(id) ON DELETE CASCADE,
          CONSTRAINT fk_jobs_scope_episode FOREIGN KEY(scope_episode_id) REFERENCES episodes(id) ON DELETE SET NULL,
          CONSTRAINT fk_jobs_scope_shot FOREIGN KEY(scope_shot_id) REFERENCES shots(id) ON DELETE SET NULL,
          CONSTRAINT fk_jobs_stage_code FOREIGN KEY(stage_code) REFERENCES job_stage_definitions(code) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        INSERT INTO jobs_canonical_new (
          id,type,project_id,subject_type,subject_id,subject_kind,
          scope_project_id,scope_episode_id,scope_shot_id,stage_code,
          state,channel,idempotency_key,input_snapshot_json,execution_profile_version_id,
          created_at,updated_at,created_by,revision,schema_version,priority,max_attempts,
          next_run_at,cancel_requested_at,last_error_code,progress_json,progress_updated_at,
          started_at,finished_at,last_error_detail_redacted
        )
        SELECT
          j.id,j.type,j.project_id,j.subject_type,j.subject_id,j.subject_type,
          j.project_id,
          (SELECT s.episode_id FROM shots s WHERE s.id=(
            CASE
              WHEN j.subject_type='SHOT' THEN j.subject_id
              WHEN j.subject_type='GENERATION_VARIANT' THEN (
                SELECT gi.owner_id FROM generation_variants gv
                JOIN generation_intents gi ON gi.id=gv.intent_id
                WHERE gv.id=j.subject_id AND gi.owner_type='SHOT'
              )
              ELSE NULL
            END
          )),
          CASE
            WHEN j.subject_type='SHOT' AND EXISTS(SELECT 1 FROM shots s WHERE s.id=j.subject_id)
              THEN j.subject_id
            WHEN j.subject_type='GENERATION_VARIANT' THEN (
              SELECT gi.owner_id FROM generation_variants gv
              JOIN generation_intents gi ON gi.id=gv.intent_id
              WHERE gv.id=j.subject_id AND gi.owner_type='SHOT'
            )
            ELSE NULL
          END,
          CASE
            WHEN j.type='GENERATION_VARIANT' THEN COALESCE((
              SELECT CASE
                WHEN upper(gi.purpose) LIKE '%VIDEO%' OR upper(gi.purpose) LIKE '%I2V%'
                     OR upper(gi.purpose) LIKE '%T2V%' THEN 'VIDEO'
                ELSE 'SHOT_IMAGE'
              END
              FROM generation_variants gv JOIN generation_intents gi ON gi.id=gv.intent_id
              WHERE gv.id=j.subject_id
            ),'LEGACY_UNCLASSIFIED')
            WHEN upper(j.type) LIKE '%TTS%' OR upper(j.type) LIKE '%AUDIO%'
                 OR upper(j.type) LIKE '%SUBTITLE%' THEN 'AUDIO_SUBTITLE'
            WHEN upper(j.type) LIKE '%COMPOSE%' OR upper(j.type) LIKE '%RENDER%'
                 OR upper(j.type) LIKE '%QC%' OR upper(j.type) LIKE '%DELIVERY%'
                 OR upper(j.type) LIKE '%TIMELINE%' THEN 'COMPOSE_QC'
            WHEN upper(j.type) LIKE '%VIDEO%' THEN 'VIDEO'
            WHEN upper(j.type) LIKE '%IMAGE%' OR upper(j.type) LIKE '%KEYFRAME%'
                 OR upper(j.type) LIKE '%MULTIVIEW%' THEN 'SHOT_IMAGE'
            WHEN upper(j.type) LIKE '%BREAKDOWN%' OR upper(j.type) LIKE '%STORY%'
                 OR upper(j.type) LIKE '%LLM%' THEN 'STORY_ANALYSIS'
            WHEN upper(j.type) LIKE '%MEDIA%' OR upper(j.type) LIKE '%THUMBNAIL%'
                 OR upper(j.type) LIKE '%PROBE%' THEN 'MEDIA_MAINTENANCE'
            WHEN upper(j.type) LIKE '%LAB%' THEN 'VISUAL_LAB'
            WHEN upper(j.type) LIKE '%AUTOMATION%' OR upper(j.type) LIKE '%WORKFLOW%' THEN 'AUTOMATION'
            ELSE 'LEGACY_UNCLASSIFIED'
          END,
          j.state,j.channel,j.idempotency_key,j.input_snapshot_json,j.execution_profile_version_id,
          j.created_at,j.updated_at,j.created_by,j.revision,j.schema_version,j.priority,j.max_attempts,
          j.next_run_at,j.cancel_requested_at,j.last_error_code,j.progress_json,j.progress_updated_at,
          j.started_at,j.finished_at,j.last_error_detail_redacted
        FROM jobs j
        """
    )
    op.drop_index("ix_jobs_project_id_state_channel", table_name="jobs")
    op.drop_index("ix_jobs_queue_eligible", table_name="jobs")
    op.drop_index("ix_jobs_subject_state", table_name="jobs")
    op.drop_table("jobs")
    op.rename_table("jobs_canonical_new", "jobs")
    op.create_index("ix_jobs_project_id_state_channel", "jobs", ["project_id", "state", "channel"])
    op.create_index("ix_jobs_queue_eligible", "jobs", ["state", "channel", "priority", "next_run_at"])
    op.create_index("ix_jobs_subject_state", "jobs", ["subject_id", "state"])
    op.create_index(
        "ix_jobs_canonical_scope_state",
        "jobs",
        ["scope_project_id", "scope_episode_id", "scope_shot_id", "state"],
    )
    op.create_index("ix_jobs_stage_state", "jobs", ["stage_code", "state"])
    bind.commit()
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(f"0062 canonical Job table switch produced foreign key violations: {violations[:10]}")
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    raise RuntimeError("0062 is an irreversible canonical Job identity cutover; restore the pre-upgrade backup instead")

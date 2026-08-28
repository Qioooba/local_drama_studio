"""Separate quick generations from project production.

Revision ID: 0064_quick_generation_domain
Revises: 0063_audio_mix_drafts
"""

from __future__ import annotations

from alembic import op

revision = "0064_quick_generation_domain"
down_revision = "0063_audio_mix_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.commit()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")

    # A queue item may belong to a production project or to a global product
    # surface such as Quick Create. Project scope is therefore optional and
    # scope_kind carries the canonical ownership boundary.
    op.execute(
        """
        CREATE TABLE jobs_quick_scope_new (
          id VARCHAR(36) NOT NULL PRIMARY KEY,
          type VARCHAR(80) NOT NULL,
          project_id VARCHAR(36),
          subject_type VARCHAR(40) NOT NULL,
          subject_id VARCHAR(64) NOT NULL,
          subject_kind VARCHAR(40) NOT NULL,
          scope_kind VARCHAR(40) NOT NULL DEFAULT 'PROJECT',
          scope_project_id VARCHAR(36),
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
          CONSTRAINT ck_jobs_scope_kind CHECK(scope_kind IN ('PROJECT','QUICK_GENERATION','SYSTEM')),
          CONSTRAINT ck_jobs_project_scope CHECK(
            (scope_kind='PROJECT' AND project_id IS NOT NULL AND scope_project_id=project_id)
            OR (scope_kind<>'PROJECT' AND project_id IS NULL AND scope_project_id IS NULL)
          ),
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
        INSERT INTO jobs_quick_scope_new (
          id,type,project_id,subject_type,subject_id,subject_kind,scope_kind,
          scope_project_id,scope_episode_id,scope_shot_id,stage_code,state,channel,
          idempotency_key,input_snapshot_json,execution_profile_version_id,created_at,
          updated_at,created_by,revision,schema_version,priority,max_attempts,next_run_at,
          cancel_requested_at,last_error_code,progress_json,progress_updated_at,started_at,
          finished_at,last_error_detail_redacted
        )
        SELECT id,type,project_id,subject_type,subject_id,subject_kind,'PROJECT',
          scope_project_id,scope_episode_id,scope_shot_id,stage_code,state,channel,
          idempotency_key,input_snapshot_json,execution_profile_version_id,created_at,
          updated_at,created_by,revision,schema_version,priority,max_attempts,next_run_at,
          cancel_requested_at,last_error_code,progress_json,progress_updated_at,started_at,
          finished_at,last_error_detail_redacted
        FROM jobs
        """
    )
    op.drop_index("ix_jobs_project_id_state_channel", table_name="jobs")
    op.drop_index("ix_jobs_queue_eligible", table_name="jobs")
    op.drop_index("ix_jobs_subject_state", table_name="jobs")
    op.drop_index("ix_jobs_canonical_scope_state", table_name="jobs")
    op.drop_index("ix_jobs_stage_state", table_name="jobs")
    op.drop_table("jobs")
    op.rename_table("jobs_quick_scope_new", "jobs")
    op.create_index("ix_jobs_project_id_state_channel", "jobs", ["project_id", "state", "channel"])
    op.create_index("ix_jobs_queue_eligible", "jobs", ["state", "channel", "priority", "next_run_at"])
    op.create_index("ix_jobs_subject_state", "jobs", ["subject_id", "state"])
    op.create_index("ix_jobs_canonical_scope_state", "jobs", ["scope_project_id", "scope_episode_id", "scope_shot_id", "state"])
    op.create_index("ix_jobs_scope_kind_state", "jobs", ["scope_kind", "subject_id", "state"])
    op.create_index("ix_jobs_stage_state", "jobs", ["stage_code", "state"])
    op.execute("INSERT INTO job_stage_definitions(code,title,domain,active) VALUES ('QUICK_GENERATION','快速生成','QUICK_GENERATION',1)")

    # Replace the original project-backed aggregate with a standalone domain.
    op.rename_table("one_sentence_video_run_events", "one_sentence_video_run_events_legacy")
    op.rename_table("one_sentence_video_candidates", "one_sentence_video_candidates_legacy")
    op.rename_table("one_sentence_video_runs", "one_sentence_video_runs_legacy")

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
          video_profile_version_id VARCHAR(64) NOT NULL,
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
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          confirmed_at TEXT,
          completed_at TEXT,
          created_by TEXT NOT NULL DEFAULT 'local-user',
          revision INTEGER NOT NULL DEFAULT 1,
          schema_version TEXT NOT NULL DEFAULT 'v2',
          FOREIGN KEY(job_id) REFERENCES jobs(id),
          CHECK(mode IN ('DIRECT_T2V','KEYFRAME_I2V')),
          CHECK(image_candidate_count BETWEEN 1 AND 8)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE quick_generation_candidates (
          id VARCHAR(64) NOT NULL PRIMARY KEY,
          run_id VARCHAR(64) NOT NULL,
          batch_no INTEGER NOT NULL,
          ordinal INTEGER NOT NULL,
          state VARCHAR(32) NOT NULL,
          seed BIGINT NOT NULL,
          job_id VARCHAR(64),
          output_id VARCHAR(64),
          parent_candidate_id VARCHAR(64),
          error_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(run_id) REFERENCES quick_generation_runs(id) ON DELETE CASCADE,
          FOREIGN KEY(job_id) REFERENCES jobs(id),
          FOREIGN KEY(parent_candidate_id) REFERENCES quick_generation_candidates(id),
          UNIQUE(run_id,batch_no,ordinal)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE quick_generation_outputs (
          id VARCHAR(64) NOT NULL PRIMARY KEY,
          run_id VARCHAR(64) NOT NULL,
          candidate_id VARCHAR(64),
          media_kind VARCHAR(16) NOT NULL,
          source_artifact_id VARCHAR(64),
          legacy_media_version_id VARCHAR(64),
          rel_path TEXT,
          mime_type VARCHAR(128) NOT NULL,
          byte_size INTEGER NOT NULL,
          sha256 VARCHAR(64) NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES quick_generation_runs(id) ON DELETE CASCADE,
          FOREIGN KEY(candidate_id) REFERENCES quick_generation_candidates(id) ON DELETE CASCADE,
          FOREIGN KEY(source_artifact_id) REFERENCES artifacts(id),
          FOREIGN KEY(legacy_media_version_id) REFERENCES media_versions(id),
          CHECK(media_kind IN ('IMAGE','VIDEO')),
          CHECK((source_artifact_id IS NOT NULL AND rel_path IS NOT NULL) OR legacy_media_version_id IS NOT NULL)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE quick_generation_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id VARCHAR(64) NOT NULL,
          stage VARCHAR(32) NOT NULL,
          state VARCHAR(32) NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES quick_generation_runs(id) ON DELETE CASCADE
        )
        """
    )

    # Preserve existing runs as legacy outputs while removing their project
    # identity from the new aggregate.
    op.execute(
        """
        INSERT INTO quick_generation_runs (
          id,idempotency_key,mode,state,stage,story_json,story_sha256,language,
          llm_profile_version_id,image_profile_version_id,video_profile_version_id,
          image_candidate_count,remote_outbound_confirmed,plan_json,plan_hash,
          execution_fingerprint,selected_candidate_id,selected_image_output_id,
          job_id,output_id,seed,retry_count,error_json,created_at,updated_at,
          confirmed_at,completed_at,created_by,revision,schema_version
        )
        SELECT id,idempotency_key,mode,state,stage,story_json,story_sha256,language,
          llm_profile_version_id,image_profile_version_id,video_profile_version_id,
          image_candidate_count,remote_outbound_confirmed,plan_json,plan_hash,
          execution_fingerprint,selected_candidate_id,selected_image_media_version_id,
          job_id,media_version_id,seed,retry_count,error_json,created_at,updated_at,
          confirmed_at,completed_at,created_by,revision,'v2'
        FROM one_sentence_video_runs_legacy
        """
    )
    op.execute(
        """
        INSERT INTO quick_generation_candidates (
          id,run_id,batch_no,ordinal,state,seed,job_id,output_id,parent_candidate_id,
          error_json,created_at,updated_at,revision
        )
        SELECT id,run_id,batch_no,ordinal,state,seed,job_id,media_version_id,
          parent_candidate_id,error_json,created_at,updated_at,revision
        FROM one_sentence_video_candidates_legacy
        """
    )
    op.execute(
        """
        INSERT OR IGNORE INTO quick_generation_outputs (
          id,run_id,candidate_id,media_kind,legacy_media_version_id,mime_type,byte_size,sha256,created_at
        )
        SELECT mv.id,r.id,NULL,ma.media_kind,mv.id,mv.mime_type,mv.byte_size,mv.sha256,r.updated_at
        FROM one_sentence_video_runs_legacy r
        JOIN media_versions mv ON mv.id=r.media_version_id
        JOIN media_assets ma ON ma.id=mv.media_asset_id
        UNION ALL
        SELECT mv.id,c.run_id,c.id,ma.media_kind,mv.id,mv.mime_type,mv.byte_size,mv.sha256,c.updated_at
        FROM one_sentence_video_candidates_legacy c
        JOIN media_versions mv ON mv.id=c.media_version_id
        JOIN media_assets ma ON ma.id=mv.media_asset_id
        """
    )
    op.execute(
        """
        INSERT INTO quick_generation_events(id,run_id,stage,state,metadata_json,created_at)
        SELECT id,run_id,stage,state,metadata_json,created_at FROM one_sentence_video_run_events_legacy
        """
    )

    op.create_index("ix_quick_generation_runs_updated", "quick_generation_runs", ["updated_at"])
    op.create_index("ix_quick_generation_runs_job", "quick_generation_runs", ["job_id"])
    op.create_index("ix_quick_generation_candidates_run", "quick_generation_candidates", ["run_id", "batch_no", "ordinal"])
    op.create_index("ix_quick_generation_candidates_job", "quick_generation_candidates", ["job_id"])
    op.create_index("ix_quick_generation_outputs_run", "quick_generation_outputs", ["run_id", "created_at"])
    op.create_index("ix_quick_generation_events_run", "quick_generation_events", ["run_id", "id"])

    op.drop_table("one_sentence_video_run_events_legacy")
    op.drop_table("one_sentence_video_candidates_legacy")
    op.drop_table("one_sentence_video_runs_legacy")

    bind.commit()
    violations = list(bind.exec_driver_sql("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(f"0064 quick-generation migration produced foreign key violations: {violations[:10]}")
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    raise RuntimeError("0064 is an irreversible domain cutover; restore the pre-upgrade backup instead")

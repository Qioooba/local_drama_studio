"""Explainer factory foundation: EXPLAINER product kind, domain tables and stages.

Implements W01 of the Explainer Factory design (《解说工厂完整设计与开发规范》§13.1
and 《源码接入与开发任务清单》§3/§4/§8).

Scope of this migration:

* ``projects.product_kind`` distinguishes ``DRAMA`` (every pre-existing project)
  from ``EXPLAINER``.  The default and the backfill are ``DRAMA`` so no existing
  project changes meaning.
* New ``explainer_*`` / ``narration_*`` / ``composition_*`` /
  ``publication_*`` / ``schedule_*`` tables.  Names deliberately avoid the
  existing ``subtitle_revisions``, ``audio_bindings``, ``delivery_packages``
  and ``episodes`` tables so legacy drama semantics keep their own truth.
* New ``job_stage_definitions`` rows for the explainer job families.  Every new
  job must reference a real active stage (``jobs.stage_code`` FK).

Design constraints honoured here:

* Execution state authority stays with the existing ``jobs`` /
  ``job_attempts`` / ``automation_workflow_runs`` tables.  ``explainer_runs``
  only stores business scope, frozen plan/version snapshots and the
  ``automation_workflow_run_id`` projection.
* ``narration_*`` never reuses ``dialogue_lines``: the legacy TTS handler is
  bound to dialogue revisions and a ``LOCAL_TEST_ONLY`` snapshot gate.
* ``composition_revisions`` holds an immutable manifest and
  ``composition_items`` are real rows with an ``edition_id`` foreign key, so a
  render never reads "the latest candidate".
* The schedule de-duplication key is ``(schedule_id, scheduled_for)`` only.
  ``config_revision`` is a frozen snapshot column and is deliberately *not*
  part of the unique constraint.

The migration is additive.  Known limitation: SQLite cannot add a CHECK
constraint to the existing ``projects`` table without an offline parent-table
rebuild, so the ``product_kind`` domain is enforced by the service layer and by
``ck_explainer_*`` constraints on the new tables.  ``downgrade`` is
intentionally unsupported, matching the repository rollback policy
(``restore_verified_pre_migration_backup_with_matching_code``).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0102_explainer_factory_foundation"
down_revision = "0101_versioned_source_parsing"
branch_labels = None
depends_on = None


PRODUCT_KIND_DRAMA = "DRAMA"

EXPLAINER_JOB_STAGES = (
    ("RESEARCH_ACQUIRE", "资料获取", "EXPLAINER"),
    ("FACT_EXTRACT", "事实提取与冲突核验", "EXPLAINER"),
    ("NARRATION_WRITE", "解说稿编写与翻译", "EXPLAINER"),
    ("NARRATION_TTS", "旁白合成", "EXPLAINER"),
    ("NARRATION_ALIGN", "旁白强制对齐与复核", "EXPLAINER"),
    ("EXPLAINER_STORYBOARD", "解说分镜与画面", "EXPLAINER"),
    ("EXPLAINER_VISUAL_QC", "解说视觉与内容质检", "EXPLAINER"),
    ("COMPOSITION_RENDER", "解说合成渲染", "EXPLAINER"),
    ("COMPOSITION_QC", "解说成片质检", "EXPLAINER"),
    ("EXPLAINER_POLICY_EVALUATE", "解说政策判定", "EXPLAINER"),
    ("EXPLAINER_EXPORT", "解说交付与导出", "EXPLAINER"),
)


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="system"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="v2"),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    # ---------------------------------------------------------------- product kind
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("product_kind", sa.String(24), nullable=False, server_default=PRODUCT_KIND_DRAMA)
        )
    op.create_index("ix_projects_product_kind", "projects", ["product_kind"])
    bind.exec_driver_sql(
        "UPDATE projects SET product_kind=? WHERE product_kind IS NULL OR trim(product_kind)=''",
        (PRODUCT_KIND_DRAMA,),
    )

    # ---------------------------------------------------------------- job stages
    stage_table = sa.table(
        "job_stage_definitions",
        sa.column("code", sa.String),
        sa.column("title", sa.String),
        sa.column("domain", sa.String),
        sa.column("active", sa.Boolean),
    )
    existing_stages = {
        str(row[0]) for row in bind.exec_driver_sql("SELECT code FROM job_stage_definitions").fetchall()
    }
    rows = [
        {"code": code, "title": title, "domain": domain, "active": True}
        for code, title, domain in EXPLAINER_JOB_STAGES
        if code not in existing_stages
    ]
    if rows:
        op.bulk_insert(stage_table, rows)

    # ---------------------------------------------------------------- channel profiles
    op.create_table(
        "channel_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36)),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("current_version_id", sa.String(36)),
        sa.Column("scope", sa.String(16), nullable=False, server_default="PROJECT"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_channel_profiles_status"),
        sa.CheckConstraint("scope IN ('PROJECT','GLOBAL')", name="ck_channel_profiles_scope"),
        sa.CheckConstraint("length(trim(code)) > 0", name="ck_channel_profiles_code"),
        sa.UniqueConstraint("project_id", "code", name="uq_channel_profiles_project_code"),
    )

    op.create_table(
        "channel_profile_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_profile_id", sa.String(36), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="FROZEN"),
        sa.Column("render_style", sa.Text(), nullable=False, server_default=""),
        sa.Column("palette_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("camera_grammar_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("lighting_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("typography_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("subtitle_safe_area_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("transition_set_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("voice_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("bgm_policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("negative_constraints_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("license_policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_version_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["channel_profile_id"], ["channel_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_version_id"], ["channel_profile_versions.id"], ondelete="SET NULL"),
        sa.CheckConstraint("version_no > 0", name="ck_channel_profile_versions_no"),
        sa.CheckConstraint("status IN ('DRAFT','FROZEN','RETIRED')", name="ck_channel_profile_versions_status"),
        sa.UniqueConstraint("channel_profile_id", "version_no", name="uq_channel_profile_versions_no"),
    )

    # ---------------------------------------------------------------- videos
    op.create_table(
        "explainer_videos",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_kind", sa.String(32), nullable=False, server_default="FACTUAL_EXPLAINER"),
        sa.Column("source_locale", sa.String(32), nullable=False, server_default="zh-CN"),
        sa.Column("input_kind", sa.String(32), nullable=False, server_default="TOPIC"),
        sa.Column("input_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("duration_mode", sa.String(16), nullable=False, server_default="TARGET"),
        sa.Column("target_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("tolerance_percent", sa.Float(), nullable=False, server_default="5"),
        sa.Column("automation_mode", sa.String(32), nullable=False, server_default="AUTO_WITH_EXCEPTIONS"),
        sa.Column("inference_mode", sa.String(32), nullable=False, server_default="LOCAL_ONLY"),
        sa.Column("research_mode", sa.String(32), nullable=False, server_default="OFFLINE_IMPORT"),
        sa.Column("research_allowed_domains_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("current_script_revision_id", sa.String(36)),
        sa.Column("current_channel_profile_version_id", sa.String(36)),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["current_channel_profile_version_id"], ["channel_profile_versions.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("duration_mode IN ('TARGET','FIXED')", name="ck_explainer_videos_duration_mode"),
        sa.CheckConstraint("target_seconds > 0", name="ck_explainer_videos_target_seconds"),
        sa.CheckConstraint("tolerance_percent >= 0 AND tolerance_percent <= 25", name="ck_explainer_videos_tolerance"),
        sa.CheckConstraint(
            "content_kind IN ('FACTUAL_EXPLAINER','ORIGINAL_FICTION')", name="ck_explainer_videos_content_kind"
        ),
        sa.CheckConstraint(
            "automation_mode IN ('AUTO_WITH_EXCEPTIONS','REVIEW_BEFORE_RENDER','MANUAL_REVIEW')",
            name="ck_explainer_videos_automation_mode",
        ),
        sa.CheckConstraint(
            "inference_mode IN ('LOCAL_ONLY','ALLOW_CONFIGURED_CLOUD')", name="ck_explainer_videos_inference_mode"
        ),
        sa.CheckConstraint(
            "research_mode IN ('OFFLINE_IMPORT','WEB_RESEARCH')", name="ck_explainer_videos_research_mode"
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACTIVE','ARCHIVED')", name="ck_explainer_videos_status"
        ),
        sa.UniqueConstraint("project_id", name="uq_explainer_videos_project"),
    )

    # ---------------------------------------------------------------- research
    op.create_table(
        "explainer_research_packets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("mode", sa.String(32), nullable=False, server_default="OFFLINE_IMPORT"),
        sa.Column("topic", sa.Text(), nullable=False, server_default=""),
        sa.Column("allowed_domains_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("external_request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_external_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("blockers_json", sa.Text(), nullable=False, server_default="[]"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.CheckConstraint("revision_no > 0", name="ck_explainer_research_packets_rev"),
        sa.CheckConstraint(
            "status IN ('DRAFT','COLLECTING','READY','BLOCKED','SUPERSEDED')",
            name="ck_explainer_research_packets_status",
        ),
        sa.CheckConstraint("external_request_count >= 0", name="ck_explainer_research_packets_requests"),
        sa.UniqueConstraint("video_id", "revision_no", name="uq_explainer_research_packets_rev"),
    )

    op.create_table(
        "explainer_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("packet_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("url", sa.Text()),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("author_or_publisher", sa.String(200)),
        sa.Column("published_at", sa.Text()),
        sa.Column("updated_at_source", sa.Text()),
        sa.Column("event_date", sa.Text()),
        sa.Column("event_date_precision", sa.String(24), nullable=False, server_default="UNKNOWN"),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("language", sa.String(32)),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("rel_path", sa.Text()),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credibility_kind", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("rights_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("upstream_source_id", sa.String(36)),
        sa.Column("import_session_id", sa.String(36)),
        sa.Column("source_document_version_id", sa.String(36)),
        sa.Column("retrieved_via", sa.String(32), nullable=False, server_default="OFFLINE_IMPORT"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["packet_id"], ["explainer_research_packets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["upstream_source_id"], ["explainer_sources.id"], ondelete="SET NULL"),
        sa.CheckConstraint("byte_size >= 0", name="ck_explainer_sources_size"),
        sa.CheckConstraint(
            "source_kind IN ('DOCUMENT_IMPORT','WEB_PAGE','REFERENCE_LINK','LICENSED_MEDIA','AUTHORED_FICTION_PACK')",
            name="ck_explainer_sources_kind",
        ),
        sa.CheckConstraint(
            "event_date_precision IN ('UNKNOWN','YEAR','MONTH','DAY','MINUTE','SECOND')",
            name="ck_explainer_sources_date_precision",
        ),
        sa.CheckConstraint(
            "credibility_kind IN ('PRIMARY','SECONDARY','AGGREGATOR','USER_GENERATED','AUTHORED_FICTION','UNKNOWN')",
            name="ck_explainer_sources_credibility",
        ),
        sa.CheckConstraint(
            "retrieved_via IN ('OFFLINE_IMPORT','WEB_RESEARCH','USER_SUPPLIED')",
            name="ck_explainer_sources_retrieved_via",
        ),
    )
    op.create_index("ix_explainer_sources_packet", "explainer_sources", ["packet_id"])
    op.create_index("ix_explainer_sources_video", "explainer_sources", ["video_id"])

    op.create_table(
        "explainer_source_spans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("packet_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("quote_text", sa.Text(), nullable=False),
        sa.Column("span_hash", sa.String(64), nullable=False),
        sa.Column("page_no", sa.Integer()),
        sa.Column("paragraph_no", sa.Integer()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["source_id"], ["explainer_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["packet_id"], ["explainer_research_packets.id"], ondelete="CASCADE"),
        sa.CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="ck_explainer_source_spans_range"),
        sa.UniqueConstraint("source_id", "ordinal", name="uq_explainer_source_spans_ordinal"),
    )

    # ---------------------------------------------------------------- claims / events / entities
    op.create_table(
        "explainer_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("packet_id", sa.String(36)),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("statement_kind", sa.String(32), nullable=False, server_default="FACT"),
        sa.Column("status", sa.String(24), nullable=False, server_default="UNVERIFIED"),
        sa.Column("importance", sa.String(16), nullable=False, server_default="KEY"),
        sa.Column("confidence_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("verified_as_history", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("disambiguation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("verification_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["packet_id"], ["explainer_research_packets.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('SUPPORTED','DISPUTED','UNVERIFIED','EXCLUDED')", name="ck_explainer_claims_status"
        ),
        sa.CheckConstraint(
            "statement_kind IN ('FACT','ORIGINAL_EXPLANATION','TRANSITION','FICTION','QUESTION')",
            name="ck_explainer_claims_statement_kind",
        ),
        sa.CheckConstraint("importance IN ('CORE','KEY','SUPPORTING')", name="ck_explainer_claims_importance"),
        sa.UniqueConstraint("video_id", "code", name="uq_explainer_claims_code"),
    )
    op.create_index("ix_explainer_claims_video", "explainer_claims", ["video_id"])
    op.create_index("ix_explainer_claims_status", "explainer_claims", ["video_id", "status"])

    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_span_id", sa.String(36), nullable=False),
        sa.Column("stance", sa.String(16), nullable=False),
        sa.Column("independence_key", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["claim_id"], ["explainer_claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["explainer_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_span_id"], ["explainer_source_spans.id"], ondelete="CASCADE"),
        sa.CheckConstraint("stance IN ('SUPPORTS','REFUTES','CONTEXT')", name="ck_claim_evidence_stance"),
        sa.UniqueConstraint("claim_id", "source_span_id", "stance", name="uq_claim_evidence_span_stance"),
    )
    op.create_index("ix_claim_evidence_claim", "claim_evidence", ["claim_id"])

    op.create_table(
        "explainer_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("story_time_start", sa.Text()),
        sa.Column("story_time_end", sa.Text()),
        sa.Column("story_time_precision", sa.String(24), nullable=False, server_default="UNKNOWN"),
        sa.Column("calendar_system", sa.String(64)),
        sa.Column("place_entity_id", sa.String(36)),
        sa.Column("place_label", sa.String(200)),
        sa.Column("participant_entity_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("claim_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("causal_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("sequence_no", sa.Integer(), nullable=False, server_default="0"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "story_time_precision IN ('UNKNOWN','YEAR','MONTH','DAY','MINUTE','SECOND')",
            name="ck_explainer_events_precision",
        ),
        sa.UniqueConstraint("video_id", "code", name="uq_explainer_events_code"),
    )
    op.create_index("ix_explainer_events_video", "explainer_events", ["video_id"])

    op.create_table(
        "explainer_entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("latin_name", sa.String(200)),
        sa.Column("aliases_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("fictional", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("descriptive_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("disambiguation_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("story_asset_id", sa.String(36)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("canonical_state_revision_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_asset_id"], ["story_assets.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "entity_type IN ('REAL_PERSON','FICTIONAL_CHARACTER','GROUP','LOCATION','PROP','ORGANIZATION','CONCEPT')",
            name="ck_explainer_entities_type",
        ),
        sa.CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="ck_explainer_entities_status"),
        sa.UniqueConstraint("video_id", "code", name="uq_explainer_entities_code"),
    )
    op.create_index("ix_explainer_entities_video", "explainer_entities", ["video_id"])

    op.create_table(
        "entity_state_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("age", sa.Integer()),
        sa.Column("wardrobe", sa.Text(), nullable=False, server_default=""),
        sa.Column("condition", sa.Text(), nullable=False, server_default=""),
        sa.Column("carried_prop_entity_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("valid_from_story_time", sa.Text()),
        sa.Column("valid_to_story_time", sa.Text()),
        sa.Column("identity_pack_version_id", sa.String(36)),
        sa.Column("reference_media_version_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_state_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["entity_id"], ["explainer_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_state_id"], ["entity_state_revisions.id"], ondelete="SET NULL"),
        sa.CheckConstraint("revision_no > 0", name="ck_entity_state_revisions_no"),
        sa.CheckConstraint("age IS NULL OR (age >= 0 AND age <= 200)", name="ck_entity_state_revisions_age"),
        sa.UniqueConstraint("entity_id", "revision_no", name="uq_entity_state_revisions_no"),
    )

    # ---------------------------------------------------------------- script / narration
    op.create_table(
        "explainer_script_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(32), nullable=False, server_default="zh-CN"),
        sa.Column("source_script_revision_id", sa.String(36)),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("outline_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("terminology_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("frozen_at", sa.Text()),
        sa.Column("frozen_by", sa.Text()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parent_plan_id", sa.String(36)),
        sa.Column("provenance_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_script_revision_id"], ["explainer_script_revisions.id"], ondelete="SET NULL"),
        sa.CheckConstraint("revision_no > 0", name="ck_explainer_script_revisions_no"),
        sa.CheckConstraint(
            "status IN ('DRAFT','IN_REVIEW','FROZEN','SUPERSEDED')", name="ck_explainer_script_revisions_status"
        ),
        sa.UniqueConstraint("video_id", "locale", "revision_no", name="uq_explainer_script_revisions_no"),
    )
    op.create_index("ix_explainer_script_revisions_video", "explainer_script_revisions", ["video_id", "locale"])

    op.create_table(
        "explainer_chapters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("script_revision_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("audience_question", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("claim_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_ids_json", sa.Text(), nullable=False, server_default="[]"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["script_revision_id"], ["explainer_script_revisions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("script_revision_id", "ordinal", name="uq_explainer_chapters_ordinal"),
        sa.UniqueConstraint("script_revision_id", "code", name="uq_explainer_chapters_code"),
    )

    op.create_table(
        "narration_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("script_revision_id", sa.String(36), nullable=False),
        sa.Column("chapter_id", sa.String(36)),
        sa.Column("canonical_segment_id", sa.String(64), nullable=False),
        sa.Column("locale", sa.String(32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("spoken_text", sa.Text(), nullable=False),
        sa.Column("statement_type", sa.String(32), nullable=False, server_default="FACT"),
        sa.Column("claim_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("pronunciation_map_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("speaker", sa.String(120)),
        sa.Column("emotion", sa.String(64)),
        sa.Column("pause_after_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_duration_ms", sa.Integer()),
        sa.Column("content_locked_by_human", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("locked_by", sa.Text()),
        sa.Column("locked_at", sa.Text()),
        sa.Column("segment_hash", sa.String(64), nullable=False),
        sa.Column("previous_segment_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["script_revision_id"], ["explainer_script_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["explainer_chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["previous_segment_id"], ["narration_segments.id"], ondelete="SET NULL"),
        sa.CheckConstraint("ordinal >= 0", name="ck_narration_segments_ordinal"),
        sa.CheckConstraint(
            "statement_type IN ('FACT','ORIGINAL_EXPLANATION','TRANSITION','FICTION','QUESTION')",
            name="ck_narration_segments_statement_type",
        ),
        sa.CheckConstraint("target_duration_ms IS NULL OR target_duration_ms > 0", name="ck_narration_segments_target"),
        sa.CheckConstraint("pause_after_ms >= 0", name="ck_narration_segments_pause"),
        sa.UniqueConstraint("script_revision_id", "canonical_segment_id", name="uq_narration_segments_canonical"),
    )
    op.create_index("ix_narration_segments_script", "narration_segments", ["script_revision_id", "ordinal"])
    op.create_index("ix_narration_segments_canonical", "narration_segments", ["video_id", "canonical_segment_id"])

    # ---------------------------------------------------------------- beats
    op.create_table(
        "explainer_visual_beats",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("render_type", sa.String(24), nullable=False),
        sa.Column("visual_intent", sa.Text(), nullable=False, server_default=""),
        sa.Column("must_be_motion", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reference_policy", sa.String(40), nullable=False, server_default="LOCKED_IDENTITY_AND_SCENE"),
        sa.Column("allowed_fallbacks_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("preferred_duration_ms", sa.Integer()),
        sa.Column("entity_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("claim_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("visual_factuality", sa.String(24), nullable=False, server_default="RECONSTRUCTION"),
        sa.Column("shot_grammar_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("prompt_intent", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="PLANNED"),
        sa.Column("actual_fallback_type", sa.String(24)),
        sa.Column("fallback_reason", sa.Text()),
        sa.Column("locked_by_human", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("locked_by", sa.Text()),
        sa.Column("locked_at", sa.Text()),
        sa.Column("origin", sa.String(24), nullable=False, server_default="PLANNED"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "render_type IN ('STILL_MOTION','PARALLAX','I2V','INFOGRAPHIC','LICENSED_MEDIA')",
            name="ck_explainer_visual_beats_render_type",
        ),
        sa.CheckConstraint(
            "visual_factuality IN ('DOCUMENTED','RECONSTRUCTION','SYMBOLIC','FICTIONAL')",
            name="ck_explainer_visual_beats_factuality",
        ),
        sa.CheckConstraint("preferred_duration_ms IS NULL OR preferred_duration_ms > 0", name="ck_explainer_beats_duration"),
        sa.CheckConstraint("ordinal >= 0", name="ck_explainer_visual_beats_ordinal"),
        sa.UniqueConstraint("video_id", "code", name="uq_explainer_visual_beats_code"),
    )
    op.create_index("ix_explainer_visual_beats_video", "explainer_visual_beats", ["video_id", "ordinal"])

    op.create_table(
        "beat_narration_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("beat_id", sa.String(36), nullable=False),
        sa.Column("narration_segment_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["beat_id"], ["explainer_visual_beats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["narration_segment_id"], ["narration_segments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("beat_id", "narration_segment_id", name="uq_beat_narration_links_pair"),
    )
    op.create_index("ix_beat_narration_links_segment", "beat_narration_links", ["narration_segment_id"])

    # ---------------------------------------------------------------- editions
    op.create_table(
        "explainer_editions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("edition_key", sa.String(64), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("voice_locale", sa.String(32), nullable=False),
        sa.Column("subtitle_locales_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("subtitle_mode", sa.String(24), nullable=False, server_default="NONE"),
        sa.Column("aspect_ratio", sa.String(16), nullable=False, server_default="16:9"),
        sa.Column("fps_num", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("fps_den", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="1920"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="1080"),
        sa.Column("audio_sample_rate_hz", sa.Integer(), nullable=False, server_default="48000"),
        sa.Column("duration_policy", sa.String(32), nullable=False, server_default="NATURAL_NARRATION"),
        sa.Column("target_seconds", sa.Integer()),
        sa.Column("tolerance_percent", sa.Float(), nullable=False, server_default="5"),
        sa.Column("allow_soft_subtitle_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("frozen_script_revision_id", sa.String(36)),
        sa.Column("frozen_narration_take_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("frozen_subtitle_revision_id", sa.String(36)),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["frozen_script_revision_id"], ["explainer_script_revisions.id"], ondelete="SET NULL"),
        sa.CheckConstraint("revision_no > 0", name="ck_explainer_editions_rev"),
        sa.CheckConstraint("fps_num > 0", name="ck_explainer_editions_fps_num"),
        sa.CheckConstraint("fps_den > 0", name="ck_explainer_editions_fps_den"),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_explainer_editions_geometry"),
        sa.CheckConstraint("audio_sample_rate_hz > 0", name="ck_explainer_editions_sample_rate"),
        sa.CheckConstraint("aspect_ratio IN ('16:9','9:16','3:4','1:1')", name="ck_explainer_editions_aspect"),
        sa.CheckConstraint("subtitle_mode IN ('NONE','BURNED','SOFT','BILINGUAL_BURNED')", name="ck_explainer_editions_subtitle_mode"),
        sa.CheckConstraint(
            "duration_policy IN ('USE_SOURCE_TARGET','NATURAL_NARRATION','FIXED_FRAMES')",
            name="ck_explainer_editions_duration_policy",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','PLANNED','READY_TO_RENDER','RENDERING','READY','FAILED','CANCELLED')",
            name="ck_explainer_editions_status",
        ),
        sa.UniqueConstraint("video_id", "edition_key", "revision_no", name="uq_explainer_editions_key_rev"),
    )
    op.create_index("ix_explainer_editions_video", "explainer_editions", ["video_id"])

    # ---------------------------------------------------------------- narration takes / alignment / subtitles
    op.create_table(
        "narration_takes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("segment_id", sa.String(36), nullable=False),
        sa.Column("canonical_segment_id", sa.String(64), nullable=False),
        sa.Column("locale", sa.String(32), nullable=False),
        sa.Column("take_no", sa.Integer(), nullable=False),
        sa.Column("media_asset_id", sa.String(36), nullable=False),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("media_sha256", sa.String(64), nullable=False),
        sa.Column("segment_hash", sa.String(64), nullable=False),
        sa.Column("voice_profile_version_id", sa.String(36)),
        sa.Column("model_ref", sa.String(200), nullable=False, server_default=""),
        sa.Column("emotion", sa.String(64)),
        sa.Column("speech_rate", sa.Float(), nullable=False, server_default="1"),
        sa.Column("measured_duration_ms", sa.Integer()),
        sa.Column("measured_sample_count", sa.Integer()),
        sa.Column("sample_rate_hz", sa.Integer()),
        sa.Column("lead_silence_ms", sa.Integer()),
        sa.Column("trail_silence_ms", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False, server_default="GENERATED"),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_job_attempt_id", sa.String(36)),
        sa.Column("generation_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["narration_segments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="CASCADE"),
        sa.CheckConstraint("take_no > 0", name="ck_narration_takes_no"),
        sa.CheckConstraint(
            "status IN ('GENERATED','VERIFIED','REJECTED','SUPERSEDED')", name="ck_narration_takes_status"
        ),
        sa.CheckConstraint("measured_duration_ms IS NULL OR measured_duration_ms >= 0", name="ck_narration_takes_duration"),
        sa.UniqueConstraint("segment_id", "take_no", name="uq_narration_takes_no"),
    )
    op.create_index("ix_narration_takes_video", "narration_takes", ["video_id", "locale"])

    op.create_table(
        "narration_alignment_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("take_id", sa.String(36), nullable=False),
        sa.Column("segment_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(32), nullable=False),
        sa.Column("script_hash", sa.String(64), nullable=False),
        sa.Column("media_sha256", sa.String(64), nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("sample_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_samples", sa.Integer()),
        sa.Column("word_timings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("display_map_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("alignment_status", sa.String(24), nullable=False, server_default="ALIGNED"),
        sa.Column("unaligned_tokens_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("asr_review_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("detector_version", sa.String(64), nullable=False, server_default=""),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["take_id"], ["narration_takes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["narration_segments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.CheckConstraint("revision_no > 0", name="ck_narration_alignment_revisions_no"),
        sa.CheckConstraint("sample_offset >= 0", name="ck_narration_alignment_revisions_offset"),
        sa.CheckConstraint("total_samples IS NULL OR total_samples > 0", name="ck_narration_alignment_revisions_samples"),
        sa.CheckConstraint(
            "alignment_status IN ('ALIGNED','PARTIAL','FAILED')", name="ck_narration_alignment_revisions_status"
        ),
        sa.UniqueConstraint("take_id", "revision_no", name="uq_narration_alignment_revisions_no"),
    )
    op.create_index("ix_narration_alignment_revisions_video", "narration_alignment_revisions", ["video_id"])

    op.create_table(
        "explainer_subtitle_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("edition_id", sa.String(36)),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(32), nullable=False),
        sa.Column("paired_locale", sa.String(32)),
        sa.Column("format", sa.String(16), nullable=False, server_default="JSON"),
        sa.Column("text_authority", sa.String(32), nullable=False, server_default="NARRATION_SCRIPT"),
        sa.Column("script_revision_id", sa.String(36)),
        sa.Column("narration_alignment_revision_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("style_version_id", sa.String(36)),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("cues_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("layout_report_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["script_revision_id"], ["explainer_script_revisions.id"], ondelete="SET NULL"),
        sa.CheckConstraint("revision_no > 0", name="ck_explainer_subtitle_revisions_no"),
        sa.CheckConstraint("format IN ('JSON','SRT','VTT','ASS')", name="ck_explainer_subtitle_revisions_format"),
        sa.CheckConstraint(
            "text_authority IN ('NARRATION_SCRIPT','TRANSLATED_SCRIPT','LEGACY_SCRIPT')",
            name="ck_explainer_subtitle_revisions_authority",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','FROZEN','SUPERSEDED')", name="ck_explainer_subtitle_revisions_status"
        ),
        sa.UniqueConstraint("video_id", "locale", "revision_no", name="uq_explainer_subtitle_revisions_no"),
    )

    # ---------------------------------------------------------------- composition
    op.create_table(
        "composition_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("edition_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("manifest_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("fps_num", sa.Integer(), nullable=False),
        sa.Column("fps_den", sa.Integer(), nullable=False),
        sa.Column("total_frames", sa.Integer(), nullable=False),
        sa.Column("audio_sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("total_samples", sa.Integer()),
        sa.Column("duration_policy", sa.String(32), nullable=False, server_default="NATURAL_NARRATION"),
        sa.Column("target_frames", sa.Integer()),
        sa.Column("frozen_media_hash", sa.String(64)),
        sa.Column("frozen_at", sa.Text()),
        sa.Column("frozen_by", sa.Text()),
        sa.Column("validation_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint("revision_no > 0", name="ck_composition_revisions_no"),
        sa.CheckConstraint("fps_num > 0", name="ck_composition_revisions_fps_num"),
        sa.CheckConstraint("fps_den > 0", name="ck_composition_revisions_fps_den"),
        sa.CheckConstraint("total_frames >= 0", name="ck_composition_revisions_frames"),
        sa.CheckConstraint("audio_sample_rate_hz > 0", name="ck_composition_revisions_rate"),
        sa.CheckConstraint("status IN ('DRAFT','FROZEN','SUPERSEDED')", name="ck_composition_revisions_status"),
        sa.UniqueConstraint("edition_id", "revision_no", name="uq_composition_revisions_no"),
    )
    op.create_index("ix_composition_revisions_edition", "composition_revisions", ["edition_id"])

    op.create_table(
        "composition_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("composition_revision_id", sa.String(36), nullable=False),
        sa.Column("edition_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("item_kind", sa.String(24), nullable=False),
        sa.Column("track", sa.String(24), nullable=False),
        sa.Column("beat_id", sa.String(36)),
        sa.Column("narration_segment_id", sa.String(36)),
        sa.Column("narration_take_id", sa.String(36)),
        sa.Column("media_asset_id", sa.String(36)),
        sa.Column("media_version_id", sa.String(36)),
        sa.Column("media_sha256", sa.String(64)),
        sa.Column("start_frame", sa.Integer(), nullable=False),
        sa.Column("end_frame_exclusive", sa.Integer(), nullable=False),
        sa.Column("source_in_us", sa.Integer()),
        sa.Column("source_out_us", sa.Integer()),
        sa.Column("sample_start", sa.Integer()),
        sa.Column("sample_end_exclusive", sa.Integer()),
        sa.Column("layer_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("transform_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("subtitle_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("audio_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("transition_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("render_type_planned", sa.String(24)),
        sa.Column("render_type_actual", sa.String(24)),
        sa.Column("media_kind", sa.String(24)),
        sa.Column("item_hash", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(
            ["composition_revision_id"], ["composition_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["beat_id"], ["explainer_visual_beats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["narration_segment_id"], ["narration_segments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["narration_take_id"], ["narration_takes.id"], ondelete="SET NULL"),
        sa.CheckConstraint("ordinal >= 0", name="ck_composition_items_ordinal"),
        sa.CheckConstraint("start_frame >= 0", name="ck_composition_items_start"),
        sa.CheckConstraint("end_frame_exclusive > start_frame", name="ck_composition_items_end"),
        sa.CheckConstraint(
            "track IN ('VIDEO','NARRATION','BGM','SFX','SUBTITLE','OVERLAY')", name="ck_composition_items_track"
        ),
        sa.CheckConstraint(
            "item_kind IN ('VIDEO_CLIP','IMAGE_CLIP','MOTION_CLIP','INFOGRAPHIC','AUDIO_CLIP','SUBTITLE_CUE','TEXT_LAYER')",
            name="ck_composition_items_kind",
        ),
        sa.CheckConstraint(
            "source_in_us IS NULL OR source_in_us >= 0", name="ck_composition_items_source_in"
        ),
        sa.CheckConstraint(
            "source_out_us IS NULL OR source_in_us IS NULL OR source_out_us >= source_in_us",
            name="ck_composition_items_source_range",
        ),
        sa.CheckConstraint(
            "sample_end_exclusive IS NULL OR sample_start IS NULL OR sample_end_exclusive >= sample_start",
            name="ck_composition_items_sample_range",
        ),
        sa.UniqueConstraint("composition_revision_id", "track", "ordinal", name="uq_composition_items_ordinal"),
    )
    op.create_index("ix_composition_items_revision", "composition_items", ["composition_revision_id"])
    op.create_index("ix_composition_items_beat", "composition_items", ["beat_id"])

    op.create_table(
        "composition_renders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("edition_id", sa.String(36), nullable=False),
        sa.Column("composition_revision_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("render_kind", sa.String(24), nullable=False, server_default="FULL"),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("media_asset_id", sa.String(36)),
        sa.Column("media_version_id", sa.String(36)),
        sa.Column("rel_path", sa.Text()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("frame_count", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("probe_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("integrity_status", sa.String(24), nullable=False, server_default="UNKNOWN"),
        sa.Column("parent_render_id", sa.String(36)),
        sa.Column("job_id", sa.String(36)),
        sa.Column("human_approval_id", sa.String(36)),
        sa.Column("machine_policy_decision_id", sa.String(36)),
        sa.Column("blockers_json", sa.Text(), nullable=False, server_default="[]"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["composition_revision_id"], ["composition_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_render_id"], ["composition_renders.id"], ondelete="SET NULL"),
        sa.CheckConstraint("revision_no > 0", name="ck_composition_renders_no"),
        sa.CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_composition_renders_size"),
        sa.CheckConstraint("frame_count IS NULL OR frame_count >= 0", name="ck_composition_renders_frames"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_composition_renders_duration"),
        sa.CheckConstraint("render_kind IN ('FULL','CHUNK','PREVIEW','EXPORT')", name="ck_composition_renders_kind"),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED','QUARANTINED')",
            name="ck_composition_renders_status",
        ),
        sa.CheckConstraint(
            "integrity_status IN ('UNKNOWN','VERIFIED','CORRUPT','MISSING','HASH_MISMATCH')",
            name="ck_composition_renders_integrity",
        ),
        sa.UniqueConstraint("edition_id", "revision_no", name="uq_composition_renders_no"),
    )
    op.create_index("ix_composition_renders_revision", "composition_renders", ["composition_revision_id"])

    op.create_table(
        "composition_render_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("render_id", sa.String(36), nullable=False),
        sa.Column("composition_revision_id", sa.String(36), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("start_frame", sa.Integer(), nullable=False),
        sa.Column("end_frame_exclusive", sa.Integer(), nullable=False),
        sa.Column("handle_in_frames", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("handle_out_frames", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("rel_path", sa.Text()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("chunk_hash", sa.String(64), nullable=False),
        sa.Column("codec_signature_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("job_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["render_id"], ["composition_renders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["composition_revision_id"], ["composition_revisions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("start_frame >= 0", name="ck_composition_render_chunks_start"),
        sa.CheckConstraint("end_frame_exclusive > start_frame", name="ck_composition_render_chunks_end"),
        sa.CheckConstraint("handle_in_frames >= 0 AND handle_out_frames >= 0", name="ck_composition_render_chunks_handles"),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED','QUARANTINED')",
            name="ck_composition_render_chunks_status",
        ),
        sa.UniqueConstraint("render_id", "chunk_no", name="uq_composition_render_chunks_no"),
    )

    op.create_table(
        "composition_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("edition_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("render_id", sa.String(36), nullable=False),
        sa.Column("composition_revision_id", sa.String(36), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("output_profile_version_id", sa.String(36)),
        sa.Column("output_profile_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("media_asset_id", sa.String(36)),
        sa.Column("media_version_id", sa.String(36)),
        sa.Column("rel_path", sa.Text()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("probe_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("integrity_status", sa.String(24), nullable=False, server_default="UNKNOWN"),
        sa.Column("files_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("licenses_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("handoff_reason", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["render_id"], ["composition_renders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["composition_revision_id"], ["composition_revisions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','READY','NEEDS_MANUAL_PUBLISH','PUBLISHED','FAILED','WITHDRAWN')",
            name="ck_composition_deliveries_status",
        ),
        sa.CheckConstraint(
            "integrity_status IN ('UNKNOWN','VERIFIED','CORRUPT','MISSING','HASH_MISMATCH')",
            name="ck_composition_deliveries_integrity",
        ),
    )
    op.create_index("ix_composition_deliveries_edition", "composition_deliveries", ["edition_id"])

    # ---------------------------------------------------------------- runs / bindings / dependencies
    op.create_table(
        "explainer_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("automation_mode", sa.String(32), nullable=False, server_default="AUTO_WITH_EXCEPTIONS"),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("parent_plan_id", sa.String(36)),
        sa.Column("plan_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("plan_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("frozen_inputs_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("policy_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("capability_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("budget_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("inference_mode", sa.String(32), nullable=False, server_default="LOCAL_ONLY"),
        sa.Column("research_mode", sa.String(32), nullable=False, server_default="OFFLINE_IMPORT"),
        sa.Column("inference_egress_denied_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("automation_workflow_run_id", sa.String(36)),
        sa.Column("automation_workflow_id", sa.String(36)),
        sa.Column("current_stage_code", sa.String(40)),
        sa.Column("progress_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("blockers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("budget_used_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("started_at", sa.Text()),
        sa.Column("finished_at", sa.Text()),
        sa.Column("cancel_requested_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_plan_id"], ["explainer_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["automation_workflow_run_id"], ["automation_workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["automation_workflow_id"], ["automation_workflows.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('QUEUED','PREFLIGHT','RUNNING','QC_RUNNING','READY_TO_EXPORT','EXPORTING','COMPLETED',"
            "'PAUSING','PAUSED','WAITING_INPUT','FAILED','CANCELLING','CANCELLED')",
            name="ck_explainer_runs_status",
        ),
        sa.CheckConstraint(
            "inference_mode IN ('LOCAL_ONLY','ALLOW_CONFIGURED_CLOUD')", name="ck_explainer_runs_inference"
        ),
        sa.CheckConstraint(
            "research_mode IN ('OFFLINE_IMPORT','WEB_RESEARCH')", name="ck_explainer_runs_research"
        ),
        sa.CheckConstraint(
            "automation_mode IN ('AUTO_WITH_EXCEPTIONS','REVIEW_BEFORE_RENDER','MANUAL_REVIEW')",
            name="ck_explainer_runs_automation_mode",
        ),
        sa.CheckConstraint("inference_egress_denied_count >= 0", name="ck_explainer_runs_egress_count"),
        sa.CheckConstraint("plan_revision > 0", name="ck_explainer_runs_plan_revision"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_explainer_runs_idempotency"),
    )
    op.create_index("ix_explainer_runs_video", "explainer_runs", ["video_id"])

    op.create_table(
        "explainer_step_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("planned_step_code", sa.String(64), nullable=False),
        sa.Column("task_key", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("automation_task_id", sa.String(36)),
        sa.Column("job_id", sa.String(36)),
        sa.Column("job_attempt_id", sa.String(36)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("planned_inputs_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resolved_inputs_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("output_kind", sa.String(40), nullable=False, server_default=""),
        sa.Column("output_ref_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("subplan_id", sa.String(36)),
        sa.Column("skip_reason", sa.Text()),
        sa.Column("blocker_code", sa.String(64)),
        sa.Column("error_detail_redacted", sa.Text()),
        sa.Column("started_at", sa.Text()),
        sa.Column("finished_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["explainer_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_explainer_step_bindings_attempts"),
        sa.CheckConstraint(
            "status IN ('PENDING','BLOCKED','RUNNING','SUCCEEDED','SKIPPED_WITH_REASON','RETRYABLE_FAILED',"
            "'TERMINAL_FAILED','CANCELLED','STALE')",
            name="ck_explainer_step_bindings_status",
        ),
        sa.CheckConstraint(
            "status <> 'SKIPPED_WITH_REASON' OR (skip_reason IS NOT NULL AND length(trim(skip_reason)) > 0)",
            name="ck_explainer_step_bindings_skip_reason",
        ),
        sa.UniqueConstraint("run_id", "task_key", name="uq_explainer_step_bindings_task"),
    )
    op.create_index("ix_explainer_step_bindings_run", "explainer_step_bindings", ["run_id"])

    op.create_table(
        "artifact_dependencies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("edition_id", sa.String(36)),
        sa.Column("upstream_kind", sa.String(40), nullable=False),
        sa.Column("upstream_id", sa.String(64), nullable=False),
        sa.Column("upstream_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("downstream_kind", sa.String(40), nullable=False),
        sa.Column("downstream_id", sa.String(64), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stale_reason", sa.Text()),
        sa.Column("stale_at", sa.Text()),
        sa.Column("invalidated_by", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "downstream_kind IN ('SCRIPT_SEGMENT','NARRATION_TAKE','ALIGNMENT','SUBTITLE_REVISION','VISUAL_BEAT',"
            "'BEAT_SELECTION','COMPOSITION_REVISION','RENDER','DELIVERY','QC_REPORT','EDITION')",
            name="ck_artifact_dependencies_downstream",
        ),
        sa.UniqueConstraint(
            "upstream_kind", "upstream_id", "downstream_kind", "downstream_id",
            name="uq_artifact_dependencies_edge",
        ),
    )
    op.create_index("ix_artifact_dependencies_downstream", "artifact_dependencies", ["downstream_kind", "downstream_id"])
    op.create_index("ix_artifact_dependencies_video", "artifact_dependencies", ["video_id", "stale"])

    # ---------------------------------------------------------------- media / takes / selections
    op.create_table(
        "explainer_media_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("beat_id", sa.String(36), nullable=False),
        sa.Column("variant_no", sa.Integer(), nullable=False),
        sa.Column("candidate_kind", sa.String(24), nullable=False, server_default="CREATIVE"),
        sa.Column("purpose", sa.String(32), nullable=False, server_default="VISUAL"),
        sa.Column("media_asset_id", sa.String(36)),
        sa.Column("media_version_id", sa.String(36)),
        sa.Column("media_sha256", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("job_id", sa.String(36)),
        sa.Column("source_job_attempt_id", sa.String(36)),
        sa.Column("execution_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("lineage_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("render_type_planned", sa.String(24)),
        sa.Column("render_type_actual", sa.String(24)),
        sa.Column("fallback_reason", sa.Text()),
        sa.Column("technical_retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("creative_repair_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qc_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("adopted", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["beat_id"], ["explainer_visual_beats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="SET NULL"),
        sa.CheckConstraint("variant_no > 0", name="ck_explainer_media_candidates_variant"),
        sa.CheckConstraint("candidate_kind IN ('CREATIVE','TECHNICAL_RETRY')", name="ck_explainer_media_candidates_kind"),
        sa.CheckConstraint(
            "status IN ('PENDING','GENERATING','READY','REJECTED','FAILED','SUPERSEDED')",
            name="ck_explainer_media_candidates_status",
        ),
        sa.CheckConstraint("technical_retry_count >= 0 AND creative_repair_count >= 0", name="ck_explainer_media_candidates_counts"),
        sa.UniqueConstraint("beat_id", "candidate_kind", "variant_no", name="uq_explainer_media_candidates_variant"),
    )
    op.create_index("ix_explainer_media_candidates_beat", "explainer_media_candidates", ["beat_id"])

    op.create_table(
        "explainer_beat_selections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("beat_id", sa.String(36), nullable=False),
        sa.Column("edition_id", sa.String(36)),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("media_asset_id", sa.String(36), nullable=False),
        sa.Column("media_version_id", sa.String(36), nullable=False),
        sa.Column("media_sha256", sa.String(64), nullable=False),
        sa.Column("source_in_us", sa.Integer()),
        sa.Column("source_out_us", sa.Integer()),
        sa.Column("adoption_authority", sa.String(24), nullable=False, server_default="MACHINE_POLICY"),
        sa.Column("locked_by_human", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actor", sa.Text()),
        sa.Column("decided_at", sa.Text()),
        sa.Column("policy_decision_id", sa.String(36)),
        sa.Column("render_type_actual", sa.String(24)),
        sa.Column("fallback_reason", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["beat_id"], ["explainer_visual_beats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["explainer_media_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_version_id"], ["media_versions.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "adoption_authority IN ('MACHINE_POLICY','HUMAN')", name="ck_explainer_beat_selections_authority"
        ),
        sa.CheckConstraint("status IN ('ACTIVE','SUPERSEDED')", name="ck_explainer_beat_selections_status"),
        sa.CheckConstraint(
            "adoption_authority <> 'HUMAN' OR (actor IS NOT NULL AND length(trim(actor)) > 0)",
            name="ck_explainer_beat_selections_actor",
        ),
    )
    op.create_index("ix_explainer_beat_selections_beat", "explainer_beat_selections", ["beat_id"], unique=False)

    # ---------------------------------------------------------------- identity bindings
    op.create_table(
        "run_identity_inputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("entity_state_revision_id", sa.String(36)),
        sa.Column("identity_pack_id", sa.String(36)),
        sa.Column("identity_pack_version_id", sa.String(36)),
        sa.Column("snapshot_kind", sa.String(32), nullable=False, server_default="MACHINE_TEMPORARY"),
        sa.Column("slot_hashes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("verified_slot_hashes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("draft_hash", sa.String(64), nullable=False),
        sa.Column("verified_at", sa.Text()),
        sa.Column("verification_status", sa.String(24), nullable=False, server_default="UNVERIFIED"),
        sa.Column("human_approval_id", sa.String(36)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["explainer_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["explainer_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_state_revision_id"], ["entity_state_revisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["identity_pack_version_id"], ["character_identity_pack_versions.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "snapshot_kind IN ('MACHINE_TEMPORARY','HUMAN_APPROVED','SHARED_PACK')",
            name="ck_run_identity_inputs_snapshot_kind",
        ),
        sa.CheckConstraint(
            "verification_status IN ('UNVERIFIED','VERIFIED','DRIFTED','MISSING_SLOT')",
            name="ck_run_identity_inputs_verification",
        ),
        sa.CheckConstraint("status IN ('ACTIVE','SUPERSEDED')", name="ck_run_identity_inputs_status"),
        sa.UniqueConstraint("run_id", "entity_id", "status", name="uq_run_identity_inputs_entity"),
    )

    op.create_table(
        "entity_identity_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("edition_id", sa.String(36)),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("beat_id", sa.String(36)),
        sa.Column("identity_pack_version_id", sa.String(36)),
        sa.Column("entity_state_revision_id", sa.String(36)),
        sa.Column("reference_media_version_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reference_slot_roles_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("consumed_slot_roles_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("source", sa.String(24), nullable=False, server_default="MACHINE_POLICY"),
        sa.Column("human_approval_id", sa.String(36)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["explainer_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["beat_id"], ["explainer_visual_beats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["identity_pack_version_id"], ["character_identity_pack_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["entity_state_revision_id"], ["entity_state_revisions.id"], ondelete="SET NULL"),
        sa.CheckConstraint("source IN ('MACHINE_POLICY','HUMAN','SHARED_PACK')", name="ck_entity_identity_bindings_source"),
        sa.CheckConstraint("status IN ('ACTIVE','SUPERSEDED')", name="ck_entity_identity_bindings_status"),
        sa.UniqueConstraint("video_id", "entity_id", "beat_id", "status", name="uq_entity_identity_bindings_target"),
    )
    op.create_index("ix_entity_identity_bindings_entity", "entity_identity_bindings", ["entity_id"])

    # ---------------------------------------------------------------- qc / decisions
    op.create_table(
        "explainer_qc_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("edition_id", sa.String(36)),
        sa.Column("subject_kind", sa.String(32), nullable=False),
        sa.Column("subject_revision_id", sa.String(64), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("policy_version", sa.String(64), nullable=False, server_default="explainer_standard_v1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="NOT_RUN"),
        sa.Column("coverage_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("unverified_checks_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("detectors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("policy_decision_id", sa.String(36)),
        sa.Column("human_decision_id", sa.String(36)),
        sa.Column("job_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "subject_kind IN ('COMPOSITION_RENDER','COMPOSITION_REVISION','NARRATION_ALIGNMENT','SUBTITLE_REVISION',"
            "'VISUAL_BEAT','EDITION','SCRIPT_REVISION')",
            name="ck_explainer_qc_reports_subject_kind",
        ),
        sa.CheckConstraint(
            "status IN ('NOT_RUN','RUNNING','PASS','PASS_WITH_ISSUES','BLOCKED','FAILED','STALE')",
            name="ck_explainer_qc_reports_status",
        ),
    )
    op.create_index("ix_explainer_qc_reports_subject", "explainer_qc_reports", ["subject_kind", "subject_revision_id"])
    op.create_index("ix_explainer_qc_reports_video", "explainer_qc_reports", ["video_id"])

    op.create_table(
        "explainer_qc_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("report_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("edition_id", sa.String(36)),
        sa.Column("issue_kind", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("detector", sa.String(64), nullable=False),
        sa.Column("detector_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("scope_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("locale", sa.String(32)),
        sa.Column("start_frame", sa.Integer()),
        sa.Column("end_frame_exclusive", sa.Integer()),
        sa.Column("start_ms", sa.Integer()),
        sa.Column("end_ms", sa.Integer()),
        sa.Column("beat_id", sa.String(36)),
        sa.Column("narration_segment_id", sa.String(36)),
        sa.Column("subtitle_cue_id", sa.String(36)),
        sa.Column("observed", sa.Text(), nullable=False, server_default=""),
        sa.Column("expected", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float()),
        sa.Column("unknown_reason", sa.Text()),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("evidence_media_version_id", sa.String(36)),
        sa.Column("evidence_text_span", sa.Text()),
        sa.Column("suggested_repair", sa.Text(), nullable=False, server_default=""),
        sa.Column("responsible_step_code", sa.String(64)),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("closed_by_decision_id", sa.String(36)),
        sa.Column("closed_evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("superseded_by_issue_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["report_id"], ["explainer_qc_reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["beat_id"], ["explainer_visual_beats.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["narration_segment_id"], ["narration_segments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["superseded_by_issue_id"], ["explainer_qc_issues.id"], ondelete="SET NULL"),
        sa.CheckConstraint("severity IN ('BLOCKER','MAJOR','MINOR','INFO','UNKNOWN')", name="ck_explainer_qc_issues_severity"),
        sa.CheckConstraint("status IN ('OPEN','FIXING','CLOSED','ACCEPTED_AS_IS','SUPERSEDED')", name="ck_explainer_qc_issues_status"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_explainer_qc_issues_confidence"),
        sa.CheckConstraint("start_frame IS NULL OR start_frame >= 0", name="ck_explainer_qc_issues_start_frame"),
        sa.CheckConstraint("end_frame_exclusive IS NULL OR start_frame IS NULL OR end_frame_exclusive >= start_frame", name="ck_explainer_qc_issues_frame_range"),
        sa.CheckConstraint("end_ms IS NULL OR start_ms IS NULL OR end_ms >= start_ms", name="ck_explainer_qc_issues_ms_range"),
    )
    op.create_index("ix_explainer_qc_issues_report", "explainer_qc_issues", ["report_id"])
    op.create_index("ix_explainer_qc_issues_status", "explainer_qc_issues", ["video_id", "status", "severity"])

    op.create_table(
        "explainer_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("edition_id", sa.String(36)),
        sa.Column("decision_kind", sa.String(24), nullable=False),
        sa.Column("subject_kind", sa.String(32), nullable=False),
        sa.Column("subject_revision_id", sa.String(64), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("rule_version", sa.String(64)),
        sa.Column("thresholds_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("limitations", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.Text()),
        sa.Column("actor_type", sa.String(24), nullable=False),
        sa.Column("reviewed_intervals_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_hash_at_decision", sa.String(64), nullable=False, server_default=""),
        sa.Column("source_qc_report_id", sa.String(36)),
        sa.Column("policy_processor", sa.String(64)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("stale_reason", sa.Text()),
        sa.Column("decided_at", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_qc_report_id"], ["explainer_qc_reports.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "decision_kind IN ('POLICY_ACCEPTED','HUMAN_APPROVED','PUBLICATION_AUTHORIZED','REJECTED','CHANGES_REQUESTED')",
            name="ck_explainer_decisions_kind",
        ),
        sa.CheckConstraint("actor_type IN ('MACHINE','HUMAN','SYSTEM')", name="ck_explainer_decisions_actor_type"),
        sa.CheckConstraint("status IN ('ACTIVE','SUPERSEDED','REVOKED')", name="ck_explainer_decisions_status"),
        sa.CheckConstraint(
            "decision_kind <> 'POLICY_ACCEPTED' OR actor_type = 'MACHINE'",
            name="ck_explainer_decisions_policy_is_machine",
        ),
        sa.CheckConstraint(
            "decision_kind <> 'HUMAN_APPROVED' OR (actor_type = 'HUMAN' AND actor IS NOT NULL AND length(trim(actor)) > 0)",
            name="ck_explainer_decisions_human_requires_actor",
        ),
        sa.CheckConstraint(
            "decision_kind <> 'POLICY_ACCEPTED' OR (policy_processor IS NOT NULL AND length(trim(policy_processor)) > 0)",
            name="ck_explainer_decisions_policy_processor",
        ),
    )
    op.create_index("ix_explainer_decisions_subject", "explainer_decisions", ["subject_kind", "subject_revision_id"])
    op.create_index("ix_explainer_decisions_video", "explainer_decisions", ["video_id", "decision_kind"])

    # ---------------------------------------------------------------- schedules
    op.create_table(
        "explainer_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36)),
        sa.Column("channel_profile_id", sa.String(36), nullable=False),
        sa.Column("channel_profile_version_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("rule_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("rule_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("topic_scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_allowlist_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("daily_budget_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("duplicate_window_hours", sa.Integer(), nullable=False, server_default="72"),
        sa.Column("insufficient_topic_policy", sa.String(24), nullable=False, server_default="SKIP_WITH_REASON"),
        sa.Column("closed_window_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("failure_notification_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("durations_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("outputs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("automation_mode", sa.String(32), nullable=False, server_default="AUTO_WITH_EXCEPTIONS"),
        sa.Column("next_occurrence_at", sa.Text()),
        sa.Column("last_occurrence_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_profile_id"], ["channel_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_profile_version_id"], ["channel_profile_versions.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('ACTIVE','PAUSED','ARCHIVED')", name="ck_explainer_schedules_status"),
        sa.CheckConstraint("max_concurrent_runs >= 1 AND max_concurrent_runs <= 8", name="ck_explainer_schedules_concurrency"),
        sa.CheckConstraint("duplicate_window_hours >= 0", name="ck_explainer_schedules_duplicate_window"),
        sa.CheckConstraint("rule_version > 0 AND config_revision > 0", name="ck_explainer_schedules_versions"),
        sa.CheckConstraint(
            "insufficient_topic_policy IN ('SKIP_WITH_REASON','WAIT_FOR_INPUT','FAIL')",
            name="ck_explainer_schedules_topic_policy",
        ),
        sa.UniqueConstraint("code", name="uq_explainer_schedules_code"),
    )

    op.create_table(
        "schedule_occurrences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schedule_id", sa.String(36), nullable=False),
        sa.Column("scheduled_for", sa.Text(), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("occurrence_kind", sa.String(16), nullable=False, server_default="SCHEDULED"),
        sa.Column("rule_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("lease_owner", sa.String(120)),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_expires_at", sa.Text()),
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_id", sa.String(36)),
        sa.Column("video_id", sa.String(36)),
        sa.Column("project_id", sa.String(36)),
        sa.Column("skip_reason", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_detail_redacted", sa.Text()),
        sa.Column("started_at", sa.Text()),
        sa.Column("finished_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["schedule_id"], ["explainer_schedules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["explainer_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('PENDING','CLAIMED','RUNNING','COMPLETED','SKIPPED_WITH_REASON','FAILED','MISSED')",
            name="ck_schedule_occurrences_status",
        ),
        sa.CheckConstraint("occurrence_kind IN ('SCHEDULED','MANUAL')", name="ck_schedule_occurrences_kind"),
        sa.CheckConstraint("fencing_token >= 0 AND claim_count >= 0", name="ck_schedule_occurrences_lease_counters"),
        sa.CheckConstraint("config_revision > 0 AND rule_version > 0", name="ck_schedule_occurrences_versions"),
        sa.CheckConstraint(
            "status <> 'SKIPPED_WITH_REASON' OR (skip_reason IS NOT NULL AND length(trim(skip_reason)) > 0)",
            name="ck_schedule_occurrences_skip_reason",
        ),
        # Logical de-duplication key.  config_revision is deliberately excluded:
        # editing a schedule must never re-run the same logical trigger point.
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="uq_schedule_occurrences_trigger"),
    )
    op.create_index("ix_schedule_occurrences_status", "schedule_occurrences", ["status", "scheduled_for"])

    # ---------------------------------------------------------------- publication
    op.create_table(
        "publication_packages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("edition_id", sa.String(36), nullable=False),
        sa.Column("render_id", sa.String(36)),
        sa.Column("composition_revision_id", sa.String(36)),
        sa.Column("composition_delivery_id", sa.String(36)),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("platform_code", sa.String(40)),
        sa.Column("platform_preset_version", sa.String(40)),
        sa.Column("preset_verified_at", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("rel_path", sa.Text()),
        sa.Column("zip_sha256", sa.String(64)),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("files_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("license_scope_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("license_blockers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("ai_disclosure_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("requested_territories_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("ready_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["explainer_editions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["render_id"], ["composition_renders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["composition_revision_id"], ["composition_revisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["composition_delivery_id"], ["composition_deliveries.id"], ondelete="SET NULL"),
        sa.CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_publication_packages_size"),
        sa.CheckConstraint(
            "status IN ('DRAFT','BUILDING','READY','NEEDS_MANUAL_PUBLISH','PUBLISHING','PUBLISHED',"
            "'PUBLICATION_RESULT_UNKNOWN','BLOCKED','FAILED','WITHDRAWN')",
            name="ck_publication_packages_status",
        ),
    )
    op.create_index("ix_publication_packages_video", "publication_packages", ["video_id"])

    op.create_table(
        "publication_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("package_id", sa.String(36), nullable=False),
        sa.Column("video_id", sa.String(36), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("publisher_adapter", sa.String(64)),
        sa.Column("platform_code", sa.String(40)),
        sa.Column("account_ref", sa.String(120)),
        sa.Column("package_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(36), nullable=False),
        sa.Column("remote_upload_session_id", sa.String(200)),
        sa.Column("remote_resource_id", sa.String(200)),
        sa.Column("remote_url", sa.Text()),
        sa.Column("response_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_detail_redacted", sa.Text()),
        sa.Column("handoff_note", sa.Text()),
        sa.Column("started_at", sa.Text()),
        sa.Column("finished_at", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["package_id"], ["publication_packages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["explainer_videos.id"], ondelete="CASCADE"),
        sa.CheckConstraint("attempt_no > 0", name="ck_publication_receipts_attempt"),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED','FAILED','TIMEOUT','NEEDS_MANUAL_PUBLISH','PUBLICATION_RESULT_UNKNOWN',"
            "'QUERIED_EXISTING','AUTHORIZATION_MISSING')",
            name="ck_publication_receipts_status",
        ),
        sa.UniqueConstraint("package_id", "platform_code", "account_ref", "attempt_no", name="uq_publication_receipts_attempt"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "explainer factory foundation is additive; restore the verified pre-migration backup "
        "with matching code instead of downgrading"
    )

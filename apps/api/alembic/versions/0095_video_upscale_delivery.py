"""Add project-level episode delivery upscaling foundations.

This migration deliberately keeps existing episode renders as ``COMPOSE``.
Super-resolution outputs are immutable derived renders and are only promoted
through an explicit delivery selection.
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa

from alembic import op

revision = "0095_video_upscale_delivery"
down_revision = "0094_project_target_duration"
branch_labels = None
depends_on = None


def _audit_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'system'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default=sa.text("'video-upscale.v1'")),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _seed_builtin_presets() -> None:
    presets = (
        (
            "builtin-upscale-anime-1080-standard",
            "ANIME_1080_STANDARD",
            "漫剧 1080p · 标准",
            {
                "target": {"mode": "FOLLOW_ORIENTATION_1080", "fit": "CONTAIN", "allow_cross_orientation": False},
                "scale_policy": "AUTO_NATIVE",
                "fps_policy": "PRESERVE_CFR",
                "audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC",
                "aac_bitrate_kbps": 192,
                "subtitle_policy": "INHERIT",
                "watermark_policy": "INHERIT_SOURCE_STATE",
                "encoder": "libx264",
                "crf": 18,
                "preset": "veryfast",
                "container": "mp4",
                "pix_fmt": "yuv420p",
                "source_policy": "PREFER_FINAL_DELIVERY",
                "existing_result_policy": "REUSE_EQUIVALENT",
                "chunk_frames": 240,
                "max_attempts": 2,
            },
            {
                "adapter_code": "ncnn.realesrgan.video.v1",
                "model_name": "realesr-animevideov3",
                "native_scales": [2, 3, 4],
                "tile_size": 0,
                "tta": False,
                "load_threads": 1,
                "proc_threads": 1,
                "save_threads": 2,
            },
        ),
        (
            "builtin-upscale-anime-1080-low-vram",
            "ANIME_1080_LOW_VRAM",
            "漫剧 1080p · 低显存",
            {
                "target": {"mode": "FOLLOW_ORIENTATION_1080", "fit": "CONTAIN", "allow_cross_orientation": False},
                "scale_policy": "AUTO_NATIVE",
                "fps_policy": "PRESERVE_CFR",
                "audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC",
                "aac_bitrate_kbps": 192,
                "subtitle_policy": "INHERIT",
                "watermark_policy": "INHERIT_SOURCE_STATE",
                "encoder": "libx264",
                "crf": 18,
                "preset": "veryfast",
                "container": "mp4",
                "pix_fmt": "yuv420p",
                "source_policy": "PREFER_FINAL_DELIVERY",
                "existing_result_policy": "REUSE_EQUIVALENT",
                "chunk_frames": 96,
                "max_attempts": 2,
            },
            {
                "adapter_code": "ncnn.realesrgan.video.v1",
                "model_name": "realesr-animevideov3",
                "native_scales": [2, 3, 4],
                "tile_size": 128,
                "tta": False,
                "load_threads": 1,
                "proc_threads": 1,
                "save_threads": 1,
            },
        ),
        (
            "builtin-upscale-anime-1080-detail",
            "ANIME_1080_DETAIL",
            "漫剧 1080p · 精细",
            {
                "target": {"mode": "FOLLOW_ORIENTATION_1080", "fit": "CONTAIN", "allow_cross_orientation": False},
                "scale_policy": "EXPLICIT_NATIVE",
                "native_scale": 4,
                "fps_policy": "PRESERVE_CFR",
                "audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC",
                "aac_bitrate_kbps": 192,
                "subtitle_policy": "INHERIT",
                "watermark_policy": "INHERIT_SOURCE_STATE",
                "encoder": "libx264",
                "crf": 16,
                "preset": "medium",
                "container": "mp4",
                "pix_fmt": "yuv420p",
                "source_policy": "PREFER_FINAL_DELIVERY",
                "existing_result_policy": "REUSE_EQUIVALENT",
                "chunk_frames": 240,
                "max_attempts": 2,
            },
            {
                "adapter_code": "ncnn.realesrgan.video.v1",
                "model_name": "realesrgan-x4plus-anime",
                "native_scales": [4],
                "tile_size": 0,
                "tta": False,
                "load_threads": 1,
                "proc_threads": 1,
                "save_threads": 2,
            },
        ),
        (
            "builtin-upscale-general-1080",
            "GENERAL_1080",
            "通用成片 1080p",
            {
                "target": {"mode": "FOLLOW_ORIENTATION_1080", "fit": "CONTAIN", "allow_cross_orientation": False},
                "scale_policy": "EXPLICIT_NATIVE",
                "native_scale": 4,
                "fps_policy": "PRESERVE_CFR",
                "audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC",
                "aac_bitrate_kbps": 192,
                "subtitle_policy": "INHERIT",
                "watermark_policy": "INHERIT_SOURCE_STATE",
                "encoder": "libx264",
                "crf": 18,
                "preset": "veryfast",
                "container": "mp4",
                "pix_fmt": "yuv420p",
                "source_policy": "PREFER_FINAL_DELIVERY",
                "existing_result_policy": "REUSE_EQUIVALENT",
                "chunk_frames": 240,
                "max_attempts": 2,
            },
            {
                "adapter_code": "ncnn.realesrgan.video.v1",
                "model_name": "realesrgan-x4plus",
                "native_scales": [4],
                "tile_size": 0,
                "tta": False,
                "load_threads": 1,
                "proc_threads": 1,
                "save_threads": 2,
            },
        ),
    )
    connection = op.get_bind()
    for preset_id, code, title, pipeline, model in presets:
        version_id = f"{preset_id}-v1"
        content_hash = hashlib.sha256(
            _canonical_json({"pipeline": pipeline, "model": model}).encode("utf-8")
        ).hexdigest()
        connection.execute(
            sa.text(
                """INSERT INTO video_upscale_presets
                (id,project_id,code,title,builtin,current_version_id,status,created_by)
                VALUES (:id,NULL,:code,:title,1,:version_id,'ACTIVE','migration')"""
            ),
            {"id": preset_id, "code": code, "title": title, "version_id": version_id},
        )
        connection.execute(
            sa.text(
                """INSERT INTO video_upscale_preset_versions
                (id,preset_id,version_no,profile_version_id,pipeline_options_json,model_options_json,
                 content_hash,parent_version_id,created_by)
                VALUES (:id,:preset_id,1,NULL,:pipeline,:model,:content_hash,NULL,'migration')"""
            ),
            {
                "id": version_id,
                "preset_id": preset_id,
                "pipeline": _canonical_json(pipeline),
                "model": _canonical_json(model),
                "content_hash": content_hash,
            },
        )


def upgrade() -> None:
    op.execute(
        """INSERT INTO job_stage_definitions(code,title,domain,active) VALUES
        ('VIDEO_UPSCALE_PREFLIGHT','视频超分预检','DELIVERY',1),
        ('VIDEO_UPSCALE','视频超分','DELIVERY',1)"""
    )
    op.create_table(
        "video_upscale_presets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(36)),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("builtin", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_version_id", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'ACTIVE'")),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "code", name="uq_video_upscale_presets_project_code"),
        sa.CheckConstraint("builtin IN (0,1)", name="ck_video_upscale_presets_builtin"),
    )
    op.create_table(
        "video_upscale_preset_versions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("preset_id", sa.String(64), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("profile_version_id", sa.String(36)),
        sa.Column("pipeline_options_json", sa.Text(), nullable=False),
        sa.Column("model_options_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parent_version_id", sa.String(80)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["preset_id"], ["video_upscale_presets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_version_id"], ["mp_execution_profile_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_version_id"], ["video_upscale_preset_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("preset_id", "version_no", name="uq_video_upscale_preset_versions_no"),
        sa.UniqueConstraint("preset_id", "content_hash", name="uq_video_upscale_preset_versions_hash"),
    )
    op.create_table(
        "project_upscale_settings",
        sa.Column("project_id", sa.String(36), primary_key=True),
        sa.Column("preset_version_id", sa.String(80), nullable=False),
        sa.Column("overrides_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["preset_version_id"], ["video_upscale_preset_versions.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "video_upscale_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("selection_snapshot_json", sa.Text(), nullable=False),
        sa.Column("check_job_id", sa.String(36)),
        sa.Column("items_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("plan_hash", sa.String(64)),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("supersedes_id", sa.String(36)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_detail", sa.Text()),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["check_job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["video_upscale_plans.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "request_hash", name="uq_video_upscale_plans_request"),
    )
    op.create_table(
        "video_upscale_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("preset_version_id", sa.String(80), nullable=False),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("control_state", sa.String(24), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["preset_version_id"], ["video_upscale_preset_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id"], ["video_upscale_plans.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_video_upscale_batches_idempotency"),
    )
    op.create_table(
        "video_upscale_batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_descriptor_json", sa.Text(), nullable=False),
        sa.Column("effective_options_json", sa.Text(), nullable=False),
        sa.Column("item_fingerprint", sa.String(64), nullable=False),
        sa.Column("current_run_id", sa.String(36)),
        sa.Column("participation_state", sa.String(24), nullable=False, server_default=sa.text("'ACTIVE'")),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["batch_id"], ["video_upscale_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("batch_id", "episode_id", name="uq_video_upscale_batch_items_episode"),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_video_upscale_batch_items_ordinal"),
    )
    op.create_table(
        "video_upscale_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("batch_item_id", sa.String(36)),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("execution_snapshot_id", sa.String(36)),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("variant_nonce", sa.String(100), nullable=False, server_default=""),
        sa.Column("source_render_id", sa.String(36), nullable=False),
        sa.Column("root_render_id", sa.String(36), nullable=False),
        sa.Column("output_render_id", sa.String(36)),
        sa.Column("sample_artifact_id", sa.String(36)),
        sa.Column("qc_run_id", sa.String(36)),
        sa.Column("progress_summary_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_item_id"], ["video_upscale_batch_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["execution_snapshot_id"], ["mp_execution_snapshots.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_render_id"], ["episode_render_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["root_render_id"], ["episode_render_versions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", name="uq_video_upscale_runs_job"),
        sa.UniqueConstraint("project_id", "fingerprint", "variant_nonce", name="uq_video_upscale_runs_fingerprint"),
    )
    op.create_table(
        "video_upscale_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_frame", sa.Integer(), nullable=False),
        sa.Column("end_frame_exclusive", sa.Integer(), nullable=False),
        sa.Column("source_manifest_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("output_rel", sa.Text()),
        sa.Column("output_sha256", sa.String(64)),
        sa.Column("frame_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_id", sa.String(36)),
        sa.Column("fencing_token", sa.String(128)),
        sa.Column("actual_parameters_json", sa.Text(), nullable=False, server_default="{}"),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["video_upscale_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attempt_id"], ["job_attempts.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_video_upscale_chunks_ordinal"),
        sa.CheckConstraint("start_frame >= 0 AND end_frame_exclusive > start_frame", name="ck_video_upscale_chunks_range"),
    )
    op.create_table(
        "episode_delivery_selections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("episode_id", sa.String(36), nullable=False),
        sa.Column("target_slot", sa.String(120), nullable=False),
        sa.Column("selected_render_id", sa.String(36), nullable=False),
        sa.Column("root_compose_render_id", sa.String(36), nullable=False),
        sa.Column("approval_id", sa.String(36), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_render_id"], ["episode_render_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["root_compose_render_id"], ["episode_render_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approval_id"], ["review_decisions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("episode_id", "target_slot", name="uq_episode_delivery_selections_slot"),
    )
    op.create_table(
        "video_upscale_delivery_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_item_id", sa.String(36), nullable=False),
        sa.Column("selected_render_id", sa.String(36), nullable=False),
        sa.Column("target_version_id", sa.String(36), nullable=False),
        sa.Column("delivery_fingerprint", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(36)),
        sa.Column("package_id", sa.String(36)),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["batch_item_id"], ["video_upscale_batch_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_render_id"], ["episode_render_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_version_id"], ["delivery_target_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["package_id"], ["delivery_packages.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("delivery_fingerprint", name="uq_video_upscale_delivery_links_fingerprint"),
    )

    op.add_column(
        "episode_render_versions",
        sa.Column("render_kind", sa.String(32), nullable=False, server_default=sa.text("'COMPOSE'")),
    )
    op.add_column("episode_render_versions", sa.Column("parent_render_version_id", sa.String(36)))
    op.add_column("episode_render_versions", sa.Column("upscale_run_id", sa.String(36)))
    op.add_column("episode_render_versions", sa.Column("derivation_fingerprint", sa.String(64)))

    op.create_index(
        "ix_episode_renders_episode_kind_created",
        "episode_render_versions",
        ["episode_id", "render_kind", "created_at", "id"],
    )
    op.create_index("ix_episode_renders_parent", "episode_render_versions", ["parent_render_version_id"])
    op.create_index("ix_episode_renders_derivation", "episode_render_versions", ["derivation_fingerprint"])
    op.create_index("uq_episode_renders_upscale_run", "episode_render_versions", ["upscale_run_id"], unique=True)
    op.create_index("ix_video_upscale_presets_scope", "video_upscale_presets", ["project_id", "status", "code"])
    op.create_index("ix_video_upscale_plans_project_status", "video_upscale_plans", ["project_id", "status", "created_at"])
    op.create_index("ix_video_upscale_batches_project_created", "video_upscale_batches", ["project_id", "created_at"])
    op.create_index("ix_video_upscale_batch_items_run", "video_upscale_batch_items", ["current_run_id"])
    op.create_index("ix_video_upscale_runs_project_created", "video_upscale_runs", ["project_id", "created_at"])
    op.create_index("ix_video_upscale_chunks_run_state", "video_upscale_chunks", ["run_id", "state", "ordinal"])
    op.create_index("ix_episode_delivery_selections_render", "episode_delivery_selections", ["selected_render_id"])

    _seed_builtin_presets()


def downgrade() -> None:
    raise RuntimeError("Video upscale delivery history is immutable; restore migration preflight backup")

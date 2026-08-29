"""Create the independent Model Platform V2 persistence foundation.

Revision ID: 0070_model_platform_v2_foundation
Revises: 0069_job_history_deletion
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from alembic import op
from local_drama.model_platform.domain.capabilities import CAPABILITY_DEFINITIONS

revision = "0070_model_platform_v2_foundation"
down_revision = "0069_job_history_deletion"
branch_labels = None
depends_on = None


def _id() -> sa.Column[sa.String]:
    return sa.Column("id", sa.String(36), primary_key=True)


def _timestamps() -> tuple[sa.Column[sa.Text], sa.Column[sa.Text]]:
    return (sa.Column("created_at", sa.Text(), nullable=False), sa.Column("updated_at", sa.Text(), nullable=False))


def upgrade() -> None:
    op.create_table("mp_compute_nodes", _id(), sa.Column("code", sa.String(80), nullable=False, unique=True), sa.Column("display_name", sa.Text(), nullable=False), sa.Column("fingerprint", sa.String(128), nullable=False, unique=True), sa.Column("host_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("last_seen_at", sa.Text()), *_timestamps())
    op.create_table("mp_model_libraries", _id(), sa.Column("node_id", sa.String(36), sa.ForeignKey("mp_compute_nodes.id"), nullable=False), sa.Column("code", sa.String(80), nullable=False), sa.Column("kind", sa.String(32), nullable=False), sa.Column("root_path_local", sa.Text(), nullable=False), sa.Column("managed", sa.Boolean(), nullable=False), sa.Column("read_only", sa.Boolean(), nullable=False), sa.Column("scan_policy_json", sa.Text(), nullable=False, server_default="{}"), *_timestamps(), sa.UniqueConstraint("node_id", "code", name="uq_mp_model_libraries_node_code"))
    op.create_table("mp_model_families", _id(), sa.Column("code", sa.String(120), nullable=False, unique=True), sa.Column("title", sa.Text(), nullable=False), sa.Column("vendor", sa.Text()), sa.Column("license_json", sa.Text(), nullable=False, server_default="{}"), *_timestamps())
    op.create_table("mp_model_releases", _id(), sa.Column("family_id", sa.String(36), sa.ForeignKey("mp_model_families.id"), nullable=False), sa.Column("code", sa.String(140), nullable=False, unique=True), sa.Column("upstream_id", sa.Text(), nullable=False), sa.Column("revision", sa.Text(), nullable=False), sa.Column("format", sa.String(40), nullable=False), sa.Column("quantization", sa.String(80)), sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"), *_timestamps(), sa.UniqueConstraint("family_id", "revision", "format", "quantization", name="uq_mp_model_releases_family_revision_format_quant"))
    op.create_table("mp_model_artifacts", _id(), sa.Column("kind", sa.String(32), nullable=False), sa.Column("content_sha256", sa.String(64), nullable=False), sa.Column("size_bytes", sa.BigInteger(), nullable=False), sa.Column("format", sa.String(40), nullable=False), sa.Column("manifest_json", sa.Text(), nullable=False, server_default="{}"), *_timestamps(), sa.UniqueConstraint("content_sha256", "size_bytes", name="uq_mp_model_artifacts_content"))
    op.create_table("mp_model_artifact_locations", _id(), sa.Column("artifact_id", sa.String(36), sa.ForeignKey("mp_model_artifacts.id"), nullable=False), sa.Column("library_id", sa.String(36), sa.ForeignKey("mp_model_libraries.id"), nullable=False), sa.Column("relative_path", sa.Text(), nullable=False), sa.Column("presence", sa.String(24), nullable=False, server_default="DISCOVERED"), sa.Column("observed_size_bytes", sa.BigInteger()), sa.Column("last_observed_at", sa.Text()), *_timestamps(), sa.UniqueConstraint("library_id", "relative_path", name="uq_mp_artifact_locations_library_path"))
    op.create_table("mp_model_components", _id(), sa.Column("release_id", sa.String(36), sa.ForeignKey("mp_model_releases.id"), nullable=False), sa.Column("artifact_id", sa.String(36), sa.ForeignKey("mp_model_artifacts.id"), nullable=False), sa.Column("role", sa.String(32), nullable=False), sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"), sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()), *_timestamps(), sa.UniqueConstraint("release_id", "artifact_id", "role", "ordinal", name="uq_mp_model_components_release_artifact_role"))
    op.create_table("mp_runtime_installations", _id(), sa.Column("node_id", sa.String(36), sa.ForeignKey("mp_compute_nodes.id"), nullable=False), sa.Column("code", sa.String(120), nullable=False), sa.Column("kind", sa.String(32), nullable=False), sa.Column("owner_mode", sa.String(24), nullable=False), sa.Column("display_name", sa.Text(), nullable=False), *_timestamps(), sa.UniqueConstraint("node_id", "code", name="uq_mp_runtime_installations_node_code"))
    op.create_table("mp_runtime_installation_versions", _id(), sa.Column("runtime_installation_id", sa.String(36), sa.ForeignKey("mp_runtime_installations.id"), nullable=False), sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("adapter_code", sa.String(120), nullable=False), sa.Column("adapter_version", sa.String(80), nullable=False), sa.Column("transport", sa.String(32), nullable=False), sa.Column("configuration_json", sa.Text(), nullable=False), sa.Column("fingerprint", sa.String(128), nullable=False), sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"), *_timestamps(), sa.UniqueConstraint("runtime_installation_id", "version_no", name="uq_mp_runtime_version_no"), sa.UniqueConstraint("runtime_installation_id", "fingerprint", name="uq_mp_runtime_version_fingerprint"))
    op.create_table("mp_runtime_instances", _id(), sa.Column("runtime_installation_version_id", sa.String(36), sa.ForeignKey("mp_runtime_installation_versions.id"), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("health_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("last_heartbeat_at", sa.Text()), *_timestamps())
    op.create_table("mp_runtime_model_installations", _id(), sa.Column("release_id", sa.String(36), sa.ForeignKey("mp_model_releases.id"), nullable=False), sa.Column("runtime_installation_version_id", sa.String(36), sa.ForeignKey("mp_runtime_installation_versions.id"), nullable=False), sa.Column("native_locator", sa.Text(), nullable=False), sa.Column("install_state", sa.String(24), nullable=False, server_default="DISCOVERED"), sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"), *_timestamps(), sa.UniqueConstraint("runtime_installation_version_id", "native_locator", name="uq_mp_runtime_model_locator"))
    op.create_table("mp_capability_definitions", _id(), sa.Column("code", sa.String(80), nullable=False, unique=True), sa.Column("title", sa.Text(), nullable=False), sa.Column("family", sa.String(32), nullable=False), sa.Column("input_modalities_json", sa.Text(), nullable=False), sa.Column("output_modalities_json", sa.Text(), nullable=False), sa.Column("business_surfaces_json", sa.Text(), nullable=False), sa.Column("background_only", sa.Boolean(), nullable=False, server_default=sa.false()), *_timestamps())
    capability_table = sa.table(
        "mp_capability_definitions",
        sa.column("id", sa.String()),
        sa.column("code", sa.String()),
        sa.column("title", sa.Text()),
        sa.column("family", sa.String()),
        sa.column("input_modalities_json", sa.Text()),
        sa.column("output_modalities_json", sa.Text()),
        sa.column("business_surfaces_json", sa.Text()),
        sa.column("background_only", sa.Boolean()),
        sa.column("created_at", sa.Text()),
        sa.column("updated_at", sa.Text()),
    )
    seed_time = "2026-08-29T00:00:00+00:00"
    op.bulk_insert(
        capability_table,
        [
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"localdramastudio:model-platform:capability:{item.code}")),
                "code": item.code,
                "title": item.title,
                "family": item.family.value,
                "input_modalities_json": json.dumps(item.input_modalities, ensure_ascii=False),
                "output_modalities_json": json.dumps(item.output_modalities, ensure_ascii=False),
                "business_surfaces_json": json.dumps(item.business_surfaces, ensure_ascii=False),
                "background_only": item.background_only,
                "created_at": seed_time,
                "updated_at": seed_time,
            }
            for item in CAPABILITY_DEFINITIONS.values()
        ],
    )
    op.create_table("mp_capability_offerings", _id(), sa.Column("runtime_model_installation_id", sa.String(36), sa.ForeignKey("mp_runtime_model_installations.id"), nullable=False), sa.Column("capability_definition_id", sa.String(36), sa.ForeignKey("mp_capability_definitions.id"), nullable=False), sa.Column("native_metadata_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("validation_status", sa.String(24), nullable=False, server_default="NOT_RUN"), *_timestamps(), sa.UniqueConstraint("runtime_model_installation_id", "capability_definition_id", name="uq_mp_offering_runtime_model_capability"))
    op.create_table("mp_parameter_contract_versions", _id(), sa.Column("capability_definition_id", sa.String(36), sa.ForeignKey("mp_capability_definitions.id"), nullable=False), sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("schema_json", sa.Text(), nullable=False), sa.Column("ui_schema_json", sa.Text(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False), *_timestamps(), sa.UniqueConstraint("capability_definition_id", "version_no", name="uq_mp_parameter_contract_version"), sa.UniqueConstraint("capability_definition_id", "content_hash", name="uq_mp_parameter_contract_hash"))
    op.create_table("mp_adapter_binding_contract_versions", _id(), sa.Column("runtime_kind", sa.String(32), nullable=False), sa.Column("adapter_code", sa.String(120), nullable=False), sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("binding_json", sa.Text(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False), *_timestamps(), sa.UniqueConstraint("runtime_kind", "adapter_code", "version_no", name="uq_mp_adapter_binding_version"))
    op.create_table("mp_resource_policy_versions", _id(), sa.Column("code", sa.String(100), nullable=False), sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("policy_json", sa.Text(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False), *_timestamps(), sa.UniqueConstraint("code", "version_no", name="uq_mp_resource_policy_version"))
    op.create_table("mp_execution_profiles", _id(), sa.Column("code", sa.String(180), nullable=False, unique=True), sa.Column("title", sa.Text(), nullable=False), *_timestamps())
    op.create_table("mp_execution_profile_versions", _id(), sa.Column("profile_id", sa.String(36), sa.ForeignKey("mp_execution_profiles.id"), nullable=False), sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("capability_definition_id", sa.String(36), sa.ForeignKey("mp_capability_definitions.id"), nullable=False), sa.Column("runtime_installation_version_id", sa.String(36), sa.ForeignKey("mp_runtime_installation_versions.id"), nullable=False), sa.Column("parameter_contract_version_id", sa.String(36), sa.ForeignKey("mp_parameter_contract_versions.id"), nullable=False), sa.Column("adapter_binding_contract_version_id", sa.String(36), sa.ForeignKey("mp_adapter_binding_contract_versions.id"), nullable=False), sa.Column("resource_policy_version_id", sa.String(36), sa.ForeignKey("mp_resource_policy_versions.id"), nullable=False), sa.Column("workflow_version_id", sa.String(36), nullable=True), sa.Column("payload_json", sa.Text(), nullable=False), sa.Column("payload_hash", sa.String(64), nullable=False), *_timestamps(), sa.UniqueConstraint("profile_id", "version_no", name="uq_mp_profile_version_no"), sa.UniqueConstraint("profile_id", "payload_hash", name="uq_mp_profile_payload_hash"))
    op.create_table("mp_profile_publications", _id(), sa.Column("execution_profile_version_id", sa.String(36), sa.ForeignKey("mp_execution_profile_versions.id"), nullable=False, unique=True), sa.Column("status", sa.String(24), nullable=False), sa.Column("validation_run_id", sa.String(36)), sa.Column("published_at", sa.Text()), sa.Column("retired_at", sa.Text()), sa.Column("reason", sa.Text()), *_timestamps())
    op.create_table("mp_capability_assignments", _id(), sa.Column("scope_type", sa.String(24), nullable=False), sa.Column("scope_id", sa.String(36), nullable=False, server_default=""), sa.Column("capability_definition_id", sa.String(36), sa.ForeignKey("mp_capability_definitions.id"), nullable=False), sa.Column("resolution_mode", sa.String(16), nullable=False), sa.Column("execution_profile_version_id", sa.String(36), sa.ForeignKey("mp_execution_profile_versions.id")), sa.Column("override_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("revision", sa.Integer(), nullable=False, server_default="1"), *_timestamps(), sa.UniqueConstraint("scope_type", "scope_id", "capability_definition_id", name="uq_mp_capability_assignment_scope"))
    op.create_table("mp_discovery_runs", _id(), sa.Column("library_id", sa.String(36), sa.ForeignKey("mp_model_libraries.id")), sa.Column("runtime_installation_version_id", sa.String(36), sa.ForeignKey("mp_runtime_installation_versions.id")), sa.Column("source", sa.String(32), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("started_at", sa.Text(), nullable=False), sa.Column("finished_at", sa.Text()), *_timestamps())
    op.create_table("mp_discovery_observations", _id(), sa.Column("discovery_run_id", sa.String(36), sa.ForeignKey("mp_discovery_runs.id"), nullable=False), sa.Column("native_id", sa.Text(), nullable=False), sa.Column("kind", sa.String(32), nullable=False), sa.Column("observed_json", sa.Text(), nullable=False), sa.Column("content_hint", sa.String(128)), sa.Column("status", sa.String(24), nullable=False), *_timestamps(), sa.UniqueConstraint("discovery_run_id", "native_id", name="uq_mp_discovery_observation_native"))
    op.create_table("mp_validation_runs", _id(), sa.Column("target_kind", sa.String(40), nullable=False), sa.Column("target_id", sa.String(36), nullable=False), sa.Column("validation_kind", sa.String(40), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("started_at", sa.Text(), nullable=False), sa.Column("finished_at", sa.Text()), *_timestamps())
    op.create_table("mp_validation_evidence", _id(), sa.Column("validation_run_id", sa.String(36), sa.ForeignKey("mp_validation_runs.id"), nullable=False), sa.Column("kind", sa.String(40), nullable=False), sa.Column("content_hash", sa.String(64)), sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("artifact_ref", sa.String(36)), *_timestamps())
    op.create_table("mp_install_plans", _id(), sa.Column("target_library_id", sa.String(36), sa.ForeignKey("mp_model_libraries.id"), nullable=False), sa.Column("source_json", sa.Text(), nullable=False), sa.Column("expected_json", sa.Text(), nullable=False), sa.Column("license_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("status", sa.String(24), nullable=False), *_timestamps())
    op.create_table("mp_install_jobs", _id(), sa.Column("install_plan_id", sa.String(36), sa.ForeignKey("mp_install_plans.id"), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("progress_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("error_redacted", sa.Text()), sa.Column("started_at", sa.Text()), sa.Column("finished_at", sa.Text()), *_timestamps())

    for table, columns, name in (
        ("mp_model_artifact_locations", ["artifact_id"], "ix_mp_artifact_locations_artifact"),
        ("mp_model_components", ["release_id"], "ix_mp_model_components_release"),
        ("mp_runtime_model_installations", ["release_id", "install_state"], "ix_mp_runtime_models_release_state"),
        ("mp_capability_offerings", ["capability_definition_id", "validation_status"], "ix_mp_offerings_capability_status"),
        ("mp_execution_profile_versions", ["capability_definition_id"], "ix_mp_profile_versions_capability"),
        ("mp_capability_assignments", ["scope_type", "scope_id"], "ix_mp_assignments_scope"),
        ("mp_discovery_runs", ["status", "started_at"], "ix_mp_discovery_runs_status_started"),
        ("mp_validation_runs", ["target_kind", "target_id", "status"], "ix_mp_validation_runs_target_status"),
        ("mp_install_jobs", ["status", "updated_at"], "ix_mp_install_jobs_status_updated"),
    ):
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table in (
        ("ix_mp_install_jobs_status_updated", "mp_install_jobs"),
        ("ix_mp_validation_runs_target_status", "mp_validation_runs"),
        ("ix_mp_discovery_runs_status_started", "mp_discovery_runs"),
        ("ix_mp_assignments_scope", "mp_capability_assignments"),
        ("ix_mp_profile_versions_capability", "mp_execution_profile_versions"),
        ("ix_mp_offerings_capability_status", "mp_capability_offerings"),
        ("ix_mp_runtime_models_release_state", "mp_runtime_model_installations"),
        ("ix_mp_model_components_release", "mp_model_components"),
        ("ix_mp_artifact_locations_artifact", "mp_model_artifact_locations"),
    ):
        op.drop_index(name, table_name=table)
    for table in (
        "mp_install_jobs", "mp_install_plans", "mp_validation_evidence", "mp_validation_runs",
        "mp_discovery_observations", "mp_discovery_runs", "mp_capability_assignments",
        "mp_profile_publications", "mp_execution_profile_versions", "mp_execution_profiles",
        "mp_resource_policy_versions", "mp_adapter_binding_contract_versions",
        "mp_parameter_contract_versions", "mp_capability_offerings", "mp_capability_definitions",
        "mp_runtime_model_installations", "mp_runtime_instances", "mp_runtime_installation_versions",
        "mp_runtime_installations", "mp_model_components", "mp_model_artifact_locations",
        "mp_model_artifacts", "mp_model_releases", "mp_model_families", "mp_model_libraries",
        "mp_compute_nodes",
    ):
        op.drop_table(table)

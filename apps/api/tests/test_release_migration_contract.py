from __future__ import annotations

import json
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from local_drama.application.project_packages import PACKAGE_SCHEMA, STATE_SCHEMA

ROOT = Path(__file__).resolve().parents[3]


def test_release_migration_contract_matches_graph_and_package_authority() -> None:
    contract = json.loads((ROOT / "docs" / "release" / "migration-contract.json").read_text(encoding="utf-8"))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps" / "api" / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert contract["expected_heads"] == sorted(scripts.get_heads())
    assert contract["project_package"] == {
        "manifest_schema": PACKAGE_SCHEMA,
        "state_schema": STATE_SCHEMA,
        "older_missing_optional_fields": "safe_defaults",
        "unsupported_schema": "PROJECT_PACKAGE_SCHEMA_UNSUPPORTED",
    }
    revisions = [item["revision"] for item in contract["release_migrations"]]
    assert revisions == [
        "0042_asset_bible_states_references",
        "0043_generation_preferences",
        "0044_shot_groups",
        "0045_qc_policies",
        "0046_director_recipes",
        "0047_shot_editing",
        "0048_asset_proposals",
        "0049_canonical_capabilities",
        "0050_character_identity_packs",
        "0051_worker_sessions",
        "0052_storage_operations",
        "0053_canonical_capability_repair",
        "0054_character_identity_pack_hardening",
        "0055_breakdown_scene_applications",
        "0056_breakdown_draft_revisions",
        "0057_provider_connections",
        "0058_one_sentence_video_runs",
        "0059_automation_workflow_versions",
        "0060_visual_lab_runtime_foundation",
        "0061_shot_working_media_slots",
        "0062_canonical_job_scope_stage",
        "0063_audio_mix_drafts",
        "0064_quick_generation_domain",
        "0065_quick_generation_parameters",
        "0066_action_driven_quick_generation",
        "0067_single_gpu_runtime_orchestration",
        "0068_local_ai_model_runtime",
        "0069_job_history_deletion",
        "0070_model_platform_v2_foundation",
        "0070_adaptation_planning_foundation",
        "0071_model_platform_adaptation_merge",
        "0072_adaptation_analysis_job_stage",
        "0073_model_platform_execution_snapshots",
        "0073_adaptation_plan_materialization",
        "0074_model_platform_execution_jobs",
        "0075_model_platform_adaptation_execution_merge",
        "0076_model_platform_snapshot_runtime_configuration",
        "0077_model_platform_snapshot_model_bindings",
        "0078_model_platform_comfy_workflow_bindings",
        "0079_model_platform_comfy_smoke_jobs",
        "0080_model_platform_snapshot_execution_binding",
        "0081_model_platform_profile_version_crosswalks",
        "0082_model_platform_business_selection_rollouts",
        "0083_model_platform_scope_override_set_versions",
        "0084_model_platform_project_knowledge_indexes",
        "0085_model_platform_project_knowledge_retry_attempts",
        "0086_model_platform_quick_create_v2_runs",
        "0087_gpu_runtime_llama_cpp",
        "0088_pipeline_runs",
        "0089_pipeline_llm_mode",
        "0090_pipeline_draft_review",
        "0091_asset_image_generation_batches",
        "0092_shot_keyframe_generation_batches",
        "0093_shot_prompt_bundle_snapshots",
        "0094_project_target_duration",
    ]
    graph = {revision.revision for revision in scripts.walk_revisions()}
    assert set(revisions).issubset(graph)


def test_release_runbooks_use_contract_instead_of_stale_current_head() -> None:
    install = (ROOT / "docs" / "release" / "install-upgrade-rollback.md").read_text(encoding="utf-8")
    go_no_go = (ROOT / "docs" / "release" / "go-no-go.md").read_text(encoding="utf-8")
    rehearsal = (ROOT / "scripts" / "release_rehearsal.py").read_text(encoding="utf-8")
    assert "migration-contract.json.expected_heads" in install
    assert "恢复库 migration revision 等于备份时记录的 revision" in install
    assert "0048_asset_proposals" in go_no_go
    assert "schema_validation_status: REHEARSAL_REQUIRED" in go_no_go
    assert "历史冻结决策（2026-08-17）" in go_no_go
    assert 'default=ROOT / "docs" / "evidence" / "g10" / "upgrade-rollback-rehearsal-0039' not in rehearsal
    assert 'f"upgrade-rollback-rehearsal-{head_label}-{date_label}.json"' in rehearsal

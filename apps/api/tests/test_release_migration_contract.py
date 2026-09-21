from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from scripts import release_audit

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
        "0095_video_upscale_delivery",
        "0096_production_sessions",
        "0097_video_upscale_previews",
        "0098_production_session_identity_inputs",
        "0099_production_session_asset_inputs",
        "0100_production_session_waiting_user",
    ]
    graph = {revision.revision for revision in scripts.walk_revisions()}
    assert set(revisions).issubset(graph)


def test_release_runbooks_use_contract_instead_of_stale_current_head() -> None:
    install = (ROOT / "docs" / "release" / "install-upgrade-rollback.md").read_text(encoding="utf-8")
    go_no_go = (ROOT / "docs" / "release" / "go-no-go.md").read_text(encoding="utf-8")
    rehearsal = (ROOT / "scripts" / "release_rehearsal.py").read_text(encoding="utf-8")
    audit = (ROOT / "scripts" / "release_audit.py").read_text(encoding="utf-8")
    assert "migration-contract.json.expected_heads" in install
    assert "恢复库 migration revision 等于备份时记录的 revision" in install
    assert "0048_asset_proposals" in go_no_go
    assert "schema_validation_status: REHEARSAL_REQUIRED" in go_no_go
    assert "历史冻结决策（2026-08-17）" in go_no_go
    assert 'default=ROOT / "docs" / "evidence" / "g10" / "upgrade-rollback-rehearsal-0039' not in rehearsal
    assert 'f"upgrade-rollback-rehearsal-{head_label}-{date_label}.json"' in rehearsal
    assert "0041_character_voice_bindings" not in audit
    assert "0031_project_asset_grants" not in audit
    assert "0039_automation_task_jobs" not in audit
    assert "MIGRATION_CONTRACT_PATH" in audit
    assert 'f"release-readiness-{date_label}.json"' in audit


def test_release_audit_accepts_only_rehearsal_for_contract_heads(tmp_path, monkeypatch) -> None:
    contract = tmp_path / "migration-contract.json"
    contract.write_text(
        json.dumps({"expected_heads": ["0100_production_session_waiting_user"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(release_audit, "MIGRATION_CONTRACT_PATH", contract)
    evidence = {
        "status": "PASS",
        "source_backup": {"integrity": "ok", "migration_heads": ["0093_shot_prompt_bundle_snapshots"]},
        "upgrade_copy": {
            "integrity": "ok",
            "migration_heads": ["0100_production_session_waiting_user"],
            "expected_heads": ["0100_production_session_waiting_user"],
        },
        "restore_copy": {"integrity": "ok", "matches_source_sha256": True},
        "safety": {
            "production_database_mutated": False,
            "comfyui_contacted": False,
            "network_contacted": False,
            "jobs_created": False,
        },
    }
    path = tmp_path / "rehearsal.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    assert release_audit._rehearsal_passed(path) is True

    evidence["upgrade_copy"]["migration_heads"] = ["0099_production_session_asset_inputs"]
    path.write_text(json.dumps(evidence), encoding="utf-8")
    assert release_audit._rehearsal_passed(path) is False


def test_release_audit_reports_old_database_instead_of_querying_new_schema(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO alembic_version (version_num) VALUES ('0093_shot_prompt_bundle_snapshots')"
        )
        connection.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
        connection.execute("INSERT INTO projects VALUES ('old-project','2026-09-21T00:00:00Z')")

    monkeypatch.setattr(release_audit, "DB_PATH", database_path)
    monkeypatch.setattr(
        release_audit,
        "_expected_migration_heads",
        lambda: ["0100_production_session_waiting_user"],
    )
    result = release_audit.audit()

    migration = next(item for item in result["checks"] if item["code"] == "MIGRATION_HEAD")
    assert result["status"] == "IN_PROGRESS"
    assert migration["passed"] is False
    assert migration["observed"] == ["0093_shot_prompt_bundle_snapshots"]
    assert migration["expected"] == ["0100_production_session_waiting_user"]
    assert next(item for item in result["checks"] if item["code"] == "ORDERED_G7")["observed"] == "NOT_EVALUATED"

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

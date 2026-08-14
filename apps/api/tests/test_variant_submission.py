from __future__ import annotations

import json

import pytest

from local_drama.application.generation import GenerationService
from local_drama.application.profiles import ProfileService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan


def test_confirmed_variant_and_gpu_job_are_atomic(workspace, database) -> None:
    # Reuse the published profile fixture pattern through the manifest candidate,
    # then bind a minimal published workflow contract in the isolated database.
    profile = next(item for item in ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"] if item["capability"] == "T2V")
    with database.transaction() as connection:
        project_id = connection.execute("SELECT id FROM projects LIMIT 1").fetchone()
    if project_id is None:
        from local_drama.application.projects import ProjectService

        project = ProjectService(database, workspace.projects_root).create_project(
            code="variant_submit", title="Variant submit", episode_count=1, aspect_ratio="16:9",
            fps_num=24, fps_den=1, target_duration_ms=60000, allow_unconfigured_capabilities=True,
        )
        project_value = str(project["id"])
    else:
        project_value = str(project_id[0])
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"prompt": "", "seed": 0}}
    }
    from local_drama.application.workflows import WorkflowService

    workflows = WorkflowService(database, workspace)
    version = workflows.register_package(
        "variant_submit_workflow", "Variant submit", workflow, {},
        {"PROMPT": {"node_id": "1", "input": "prompt"}, "SEED": {"node_id": "1", "input": "seed"}},
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (version["id"],))
        connection.execute(
            """UPDATE execution_profile_versions SET status='PUBLISHED', workflow_version_id=?,
            input_contract_json=?, revision=revision+1 WHERE id=?""",
            (version["id"], json.dumps({"input_slots": {}}), profile["version_id"]),
        )
    service = GenerationService(database, workspace)
    intent = service.create_intent(project_value, "PROJECT", project_value, "T2V", "four real proxy choices")
    plan = VariantPlan(
        variant_type="BASE", parent_variant_id=None, branch_reason="take one", prompt_revision_id=None,
        profile_version_id=str(profile["version_id"]),
        parameter_set={"PROMPT": "candle", "SEED": 101}, seed_policy="EXPLICIT", explicit_seed=101, bindings=(),
    )
    preflight = service.preflight_variant(str(intent["id"]), plan)
    submitted = service.submit_confirmed_variant(str(intent["id"]), plan, str(preflight["plan_hash"]), "variant-submit-1")
    assert submitted["variant"]["status"] == "QUEUED"
    assert submitted["job"]["channel"] == "GPU_H3"
    assert submitted["job"]["subject_id"] == submitted["variant"]["id"]
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE subject_id=?", (submitted["variant"]["id"],)).fetchone()[0] == 1

    mismatched = VariantPlan(
        variant_type="BASE", parent_variant_id=None, branch_reason="mismatched seed", prompt_revision_id=None,
        profile_version_id=str(profile["version_id"]), parameter_set={"PROMPT": "candle", "SEED": 102},
        seed_policy="EXPLICIT", explicit_seed=103, bindings=(),
    )
    with pytest.raises(DomainRuleError) as error:
        service.preflight_variant(str(intent["id"]), mismatched)
    assert error.value.code == "VARIANT_SEED_SNAPSHOT_MISMATCH"

from __future__ import annotations

import json

import pytest

from local_drama.application.creative_entries import CreativeEntryService
from local_drama.application.generation import GenerationService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.workflows import WorkflowService
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan


def _subject(workspace, database, code: str) -> tuple[dict, dict]:
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    season = projects.list_seasons(str(project["id"]))[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    return project, shot


def _profile(workspace, database) -> str:
    profile = next(
        item
        for item in ProfileService(database, workspace.manifest_path).sync_manifest()["profiles"]
        if item["capability"] == "VIDEO_T2V"
    )
    workflow = {"1": {"class_type": "LoadImage", "inputs": {"prompt": "", "seed": 0}}}
    version = WorkflowService(database, workspace).register_package(
        "style_context_workflow",
        "Style context",
        workflow,
        {},
        {"PROMPT": {"node_id": "1", "input": "prompt"}, "SEED": {"node_id": "1", "input": "seed"}},
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (version["id"],))
        connection.execute(
            """UPDATE execution_profile_versions SET status='PUBLISHED',workflow_version_id=?,
            input_contract_json=?,revision=revision+1 WHERE id=?""",
            (version["id"], json.dumps({"input_slots": {}}), profile["version_id"]),
        )
    return str(profile["version_id"])


def _plan(profile_id: str) -> VariantPlan:
    return VariantPlan(
        variant_type="BASE",
        parent_variant_id=None,
        branch_reason="style snapshot",
        prompt_revision_id=None,
        profile_version_id=profile_id,
        parameter_set={"PROMPT": "雨夜街道", "SEED": 7},
        seed_policy="EXPLICIT",
        explicit_seed=7,
        bindings=(),
    )


def test_submit_freezes_declarative_style_and_brand_context(workspace, database) -> None:
    project, shot = _subject(workspace, database, "style_snapshot")
    project_id = str(project["id"])
    WorkspaceAssetService(database, workspace).create_brand_kit(
        project_id,
        "VISUAL",
        "视觉品牌",
        {
            "colors": {"primary": "#A04030", "command": "rm -rf /"},
            "typography": {"mood": "克制"},
            "delivery": {"shell": "powershell"},
        },
    )
    CreativeEntryService(database).create(
        project_id,
        "STYLE",
        "STYLE_NOIR",
        "水墨黑色电影",
        {
            "visual_style": "水墨黑色电影",
            "palette": ["黛青", "朱红"],
            "style": {"lighting": "低调侧光", "executor": "python"},
            "negative_prompt": ["塑料质感"],
            "python": "import os",
        },
        "建立生成风格",
    )
    service = GenerationService(database, workspace)
    intent = service.create_intent(project_id, "SHOT", str(shot["id"]), "T2V", "style context")
    plan = _plan(_profile(workspace, database))

    preflight = service.preflight_variant(str(intent["id"]), plan)
    assert preflight["style_context"] == {
        "context_hash": preflight["style_context"]["context_hash"],
        "brand_kit_count": 1,
        "style_entry_count": 1,
        "style_reference_count": 0,
    }
    submitted = service.submit_confirmed_variant(
        str(intent["id"]), plan, str(preflight["plan_hash"]), "style-context-submit"
    )

    snapshot = submitted["job"]["input_snapshot"]
    context = snapshot["style_context"]
    assert context["context_hash"] == preflight["style_context"]["context_hash"]
    assert context["brand_kits"][0]["version_no"] == 1
    assert context["style_entries"][0]["revision_no"] == 1
    serialized = json.dumps(context, ensure_ascii=False)
    for forbidden in ("command", "rm -rf", "shell", "powershell", "executor", "python", "import os", "delivery"):
        assert forbidden not in serialized
    assert "水墨黑色电影" in snapshot["semantic_inputs"]["PROMPT"]
    assert "#A04030" in snapshot["semantic_inputs"]["PROMPT"]
    assert "negative_prompt_context" in context
    assert snapshot["recipe_hash"] == submitted["variant"]["recipe_hash"] == preflight["recipe_hash"]
    assert submitted["variant"]["input_fingerprint"]

    with database.connect() as connection:
        audit = connection.execute(
            "SELECT metadata_redacted_json FROM audit_events WHERE action='GENERATION_STYLE_CONTEXT_FROZEN' AND subject_id=?",
            (submitted["variant"]["id"],),
        ).fetchone()
    assert json.loads(str(audit["metadata_redacted_json"]))["style_context_hash"] == context["context_hash"]

    WorkspaceAssetService(database, workspace).create_brand_kit(
        project_id, "VISUAL", "视觉品牌", {"colors": {"primary": "#203040"}}
    )
    with pytest.raises(DomainRuleError) as replay_error:
        service.derive_variant_plan(
            str(submitted["variant"]["id"]), "EXACT_REPLAY", branch_reason="same inputs"
        )
    assert replay_error.value.code == "EXACT_REPLAY_STYLE_CONTEXT_MISMATCH"


def test_brand_version_change_invalidates_preflight_plan(workspace, database) -> None:
    project, shot = _subject(workspace, database, "style_stale")
    project_id = str(project["id"])
    brands = WorkspaceAssetService(database, workspace)
    brands.create_brand_kit(project_id, "VISUAL", "视觉品牌", {"colors": {"primary": "#111111"}})
    service = GenerationService(database, workspace)
    intent = service.create_intent(project_id, "SHOT", str(shot["id"]), "T2V", "style stale")
    plan = _plan(_profile(workspace, database))
    preflight = service.preflight_variant(str(intent["id"]), plan)

    brands.create_brand_kit(project_id, "VISUAL", "视觉品牌", {"colors": {"primary": "#222222"}})
    with pytest.raises(DomainRuleError) as caught:
        service.submit_confirmed_variant(str(intent["id"]), plan, str(preflight["plan_hash"]), "style-stale")
    assert caught.value.code == "VARIANT_PLAN_STALE"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_variants WHERE intent_id=?", (intent["id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs WHERE project_id=?", (project_id,)).fetchone()[0] == 0

"""Production-scale guard for published image routes.

A cheap 256x256 / 1-step *verification smoke* workflow must never become the
real production default for asset image generation.  These tests pin the three
entry points that could select it: the explicit-profile branch of the asset
image batch service, its auto-selection branch, and the global ``AUTO``
fallback of ``SqliteGenerationPreferenceRepository.auto_profile()``.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.asset_image_generation import AssetImageGenerationBatchService
from local_drama.application.projects import ProjectService
from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.workflow_contracts import (
    image_workflow_is_production_grade,
    image_workflow_production_scale,
)
from local_drama.application.workflows import WorkflowService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.infrastructure.service_composition import build_asset_image_batch
from local_drama.main import create_app

#: Mirrors the live ``qwen-image-2512-q5-smoke`` v1 graph: a 256x256 latent, a
#: single sampler step and a ``verification/`` output prefix.
SMOKE_NODES = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "local-model.safetensors"}},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 256, "height": 256, "batch_size": 1}},
    "8": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 1, "cfg": 4.0, "latent_image": ["7", 0]}},
    "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "verification/qwen-image-2512-q5", "images": ["8", 0]}},
}
SMOKE_BINDINGS = {
    "PROMPT": {"node_id": "5", "input": "text"},
    "OUTPUT_PREFIX": {"node_id": "10", "input": "filename_prefix"},
}
SMOKE_BINDINGS_WITH_SEED = {**SMOKE_BINDINGS, "SEED": {"node_id": "8", "input": "seed"}}

#: A genuine production text-to-image route: the runtime owns steps and size.
PRODUCTION_NODES = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "local-model.safetensors"}},
    "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 480, "height": 832, "batch_size": 1}},
    "8": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 4.0, "latent_image": ["7", 0]}},
    "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "local_drama/asset_hero", "images": ["8", 0]}},
}
PRODUCTION_BINDINGS = {
    "PROMPT": {"node_id": "5", "input": "text"},
    "SEED": {"node_id": "8", "input": "seed"},
    "STEPS": {"node_id": "8", "input": "steps"},
    "WIDTH": {"node_id": "7", "input": "width"},
    "HEIGHT": {"node_id": "7", "input": "height"},
    "OUTPUT_PREFIX": {"node_id": "10", "input": "filename_prefix"},
}


def _project(workspace, database, code: str) -> str:
    project = ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    return str(project["id"])


def _character(workspace, database, project_id: str, code: str) -> str:
    asset = StoryAssetService(database, workspace).create_asset(
        project_id, "CHARACTER", code, f"{code}角色", description="黑衣刀客"
    )
    return str(asset["id"])


def _register_published_workflow(
    workspace,
    database,
    code: str,
    *,
    nodes: dict,
    bindings: dict,
) -> str:
    workflow = WorkflowService(database, workspace).register_package(code, code, nodes, {}, bindings)
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='PUBLISHED' WHERE id=?", (workflow["id"],))
    return str(workflow["id"])


def _insert_profile_version(
    database,
    code: str,
    *,
    version_no: int,
    capability: str,
    workflow_version_id: str | None,
    updated_at: str,
    status: str = "PUBLISHED",
    input_slots: dict | None = None,
) -> str:
    profile_id = f"profile-{code}-{version_no}"
    version_id = f"version-{code}-{version_no}"
    slots = {"PROMPT": {"min": 1, "max": 1}, "OUTPUT_PREFIX": {"min": 0, "max": 1}} if input_slots is None else input_slots
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO execution_profiles
            (id, code, title, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'test', 1, 'v2')""",
            (profile_id, code, code),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, workflow_version_id, model_bundle_json,
             input_contract_json, parameter_schema_json, status, manifest_sha256, capability_json, worker_policy,
             runtime_version_id, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, ?, ?, '{"model":"local","provider":"LOCAL"}', ?, '{}', ?, NULL, '{}',
                    'ONE_LOCAL_LLM_TASK', NULL, '2026-01-01T00:00:00+00:00', ?, 'test', 1, 'v2')""",
            (version_id, profile_id, version_no, capability, workflow_version_id, json.dumps({"input_slots": slots}), status, updated_at),
        )
    return version_id


def _published_smoke_profile(workspace, database, code: str, *, updated_at: str = "2026-02-01T00:00:00+00:00") -> str:
    workflow_version_id = _register_published_workflow(
        workspace, database, code, nodes=SMOKE_NODES, bindings=SMOKE_BINDINGS_WITH_SEED
    )
    return _insert_profile_version(
        database,
        code,
        version_no=1,
        capability="IMAGE_CHARACTER",
        workflow_version_id=workflow_version_id,
        updated_at=updated_at,
    )


def _published_production_profile(workspace, database, code: str, *, updated_at: str = "2026-01-01T00:00:00+00:00") -> str:
    workflow_version_id = _register_published_workflow(
        workspace, database, code, nodes=PRODUCTION_NODES, bindings=PRODUCTION_BINDINGS
    )
    return _insert_profile_version(
        database,
        code,
        version_no=1,
        capability="IMAGE_CHARACTER",
        workflow_version_id=workflow_version_id,
        updated_at=updated_at,
    )


def test_explicit_profile_rejects_verification_smoke_route(workspace, database) -> None:
    project_id = _project(workspace, database, "prod_grade_explicit_smoke")
    asset_id = _character(workspace, database, project_id, "CHAR_SMOKE")
    # The live smoke route binds no SEED at all; the guard must not depend on
    # that accident of the graph, so both binding shapes are covered.
    smoke_version_id = _insert_profile_version(
        database,
        "asset-image-smoke-exact",
        version_no=1,
        capability="IMAGE_CHARACTER",
        workflow_version_id=_register_published_workflow(
            workspace, database, "asset-image-smoke-exact", nodes=SMOKE_NODES, bindings=SMOKE_BINDINGS
        ),
        updated_at="2026-02-01T00:00:00+00:00",
    )
    smoke_with_seed_id = _published_smoke_profile(workspace, database, "asset-image-smoke-seeded")

    for profile_version_id in (smoke_version_id, smoke_with_seed_id):
        service = build_asset_image_batch(database, workspace)
        with pytest.raises(DomainRuleError) as caught:
            service.plan(
                project_id,
                asset_kind="CHARACTER",
                asset_ids=[asset_id],
                profile_version_id=profile_version_id,
            )
        assert caught.value.code == "ASSET_IMAGE_PROFILE_NOT_PRODUCTION_GRADE"
        assert caught.value.details["profile_version_id"] == profile_version_id
        assert caught.value.details["workflow_version_id"]
        assert caught.value.details["scale"]["production_grade"] is False
        assert caught.value.details["scale"]["reason"] == "SMOKE_SCALE"
        assert caught.value.details["scale"]["sampler_steps"] == 1
        assert caught.value.details["scale"]["latent_max_edge"] == 256

        # Submission fails closed on the same route and creates no batch rows.
        with pytest.raises(DomainRuleError) as submit_error:
            service.submit(
                project_id,
                asset_kind="CHARACTER",
                asset_ids=[asset_id],
                profile_version_id=profile_version_id,
                expected_plan_hash="0" * 64,
                idempotency_key=f"smoke-{profile_version_id}",
            )
        assert submit_error.value.code == "ASSET_IMAGE_PROFILE_NOT_PRODUCTION_GRADE"

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM asset_image_generation_batches").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM generation_intents WHERE purpose='ASSET_HERO_IMAGE'").fetchone()[0] == 0


def test_asset_image_batch_api_rejects_explicit_smoke_route(workspace, database) -> None:
    """The web UI sends an explicit profile id; the API must fail closed on it."""

    project_id = _project(workspace, database, "prod_grade_api_smoke")
    asset_id = _character(workspace, database, project_id, "CHAR_API_SMOKE")
    smoke_profile_id = _published_smoke_profile(workspace, database, "asset-image-api-smoke")

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/asset-image-batches:plan",
            json={
                "asset_kind": "CHARACTER",
                "asset_ids": [asset_id],
                "profile_version_id": smoke_profile_id,
                "mode": "MISSING_ONLY",
            },
        )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "ASSET_IMAGE_PROFILE_NOT_PRODUCTION_GRADE"
    assert error["details"]["profile_version_id"] == smoke_profile_id
    assert error["details"]["workflow_version_id"]
    assert error["details"]["scale"]["reason"] == "SMOKE_SCALE"


def test_explicit_profile_rejects_route_without_scale_evidence(workspace, database) -> None:
    project_id = _project(workspace, database, "prod_grade_no_evidence")
    asset_id = _character(workspace, database, project_id, "CHAR_NO_EVIDENCE")
    # Semantically complete (PROMPT + SEED) but the graph proves nothing about
    # its render scale: under-specified, so it must not be used for real assets.
    workflow_version_id = _register_published_workflow(
        workspace,
        database,
        "asset-image-no-scale-evidence",
        nodes={"1": {"class_type": "SaveImage", "inputs": {"prompt": "", "seed": 0, "filename_prefix": ""}}},
        bindings={
            "PROMPT": {"node_id": "1", "input": "prompt"},
            "SEED": {"node_id": "1", "input": "seed"},
        },
    )
    profile_version_id = _insert_profile_version(
        database,
        "asset-image-no-scale-evidence",
        version_no=1,
        capability="IMAGE_CHARACTER",
        workflow_version_id=workflow_version_id,
        updated_at="2026-02-01T00:00:00+00:00",
    )

    with pytest.raises(DomainRuleError) as caught:
        build_asset_image_batch(database, workspace).plan(
            project_id,
            asset_kind="CHARACTER",
            asset_ids=[asset_id],
            profile_version_id=profile_version_id,
        )

    assert caught.value.code == "ASSET_IMAGE_PROFILE_NOT_PRODUCTION_GRADE"
    assert caught.value.details["scale"]["reason"] == "SCALE_EVIDENCE_MISSING"
    assert caught.value.details["scale"]["missing_scale_roles"] == ["HEIGHT", "STEPS", "WIDTH"]


def test_explicit_profile_accepts_production_grade_route(workspace, database) -> None:
    project_id = _project(workspace, database, "prod_grade_explicit_ok")
    asset_id = _character(workspace, database, project_id, "CHAR_PRODUCTION")
    profile_version_id = _published_production_profile(workspace, database, "asset-image-production")

    plan = build_asset_image_batch(database, workspace).plan(
        project_id,
        asset_kind="CHARACTER",
        asset_ids=[asset_id],
        profile_version_id=profile_version_id,
    )

    assert plan["valid"] is True
    assert plan["profile_version_id"] == profile_version_id
    assert plan["items"][0]["status"] == "READY"
    assert plan["summary"]["jobs"] == 1


def test_auto_selection_and_explicit_selection_share_the_scale_requirement(workspace, database) -> None:
    project_id = _project(workspace, database, "prod_grade_auto_agrees")
    asset_id = _character(workspace, database, project_id, "CHAR_AUTO_AGREES")
    smoke_profile_id = _published_smoke_profile(workspace, database, "asset-image-auto-smoke")
    production_profile_id = _published_production_profile(workspace, database, "asset-image-auto-production")

    # The newer smoke profile is skipped and the older production route wins,
    # exactly as the explicit branch would refuse the smoke route.
    plan = build_asset_image_batch(database, workspace).plan(
        project_id,
        asset_kind="CHARACTER",
        asset_ids=[asset_id],
    )

    assert plan["valid"] is True
    assert plan["profile_version_id"] == production_profile_id
    assert plan["profile_version_id"] != smoke_profile_id
    assert str(plan["profile_resolution"]["profile_version_id"]) == production_profile_id


def test_auto_profile_skips_newer_smoke_scale_profile_for_older_production_route(workspace, database) -> None:
    _published_smoke_profile(workspace, database, "asset-image-pref-smoke", updated_at="2026-02-01T00:00:00+00:00")
    production_id = _published_production_profile(
        workspace, database, "asset-image-pref-production", updated_at="2026-01-01T00:00:00+00:00"
    )

    with database.connect() as connection:
        selected = SqliteGenerationPreferenceRepository(connection).auto_profile("IMAGE_CHARACTER")

    assert selected is not None
    assert str(selected["id"]) == production_id


def test_auto_profile_skips_missing_retired_and_smoke_workflow_candidates(workspace, database) -> None:
    missing_id = _insert_profile_version(
        database,
        "asset-image-pref-missing",
        version_no=1,
        capability="IMAGE_CHARACTER",
        workflow_version_id=None,
        updated_at="2026-03-01T00:00:00+00:00",
    )
    retired_workflow_id = _register_published_workflow(
        workspace, database, "asset-image-pref-retired", nodes=PRODUCTION_NODES, bindings=PRODUCTION_BINDINGS
    )
    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='RETIRED' WHERE id=?", (retired_workflow_id,))
    retired_id = _insert_profile_version(
        database,
        "asset-image-pref-retired",
        version_no=1,
        capability="IMAGE_CHARACTER",
        workflow_version_id=retired_workflow_id,
        updated_at="2026-02-15T00:00:00+00:00",
    )
    _published_smoke_profile(workspace, database, "asset-image-pref-smoke-2", updated_at="2026-02-01T00:00:00+00:00")
    production_id = _published_production_profile(
        workspace, database, "asset-image-pref-production-2", updated_at="2026-01-01T00:00:00+00:00"
    )

    with database.connect() as connection:
        # Capability matching stays case-insensitive.
        selected = SqliteGenerationPreferenceRepository(connection).auto_profile("image_character")

    assert selected is not None
    assert str(selected["id"]) == production_id
    assert str(selected["id"]) not in {missing_id, retired_id}


def test_auto_profile_returns_none_when_no_candidate_is_production_grade(workspace, database) -> None:
    _published_smoke_profile(workspace, database, "asset-image-only-smoke", updated_at="2026-02-01T00:00:00+00:00")

    with database.connect() as connection:
        assert SqliteGenerationPreferenceRepository(connection).auto_profile("IMAGE_CHARACTER") is None


def test_auto_profile_keeps_non_image_capabilities_provider_based(workspace, database) -> None:
    video_id = _insert_profile_version(
        database,
        "video-pref-t2v",
        version_no=1,
        capability="VIDEO_T2V",
        workflow_version_id=None,
        updated_at="2026-02-01T00:00:00+00:00",
    )

    with database.connect() as connection:
        selected = SqliteGenerationPreferenceRepository(connection).auto_profile("VIDEO_T2V")

    assert selected is not None
    assert str(selected["id"]) == video_id


def test_auto_resolution_reports_the_production_route_and_keeps_selection_reason(workspace, database) -> None:
    project_id = _project(workspace, database, "prod_grade_resolution")
    _published_smoke_profile(workspace, database, "asset-image-resolve-smoke", updated_at="2026-02-01T00:00:00+00:00")
    production_id = _published_production_profile(
        workspace, database, "asset-image-resolve-production", updated_at="2026-01-01T00:00:00+00:00"
    )

    with database.connect() as connection:
        resolution = GenerationPreferenceQueryService(
            SqliteGenerationPreferenceRepository(connection)
        ).resolve(project_id=project_id, capability="IMAGE_CHARACTER")

    assert resolution["profile_version_id"] == production_id
    assert resolution["source"] == "AUTO"
    assert resolution["blocked_reason"] is None
    assert resolution["recommendation"]["selection_reason"] == "AUTO_NEWEST_PUBLISHED_EXACT_CAPABILITY"


def test_scale_decision_uses_graph_facts_not_the_workflow_code_string(workspace, database) -> None:
    # A route named ``...-smoke`` that binds a real production scale is usable.
    smoke_named_production = _register_published_workflow(
        workspace, database, "qwen-image-2512-q5-smoke", nodes=PRODUCTION_NODES, bindings=PRODUCTION_BINDINGS
    )
    # A route named ``...-production`` that bakes a verification scale is not.
    production_named_smoke = _register_published_workflow(
        workspace, database, "qwen-image-2512-production", nodes=SMOKE_NODES, bindings=SMOKE_BINDINGS_WITH_SEED
    )

    with database.connect() as connection:
        assert image_workflow_is_production_grade(connection, smoke_named_production) is True
        report = image_workflow_production_scale(connection, production_named_smoke)
        assert report["production_grade"] is False
        assert report["reason"] == "SMOKE_SCALE"
        # A missing or unpublished route can never be production-grade.
        assert image_workflow_is_production_grade(connection, None) is False
        assert image_workflow_is_production_grade(connection, "no-such-workflow-version") is False

    with database.transaction() as connection:
        connection.execute("UPDATE workflow_versions SET status='RETIRED' WHERE id=?", (smoke_named_production,))
    with database.connect() as connection:
        assert image_workflow_is_production_grade(connection, smoke_named_production) is False


def test_scale_decision_keeps_bound_routes_with_cheap_baked_presets(workspace, database) -> None:
    """A bound production scale wins over a cheap baked authoring default."""

    # SDXL Turbo legitimately bakes 4 steps at 480x832.  Because the runtime
    # owns STEPS/WIDTH/HEIGHT the baked preset is not a verification route.
    turbo_nodes = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl_turbo_fp16.safetensors"}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
        "7": {"class_type": "EmptyLatentImage", "inputs": {"width": 480, "height": 832, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 4, "cfg": 1.0, "latent_image": ["7", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "local_drama/t2i", "images": ["8", 0]}},
    }
    workflow_version_id = _register_published_workflow(
        workspace, database, "sdxl-turbo-t2i", nodes=turbo_nodes, bindings=PRODUCTION_BINDINGS
    )

    with database.connect() as connection:
        report = image_workflow_production_scale(connection, workflow_version_id)

    assert report["production_grade"] is True
    assert report["reason"] == "BOUND_PRODUCTION_SCALE"
    assert report["missing_scale_roles"] == []
    # The same graph without a bound size would still not be a smoke route: its
    # latent is production sized even though the sampler runs a turbo preset.
    unbound_version_id = _register_published_workflow(
        workspace,
        database,
        "sdxl-turbo-t2i-unbound",
        nodes=turbo_nodes,
        bindings={"PROMPT": {"node_id": "5", "input": "text"}, "SEED": {"node_id": "8", "input": "seed"}},
    )
    with database.connect() as connection:
        unbound_report = image_workflow_production_scale(connection, unbound_version_id)
    assert unbound_report["production_grade"] is True
    assert unbound_report["reason"] == "GRAPH_PRODUCTION_SCALE"


def test_asset_batch_execution_gate_is_shared_by_explicit_and_auto_selection(workspace, database) -> None:
    """Both selection paths must read the same executable-semantics predicate."""

    smoke_workflow_id = _register_published_workflow(
        workspace, database, "asset-image-shared-smoke", nodes=SMOKE_NODES, bindings=SMOKE_BINDINGS_WITH_SEED
    )
    production_workflow_id = _register_published_workflow(
        workspace, database, "asset-image-shared-production", nodes=PRODUCTION_NODES, bindings=PRODUCTION_BINDINGS
    )
    smoke_profile_id = _insert_profile_version(
        database,
        "asset-image-shared-smoke",
        version_no=1,
        capability="IMAGE_CHARACTER",
        workflow_version_id=smoke_workflow_id,
        updated_at="2026-02-01T00:00:00+00:00",
    )
    production_profile_id = _insert_profile_version(
        database,
        "asset-image-shared-production",
        version_no=1,
        capability="IMAGE_CHARACTER",
        workflow_version_id=production_workflow_id,
        updated_at="2026-01-01T00:00:00+00:00",
    )

    with database.connect() as connection:
        smoke_profile = connection.execute(
            "SELECT * FROM execution_profile_versions WHERE id=?", (smoke_profile_id,)
        ).fetchone()
        production_profile = connection.execute(
            "SELECT * FROM execution_profile_versions WHERE id=?", (production_profile_id,)
        ).fetchone()
        assert smoke_profile is not None and production_profile is not None
        assert AssetImageGenerationBatchService._supports_production_scale(connection, smoke_profile) is False
        assert AssetImageGenerationBatchService._supports_production_scale(connection, production_profile) is True
        assert AssetImageGenerationBatchService._supports_asset_batch_execution(connection, smoke_profile) is False
        assert AssetImageGenerationBatchService._supports_asset_batch_execution(connection, production_profile) is True
        # Auto-selection can only ever return a profile this gate accepts.
        for row in connection.execute(
            "SELECT * FROM execution_profile_versions WHERE status='PUBLISHED' AND UPPER(capability)='IMAGE_CHARACTER'"
        ).fetchall():
            assert AssetImageGenerationBatchService._supports_asset_batch_execution(connection, row) is (
                str(row["id"]) == production_profile_id
            )

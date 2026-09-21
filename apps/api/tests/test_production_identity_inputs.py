from __future__ import annotations

import uuid

import pytest

from local_drama.application.asset_multiview import AssetMultiViewService
from local_drama.application.character_identity_packs import CharacterIdentityPackService
from local_drama.application.comfy_jobs import ComfyGenerationService
from local_drama.application.commands.asset_bible import AssetBibleCommandService
from local_drama.application.episode_front_half_actions import EpisodeFrontHalfActionService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.production_identity_inputs import (
    ProductionIdentityGenerationCompletionService,
    ProductionIdentityHeroPreparationService,
    ProductionIdentityInputService,
    ProductionIdentityPreparationService,
    session_identity_snapshot_for_shot,
)
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.story_assets import StoryAssetService
from local_drama.application.workspace_assets import WorkspaceAssetService
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.infrastructure.database.asset_bible_repository import (
    SqliteAssetBibleRepository,
)


def _session(service: ProductionSessionService, project_id: str, episode_id: str, key: str):
    request = {
        "scope_type": "SINGLE_EPISODE",
        "episode_ids": [episode_id],
        "production_mode": "BALANCED",
        "checkpoint_policy": "ON_EXCEPTION",
        "tts_enabled": True,
        "max_parallel_episodes": 1,
        "min_free_disk_bytes": 1,
    }
    plan = service.plan(project_id, request)
    return service.create(
        project_id,
        {**request, "expected_plan_hash": plan["plan_hash"], "actor": "test"},
        idempotency_key=key,
    )["session"]


def _fixture(workspace, database):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="session_identity",
        title="会话身份输入",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    episode_id = str(episode["id"])
    shot = projects.create_shot(episode_id, "S001", 4_000)
    shot_id = str(shot["id"])
    character = StoryAssetService(database, workspace).create_asset(
        project_id, "CHARACTER", "HERO", "主角", "黑发青年"
    )
    media = MediaService(database, workspace)
    authorization = WorkspaceAssetService(database, workspace)
    media_ids: dict[str, str] = {}
    for slot in ("FRONT", "LEFT", "RIGHT", "FRONT_V2"):
        source = workspace.work_root / f"session-identity-{slot.lower()}.png"
        source.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            + slot.encode("ascii")
            + uuid.uuid4().bytes
        )
        imported = media.import_file(project_id, source, media_kind="IMAGE")
        media_version_id = str(imported["media_version_id"])
        authorization.authorize_media_version(project_id, media_version_id)
        media_ids[slot] = media_version_id
    packs = CharacterIdentityPackService(database)
    pack = packs.create_pack(project_id, str(character["id"]), "SESSION_DRAFT", "机器草稿")
    version_id = str(pack["versions"][0]["id"])
    for slot in ("FRONT", "LEFT", "RIGHT"):
        packs.set_version_slot(version_id, slot, media_ids[slot])
    now = "2026-09-21T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO shot_asset_bindings
               (id,shot_id,asset_id,role_in_shot,identity_pack_version_id,
                created_at,created_by,revision,schema_version)
               VALUES (?,?,?,'main',?,?, 'test',1,'v2')""",
            (str(uuid.uuid4()), shot_id, str(character["id"]), version_id, now),
        )
    session = _session(ProductionSessionService(database), project_id, episode_id, "identity-1")
    return {
        "project_id": project_id,
        "episode_id": episode_id,
        "shot_id": shot_id,
        "asset_id": str(character["id"]),
        "version_id": version_id,
        "front_v2": media_ids["FRONT_V2"],
        "front": media_ids["FRONT"],
        "media_ids": media_ids,
        "session_id": str(session["id"]),
    }


def test_session_draft_identity_is_isolated_and_slot_changes_invalidate_snapshot(
    workspace, database
) -> None:
    ctx = _fixture(workspace, database)
    packs = CharacterIdentityPackService(database)
    service = ProductionIdentityInputService(database)

    with database.connect() as connection:
        with pytest.raises(DomainRuleError) as formal:
            packs.generation_snapshot_for_intent(
                connection,
                {
                    "owner_type": "SHOT",
                    "owner_id": ctx["shot_id"],
                    "project_id": ctx["project_id"],
                },
            )
    assert formal.value.code in {
        "IDENTITY_PACK_BINDING_STALE",
        "APPROVED_IDENTITY_PACK_REQUIRED",
    }

    registered = service.register(ctx["session_id"], ctx["version_id"])
    assert registered["state"] == "ACTIVE"
    assert registered["idempotent_replay"] is False
    with database.connect() as connection:
        snapshot = session_identity_snapshot_for_shot(
            connection, ctx["session_id"], ctx["project_id"], ctx["shot_id"]
        )
    assert snapshot is not None
    assert snapshot["selection_authority"] == "MACHINE_TEMPORARY"
    assert snapshot["human_approved"] is False
    assert snapshot["packs"][0]["pack_version_status"] == "DRAFT"
    plan = VariantPlan(
        variant_type="BASE",
        parent_variant_id=None,
        branch_reason="SESSION_IDENTITY_TEST",
        prompt_revision_id=None,
        profile_version_id="profile-test",
        parameter_set={},
        seed_policy="EXPLICIT",
        explicit_seed=1,
        bindings=(),
        expected_identity_pack_snapshot_hash=snapshot["snapshot_hash"],
        production_session_id=ctx["session_id"],
    )
    with database.connect() as connection:
        generation_snapshot = GenerationService._identity_snapshot_for_plan(
            connection,
            {
                "owner_type": "SHOT",
                "owner_id": ctx["shot_id"],
                "project_id": ctx["project_id"],
            },
            plan,
        )
    assert generation_snapshot == snapshot

    packs.set_version_slot(ctx["version_id"], "FRONT", ctx["front_v2"])
    with database.connect() as connection:
        with pytest.raises(DomainRuleError) as stale:
            session_identity_snapshot_for_shot(
                connection, ctx["session_id"], ctx["project_id"], ctx["shot_id"]
            )
    assert stale.value.code == "PRODUCTION_SESSION_IDENTITY_INPUT_STALE"

    refreshed = service.register(ctx["session_id"], ctx["version_id"])
    assert refreshed["content_hash"] != registered["content_hash"]
    with database.transaction() as connection:
        connection.execute(
            "UPDATE production_sessions SET status='COMPLETED',finished_at=? WHERE id=?",
            ("2026-09-21T01:00:00Z", ctx["session_id"]),
        )
    second = _session(
        ProductionSessionService(database),
        ctx["project_id"],
        ctx["episode_id"],
        "identity-2",
    )
    with database.connect() as connection:
        with pytest.raises(DomainRuleError) as isolated:
            session_identity_snapshot_for_shot(
                connection,
                str(second["id"]),
                ctx["project_id"],
                ctx["shot_id"],
            )
    assert isolated.value.code == "PRODUCTION_SESSION_IDENTITY_INPUT_REQUIRED"


def test_session_asset_completion_reuses_complete_draft_without_approval(
    workspace, database
) -> None:
    ctx = _fixture(workspace, database)
    result = ProductionIdentityInputService(database).ensure_episode_inputs(
        ctx["session_id"], ctx["episode_id"]
    )
    assert result["ready"] is True
    assert result["human_approved"] is False
    assert len(result["registered"]) == 1
    report, _ = EpisodeFrontHalfActionService(database, workspace).run(
        "ASSET_COMPLETION",
        ctx["episode_id"],
        production_session_id=ctx["session_id"],
    )
    assert report["machine_check"]["status"] == "PASS"
    assert report["machine_check"]["human_approval_created"] is False
    with database.connect() as connection:
        status = connection.execute(
            "SELECT status FROM character_identity_pack_versions WHERE id=?",
            (ctx["version_id"],),
        ).fetchone()[0]
        approvals = connection.execute(
            """SELECT COUNT(*) FROM audit_events
               WHERE action='CHARACTER_IDENTITY_PACK_APPROVED'
                 AND subject_type='character_identity_pack_version' AND subject_id=?""",
            (ctx["version_id"],),
        ).fetchone()[0]
    assert status == "DRAFT"
    assert approvals == 0


def test_session_identity_preparation_dispatches_existing_multiview_jobs_once(
    workspace, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _fixture(workspace, database)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE shot_asset_bindings SET identity_pack_version_id=NULL WHERE shot_id=?",
            (ctx["shot_id"],),
        )
        connection.execute(
            "UPDATE character_identity_packs SET status='RETIRED' WHERE story_asset_id=?",
            (ctx["asset_id"],),
        )
    jobs = JobService(database, workspace)
    generated_jobs = [
        jobs.create_job(
            ctx["project_id"],
            "CPU_TEST",
            "STORY_ASSET",
            ctx["asset_id"],
            "CPU",
            {"slot_kind": slot},
            f"identity-preparation-{slot}",
            subject_kind="STORY_ASSET",
            scope_kind="PROJECT",
            scope_project_id=ctx["project_id"],
            stage_code="ASSET_IMAGE",
        )
        for slot in ("FRONT", "LEFT", "RIGHT")
    ]

    def preflight(_self, asset_id: str, **_kwargs):
        assert asset_id == ctx["asset_id"]
        return {
            "ready": True,
            "blockers": [],
            "plan_hash": "a" * 64,
            "runtime_contacted": False,
            "network_contacted": False,
        }

    def submit(_self, asset_id: str, **kwargs):
        assert asset_id == ctx["asset_id"]
        assert kwargs["idempotency_key"].startswith("production-session:")
        return {
            "items": [
                {"reference_kind": slot, "job": job}
                for slot, job in zip(("FRONT", "LEFT", "RIGHT"), generated_jobs, strict=True)
            ]
        }

    monkeypatch.setattr(AssetMultiViewService, "preflight", preflight)
    monkeypatch.setattr(AssetMultiViewService, "submit", submit)
    service = ProductionIdentityPreparationService(database, workspace)
    planned = service.plan_episode(ctx["session_id"], ctx["episode_id"])
    assert planned["ready"] is True
    assert planned["items"][0]["status"] == "READY_TO_SUBMIT"

    dispatched = service.dispatch_episode(ctx["session_id"], ctx["episode_id"])
    assert len(dispatched["submitted"]) == 3
    assert dispatched["dependency_job_ids"] == [str(job["id"]) for job in generated_jobs]
    with database.connect() as connection:
        links = connection.execute(
            """SELECT role,job_id FROM production_session_job_links
               WHERE session_id=? AND role LIKE 'IDENTITY_VIEW_%' ORDER BY role""",
            (ctx["session_id"],),
        ).fetchall()
    assert {str(row["role"]) for row in links} == {
        "IDENTITY_VIEW_FRONT",
        "IDENTITY_VIEW_LEFT",
        "IDENTITY_VIEW_RIGHT",
    }


def test_comfy_restart_reconciliation_finalizes_session_identity_view(
    workspace, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _fixture(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(
        ctx["project_id"],
        owner_type="STORY_ASSET",
        owner_id=ctx["asset_id"],
        purpose="ASSET_MULTI_VIEW",
        creative_goal="验证异步恢复会收口会话三视图",
    )
    profiles = ProfileService(database, workspace.manifest_path)
    profiles.sync_manifest()
    profile_version_id = str(profiles.list_profiles()[0]["version_id"])
    with database.transaction() as connection:
        variant_id = str(uuid.uuid4())
        now = "2026-09-21T00:00:00Z"
        connection.execute(
            """INSERT INTO generation_variants
               (id,intent_id,variant_no,variant_type,branch_reason,
                capability_profile_version_id,parameter_set_json,seed_policy,
                input_fingerprint,recipe_hash,status,created_at,updated_at,created_by,
                revision,schema_version,is_stale)
               VALUES (?,?,1,'BASE','TEST',?,'{}','EXPLICIT',?,?,'QUEUED',?,?, 'test',1,'v2',0)""",
            (
                variant_id,
                str(intent["id"]),
                profile_version_id,
                "a" * 64,
                "b" * 64,
                now,
                now,
            ),
        )
    jobs = JobService(database, workspace)
    job = jobs.create_job(
        ctx["project_id"],
        "CPU_TEST",
        "GENERATION_VARIANT",
        variant_id,
        "CPU",
        {},
        "identity-comfy-restart",
        subject_kind="GENERATION_VARIANT",
        scope_kind="PROJECT",
        scope_project_id=ctx["project_id"],
        stage_code="ASSET_IMAGE",
    )
    claim = jobs.claim("identity-restart-worker", ["CPU"])
    assert claim is not None and claim["job"]["id"] == job["id"]
    jobs.complete(
        str(claim["attempt"]["id"]),
        str(claim["attempt"]["lease_token"]),
        "identity-restart-worker",
        success=True,
    )
    with database.transaction() as connection:
        item_id = connection.execute(
            "SELECT id FROM production_session_items WHERE session_id=?",
            (ctx["session_id"],),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO production_session_job_links
               (id,session_id,session_item_id,job_id,stage_code,role,link_state,
                created_at,updated_at,created_by,revision,schema_version)
               VALUES (?,?,?,?,?,'IDENTITY_VIEW_FRONT','ACTIVE',?,?,?,1,'production-session.v1')""",
            (
                str(uuid.uuid4()),
                ctx["session_id"],
                str(item_id),
                str(job["id"]),
                "ASSETS",
                now,
                now,
                "test",
            ),
        )

    calls: list[str] = []

    def finalize(_self, job_id: str, _artifacts):
        calls.append(job_id)
        return {"status": "SUCCEEDED", "job_id": job_id}

    monkeypatch.setattr(
        ProductionIdentityGenerationCompletionService, "finalize_job", finalize
    )
    reconciled = ComfyGenerationService(
        database, workspace
    ).reconcile_succeeded_business_outputs()

    assert calls == [str(job["id"])]
    assert reconciled["inspected"] == reconciled["finalized"] == 1
    assert reconciled["items"][0]["outcomes"][-1]["kind"] == "PRODUCTION_IDENTITY_VIEW"


def test_session_hero_preparation_dispatches_existing_asset_image_batch(
    workspace, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_drama.infrastructure import service_composition

    ctx = _fixture(workspace, database)
    job = JobService(database, workspace).create_job(
        ctx["project_id"],
        "CPU_TEST",
        "STORY_ASSET",
        ctx["asset_id"],
        "CPU",
        {},
        "session-hero-test",
        subject_kind="STORY_ASSET",
        scope_kind="PROJECT",
        scope_project_id=ctx["project_id"],
        stage_code="ASSET_IMAGE",
    )

    class FakeBatch:
        def plan(self, project_id: str, **kwargs):
            assert project_id == ctx["project_id"]
            assert kwargs["asset_kind"] == "CHARACTER"
            assert kwargs["asset_ids"] == [ctx["asset_id"]]
            return {
                "plan_hash": "b" * 64,
                "valid": True,
                "items": [
                    {
                        "asset_id": ctx["asset_id"],
                        "status": "READY",
                        "blockers": [],
                    }
                ],
            }

        def submit(self, project_id: str, **kwargs):
            assert project_id == ctx["project_id"]
            assert kwargs["expected_plan_hash"] == "b" * 64
            assert kwargs["idempotency_key"].startswith("production-session:")
            return {
                "items": [
                    {"asset_id": ctx["asset_id"], "job_id": str(job["id"])}
                ]
            }

    monkeypatch.setattr(
        service_composition,
        "build_asset_image_batch",
        lambda _database, _settings: FakeBatch(),
    )
    result = ProductionIdentityHeroPreparationService(
        database, workspace
    ).dispatch_episode(ctx["session_id"], ctx["episode_id"])
    assert result["ready"] is True
    assert result["dependency_job_ids"] == [str(job["id"])]
    assert result["submitted"] == [
        {"asset_id": ctx["asset_id"], "job_id": str(job["id"])}
    ]
    with database.connect() as connection:
        link = connection.execute(
            """SELECT role,stage_code FROM production_session_job_links
               WHERE session_id=? AND job_id=?""",
            (ctx["session_id"], str(job["id"])),
        ).fetchone()
    assert dict(link) == {"role": "IDENTITY_HERO", "stage_code": "ASSETS"}


def test_session_hero_plan_treats_concurrently_completed_verified_hero_as_ready(
    workspace, database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_drama.infrastructure import service_composition

    ctx = _fixture(workspace, database)

    class FakeBatch:
        def plan(self, _project_id: str, **kwargs):
            return {
                "plan_hash": "c" * 64,
                "valid": False,
                "items": [
                    {
                        "asset_id": asset_id,
                        "status": "SKIPPED",
                        "blockers": [],
                    }
                    for asset_id in kwargs["asset_ids"]
                ],
            }

    monkeypatch.setattr(
        service_composition,
        "build_asset_image_batch",
        lambda _database, _settings: FakeBatch(),
    )
    planned = ProductionIdentityHeroPreparationService(
        database, workspace
    ).plan_episode(ctx["session_id"], ctx["episode_id"])

    assert planned["ready"] is True
    assert planned["items"][0]["status"] == "READY"
    assert planned["items"][0]["blockers"] == []


def test_completed_multiview_references_assemble_draft_and_register_session_input(
    workspace, database
) -> None:
    ctx = _fixture(workspace, database)
    with database.transaction() as connection:
        connection.execute(
            "DELETE FROM production_session_identity_inputs WHERE session_id=?",
            (ctx["session_id"],),
        )
        command = AssetBibleCommandService(SqliteAssetBibleRepository(connection))
        for slot in ("FRONT", "LEFT", "RIGHT"):
            command.add_reference(
                ctx["project_id"],
                ctx["asset_id"],
                ctx["media_ids"][slot],
                slot,
                label=f"test-{slot}",
                actor="test",
            )
    pack_version_id = ProductionIdentityGenerationCompletionService(
        database, workspace
    )._assemble_and_register(ctx["session_id"], ctx["project_id"], ctx["asset_id"])
    assert pack_version_id == ctx["version_id"]
    with database.connect() as connection:
        version = connection.execute(
            "SELECT status FROM character_identity_pack_versions WHERE id=?",
            (pack_version_id,),
        ).fetchone()
        identity_input = connection.execute(
            """SELECT state,pack_version_id FROM production_session_identity_inputs
               WHERE session_id=? AND story_asset_id=?""",
            (ctx["session_id"], ctx["asset_id"]),
        ).fetchone()
    assert str(version["status"]) == "DRAFT"
    assert dict(identity_input) == {"state": "ACTIVE", "pack_version_id": pack_version_id}

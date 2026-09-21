from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from local_drama.application.asset_proposals import AssetProposalService
from local_drama.application.breakdown_apply import BreakdownApplyService
from local_drama.application.episode_front_half_actions import EpisodeFrontHalfActionService
from local_drama.application.episode_production_runs import EpisodeProductionRunService
from local_drama.application.jobs import JobService
from local_drama.application.production_asset_inputs import ProductionAssetInputService
from local_drama.application.production_identity_inputs import (
    ProductionIdentityHeroPreparationService,
    ProductionIdentityPreparationService,
)
from local_drama.application.production_session_review import ProductionSessionReviewService
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.domain.errors import DomainRuleError
from tests.test_breakdown_apply import _persisted_draft


def _session(database, project_id: str, episode_id: str) -> dict:
    service = ProductionSessionService(database)
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
        idempotency_key="production-asset-inputs",
    )["session"]


def test_session_asset_inputs_create_provisional_assets_without_accepting_proposals(
    workspace, database
) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    BreakdownApplyService(database, workspace).apply_draft(
        draft_id,
        str(episode["id"]),
        actor="production-session-worker",
        application_authority="MACHINE_TEMPORARY",
    )
    session = _session(database, str(project["id"]), str(episode["id"]))
    service = ProductionAssetInputService(database)

    first = service.ensure_episode_inputs(
        str(session["id"]), str(episode["id"]), actor="test"
    )
    second = service.ensure_episode_inputs(
        str(session["id"]), str(episode["id"]), actor="test"
    )
    assert first["ready"] is True
    assert len(first["registered"]) == 3
    assert first["bound_shot_count"] > 0
    assert second["registered"] == []
    assert all(item["status"] == "SESSION_READY" for item in second["items"])

    report, _ = EpisodeFrontHalfActionService(database, workspace).run(
        "ASSET_IDENTITY",
        str(episode["id"]),
        production_session_id=str(session["id"]),
    )
    assert report["machine_check"]["status"] == "PASS"
    assert report["machine_check"]["human_approval_created"] is False
    with database.connect() as connection:
        proposals = connection.execute(
            """SELECT status,resolved_asset_id,suggested_asset_id
               FROM story_asset_proposals WHERE breakdown_draft_id=? ORDER BY id""",
            (draft_id,),
        ).fetchall()
        inputs = connection.execute(
            """SELECT state,input_json FROM production_session_asset_inputs
               WHERE session_id=?""",
            (session["id"],),
        ).fetchall()
        decision_audits = int(
            connection.execute(
                """SELECT COUNT(*) FROM audit_events
                   WHERE action='STORY_ASSET_PROPOSAL_DECIDED'"""
            ).fetchone()[0]
        )
        application_audit = connection.execute(
            """SELECT summary,metadata_redacted_json FROM audit_events
               WHERE action='SCRIPT_BREAKDOWN_APPLIED' AND subject_id=?""",
            (draft_id,),
        ).fetchone()
    assert len(inputs) == 3
    assert all(str(row["state"]) == "ACTIVE" for row in inputs)
    assert all(str(row["status"]) == "PENDING" for row in proposals)
    assert all(row["resolved_asset_id"] is None for row in proposals)
    assert all(row["suggested_asset_id"] is not None for row in proposals)
    assert decision_audits == 0
    assert "机器临时" in str(application_audit["summary"])
    assert '"human_approved":false' in str(
        application_audit["metadata_redacted_json"]
    )
    review = ProductionSessionReviewService(database).inspect(
        str(session["id"]), cursor=0, limit=10
    )
    review_item = review["items"][0]
    assert len(review_item["asset_inputs"]) == 3
    assert all(
        item["review_status"] == "PENDING"
        and item["human_approved"] is False
        for item in review_item["asset_inputs"]
    )
    assert "SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED" in {
        item["code"] for item in review_item["blockers"]
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE production_sessions SET status='WAITING_USER',revision=revision+1 WHERE id=?",
            (session["id"],),
        )
        connection.execute(
            """UPDATE production_session_items
               SET state='BLOCKED',current_stage='WAITING_REVIEW',revision=revision+1
               WHERE session_id=?""",
            (session["id"],),
        )
    blocked_session = ProductionSessionService(database).get(str(session["id"]))
    blocked_item = ProductionSessionService(database).list_items(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    with pytest.raises(DomainRuleError) as retry_error:
        ProductionSessionService(database).retry_item(
            str(session["id"]),
            str(blocked_item["id"]),
            {
                "expected_session_revision": blocked_session["revision"],
                "expected_item_revision": blocked_item["revision"],
                "strategy": "FULL_EPISODE",
                "actor": "test",
            },
            idempotency_key="retry-before-asset-review",
        )
    assert retry_error.value.code == "PRODUCTION_SESSION_RETRY_PREREQUISITE_REQUIRED"
    assert retry_error.value.details["prerequisites"][0]["action"] == (
        "REVIEW_ASSET_IDENTITIES"
    )
    with database.transaction() as connection:
        connection.execute(
            """UPDATE production_session_items
               SET current_stage='PREPARATION',
                   last_error_code='EPISODE_PRODUCTION_PREFLIGHT_BLOCKED',
                   last_error_message='profile configuration repaired',
                   revision=revision+1
               WHERE id=?""",
            (blocked_item["id"],),
        )
    repaired_session = ProductionSessionService(database).get(str(session["id"]))
    repaired_item = ProductionSessionService(database).list_items(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    infrastructure_retry = ProductionSessionService(database).retry_item(
        str(session["id"]),
        str(repaired_item["id"]),
        {
            "expected_session_revision": repaired_session["revision"],
            "expected_item_revision": repaired_item["revision"],
            "strategy": "RETRY_FAILED_STAGE",
            "actor": "test",
        },
        idempotency_key="retry-after-non-identity-repair",
    )
    assert infrastructure_retry["session"]["status"] == "RUNNING"
    assert infrastructure_retry["item"]["state"] == "PENDING"
    proposal_service = AssetProposalService(database)
    for proposal in proposal_service.list(str(project["id"]), status="PENDING"):
        proposal_service.decide(
            str(proposal["id"]),
            action="MERGE_EXISTING",
            expected_revision=int(proposal["revision"]),
            target_asset_id=str(proposal["suggested_asset_id"]),
            decision_note="确认本次生产使用的临时资产",
            actor="test-human",
        )
    reviewed = ProductionSessionReviewService(database).inspect(
        str(session["id"]), cursor=0, limit=10
    )["items"][0]
    assert all(
        item["review_status"] == "CONFIRMED"
        and item["human_approved"] is True
        for item in reviewed["asset_inputs"]
    )
    assert "SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED" not in {
        item["code"] for item in reviewed["blockers"]
    }


def test_concurrent_asset_input_preparation_reuses_one_provisional_asset_per_proposal(
    workspace, database, monkeypatch
) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    BreakdownApplyService(database, workspace).apply_draft(
        draft_id,
        str(episode["id"]),
        actor="production-session-worker",
        application_authority="MACHINE_TEMPORARY",
    )
    session = _session(database, str(project["id"]), str(episode["id"]))
    service = ProductionAssetInputService(database)
    original_plan = service.plan_episode
    first_plans_ready = threading.Barrier(2)
    counter_lock = threading.Lock()
    plan_call_count = 0

    def racing_plan(session_id: str, episode_id: str):
        nonlocal plan_call_count
        result = original_plan(session_id, episode_id)
        with counter_lock:
            plan_call_count += 1
            call_number = plan_call_count
        if call_number <= 2:
            first_plans_ready.wait(timeout=5)
        return result

    monkeypatch.setattr(service, "plan_episode", racing_plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                service.ensure_episode_inputs,
                str(session["id"]),
                str(episode["id"]),
                actor="concurrency-test",
            )
            for _index in range(2)
        ]
        results = [future.result(timeout=10) for future in futures]

    with database.connect() as connection:
        proposal_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM story_asset_proposals WHERE breakdown_draft_id=?",
                (draft_id,),
            ).fetchone()[0]
        )
        provisional_assets = connection.execute(
            """SELECT id,json_extract(extra_json,'$.source_asset_proposal_id') AS proposal_id
               FROM story_assets
               WHERE project_id=? AND json_extract(extra_json,'$.provisional')=1""",
            (project["id"],),
        ).fetchall()
        inputs = connection.execute(
            """SELECT id,asset_proposal_id,story_asset_id
               FROM production_session_asset_inputs WHERE session_id=? AND state='ACTIVE'""",
            (session["id"],),
        ).fetchall()
    assert proposal_count == 3
    assert len(provisional_assets) == proposal_count
    assert len({str(row["proposal_id"]) for row in provisional_assets}) == proposal_count
    assert len(inputs) == proposal_count
    assert len({str(row["asset_proposal_id"]) for row in inputs}) == proposal_count
    assert sum(len(result["registered"]) for result in results) == proposal_count


def test_session_episode_plan_accepts_complete_auto_applied_draft_without_approval(
    workspace, database
) -> None:
    project, episode, draft_id = _persisted_draft(workspace, database)
    BreakdownApplyService(database, workspace).apply_draft(
        draft_id, str(episode["id"]), actor="test"
    )
    session = _session(database, str(project["id"]), str(episode["id"]))
    report, _ = EpisodeFrontHalfActionService(database, workspace).run(
        "EPISODE_PLAN",
        str(episode["id"]),
        production_session_id=str(session["id"]),
    )
    assert report["machine_check"]["status"] == "PASS"
    assert report["machine_check"]["selection_authority"] == "MACHINE_TEMPORARY"
    assert report["machine_check"]["human_approval_created"] is False
    with database.connect() as connection:
        statuses = {
            str(row["status"])
            for row in connection.execute(
                "SELECT status FROM shots WHERE episode_id=?", (episode["id"],)
            ).fetchall()
        }
    assert statuses == {"DRAFT"}


def test_structured_scene_and_prop_inputs_bind_and_dispatch_hero_batches_by_kind(
    workspace, database, monkeypatch
) -> None:
    from local_drama.infrastructure import service_composition

    draft = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "雨夜仓库",
                "location": "旧仓库",
                "summary": "阿舟找到灯盏",
                "characters": ["阿舟"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "阿舟在仓库举起灯盏",
                        "action": "阿舟举起灯盏",
                        "dialogue": "",
                        "duration_seconds": 4,
                        "characters": ["阿舟"],
                        "props": ["灯盏"],
                    }
                ],
            }
        ]
    }
    project, episode, draft_id = _persisted_draft(workspace, database, draft=draft)
    BreakdownApplyService(database, workspace).apply_draft(
        draft_id,
        str(episode["id"]),
        actor="production-session-worker",
        application_authority="MACHINE_TEMPORARY",
    )
    session = _session(database, str(project["id"]), str(episode["id"]))
    prepared = ProductionAssetInputService(database).ensure_episode_inputs(
        str(session["id"]), str(episode["id"]), actor="test"
    )
    assert prepared["ready"] is True
    assert len(prepared["registered"]) == 3
    assert prepared["bound_shot_count"] == 3
    with database.connect() as connection:
        bound_assets = [
            dict(row)
            for row in connection.execute(
                """SELECT a.id,a.kind,b.role_in_shot FROM shot_asset_bindings b
                   JOIN story_assets a ON a.id=b.asset_id
                   JOIN shots sh ON sh.id=b.shot_id WHERE sh.episode_id=?
                   ORDER BY a.kind""",
                (episode["id"],),
            ).fetchall()
        ]
        proposals = connection.execute(
            """SELECT kind,status,resolved_asset_id FROM story_asset_proposals
               WHERE breakdown_draft_id=? ORDER BY kind""",
            (draft_id,),
        ).fetchall()
    assert [(item["kind"], item["role_in_shot"]) for item in bound_assets] == [
        ("CHARACTER", "main"),
        ("PROP", "prop"),
        ("SCENE", "location"),
    ]
    assert all(str(row["status"]) == "PENDING" for row in proposals)
    assert all(row["resolved_asset_id"] is None for row in proposals)

    jobs = JobService(database, workspace)
    jobs_by_asset = {
        str(item["id"]): str(
            jobs.create_job(
                str(project["id"]),
                "CPU_TEST",
                "STORY_ASSET",
                str(item["id"]),
                "CPU",
                {},
                f"structured-asset-hero-{item['id']}",
                subject_kind="STORY_ASSET",
                scope_kind="PROJECT",
                scope_project_id=str(project["id"]),
                stage_code="ASSET_IMAGE",
            )["id"]
        )
        for item in bound_assets
    }
    plan_calls: list[tuple[str, list[str]]] = []
    submit_calls: list[tuple[str, list[str]]] = []

    class FakeBatch:
        def plan(self, project_id: str, *, asset_kind: str, asset_ids: list[str]):
            assert project_id == str(project["id"])
            plan_calls.append((asset_kind, asset_ids))
            return {
                "plan_hash": f"{asset_kind.casefold()}-plan",
                "valid": True,
                "items": [
                    {"asset_id": asset_id, "status": "READY", "blockers": []}
                    for asset_id in asset_ids
                ],
            }

        def submit(
            self, project_id: str, *, asset_kind: str, asset_ids: list[str], **_kwargs
        ):
            assert project_id == str(project["id"])
            submit_calls.append((asset_kind, asset_ids))
            return {
                "items": [
                    {"asset_id": asset_id, "job_id": jobs_by_asset[asset_id]}
                    for asset_id in asset_ids
                ]
            }

    fake_batch = FakeBatch()
    monkeypatch.setattr(
        service_composition,
        "build_asset_image_batch",
        lambda _database, _settings: fake_batch,
    )
    dispatched = ProductionIdentityHeroPreparationService(
        database, workspace
    ).dispatch_episode(str(session["id"]), str(episode["id"]))
    assert [kind for kind, _asset_ids in plan_calls] == ["CHARACTER", "PROP", "SCENE"]
    assert [kind for kind, _asset_ids in submit_calls] == ["CHARACTER", "PROP", "SCENE"]
    assert len(dispatched["submitted"]) == 3
    assert set(dispatched["dependency_job_ids"]) == set(jobs_by_asset.values())


def test_session_preflight_defers_only_machine_preparable_draft_and_hero_gates(
    workspace, database, monkeypatch
) -> None:
    explicit_draft = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "开场",
                "summary": "母亲迎回孩子",
                "characters": ["母亲", "孩子"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "母亲近景",
                        "action": "母亲开门",
                        "dialogue": "母亲：你回来了。",
                        "duration_seconds": 4,
                        "characters": ["母亲"],
                    },
                    {
                        "shot_no": 2,
                        "visual": "孩子中景",
                        "action": "孩子走进门",
                        "dialogue": "孩子：嗯，我回来了。",
                        "duration_seconds": 3,
                        "characters": ["孩子"],
                    },
                ],
            }
        ]
    }
    project, episode, draft_id = _persisted_draft(
        workspace, database, draft=explicit_draft
    )
    BreakdownApplyService(database, workspace).apply_draft(
        draft_id,
        str(episode["id"]),
        actor="production-session-worker",
        application_authority="MACHINE_TEMPORARY",
    )
    session = _session(database, str(project["id"]), str(episode["id"]))
    ProductionAssetInputService(database).ensure_episode_inputs(
        str(session["id"]), str(episode["id"]), actor="test"
    )
    with database.connect() as connection:
        asset_ids = [
            str(row["asset_id"])
            for row in connection.execute(
                """SELECT DISTINCT sab.asset_id FROM shot_asset_bindings sab
                   JOIN shots sh ON sh.id=sab.shot_id WHERE sh.episode_id=?""",
                (episode["id"],),
            ).fetchall()
        ]

    monkeypatch.setattr(
        ProductionIdentityHeroPreparationService,
        "plan_episode",
        lambda _self, session_id, episode_id: {
            "production_session_id": session_id,
            "episode_id": episode_id,
            "ready": True,
            "blockers": [],
            "items": [
                {"asset_id": asset_id, "status": "READY_TO_SUBMIT"}
                for asset_id in asset_ids
            ],
        },
    )
    monkeypatch.setattr(
        ProductionIdentityPreparationService,
        "plan_episode",
        lambda _self, session_id, episode_id: {
            "production_session_id": session_id,
            "episode_id": episode_id,
            "ready": False,
            "items": [],
            "blockers": [
                {
                    "code": "ASSET_MULTI_VIEW_HERO_REQUIRED",
                    "asset_id": asset_id,
                    "asset_code": asset_id,
                }
                for asset_id in asset_ids
            ],
        },
    )
    preflight = EpisodeProductionRunService(database, workspace).preflight(
        str(episode["id"]),
        include_front_half=True,
        production_session_id=str(session["id"]),
        tts_enabled=False,
        min_free_disk_bytes=1,
    )
    checks = {str(item["code"]): item for item in preflight["checks"]}
    assert checks["SCRIPT_SHOT_PLAN_MISSING"]["status"] == "PASS"
    assert checks["CRITICAL_ASSETS_MISSING"]["status"] == "PASS"
    assert checks["ASSET_COMPLETION_REQUIRED"]["status"] == "PASS"
    assert checks["ASSET_COMPLETION_REQUIRED"]["evidence"]["human_approval_created"] is False

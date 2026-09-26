"""Preflight → run submission and frozen-budget contracts.

Reproduced defects on the audit snapshot (EXP-01, EXP-08):

* ``start_run`` accepted only ``project/hash/key`` and dropped ``outputs`` (plus
  ``budget`` and ``fallback_policy``).  ``submit_run`` then re-ran the preflight
  with an *empty* output list, so the recomputed hash could never equal the
  submitted one: every legitimate one-click submission returned
  ``409 STALE_PLAN`` with ``details.submitted_outputs=[]``, ``explainer_runs=0``
  and ``jobs=0``.
* The budget/fallback defaults used ``value or default``, so an explicitly
  supplied ``0`` or ``[]`` — a real instruction ("no creative repairs", "no
  fallback allowed") — was silently replaced by the product default and the frozen
  authorization snapshot grew beyond what the caller granted.
"""

from __future__ import annotations

from typing import Any

import pytest

from local_drama.application.explainers.production import (
    TASK_SKELETON,
    ExplainerProductionService,
)
from local_drama.domain.explainers.contracts import ExplainerContractError, ExplainerErrorCode
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "project-preflight-1"


def _probe_all_available(capability: str, *, project_id: str) -> dict[str, object]:
    del project_id
    return {"available": True, "profile_version_id": f"profile-{capability}", "execution_class": "LOCAL"}


def _outputs() -> list[dict[str, Any]]:
    return [
        {
            "edition_key": "zh-clean-169",
            "voice_locale": "zh-CN",
            "subtitle_locales": [],
            "subtitle_mode": "NONE",
            "aspect_ratio": "16:9",
            "fps": {"num": 25, "den": 1},
            "duration_policy": "USE_SOURCE_TARGET",
            "allow_soft_subtitle_fallback": False,
        }
    ]


def _seed(database: Database, *, input_kind: str = "DOCUMENT_IMPORT") -> dict[str, Any]:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, ?, ?, 'DRAFT', 'v2', ?, 300000, 'EXPLAINER', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, "EXP-PREFLIGHT", "灯塔最后一页值班记录", PROJECT_ID),
        )
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    video = service.create_video(
        project_id=PROJECT_ID,
        title="灯塔",
        topic="主题",
        content_kind="ORIGINAL_FICTION",
        input_kind=input_kind,
        input_payload={"source_refs": [{"reference": "x", "role": "AUTHORED_FICTION_PACK"}]},
        duration_mode="FIXED",
        target_seconds=300,
        tolerance_percent=0.0,
        source_locale="zh-CN",
        automation_mode="AUTO_WITH_EXCEPTIONS",
        inference_mode="LOCAL_ONLY",
        research_mode="OFFLINE_IMPORT",
    )
    with database.transaction() as connection:
        repository = ExplainerRepository(connection)
        packet = repository.insert(
            "explainer_research_packets",
            {
                "video_id": str(video["id"]),
                "revision_no": 1,
                "status": "READY",
                "mode": "OFFLINE_IMPORT",
                "topic": "灯塔",
                "max_external_requests": 0,
                "content_hash": "a" * 64,
            },
        )
        repository.insert(
            "explainer_sources",
            {
                "packet_id": packet["id"],
                "video_id": str(video["id"]),
                "project_id": PROJECT_ID,
                "source_kind": "AUTHORED_FICTION_PACK",
                "title": "原创虚构事实包",
                "event_date_precision": "SECOND",
                "fetched_at": "2026-01-01T00:00:00Z",
                "body_sha256": "b" * 64,
                "credibility_kind": "AUTHORED_FICTION",
                "retrieved_via": "USER_SUPPLIED",
            },
        )
    return video


def _counts(database: Database) -> dict[str, int]:
    with database.connect() as connection:
        return {
            "runs": int(connection.execute("SELECT COUNT(*) FROM explainer_runs").fetchone()[0]),
            "steps": int(connection.execute("SELECT COUNT(*) FROM explainer_step_bindings").fetchone()[0]),
        }


# --------------------------------------------------------------------------- #
# EXP-01: the submission carries the whole frozen plan
# --------------------------------------------------------------------------- #
def test_start_run_forwards_outputs_so_the_plan_hash_survives(database: Database) -> None:
    """The exact reproduction: a legitimate plan used to come back STALE_PLAN."""

    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    assert report["executable"] is True

    run = service.start_run(
        project_id=PROJECT_ID,
        plan_hash=report["plan_hash"],
        idempotency_key="one-click",
        outputs=_outputs(),
        budget={},
        fallback_policy={},
    )
    assert run["plan_hash"] == report["plan_hash"]
    assert run["id"] != ""
    assert _counts(database)["runs"] == 1
    assert _counts(database)["steps"] == len(TASK_SKELETON)


def test_submission_without_outputs_explains_what_is_missing(database: Database) -> None:
    """No silent empty re-preflight: the caller is told the plan inputs are absent."""

    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    with pytest.raises(ExplainerContractError) as error:
        service.start_run(
            project_id=PROJECT_ID,
            plan_hash=report["plan_hash"],
            idempotency_key="missing-outputs",
        )
    assert error.value.code == "PLAN_INPUTS_REQUIRED"
    assert "outputs" in error.value.details["missing"]
    assert _counts(database) == {"runs": 0, "steps": 0}


def test_real_stale_plan_still_reports_stale_plan(database: Database) -> None:
    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    with pytest.raises(ExplainerContractError) as error:
        service.submit_run(
            project_id=PROJECT_ID, plan_hash="0" * 64, idempotency_key="stale", outputs=_outputs()
        )
    assert error.value.code == ExplainerErrorCode.STALE_PLAN.value
    # The submitted outputs are now visible in the diagnosis instead of [].
    assert error.value.details["submitted_outputs"] == ["zh-clean-169"]


def test_start_run_replays_idempotently(database: Database) -> None:
    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    first = service.start_run(
        project_id=PROJECT_ID, plan_hash=report["plan_hash"], idempotency_key="same",
        outputs=_outputs(),
    )
    second = service.start_run(
        project_id=PROJECT_ID, plan_hash=report["plan_hash"], idempotency_key="same",
        outputs=_outputs(),
    )
    assert second["idempotent_replay"] is True
    assert second["id"] == first["id"]
    assert _counts(database)["runs"] == 1


def test_budget_and_fallback_travel_with_the_submission(database: Database) -> None:
    """A budget sent at submission changes the frozen snapshot, so it must match."""

    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    budget = {"max_gpu_seconds": 1200, "max_wall_seconds": 600}
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs(), budget=budget)
    run = service.start_run(
        project_id=PROJECT_ID,
        plan_hash=report["plan_hash"],
        idempotency_key="with-budget",
        outputs=_outputs(),
        budget=budget,
    )
    stored = service.get_run(run_id=str(run["id"]))
    assert int(stored["budget_json"]["max_gpu_seconds"]) == 1200
    with pytest.raises(ExplainerContractError):
        # Submitting the same plan without its budget is a different plan.
        service.submit_run(
            project_id=PROJECT_ID,
            plan_hash=report["plan_hash"],
            idempotency_key="without-budget",
            outputs=_outputs(),
        )


# --------------------------------------------------------------------------- #
# EXP-08: 0 and [] are values, not "not provided"
# --------------------------------------------------------------------------- #
def test_explicit_zero_budget_is_preserved(database: Database) -> None:
    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    report = service.preflight(
        project_id=PROJECT_ID,
        outputs=_outputs(),
        budget={"max_creative_repairs_per_beat": 0, "max_technical_retries_per_step": 0},
    )
    budget = report["budget"]
    assert int(budget["max_creative_repairs_per_beat"]) == 0
    assert int(budget["max_technical_retries_per_step"]) == 0


def test_missing_budget_fields_still_get_the_product_default(database: Database) -> None:
    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs(), budget={})
    budget = report["budget"]
    assert int(budget["max_creative_repairs_per_beat"]) == 2
    assert int(budget["max_technical_retries_per_step"]) == 2


def test_explicit_empty_fallback_list_means_no_fallback(database: Database) -> None:
    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    report = service.preflight(
        project_id=PROJECT_ID,
        outputs=_outputs(),
        fallback_policy={"allowed_visual_fallbacks": []},
    )
    policy = report["policy_snapshot"]["fallback_policy"]
    assert list(policy["allowed_visual_fallbacks"]) == []


def test_missing_fallback_field_gets_the_documented_default(database: Database) -> None:
    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    report = service.preflight(project_id=PROJECT_ID, outputs=_outputs(), fallback_policy={})
    policy = report["policy_snapshot"]["fallback_policy"]
    # The retired ``I2V_TO_MOTION_STILL`` fallback is gone: the only documented
    # degradation is the information graphic, and it is the whole default policy.
    assert tuple(policy["allowed_visual_fallbacks"]) == ("I2V_TO_INFORMATION_GRAPHIC",)


def test_zero_budget_and_empty_fallback_change_the_plan_hash(database: Database) -> None:
    """The two snapshots must not be interchangeable."""

    _seed(database)
    service = ExplainerProductionService(database, capability_probe=_probe_all_available)
    explicit = service.preflight(
        project_id=PROJECT_ID,
        outputs=_outputs(),
        budget={"max_creative_repairs_per_beat": 0},
        fallback_policy={"allowed_visual_fallbacks": []},
    )
    default = service.preflight(project_id=PROJECT_ID, outputs=_outputs())
    assert explicit["plan_hash"] != default["plan_hash"]


def test_explicit_helper_covers_the_missing_none_and_falsy_cases() -> None:
    from local_drama.application.explainers.production import _explicit

    assert _explicit({"k": 0}, "k", 2) == 0
    assert _explicit({"k": []}, "k", ["x"]) == []
    assert _explicit({"k": None}, "k", 2) == 2
    assert _explicit({}, "k", 2) == 2
    assert _explicit({"k": ""}, "k", "d") == ""

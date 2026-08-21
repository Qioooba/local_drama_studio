from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from local_drama.application.projects import ProjectService
from local_drama.main import create_app
from tests.test_generation_variants import _project, _published_profile


def _context(workspace, database, code: str) -> dict[str, str]:
    project = _project(workspace, database, code)
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "SHOT_001", 3000, "MEDIUM")
    profile_id = _published_profile(workspace, database)
    now = "2026-08-20T00:00:00Z"
    with database.transaction() as connection:
        episode_id = str(episode["id"])
        shot_id = str(shot["id"])
        intent_id, variant_id = str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute(
            """INSERT INTO generation_intents
            (id,project_id,owner_type,owner_id,purpose,creative_goal,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,'SHOT',?,'I2V','QC policy test','ACTIVE',?,?,?,1,'v2')""",
            (intent_id, project_id, shot_id, now, now, "test"),
        )
        connection.execute(
            """INSERT INTO generation_variants
            (id,intent_id,variant_no,variant_type,parent_variant_id,branch_reason,prompt_revision_id,
             capability_profile_version_id,parameter_set_json,seed_policy,explicit_seed,input_fingerprint,
             recipe_hash,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,'BASE',NULL,'base',NULL,?,'{}','EXPLICIT',1,?,?,'COMPLETED',?,?,?,1,'v2')""",
            (variant_id, intent_id, profile_id, "a" * 64, "b" * 64, now, now, "test"),
        )
    return {
        "project_id": project_id, "episode_id": episode_id, "shot_id": shot_id,
        "intent_id": intent_id, "variant_id": variant_id, "profile_id": profile_id,
    }


def _machine(database, variant_id: str, status: str) -> str:
    run_id = str(uuid.uuid4())
    now = "2026-08-20T00:00:00Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO machine_check_runs
            (id,subject_type,subject_id,policy_version,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,'GENERATION_VARIANT',?,'qc-test-v1',?,?,?,'qc-test',1,'v2')""",
            (run_id, variant_id, status, now, now),
        )
    return run_id


def _child(database, context: dict[str, str], parent_id: str, variant_no: int) -> str:
    child_id, now = str(uuid.uuid4()), "2026-08-20T00:00:01Z"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO generation_variants
            (id,intent_id,variant_no,variant_type,parent_variant_id,branch_reason,prompt_revision_id,
             capability_profile_version_id,parameter_set_json,seed_policy,explicit_seed,input_fingerprint,
             recipe_hash,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,'RESAMPLE_NEW_SEED',?,'QC_AUTO_RETRY | FACE',NULL,?,'{}','EXPLICIT',?,
             ?,?,'COMPLETED',?,?,?,1,'v2')""",
            (child_id, context["intent_id"], variant_no, parent_id, context["profile_id"], variant_no,
             f"{variant_no:064d}"[-64:], f"{variant_no + 1:064d}"[-64:], now, now, "qc-agent"),
        )
    return child_id


def test_qc_policy_inherits_project_episode_shot(workspace, database) -> None:
    context = _context(workspace, database, "qc_inheritance")
    with TestClient(create_app(workspace)) as client:
        project = client.put(f"/api/v1/projects/{context['project_id']}/qc-policies", json={
            "owner_type": "PROJECT", "owner_id": context["project_id"], "stage": "VIDEO",
            "policy": {"checks": ["identity"]}, "max_auto_rerolls": 2,
            "auto_reroll_categories": ["FACE", "IDENTITY"],
        })
        assert project.status_code == 200, project.text
        project_v1 = project.json()["policy"]
        project_v2 = client.put(f"/api/v1/projects/{context['project_id']}/qc-policies", json={
            "owner_type": "PROJECT", "owner_id": context["project_id"], "stage": "VIDEO",
            "policy": {"checks": ["identity", "continuity"]}, "max_auto_rerolls": 2,
            "auto_reroll_categories": ["IDENTITY"], "expected_revision": project_v1["revision"],
            "reason": "tighten project policy",
        })
        assert project_v2.status_code == 200 and project_v2.json()["policy"]["version_no"] == 2
        episode = client.put(f"/api/v1/projects/{context['project_id']}/qc-policies", json={
            "owner_type": "EPISODE", "owner_id": context["episode_id"], "stage": "VIDEO",
            "policy": {"checks": ["identity"]}, "max_auto_rerolls": 1,
            "auto_reroll_categories": ["IDENTITY"],
        })
        assert episode.status_code == 200, episode.text
        inherited = client.get(
            f"/api/v1/projects/{context['project_id']}/qc-policies",
            params={"stage": "VIDEO", "episode_id": context["episode_id"], "shot_id": context["shot_id"]},
        ).json()["resolution"]
        assert inherited["source"] == "EPISODE" and inherited["max_auto_rerolls"] == 1

        shot = client.put(f"/api/v1/projects/{context['project_id']}/qc-policies", json={
            "owner_type": "SHOT", "owner_id": context["shot_id"], "stage": "VIDEO",
            "policy": {"checks": ["identity"]}, "max_auto_rerolls": 0,
            "auto_reroll_categories": [],
        })
        assert shot.status_code == 200, shot.text
        resolved = client.get(
            f"/api/v1/projects/{context['project_id']}/qc-policies",
            params={"stage": "VIDEO", "episode_id": context["episode_id"], "shot_id": context["shot_id"]},
        ).json()["resolution"]
        assert resolved["source"] == "SHOT" and resolved["max_auto_rerolls"] == 0
        with database.connect() as connection:
            versions = connection.execute(
                """SELECT version_no,is_frozen FROM generation_qc_policy_versions
                WHERE policy_set_id=? ORDER BY version_no""",
                (project_v1["policy_set_id"],),
            ).fetchall()
            assert [(row["version_no"], row["is_frozen"]) for row in versions] == [(1, 1), (2, 1)]

        run_id = _machine(database, context["variant_id"], "FAIL")
        decision = client.post(
            f"/api/v1/generation/variants/{context['variant_id']}/qc:decide",
            json={"machine_check_run_id": run_id, "category": "IDENTITY"},
        ).json()["disposition"]
        assert decision["disposition"] == "WAITING_GATE"


def test_bounded_qc_reroll_preserves_candidates_and_never_approves(workspace, database) -> None:
    context = _context(workspace, database, "qc_bounded")
    with TestClient(create_app(workspace)) as client:
        response = client.put(f"/api/v1/projects/{context['project_id']}/qc-policies", json={
            "owner_type": "PROJECT", "owner_id": context["project_id"], "stage": "VIDEO",
            "policy": {"attention_selection": "REQUIRE_CONFIRMATION"}, "max_auto_rerolls": 1,
            "auto_reroll_categories": ["FACE"],
        })
        assert response.status_code == 200, response.text
        with database.connect() as connection:
            variants_before = connection.execute("SELECT COUNT(*) FROM generation_variants").fetchone()[0]
            approvals_before = connection.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0]

        first_run = _machine(database, context["variant_id"], "FAIL")
        first = client.post(
            f"/api/v1/generation/variants/{context['variant_id']}/qc:decide",
            json={"machine_check_run_id": first_run, "category": "FACE"},
        ).json()["disposition"]
        assert first["disposition"] == "AUTO_REROLL_ALLOWED" and first["retry_ordinal"] == 0
        replay = client.post(
            f"/api/v1/generation/variants/{context['variant_id']}/qc:decide",
            json={"machine_check_run_id": first_run, "category": "FACE"},
        ).json()["disposition"]
        assert replay["id"] == first["id"] and replay["idempotent_replay"] is True

        pass_run = _machine(database, context["variant_id"], "PASS")
        passed = client.post(
            f"/api/v1/generation/variants/{context['variant_id']}/qc:decide",
            json={"machine_check_run_id": pass_run, "category": "FACE"},
        ).json()["disposition"]
        assert passed["disposition"] == "PASS"

        child_id = _child(database, context, context["variant_id"], 2)
        attached = client.post(
            f"/api/v1/generation/variants/{context['variant_id']}/qc:attach-child",
            json={"link_id": first["id"], "child_variant_id": child_id},
        )
        assert attached.status_code == 200, attached.text
        second_run = _machine(database, child_id, "FAIL")
        second = client.post(
            f"/api/v1/generation/variants/{child_id}/qc:decide",
            json={"machine_check_run_id": second_run, "category": "FACE"},
        ).json()["disposition"]
        assert second["disposition"] == "WAITING_GATE" and second["retry_ordinal"] == 1

        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM generation_variants").fetchone()[0] == variants_before + 1
            assert connection.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0] == approvals_before
            assert connection.execute("SELECT COUNT(*) FROM variant_qc_links").fetchone()[0] == 3

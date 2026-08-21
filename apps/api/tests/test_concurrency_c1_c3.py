from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from local_drama.application.errors import api_error_from_domain
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.reviews import ReviewService
from local_drama.application.story_assets import StoryAssetService
from local_drama.domain.errors import DomainRuleError
from tests.test_generation_variants import _image, _plan, _project, _published_profile
from tests.test_qc_auto_reroll_policy import _child, _context


def test_c1_two_asset_updates_same_revision_have_one_winner_and_http_409_loser(workspace, database) -> None:
    project = _project(workspace, database, "concurrency_c1")
    service = StoryAssetService(database, workspace)
    asset = service.create_asset(str(project["id"]), "CHARACTER", "C1_ASSET", "原始名称")
    barrier = threading.Barrier(2)

    def update(name: str):
        barrier.wait(timeout=10)
        try:
            return ("OK", StoryAssetService(database, workspace).update_asset(str(asset["id"]), 1, name=name))
        except DomainRuleError as error:
            return ("ERROR", error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(update, ("人工更新", "后台更新")))

    winners = [value for status, value in outcomes if status == "OK"]
    losers = [value for status, value in outcomes if status == "ERROR"]
    assert len(winners) == len(losers) == 1
    assert losers[0].code == "STORY_ASSET_REVISION_CONFLICT"
    assert api_error_from_domain(losers[0]).status_code == 409
    final = service.get_asset(str(asset["id"]))
    assert final["revision"] == 2
    assert final["name"] == winners[0]["name"]
    assert final["name"] in {"人工更新", "后台更新"}
    with database.connect() as connection:
        audits = connection.execute(
            "SELECT before_revision,after_revision FROM audit_events WHERE action='STORY_ASSET_UPDATED' AND subject_id=?",
            (asset["id"],),
        ).fetchall()
    assert [(row["before_revision"], row["after_revision"]) for row in audits] == [(1, 2)]


def test_c2_human_and_workflow_rerolls_create_unique_sibling_variants(workspace, database) -> None:
    project = _project(workspace, database, "concurrency_c2")
    project_id = str(project["id"])
    media_version_id = _image(workspace, database, project_id, "concurrency-c2.png")
    profile_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "PROJECT", project_id, "I2V", "C2 sibling rerolls")
    parent = generation.create_variant(str(intent["id"]), _plan(profile_id, media_version_id))
    with database.connect() as connection:
        frozen_parent = dict(connection.execute("SELECT * FROM generation_variants WHERE id=?", (parent["id"],)).fetchone())
    barrier = threading.Barrier(2)

    def reroll(reason_code: str, seed: int, key: str):
        barrier.wait(timeout=10)
        return GenerationService(database, workspace).reroll_variant(
            str(parent["id"]), reason_code=reason_code, reason_note="C2 concurrent",
            explicit_seed=seed, profile_version_id=None, bindings=None, idempotency_key=key,
        )

    requests = (("USER_REROLL", 8, "c2-human-reroll"), ("QC_AUTO_RETRY", 9, "c2-workflow-reroll"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda item: reroll(*item), requests))

    assert len({str(item["variant"]["id"]) for item in results}) == 2
    assert len({str(item["job"]["id"]) for item in results}) == 2
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM generation_variants WHERE intent_id=? ORDER BY variant_no", (intent["id"],),
        ).fetchall()
        keys = connection.execute(
            "SELECT idempotency_key FROM jobs WHERE subject_type='GENERATION_VARIANT' AND subject_id IN (?,?) ORDER BY idempotency_key",
            tuple(str(item["variant"]["id"]) for item in results),
        ).fetchall()
    assert [int(row["variant_no"]) for row in rows] == [1, 2, 3]
    assert all(str(row["parent_variant_id"] or "") == str(parent["id"]) for row in rows[1:])
    assert {str(row["branch_reason"]).split(" | ")[0] for row in rows[1:]} == {"USER_REROLL", "QC_AUTO_RETRY"}
    assert [str(row["idempotency_key"]) for row in keys] == ["c2-human-reroll", "c2-workflow-reroll"]
    assert dict(rows[0]) == frozen_parent


def _png(workspace, name: str, color: str):
    path = workspace.work_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c={color}:s=160x90:d=0.1", "-frames:v", "1", "-y", str(path)],
        check=True, capture_output=True,
    )
    return path


def test_c3_selection_during_new_candidate_promotion_never_steals_current_pointer(workspace, database, monkeypatch) -> None:
    context = _context(workspace, database, "concurrency_c3")
    old_variant_id = context["variant_id"]
    new_variant_id = _child(database, context, old_variant_id, 2)
    old_source = _png(workspace, "c3-old.png", "blue")
    old_media = MediaService(database, workspace).import_file(
        context["project_id"], old_source, purpose="GENERATED_OUTPUT", owner_type="GENERATION_VARIANT",
        owner_id=old_variant_id, media_kind="IMAGE", stage="PROXY",
    )

    jobs = JobService(database, workspace)
    job = jobs.create_job(
        context["project_id"], "GENERATION_VARIANT", "GENERATION_VARIANT", new_variant_id,
        "CPU", {"variant_id": new_variant_id}, "c3-new-candidate", execution_profile_version_id=context["profile_id"], max_attempts=1,
    )
    claim = jobs.claim("c3-worker", ["CPU"])
    assert claim is not None and str(claim["job"]["id"]) == str(job["id"])
    new_output = _png(workspace, "jobs/c3-output.png", "red")
    artifact = jobs.register_artifact(str(claim["attempt"]["id"]), "GENERATED_IMAGE", new_output.relative_to(workspace.work_root).as_posix())
    jobs.complete(str(claim["attempt"]["id"]), str(claim["attempt"]["lease_token"]), "c3-worker", success=True)

    reviews = ReviewService(database, workspace)
    media = MediaService(database, workspace)
    promotion_prepared = threading.Event()
    selection_read = threading.Event()
    original_copy = media._copy_into_project
    original_media_read = reviews._media

    def gated_copy(*args, **kwargs):
        result = original_copy(*args, **kwargs)
        promotion_prepared.set()
        assert selection_read.wait(timeout=10), "selection did not read old candidate"
        return result

    def gated_media_read(*args, **kwargs):
        result = original_media_read(*args, **kwargs)
        selection_read.set()
        assert promotion_prepared.wait(timeout=10), "promotion did not prepare output"
        return result

    monkeypatch.setattr(media, "_copy_into_project", gated_copy)
    monkeypatch.setattr(reviews, "_media", gated_media_read)
    with ThreadPoolExecutor(max_workers=2) as executor:
        selected_future = executor.submit(reviews.select_version, str(old_media["media_version_id"]), "PROXY_WINNER")
        promoted_future = executor.submit(media.promote_job_artifact, str(artifact["id"]), purpose="GENERATED_OUTPUT", media_kind="IMAGE", stage="PROXY")
        selected = selected_future.result(timeout=15)
        promoted = promoted_future.result(timeout=15)

    with database.connect() as connection:
        old_asset = connection.execute("SELECT selected_version_id FROM media_assets WHERE id=?", (selected["media_asset_id"],)).fetchone()
        new_asset = connection.execute("SELECT selected_version_id FROM media_assets WHERE id=?", (promoted["media_asset_id"],)).fetchone()
        selections = connection.execute(
            "SELECT media_version_id,selection_type FROM selections WHERE media_asset_id=? ORDER BY created_at,id", (selected["media_asset_id"],),
        ).fetchall()
    assert str(old_asset["selected_version_id"]) == str(old_media["media_version_id"])
    assert new_asset["selected_version_id"] is None
    assert [(str(row["media_version_id"]), str(row["selection_type"])) for row in selections] == [
        (str(old_media["media_version_id"]), "PROXY_WINNER")
    ]
    assert str(promoted["media_version_id"]) != str(old_media["media_version_id"])

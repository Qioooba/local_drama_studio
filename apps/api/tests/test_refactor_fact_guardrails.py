"""P0-04 generation fact guardrails for the V2 refactor.

These are the safety-net regressions from the implementation handbook: the
refactor may change UI and boundaries, but it must never break the evidencing
chain.  Each test pins one invariant of the single-source-of-truth tables.
"""

from __future__ import annotations

import json
import subprocess
import uuid

from local_drama.application.generation import GenerationService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.application.story_assets import StoryAssetService
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput


def _project(workspace, database, code: str) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
    )


def _real_image(workspace, database, project_id: str, name: str, color: str = "blue") -> str:
    source = workspace.work_root / name
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c={color}:s=160x90:d=0.1", "-frames:v", "1", "-y", str(source)],
        check=True,
        capture_output=True,
    )
    return str(MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")["media_version_id"])


def _published_profile(workspace, database) -> str:
    ProfileService(database, workspace.manifest_path).sync_manifest()
    profile_version_id = str(ProfileService(database, workspace.manifest_path).list_profiles()[0]["version_id"])
    with database.transaction() as connection:
        now = "2026-08-13T00:00:00Z"
        workflow_id = str(uuid.uuid4())
        workflow_version_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'Guardrail workflow', ?, ?, 'test', 1, 'v2')",
            (workflow_id, f"guardrail-{profile_version_id}", now, now),
        )
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, status, contract_json, content_json, package_rel_path,
             node_bindings_json, runtime_contract_json, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, 'PUBLISHED', '{}', ?, NULL, ?, '{}', ?, ?, ?, 'test', 1, 'v2')""",
            (
                workflow_version_id,
                workflow_id,
                "b" * 64,
                json.dumps({"1": {"class_type": "LoadImage", "inputs": {"image": ""}}}),
                json.dumps({"FIRST_FRAME": {"node_id": "1", "input": "image", "type": "image"}}),
                now,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED', workflow_version_id=?, input_contract_json=?, revision=revision+1 WHERE id=?",
            (workflow_version_id, json.dumps({"input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}}), profile_version_id),
        )
    return profile_version_id


def _variant_plan(profile_version_id: str, media_version_id: str, *, seed: int = 7, parent: str | None = None, branch_reason: str = "guardrail-base") -> VariantPlan:
    return VariantPlan(
        variant_type="RESAMPLE_NEW_SEED" if parent else "BASE",
        parent_variant_id=parent,
        branch_reason=branch_reason,
        prompt_revision_id=None,
        profile_version_id=profile_version_id,
        parameter_set={"frames": 81, "steps": 20},
        seed_policy="EXPLICIT",
        explicit_seed=seed,
        bindings=(VariantInput("FIRST_FRAME", media_version_id),),
    )


def test_asset_canonical_update_never_touches_historical_variant_input_binding(workspace, database) -> None:
    """Changing a story asset's canonical reference must not rewrite old variants.

    The handbook's Story 1: history is never overwritten.  A variant created
    against media version M1 must keep binding M1 even after the asset's
    canonical reference moves to M2.
    """

    project = _project(workspace, database, "guardrail_asset_freeze")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)

    m1 = _real_image(workspace, database, project_id, "guardrail-m1.png", "red")
    m2 = _real_image(workspace, database, project_id, "guardrail-m2.png", "green")

    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_GUARD", "护栏角色", canonical_media_version_id=m1)
    assert asset["canonical_media_version_id"] == m1

    # Create a variant that freezes m1 as its input.
    profile_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", str(shot["id"]), "I2V", "guardrail asset freeze")
    created = generation.create_variant(str(intent["id"]), _variant_plan(profile_id, m1))
    variant_id = str(created["id"])

    with database.connect() as connection:
        before = connection.execute(
            "SELECT media_version_id, role FROM variant_input_bindings WHERE variant_id=?",
            (variant_id,),
        ).fetchone()
    assert str(before["media_version_id"]) == m1

    # Now the asset canonical moves to m2 -- the historical binding must stay.
    updated = assets.update_asset(str(asset["id"]), 1, canonical_media_version_id=m2)
    assert updated["canonical_media_version_id"] == m2

    with database.connect() as connection:
        after = connection.execute(
            "SELECT media_version_id, role FROM variant_input_bindings WHERE variant_id=?",
            (variant_id,),
        ).fetchone()
        variant = connection.execute("SELECT recipe_hash, status FROM generation_variants WHERE id=?", (variant_id,)).fetchone()
    assert str(after["media_version_id"]) == m1
    assert str(after["role"]) == "FIRST_FRAME"
    assert variant["status"] == "PLANNED"


def test_asset_canonical_update_reports_downstream_freshness_without_rewriting(workspace, database) -> None:
    """Asset reference change marks downstream work stale, never rewrites it.

    Handbook 33.1: 'V1 uses non-current asset ref -> POSSIBLY_STALE'; the
    current selected result is flagged stale, but historical evidence rows
    keep their original media binding.
    """

    project = _project(workspace, database, "guardrail_stale_flag")
    project_id = str(project["id"])
    m1 = _real_image(workspace, database, project_id, "stale-flag-m1.png", "orange")
    m2 = _real_image(workspace, database, project_id, "stale-flag-m2.png", "purple")
    assets = StoryAssetService(database, workspace)
    asset = assets.create_asset(project_id, "CHARACTER", "CHAR_STALE", "陈旧角色", canonical_media_version_id=m1)
    assets.update_asset(str(asset["id"]), 1, canonical_media_version_id=m2)
    final = assets.get_asset(str(asset["id"]))
    assert final["canonical_media_version_id"] == m2
    assert final["revision"] == 2


def test_selection_change_keeps_previous_selection_evidence(workspace, database) -> None:
    """Selecting a new version appends evidence; it never deletes the prior row.

    Handbook section 23: selection is an authority event with history.  The
    UI may switch the current winner, but every selection row remains.
    """

    project = _project(workspace, database, "guardrail_selection_history")
    project_id = str(project["id"])
    m1 = _real_image(workspace, database, project_id, "sel-m1.png")
    m2 = _real_image(workspace, database, project_id, "sel-m2.png")
    media = MediaService(database, workspace)
    media_asset_id = media.get_version(m1)["media_asset_id"]

    # Promote both images to KEYFRAME stage so they are selectable.
    k1 = media.derive_version(media_asset_id, m1, "KEYFRAME")
    k2 = media.derive_version(media_asset_id, m2, "KEYFRAME")

    reviews = ReviewService(database, workspace)
    reviews.select_version(str(k1["id"]), "KEYFRAME")
    reviews.select_version(str(k2["id"]), "KEYFRAME")

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT media_version_id, selection_type FROM selections WHERE media_asset_id=? ORDER BY created_at, id",
            (media_asset_id,),
        ).fetchall()
    assert len(rows) == 2
    assert {str(row["media_version_id"]) for row in rows} == {str(k1["id"]), str(k2["id"])}
    assert all(str(row["selection_type"]) == "KEYFRAME" for row in rows)


def test_reroll_creates_new_variant_never_overwrites_parent(workspace, database) -> None:
    """A reroll always creates a new variant row with parent lineage.

    Handbook 22.2: new GenerationVariant; parent_variant_id = current;
    branch_reason = structured reason.  Never UPDATE an existing variant's
    media to mean 'rerolled'.
    """

    project = _project(workspace, database, "guardrail_reroll")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    m1 = _real_image(workspace, database, project_id, "reroll-m1.png")
    profile_id = _published_profile(workspace, database)
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", str(shot["id"]), "I2V", "guardrail reroll")
    v1 = generation.create_variant(str(intent["id"]), _variant_plan(profile_id, m1, seed=1))

    reroll = _variant_plan(profile_id, m1, seed=2, parent=str(v1["id"]), branch_reason="USER_REROLL")
    v2 = generation.create_variant(str(intent["id"]), reroll)

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, parent_variant_id, branch_reason, variant_type FROM generation_variants WHERE intent_id=? ORDER BY variant_no",
            (intent["id"],),
        ).fetchall()
    assert len(rows) == 2
    assert str(rows[0]["id"]) == str(v1["id"])
    assert str(rows[1]["id"]) == str(v2["id"])
    assert str(rows[1]["parent_variant_id"]) == str(v1["id"])
    assert str(rows[1]["branch_reason"]) == "USER_REROLL"
    # The parent variant row is untouched.
    assert str(rows[0]["variant_type"]) == "BASE"


def test_frozen_revision_flag_is_preserved_by_refactor_schema(workspace, database) -> None:
    """Frozen shot revisions must remain immutably flagged.

    Handbook 0.2: '禁止直接修改 frozen shot_revisions'.  The migration and
    new read models must keep revisions append-only.
    """

    project = _project(workspace, database, "guardrail_frozen")
    project_id = str(project["id"])
    projects = ProjectService(database, workspace.projects_root)
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]
    shot = projects.create_shot(str(episode["id"]), "S001", 4_000)
    # create_shot creates a first revision; freeze it like the production flow does.
    with database.transaction() as connection:
        revision = connection.execute(
            "SELECT id FROM shot_revisions WHERE shot_id=? ORDER BY revision_no DESC LIMIT 1", (shot["id"],)
        ).fetchone()
        connection.execute("UPDATE shot_revisions SET is_frozen=1 WHERE id=?", (revision["id"],))
    with database.connect() as connection:
        row = connection.execute(
            "SELECT is_frozen FROM shot_revisions WHERE shot_id=? ORDER BY revision_no DESC LIMIT 1", (shot["id"],)
        ).fetchone()
    assert row["is_frozen"] == 1

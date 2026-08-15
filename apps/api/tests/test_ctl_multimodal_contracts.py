from __future__ import annotations

import hashlib
import json
import subprocess
import uuid

import pytest

from local_drama.application.generation import GenerationService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
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
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def _published_profile(workspace, database) -> str:
    ProfileService(database, workspace.manifest_path).sync_manifest()
    return str(ProfileService(database, workspace.manifest_path).list_profiles()[0]["version_id"])


def _media(workspace, database, project_id: str, name: str, kind: str) -> str:
    source = workspace.work_root / name
    if kind == "IMAGE":
        command = [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=0.1", "-frames:v", "1", "-y", str(source)]
    else:
        command = [workspace.ffmpeg_path, "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=2:duration=1", "-an", "-y", str(source)]
    subprocess.run(command, check=True, capture_output=True)
    return str(MediaService(database, workspace).import_file(project_id, source, media_kind=kind)["media_version_id"])


def _driving_profile(workspace, database) -> str:
    profile_version_id = _published_profile(workspace, database)
    with database.transaction() as connection:
        workflow_id = str(uuid.uuid4())
        workflow_version_id = str(uuid.uuid4())
        now = "2026-08-15T00:00:00Z"
        connection.execute(
            "INSERT INTO workflows (id, code, title, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, 'Driving contract workflow', ?, ?, 'test', 1, 'v2')",
            (workflow_id, f"driving-contract-{profile_version_id}", now, now),
        )
        connection.execute(
            """INSERT INTO workflow_versions
            (id, workflow_id, version_no, content_hash, status, contract_json, content_json, package_rel_path,
             node_bindings_json, runtime_contract_json, published_at, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, 'PUBLISHED', '{}', ?, NULL, ?, '{}', ?, ?, ?, 'test', 1, 'v2')""",
            (
                workflow_version_id,
                workflow_id,
                hashlib.sha256(workflow_version_id.encode()).hexdigest(),
                json.dumps({"1": {"class_type": "LoadVideo", "inputs": {"video": ""}}, "2": {"class_type": "LoadImage", "inputs": {"image": ""}}}),
                json.dumps({"DRIVING_VIDEO": {"node_id": "1", "input": "video"}, "CHARACTER_REFERENCE": {"node_id": "2", "input": "image"}}),
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """UPDATE execution_profile_versions SET status='PUBLISHED', workflow_version_id=?,
            input_contract_json=?, parameter_schema_json=?, revision=revision+1 WHERE id=?""",
            (
                workflow_version_id,
                json.dumps(
                    {
                        "transport": "LOOPBACK_HTTP",
                        "input_slots": {
                            "DRIVING_VIDEO": {
                                "min": 1,
                                "max": 1,
                                "media_kinds": ["VIDEO"],
                                "weight": {"min": 0.1, "max": 1.0, "required": True},
                            },
                            "CHARACTER_REFERENCE": {
                                "min": 1,
                                "max": 3,
                                "media_kinds": ["IMAGE"],
                                "supports_weight": True,
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "seed": {"support": "REQUIRED", "determinism": "BEST_EFFORT"},
                        "capabilities": {
                            "driving_video": {"enabled": True, "support": "NATIVE"},
                            "character_reference": {"enabled": True, "support": "NATIVE"},
                            "performance_binding": {"enabled": True, "support": "NATIVE"},
                            "timed_direction": {"enabled": True, "support": "NATIVE"},
                            "reference": {"enabled": True, "support": "NATIVE"},
                        },
                    }
                ),
                profile_version_id,
            ),
        )
    return profile_version_id


def _plan(profile_version_id: str, video_id: str, image_id: str, *, weight: float | None = 0.3, ordinal: int = 0) -> VariantPlan:
    return VariantPlan(
        variant_type="PERFORMANCE_DRIVEN",
        parent_variant_id=None,
        branch_reason="ctl multimodal contract",
        prompt_revision_id=None,
        profile_version_id=profile_version_id,
        parameter_set={
            "PROMPT": "a character walks and speaks",
            "SEED": 17,
            "performance_bindings": [
                {
                    "actor_id": "character-1",
                    "action": "walk_and_speak",
                    "start_us": 0,
                    "end_us": 1_000_000,
                    "binding_type": "CHARACTER_DRIVING",
                    "source_role": "DRIVING_VIDEO",
                }
            ],
            "timed_directions": [{"time_us": 0, "direction": "PAN_LEFT", "strength": 0.4}],
        },
        seed_policy="EXPLICIT",
        explicit_seed=17,
        bindings=(
            VariantInput("DRIVING_VIDEO", video_id, 0, 0.7),
            VariantInput("CHARACTER_REFERENCE", image_id, ordinal, weight),
        ),
    )


def test_ctl003_driving_variant_is_local_immutable_and_persists_weighted_bindings(workspace, database) -> None:
    project = _project(workspace, database, "ctl_multimodal")
    project_id = str(project["id"])
    profile_id = _driving_profile(workspace, database)
    video_id = _media(workspace, database, project_id, "driving.mp4", "VIDEO")
    image_id = _media(workspace, database, project_id, "character.png", "IMAGE")
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "T2V", "driving")

    plan = _plan(profile_id, video_id, image_id)
    preflight = generation.preflight_variant(str(intent["id"]), plan)
    assert preflight["status"] == "READY"
    created = generation.create_variant(str(intent["id"]), plan)
    bindings = {item["role"]: item for item in created["bindings"]}
    assert bindings["DRIVING_VIDEO"]["weight"] == 0.7
    assert bindings["CHARACTER_REFERENCE"]["weight"] == 0.3
    assert created["variant_type"] == "PERFORMANCE_DRIVEN"


def test_ctl004_rejects_wrong_kind_order_and_weight_contract(workspace, database) -> None:
    project = _project(workspace, database, "ctl_contract_rejections")
    project_id = str(project["id"])
    profile_id = _driving_profile(workspace, database)
    video_id = _media(workspace, database, project_id, "source.mp4", "VIDEO")
    image_id = _media(workspace, database, project_id, "reference.png", "IMAGE")
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "T2V", "contract rejection")

    wrong_kind = _plan(profile_id, image_id, image_id)
    with pytest.raises(DomainRuleError) as error:
        generation.preflight_variant(str(intent["id"]), wrong_kind)
    assert error.value.code == "PROFILE_INPUT_MEDIA_KIND_INVALID"

    gap = _plan(profile_id, video_id, image_id, ordinal=2)
    with pytest.raises(DomainRuleError) as error:
        generation.preflight_variant(str(intent["id"]), gap)
    assert error.value.code == "PROFILE_INPUT_ORDER_INVALID"

    with database.transaction() as connection:
        row = connection.execute("SELECT input_contract_json FROM execution_profile_versions WHERE id=?", (profile_id,)).fetchone()
        contract = json.loads(row["input_contract_json"])
        contract["input_slots"]["DRIVING_VIDEO"]["weight"] = {"min": 0.8, "max": 1.0, "required": True}
        connection.execute("UPDATE execution_profile_versions SET input_contract_json=?, revision=revision+1 WHERE id=?", (json.dumps(contract), profile_id))
    out_of_range = _plan(profile_id, video_id, image_id)
    with pytest.raises(DomainRuleError) as error:
        generation.preflight_variant(str(intent["id"]), out_of_range)
    assert error.value.code == "PROFILE_INPUT_WEIGHT_INVALID"


def test_ctl003_rejects_profile_without_declared_local_driving_capability(workspace, database) -> None:
    project = _project(workspace, database, "ctl_capability_gate")
    project_id = str(project["id"])
    profile_id = _driving_profile(workspace, database)
    video_id = _media(workspace, database, project_id, "driving-gate.mp4", "VIDEO")
    image_id = _media(workspace, database, project_id, "character-gate.png", "IMAGE")
    with database.transaction() as connection:
        row = connection.execute("SELECT parameter_schema_json FROM execution_profile_versions WHERE id=?", (profile_id,)).fetchone()
        schema = json.loads(row["parameter_schema_json"])
        schema["capabilities"].pop("driving_video")
        connection.execute("UPDATE execution_profile_versions SET parameter_schema_json=?, revision=revision+1 WHERE id=?", (json.dumps(schema), profile_id))
    generation = GenerationService(database, workspace)
    intent = generation.create_intent(project_id, "SHOT", project_id, "T2V", "unsupported driving")
    with pytest.raises(DomainRuleError) as error:
        generation.preflight_variant(str(intent["id"]), _plan(profile_id, video_id, image_id))
    assert error.value.code == "PROFILE_DRIVING_UNSUPPORTED"

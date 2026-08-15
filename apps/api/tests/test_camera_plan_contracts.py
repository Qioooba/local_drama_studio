from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.profiles import ProfileService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _profile_with_camera(workspace, database, camera: dict[str, object]) -> str:
    service = ProfileService(database, workspace.manifest_path)
    source = service.sync_manifest()["profiles"][0]
    schema = {"seed": {"determinism": "EXPLICIT"}, "capabilities": {"camera": camera}}
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED', parameter_schema_json=? WHERE id=?",
            (json.dumps(schema, separators=(",", ":")), source["version_id"]),
        )
    return str(source["version_id"])


@pytest.mark.parametrize(
    ("camera", "expected_mode", "submission_allowed", "prompt"),
    [
        ({"support": "NATIVE"}, "NATIVE", True, ""),
        ({"support": "PROMPT_FALLBACK", "prompt_fallback": True}, "PROMPT_FALLBACK", True, "camera: PUSH_IN"),
        ({"support": "UNSUPPORTED"}, "UNSUPPORTED", False, ""),
        ({}, "UNSUPPORTED", False, ""),
    ],
)
def test_camera_resolution_follows_explicit_published_capability_contract(
    workspace, database, camera, expected_mode: str, submission_allowed: bool, prompt: str
) -> None:
    profile_id = _profile_with_camera(workspace, database, camera)
    service = ProfileService(database, workspace.manifest_path)
    before = database.path.read_bytes()
    result = service.resolve_camera_plan(
        profile_id,
        shot_type="CLOSEUP",
        movement="PUSH_IN",
        direction="FORWARD",
        intensity=0.6,
        curve="EASE_IN_OUT",
    )
    after = database.path.read_bytes()

    assert result["camera_plan"]["mode"] == expected_mode
    assert result["camera_plan"]["prompt_text"] == prompt
    assert result["submission_allowed"] is submission_allowed
    assert result["runtime_contacted"] is False
    assert result["network_contacted"] is False
    assert result["mutated"] is False
    assert before == after


def test_camera_prompt_fallback_requires_an_explicit_profile_declaration(workspace, database) -> None:
    profile_id = _profile_with_camera(workspace, database, {"support": "PROMPT_FALLBACK"})
    with pytest.raises(DomainRuleError) as error:
        ProfileService(database, workspace.manifest_path).resolve_camera_plan(
            profile_id,
            shot_type="CLOSEUP",
            movement="PUSH_IN",
            direction="FORWARD",
            intensity=0.5,
            curve="LINEAR",
        )
    assert error.value.code == "PROFILE_CAMERA_FALLBACK_INVALID"


def test_camera_resolution_route_exposes_truthful_positive_and_negative_contracts(workspace, database) -> None:
    profile_id = _profile_with_camera(workspace, database, {"support": "NATIVE"})
    with TestClient(create_app(workspace)) as client:
        native = client.post(
            f"/api/v1/profile-versions/{profile_id}:resolve-camera-plan",
            json={"shot_type": "WIDE", "movement": "ORBIT", "direction": "CLOCKWISE", "intensity": 0.4, "curve": "EASE_OUT"},
        )
        assert native.status_code == 200, native.text
        assert native.json()["resolution"]["camera_plan"]["mode"] == "NATIVE"

        with database.transaction() as connection:
            connection.execute(
                "UPDATE execution_profile_versions SET parameter_schema_json=? WHERE id=?",
                (json.dumps({"capabilities": {"camera": {"support": "UNSUPPORTED"}}}), profile_id),
            )
        unsupported = client.post(
            f"/api/v1/profile-versions/{profile_id}:resolve-camera-plan",
            json={"shot_type": "WIDE", "movement": "ORBIT", "direction": "CLOCKWISE", "intensity": 0.4, "curve": "EASE_OUT"},
        )
        assert unsupported.status_code == 200, unsupported.text
        resolution = unsupported.json()["resolution"]
        assert resolution["camera_plan"]["mode"] == "UNSUPPORTED"
        assert resolution["submission_allowed"] is False
        assert resolution["runtime_contacted"] is False
        assert resolution["network_contacted"] is False


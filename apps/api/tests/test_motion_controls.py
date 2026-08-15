from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.media import MediaService
from local_drama.application.motion_controls import MotionControlService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415408d763f8cfc0f01f00050001ff89993d1d0000000049454e44ae426082"
)


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


def _profile(database, workspace, *, enabled: bool) -> str:
    ProfileService(database, workspace.manifest_path).sync_manifest()
    profile_id = str(ProfileService(database, workspace.manifest_path).list_profiles()[0]["version_id"])
    schema = {
        "capabilities": {
            "motion_mask": {"enabled": enabled, "input_role": "MOTION_MASK"},
            "inpaint": {"enabled": enabled, "input_role": "MASK"},
            "outpaint": {"enabled": enabled, "input_role": "MASK"},
        }
    }
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED', parameter_schema_json=?, revision=revision+1 WHERE id=?",
            (json.dumps(schema), profile_id),
        )
    return profile_id


def _image(workspace, database, project_id: str, name: str) -> dict[str, object]:
    source = workspace.work_root / name
    source.write_bytes(PNG if name.startswith("source") else PNG + name.encode("utf-8"))
    return MediaService(database, workspace).import_file(project_id, source, media_kind="IMAGE")


def test_motion_mask_and_vector_controls_are_immutable_and_capability_driven(workspace, database) -> None:
    project_id = str(_project(workspace, database, "motion_control")["id"])
    profile_id = _profile(database, workspace, enabled=True)
    source = _image(workspace, database, project_id, "source.png")
    mask = _image(workspace, database, project_id, "mask.png")
    service = MotionControlService(database, workspace)

    source_item = service.media.get_version(str(source["media_version_id"]))
    created = service.create(
        str(source["media_version_id"]),
        {
            "control_kind": "MOTION_MASK",
            "operation": "INPAINT",
            "subject_role": "face",
            "profile_version_id": profile_id,
            "mask_media_version_id": str(mask["media_version_id"]),
        },
    )
    control = created
    assert created["duplicate"] is False
    assert control["control_media_version_id"] == mask["media_version_id"]
    assert service.media.get_version(str(source["media_version_id"]))["sha256"] == source_item["sha256"]

    duplicate = service.create(
        str(source["media_version_id"]),
        {
            "control_kind": "MOTION_MASK",
            "operation": "INPAINT",
            "subject_role": "face",
            "profile_version_id": profile_id,
            "mask_media_version_id": str(mask["media_version_id"]),
        },
    )
    assert duplicate["duplicate"] is True
    assert duplicate["id"] == control["id"]

    vector = service.create(
        str(source["media_version_id"]),
        {
            "control_kind": "VECTOR",
            "operation": "MOTION_BRUSH",
            "subject_role": "arm",
            "profile_version_id": profile_id,
            "vector_path": [{"x": 0.1, "y": 0.2, "pressure": 0.8, "time_us": 1000}],
            "keyframes": [{"time_us": 0, "x": 0.1, "y": 0.2}],
        },
    )
    vector_item = vector["control_media"]
    assert vector_item["media_kind"] == "OTHER"
    _, vector_path = service.media.content_path(str(vector_item["id"]))
    assert json.loads(vector_path.read_text(encoding="utf-8"))["source_media_version_id"] == source["media_version_id"]
    assert service.media.get_version(str(source["media_version_id"]))["sha256"] == source_item["sha256"]

    pixels = service.create(
        str(source["media_version_id"]),
        {
            "control_kind": "VECTOR",
            "operation": "MOTION_BRUSH",
            "subject_role": "screen",
            "profile_version_id": profile_id,
            "coordinate_space": "PIXELS",
            "vector_path": [{"x": 640, "y": 360}],
        },
    )
    assert pixels["control_payload"]["coordinate_space"] == "PIXELS"


def test_motion_control_rejects_missing_profile_capability_and_cross_project_mask(workspace, database) -> None:
    project = _project(workspace, database, "motion_reject")
    other = _project(workspace, database, "motion_other")
    profile_id = _profile(database, workspace, enabled=False)
    source = _image(workspace, database, str(project["id"]), "source.png")
    other_mask = _image(workspace, database, str(other["id"]), "mask.png")
    service = MotionControlService(database, workspace)
    with pytest.raises(DomainRuleError, match="Profile"):
        service.create(
            str(source["media_version_id"]),
            {
                "control_kind": "MOTION_MASK",
                "operation": "INPAINT",
                "subject_role": "face",
                "profile_version_id": profile_id,
                "mask_media_version_id": str(other_mask["media_version_id"]),
            },
        )
    _profile(database, workspace, enabled=True)
    with pytest.raises(DomainRuleError):
        service.create(
            str(source["media_version_id"]),
            {
                "control_kind": "MOTION_MASK",
                "operation": "INPAINT",
                "subject_role": "face",
                "profile_version_id": profile_id,
                "mask_media_version_id": str(other_mask["media_version_id"]),
            },
        )


def test_motion_control_api_exposes_immutable_list(workspace, database) -> None:
    project_id = str(_project(workspace, database, "motion_api")["id"])
    profile_id = _profile(database, workspace, enabled=True)
    source = _image(workspace, database, project_id, "source.png")
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/media-versions/{source['media_version_id']}/motion-masks",
            json={
                "control_kind": "VECTOR",
                "operation": "MOTION_BRUSH",
                "subject_role": "camera-zone",
                "profile_version_id": profile_id,
                "vector_path": [{"x": 0.2, "y": 0.3}],
            },
        )
        assert response.status_code == 201, response.text
        control_id = response.json()["motion_control"]["id"]
        listed = client.get(f"/api/v1/media-versions/{source['media_version_id']}/motion-masks")
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["items"]] == [control_id]

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def _project(workspace, database) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code="g8_timeline_delivery",
        title="G8 timeline delivery",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )


def _video(workspace) -> Path:
    output = workspace.work_root / "g8-source.mp4"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=navy:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def _audio(workspace) -> Path:
    output = workspace.work_root / "g8-audio.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def test_g8_real_timeline_frame_enhancement_render_delivery_and_recovery(workspace, database) -> None:
    project = _project(workspace, database)
    project_id = str(project["id"])
    season = ProjectService(database, workspace.projects_root).list_seasons(project_id)[0]
    episode = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))[0]
    project_service = ProjectService(database, workspace.projects_root)
    first_shot = project_service.create_shot(str(episode["id"]), "S001", 500)
    second_shot = project_service.create_shot(str(episode["id"]), "S002", 500)
    video = MediaService(database, workspace).import_file(project_id, _video(workspace), purpose="SHOT_VIDEO", owner_id=str(first_shot["id"]), media_kind="VIDEO")
    audio = MediaService(database, workspace).import_file(project_id, _audio(workspace), purpose="AUDIO", media_kind="AUDIO")
    video_id = str(video["media_version_id"])

    with TestClient(create_app(workspace)) as client:
        timeline_response = client.post(
            f"/api/v1/episodes/{episode['id']}/timeline-revisions",
            json={
                "items": [{"track_type": "VIDEO", "media_version_id": video_id, "start_us": 0, "end_us": 1_000_000, "parameters": {"fit": "contain"}}],
                "input_snapshot": {"source": "g8-test"},
            },
        )
        assert timeline_response.status_code == 201, timeline_response.text
        timeline = timeline_response.json()["timeline"]
        assert timeline["revision_no"] == 1
        assert client.get(f"/api/v1/timeline-revisions/{timeline['id']}").status_code == 200

        subtitle_response = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={"format": "SRT", "cues": [{"start_us": 0, "end_us": 1_000_000, "text": "你好，世界"}]},
        )
        assert subtitle_response.status_code == 201, subtitle_response.text
        assert "00:00:00,000 --> 00:00:01,000" in subtitle_response.json()["subtitle"]["content_text"]
        overlap = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={"cues": [{"start_us": 0, "end_us": 800_000, "text": "a"}, {"start_us": 700_000, "end_us": 1_000_000, "text": "b"}]},
        )
        assert overlap.status_code == 422
        assert overlap.json()["error"]["code"] == "SUBTITLE_OVERLAP"
        cps = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={"cues": [{"start_us": 0, "end_us": 100_000, "text": "这是一个超过字符速度限制的字幕"}]},
        )
        assert cps.status_code == 422
        assert cps.json()["error"]["code"] == "SUBTITLE_CPS_EXCEEDED"

        audio_response = client.post(
            f"/api/v1/episodes/{episode['id']}/audio-bindings",
            json={"media_version_id": audio["media_version_id"], "track_type": "MUSIC", "start_us": 0, "end_us": 1_000_000, "source_license_status": "VERIFIED_LOCAL"},
        )
        assert audio_response.status_code == 201, audio_response.text
        assert len(client.get(f"/api/v1/episodes/{episode['id']}/audio-bindings").json()["items"]) == 1

        anchor_response = client.post(
            f"/api/v1/media-versions/{video_id}:create-frame-anchor",
            json={"source_time_us": 500_000, "role_hint": "LAST_FRAME"},
        )
        assert anchor_response.status_code == 201, anchor_response.text
        anchor = anchor_response.json()["frame_anchor"]
        assert anchor["extracted_media_version_id"]
        assert client.get(f"/api/v1/frame-anchors/{anchor['id']}").status_code == 200

        constraint = client.post(
            "/api/v1/shot-transitions",
            json={"from_shot_id": first_shot["id"], "to_shot_id": second_shot["id"], "constraint_type": "LAST_TO_FIRST", "from_anchor_id": anchor["id"]},
        )
        assert constraint.status_code == 201, constraint.text
        assert constraint.json()["constraint"]["compatibility_status"] == "PENDING_REVIEW"
        validation = client.post(f"/api/v1/shot-transitions/{constraint.json()['constraint']['id']}:validate")
        assert validation.status_code == 200
        assert validation.json()["validation"]["status"] == "WARNING"
        assert validation.json()["validation"]["warnings"][0]["code"] == "FRAME_ANCHOR_PROJECT_BRIDGE"

        recipe = client.post(
            "/api/v1/post-process-recipes",
            json={"code": "g8-technical-qc", "title": "G8 technical QC", "steps": [{"kind": "TECHNICAL_QC"}], "capability_contract": {"local": True}},
        )
        assert recipe.status_code == 201, recipe.text
        enhancement = client.post(
            "/api/v1/enhancement-runs",
            json={"input_media_version_id": video_id, "recipe_id": recipe.json()["recipe"]["id"], "parameters": {"preset": "local"}},
        )
        assert enhancement.status_code == 201, enhancement.text
        assert enhancement.json()["enhancement"]["status"] == "SUCCEEDED"

        render_response = client.post(f"/api/v1/timeline-revisions/{timeline['id']}:render")
        assert render_response.status_code == 201, render_response.text
        render = render_response.json()["render"]
        assert render["status"] == "VERIFIED"
        target = ConfigurationService(database).create_delivery_target(project_id, "g8-local", "G8 local", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/g8"})
        delivery_response = client.post("/api/v1/delivery-packages", json={"episode_render_version_id": render["id"], "target_version_id": target["version_id"]})
        assert delivery_response.status_code == 201, delivery_response.text
        delivery = delivery_response.json()["delivery"]
        assert delivery["status"] == "VERIFIED"
        assert client.get(f"/api/v1/delivery-packages/{delivery['id']}:verify").json()["delivery"]["status"] == "VERIFIED"

        delivery_file = workspace.projects_root / str(project["root_rel"]) / str(delivery["rel_path"]) / "EPISODE_001.mp4"
        with delivery_file.open("ab") as changed:
            changed.write(b"tamper")
        corrupted = client.get(f"/api/v1/delivery-packages/{delivery['id']}:verify")
        assert corrupted.status_code == 200
        assert corrupted.json()["delivery"]["status"] == "CORRUPT"
        withdrawn = client.post(f"/api/v1/delivery-packages/{delivery['id']}:withdraw", json={"reason": "G8 recovery evidence tamper test"})
        assert withdrawn.status_code == 200
        assert withdrawn.json()["delivery"]["status"] == "WITHDRAWN"

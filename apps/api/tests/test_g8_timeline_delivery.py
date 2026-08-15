from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.documents import DocumentImportService
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
    script_path = workspace.work_root / "g8-script.txt"
    script_path.write_text("你好，世界\n\na b\n\n这是一个超过字符速度限制的字幕", encoding="utf-8")
    script = DocumentImportService(database, workspace).import_document(project_id, script_path)
    authority = {"text_authority": "SCRIPT", "source_document_version_id": script["source_document_version_id"]}
    license_path = workspace.projects_root / str(project["root_rel"]) / "00_admin" / "audio-license.json"
    license_path.write_text('{"owner":"test-operator","scope":"g8 audio fixture"}\n', encoding="utf-8", newline="")

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
            json={"format": "SRT", "authority": authority, "cues": [{"start_us": 0, "end_us": 1_000_000, "text": "你好，世界"}]},
        )
        assert subtitle_response.status_code == 201, subtitle_response.text
        assert "00:00:00,000 --> 00:00:01,000" in subtitle_response.json()["subtitle"]["content_text"]
        assert subtitle_response.json()["subtitle"]["authority_status"] == "VERIFIED_SCRIPT"
        snapshot = subtitle_response.json()["subtitle"]["input_snapshot"]
        assert snapshot["asr_text_authority"] is False
        assert snapshot["source_passages"][0]["source_start"] == 0
        missing_authority = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={"cues": [{"start_us": 0, "end_us": 1_000_000, "text": "你好，世界"}]},
        )
        assert missing_authority.status_code == 422
        mismatched_text = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={"authority": authority, "cues": [{"start_us": 0, "end_us": 1_000_000, "text": "ASR 猜测文本"}]},
        )
        assert mismatched_text.status_code == 422
        assert mismatched_text.json()["error"]["code"] == "SUBTITLE_SCRIPT_AUTHORITY_MISMATCH"
        with database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM subtitle_revisions WHERE episode_id=?", (episode["id"],)).fetchone()[0] == 1

        other_project = ProjectService(database, workspace.projects_root).create_project(
            code="g8_subtitle_cross_project",
            title="G8 subtitle cross project",
            episode_count=1,
            aspect_ratio="16:9",
            fps_num=24,
            fps_den=1,
            target_duration_ms=1000,
            allow_unconfigured_capabilities=True,
        )
        other_script_path = workspace.work_root / "other-script.txt"
        other_script_path.write_text("你好，世界", encoding="utf-8")
        other_script = DocumentImportService(database, workspace).import_document(str(other_project["id"]), other_script_path)
        cross_project = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={
                "authority": {"text_authority": "SCRIPT", "source_document_version_id": other_script["source_document_version_id"]},
                "cues": [{"start_us": 0, "end_us": 1_000_000, "text": "你好，世界"}],
            },
        )
        assert cross_project.status_code == 422
        assert cross_project.json()["error"]["code"] == "SUBTITLE_SOURCE_PROJECT_MISMATCH"

        with database.transaction() as connection:
            connection.execute("INSERT INTO execution_profiles (id,code,title) VALUES ('asr-profile','g8-asr','G8 ASR')")
            connection.execute(
                """INSERT INTO execution_profile_versions
                (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
                 parameter_schema_json,status,capability_json,output_contract_json,resource_policy_json)
                VALUES ('asr-profile-v1','asr-profile',1,'AUDIO_ASR','{}','{}','{}','PUBLISHED','{}','{}','{}')"""
            )
        aligned = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={
                "format": "VTT",
                "authority": {
                    **authority,
                    "asr_alignment_media_version_id": audio["media_version_id"],
                    "asr_profile_version_id": "asr-profile-v1",
                },
                "cues": [{"start_us": 100_000, "end_us": 1_000_000, "text": "你好，世界"}],
            },
        )
        assert aligned.status_code == 201, aligned.text
        assert aligned.json()["subtitle"]["input_snapshot"]["asr_alignment"]["purpose"] == "TIMING_ALIGNMENT_ONLY"
        assert aligned.json()["subtitle"]["input_snapshot"]["asr_alignment"]["text_authority"] is False
        ass = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={
                "format": "ASS",
                "authority": authority,
                "cues": [{"start_us": 0, "end_us": 1_000_000, "text": "你好，世界"}],
            },
        )
        assert ass.status_code == 201, ass.text
        assert "[Events]" in ass.json()["subtitle"]["content_text"]
        assert "Dialogue: 0,0:00:00.00,0:00:01.00,你好，世界" in ass.json()["subtitle"]["content_text"]
        overlap = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={"authority": authority, "cues": [{"start_us": 0, "end_us": 800_000, "text": "a"}, {"start_us": 700_000, "end_us": 1_000_000, "text": "b"}]},
        )
        assert overlap.status_code == 422
        assert overlap.json()["error"]["code"] == "SUBTITLE_OVERLAP"
        cps = client.post(
            f"/api/v1/episodes/{episode['id']}/subtitle-revisions",
            json={"authority": authority, "cues": [{"start_us": 0, "end_us": 100_000, "text": "这是一个超过字符速度限制的字幕"}]},
        )
        assert cps.status_code == 422
        assert cps.json()["error"]["code"] == "SUBTITLE_CPS_EXCEEDED"

        audio_response = client.post(
            f"/api/v1/episodes/{episode['id']}/audio-bindings",
            json={"media_version_id": audio["media_version_id"], "track_type": "MUSIC", "start_us": 0, "end_us": 1_000_000, "source_license_status": "VERIFIED_LOCAL", "license_evidence_path_rel": "00_admin/audio-license.json", "loop_enabled": True, "fade_in_us": 50_000, "fade_out_us": 50_000},
        )
        assert audio_response.status_code == 201, audio_response.text
        audio_items = client.get(f"/api/v1/episodes/{episode['id']}/audio-bindings").json()["items"]
        assert len(audio_items) == 1
        assert audio_items[0]["authorization_status"] == "VERIFIED_EVIDENCE"
        assert audio_items[0]["loop_enabled"] is True

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
            json={
                "code": "g8-enhance", "title": "G8 versioned enhancement",
                "steps": [
                    {"kind": "SCALE", "width": 320, "height": 180, "fit": "CONTAIN", "executor_ref": "builtin:ffmpeg"},
                    {"kind": "TECHNICAL_QC", "executor_ref": "builtin:ffprobe"},
                    {"kind": "ENCODE", "codec": "H264", "preset": "ultrafast", "crf": 24, "executor_ref": "builtin:ffmpeg"},
                ],
                "capability_contract": {"transport": "LOCAL_PROCESS", "network_allowed": False},
            },
        )
        assert recipe.status_code == 201, recipe.text
        draft_plan = client.post(
            "/api/v1/enhancement-runs:plan",
            json={"input_media_version_id": video_id, "recipe_id": recipe.json()["recipe"]["id"]},
        )
        assert draft_plan.status_code == 422
        assert draft_plan.json()["error"]["code"] == "POST_PROCESS_RECIPE_NOT_ACTIVE"
        published_recipe = client.post(f"/api/v1/post-process-recipes/{recipe.json()['recipe']['id']}:publish")
        assert published_recipe.status_code == 200, published_recipe.text
        enhancement_plan = client.post(
            "/api/v1/enhancement-runs:plan",
            json={"input_media_version_id": video_id, "recipe_id": recipe.json()["recipe"]["id"], "parameters": {"purpose": "local-fixture"}},
        )
        assert enhancement_plan.status_code == 200, enhancement_plan.text
        assert enhancement_plan.json()["plan"]["would_create_run"] is False
        stale_plan = client.post(
            "/api/v1/enhancement-runs",
            json={"input_media_version_id": video_id, "recipe_id": recipe.json()["recipe"]["id"], "plan_hash": "0" * 64},
        )
        assert stale_plan.status_code == 422
        assert stale_plan.json()["error"]["code"] == "ENHANCEMENT_PLAN_STALE"
        enhancement = client.post(
            "/api/v1/enhancement-runs",
            json={"input_media_version_id": video_id, "recipe_id": recipe.json()["recipe"]["id"], "parameters": {"purpose": "local-fixture"}, "plan_hash": enhancement_plan.json()["plan"]["plan_hash"]},
        )
        assert enhancement.status_code == 201, enhancement.text
        assert enhancement.json()["enhancement"]["status"] == "SUCCEEDED"
        assert enhancement.json()["enhancement"]["qc"]["passed"] is True
        assert set(enhancement.json()["enhancement"]["qc"]["before"]) == {"duration_ms", "video", "audio"}
        assert "filename" not in json.dumps(enhancement.json()["enhancement"]["qc"])
        assert enhancement.json()["enhancement"]["bypass_comparison"]["input_preserved"] is True
        trace = enhancement.json()["enhancement"]["execution_snapshot"]["step_trace"]
        assert [step["kind"] for step in trace] == ["SCALE", "TECHNICAL_QC", "ENCODE"]
        assert trace[0]["input_sha256"] == enhancement.json()["enhancement"]["input_sha256"]
        assert trace[0]["output_sha256"] == trace[1]["input_sha256"] == trace[1]["output_sha256"] == trace[2]["input_sha256"]
        assert trace[2]["output_sha256"] == enhancement.json()["enhancement"]["output_sha256"]

        derived = client.post(
            "/api/v1/post-process-recipes",
            json={
                "code": "ignored-when-parent-is-set",
                "title": "G8 versioned enhancement v2",
                "parent_recipe_id": recipe.json()["recipe"]["id"],
                "steps": recipe.json()["recipe"]["steps"],
                "capability_contract": recipe.json()["recipe"]["capability_contract"],
            },
        )
        assert derived.status_code == 201
        assert derived.json()["recipe"]["version_no"] == 2
        assert derived.json()["recipe"]["parent_recipe_id"] == recipe.json()["recipe"]["id"]
        assert derived.json()["recipe"]["status"] == "DRAFT"

        render_response = client.post(f"/api/v1/timeline-revisions/{timeline['id']}:render")
        assert render_response.status_code == 201, render_response.text
        render = render_response.json()["render"]
        assert render["status"] == "VERIFIED"
        templates = client.get("/api/v1/review-templates").json()["items"]
        render_template = next(item for item in templates if item["code"] == "episode_render")
        render_review = client.post(
            f"/api/v1/subjects/EPISODE_RENDER_VERSION/{render['id']}/reviews",
            json={
                "template_version_id": render_template["id"],
                "decision": "APPROVED",
                "expected_subject_revision": 1,
                "checks": [{"item_id": item["id"], "result": "PASS"} for item in render_template["items"]],
                "comment": "本地整集渲染机器完整性与画面/音频/字幕证据已复核",
            },
        )
        assert render_review.status_code == 201, render_review.text
        assert render_review.json()["review"]["subject_type"] == "EPISODE_RENDER_VERSION"
        target = ConfigurationService(database).create_delivery_target(project_id, "g8-local", "G8 local", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/g8"})
        delivery_response = client.post("/api/v1/delivery-packages", json={"episode_render_version_id": render["id"], "target_version_id": target["version_id"]})
        assert delivery_response.status_code == 201, delivery_response.text
        delivery = delivery_response.json()["delivery"]
        assert delivery["status"] == "VERIFIED"
        assert client.get(f"/api/v1/delivery-packages/{delivery['id']}:verify").json()["delivery"]["status"] == "VERIFIED"
        observed = client.get(f"/api/v1/episodes/{episode['id']}/timeline-status")
        assert observed.status_code == 200, observed.text
        status = observed.json()["status"]
        assert status["timeline"]["revision_count"] == 1
        assert status["subtitles"]["revision_count"] == 3
        assert status["audio"] == {"binding_count": 1, "verified_local_count": 1}
        assert status["renders"]["count"] == 1
        assert status["renders"]["verified_count"] == 1
        assert status["delivery"]["count"] == 1
        assert status["delivery"]["verified_count"] == 1
        assert status["read_only"] is True
        assert status["runtime_contacted"] is False and status["network_contacted"] is False and status["mutated"] is False

        delivery_file = workspace.projects_root / str(project["root_rel"]) / str(delivery["rel_path"]) / "EPISODE_001.mp4"
        with delivery_file.open("ab") as changed:
            changed.write(b"tamper")
        corrupted = client.get(f"/api/v1/delivery-packages/{delivery['id']}:verify")
        assert corrupted.status_code == 200
        assert corrupted.json()["delivery"]["status"] == "CORRUPT"
        withdrawn = client.post(f"/api/v1/delivery-packages/{delivery['id']}:withdraw", json={"reason": "G8 recovery evidence tamper test"})
        assert withdrawn.status_code == 200
        assert withdrawn.json()["delivery"]["status"] == "WITHDRAWN"

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.documents import DocumentImportService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
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
        assert render["input_snapshot"]["schema_version"] == "localdrama.episode-render-input.v1"
        assert render["input_snapshot"]["timeline_revision_id"] == timeline["id"]
        assert render["input_snapshot"]["items"]
        assert render["ffmpeg_command"]["executor"] == "builtin:ffmpeg"
        assert render["ffmpeg_command"]["returncode"] == 0
        assert '"stderr_tail"' in render["execution_log"]
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
        assert status["renders"]["latest"]["evidence_status"] == "VERIFIED"
        assert status["renders"]["latest"]["input_snapshot"]["timeline_revision_id"] == timeline["id"]
        assert status["renders"]["latest"]["ffmpeg_command"]["returncode"] == 0
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
        corrupt_review = client.post(
            f"/api/v1/delivery-packages/{delivery['id']}:review",
            json={"reviewer_type": "HUMAN", "decision": "APPROVED", "note": "不应批准已篡改文件"},
        )
        assert corrupt_review.status_code == 422
        assert corrupt_review.json()["error"]["code"] == "DELIVERY_NOT_VERIFIED"
        withdrawn = client.post(f"/api/v1/delivery-packages/{delivery['id']}:withdraw", json={"reason": "G8 recovery evidence tamper test"})
        assert withdrawn.status_code == 200
        assert withdrawn.json()["delivery"]["status"] == "WITHDRAWN"


def test_optional_post_process_steps_are_local_and_preserve_input(workspace, database) -> None:
    project = _project(workspace, database)
    project_id = str(project["id"])
    source = MediaService(database, workspace).import_file(project_id, _video(workspace), purpose="SHOT_VIDEO", media_kind="VIDEO")
    video_id = str(source["media_version_id"])
    original_sha = str(source["sha256"])
    lut_path = workspace.projects_root / str(project["root_rel"]) / "00_admin" / "identity.cube"
    lut_path.write_text(
        "TITLE \"Identity\"\nLUT_3D_SIZE 2\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n"
        "0 0 0\n0 0 1\n0 1 0\n0 1 1\n1 0 0\n1 0 1\n1 1 0\n1 1 1\n",
        encoding="utf-8",
    )
    steps = [
        {"kind": "SCALE", "width": 160, "height": 90, "fit": "CONTAIN", "executor_ref": "builtin:ffmpeg"},
        {"kind": "FRAME_INTERPOLATION", "target_fps": 24, "mode": "MCI", "executor_ref": "builtin:ffmpeg"},
        {"kind": "DENOISE", "strength": 0.6, "executor_ref": "builtin:ffmpeg"},
        {"kind": "STABILIZE", "mode": "DESHAKE", "executor_ref": "builtin:ffmpeg"},
        {"kind": "LUT_3D", "path_rel": "00_admin/identity.cube", "executor_ref": "builtin:ffmpeg"},
        {"kind": "TECHNICAL_QC", "executor_ref": "builtin:ffprobe"},
        {"kind": "ENCODE", "codec": "H264", "preset": "ultrafast", "crf": 30, "executor_ref": "builtin:ffmpeg"},
    ]
    with TestClient(create_app(workspace)) as client:
        recipe = client.post(
            "/api/v1/post-process-recipes",
            json={"code": "optional-local", "title": "可选本地步骤", "steps": steps, "capability_contract": {"transport": "LOCAL_PROCESS", "network_allowed": False, "optional_steps": ["FRAME_INTERPOLATION", "DENOISE", "STABILIZE", "LUT_3D"]}},
        )
        assert recipe.status_code == 201, recipe.text
        published = client.post(f"/api/v1/post-process-recipes/{recipe.json()['recipe']['id']}:publish")
        assert published.status_code == 200, published.text
        planned = client.post("/api/v1/enhancement-runs:plan", json={"input_media_version_id": video_id, "recipe_id": recipe.json()["recipe"]["id"]})
        assert planned.status_code == 200, planned.text
        plan = planned.json()["plan"]
        assert plan["command_preview"]["optional_steps"] == ["FRAME_INTERPOLATION", "DENOISE", "STABILIZE", "LUT_3D"]
        run = client.post("/api/v1/enhancement-runs", json={"input_media_version_id": video_id, "recipe_id": recipe.json()["recipe"]["id"], "plan_hash": plan["plan_hash"]})
        assert run.status_code == 201, run.text
        enhancement = run.json()["enhancement"]
        assert enhancement["status"] == "SUCCEEDED"
        assert enhancement["qc"]["passed"] is True
        assert [step["kind"] for step in enhancement["execution_snapshot"]["step_trace"]] == ["SCALE", "FRAME_INTERPOLATION", "DENOISE", "STABILIZE", "LUT_3D", "TECHNICAL_QC", "ENCODE"]
        assert enhancement["input_sha256"] == original_sha
        assert enhancement["bypass_comparison"]["input_preserved"] is True
        invalid = client.post(
            "/api/v1/post-process-recipes",
            json={
                "code": "bad-interpolation",
                "title": "非法补帧",
                "steps": [
                    steps[0],
                    {"kind": "FRAME_INTERPOLATION", "target_fps": 0, "executor_ref": "builtin:ffmpeg"},
                    steps[-2],
                    steps[-1],
                ],
                "capability_contract": {"transport": "LOCAL_PROCESS", "network_allowed": False, "optional_steps": ["FRAME_INTERPOLATION"]},
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "POST_PROCESS_INTERPOLATION_INVALID"


def test_optional_post_process_failure_does_not_register_or_overwrite_input(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database)
    project_id = str(project["id"])
    source = MediaService(database, workspace).import_file(project_id, _video(workspace), purpose="SHOT_VIDEO", media_kind="VIDEO")
    video_id = str(source["media_version_id"])
    original_sha = str(source["sha256"])
    steps = [
        {"kind": "SCALE", "width": 160, "height": 90, "fit": "CONTAIN", "executor_ref": "builtin:ffmpeg"},
        {"kind": "DENOISE", "strength": 1.0, "executor_ref": "builtin:ffmpeg"},
        {"kind": "TECHNICAL_QC", "executor_ref": "builtin:ffprobe"},
        {"kind": "ENCODE", "codec": "H264", "preset": "ultrafast", "crf": 30, "executor_ref": "builtin:ffmpeg"},
    ]
    with TestClient(create_app(workspace)) as client:
        recipe = client.post(
            "/api/v1/post-process-recipes",
            json={"code": "optional-failure", "title": "可选步骤失败", "steps": steps, "capability_contract": {"transport": "LOCAL_PROCESS", "network_allowed": False, "optional_steps": ["DENOISE"]}},
        )
        assert recipe.status_code == 201, recipe.text
        recipe_id = recipe.json()["recipe"]["id"]
        assert client.post(f"/api/v1/post-process-recipes/{recipe_id}:publish").status_code == 200
        plan = client.post("/api/v1/enhancement-runs:plan", json={"input_media_version_id": video_id, "recipe_id": recipe_id}).json()["plan"]

    original_run = TimelineService._run_ffmpeg

    def fail_denoise(self: TimelineService, args: list[str], *, timeout: int) -> dict[str, object]:
        if any("hqdn3d" in str(argument) for argument in args):
            from local_drama.domain.errors import DomainRuleError

            raise DomainRuleError("OPTIONAL_STEP_EXECUTION_FAILED", "测试模拟降噪步骤失败")
        return original_run(self, args, timeout=timeout)

    monkeypatch.setattr(TimelineService, "_run_ffmpeg", fail_denoise)
    with TestClient(create_app(workspace)) as client:
        failed = client.post("/api/v1/enhancement-runs", json={"input_media_version_id": video_id, "recipe_id": recipe_id, "plan_hash": plan["plan_hash"]})
        assert failed.status_code == 422
        assert failed.json()["error"]["code"] == "OPTIONAL_STEP_EXECUTION_FAILED"
    with database.connect() as connection:
        run = connection.execute("SELECT status, output_media_version_id FROM enhancement_runs WHERE recipe_id=? ORDER BY created_at DESC LIMIT 1", (recipe_id,)).fetchone()
        assert run["status"] == "FAILED" and run["output_media_version_id"] is None
        assert int(connection.execute("SELECT COUNT(*) FROM media_versions WHERE parent_version_id=?", (video_id,)).fetchone()[0]) == 0
    assert MediaService(database, workspace).verify_content_integrity(video_id)["sha256"] == original_sha


def test_brand_watermark_and_compliance_versions_bind_delivery_without_auto_review(workspace, database) -> None:
    project = _project(workspace, database)
    project_id = str(project["id"])
    project_service = ProjectService(database, workspace.projects_root)
    season = project_service.list_seasons(project_id)[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    shot = project_service.create_shot(str(episode["id"]), "S001", 1000)
    source = MediaService(database, workspace).import_file(project_id, _video(workspace), purpose="SHOT_VIDEO", owner_id=str(shot["id"]), media_kind="VIDEO")
    with TestClient(create_app(workspace)) as client:
        timeline = client.post(f"/api/v1/episodes/{episode['id']}/timeline-revisions", json={"items": [{"track_type": "VIDEO", "media_version_id": source["media_version_id"], "start_us": 0, "end_us": 1_000_000, "parameters": {}}], "input_snapshot": {"source": "brand-control-test"}})
        assert timeline.status_code == 201, timeline.text
        render = client.post(f"/api/v1/timeline-revisions/{timeline.json()['timeline']['id']}:render")
        assert render.status_code == 201, render.text
        # FR-DEL-001 requires an explicit, latest human approval for the
        # immutable episode render before a delivery candidate may be built.
        render_template = next(item for item in client.get("/api/v1/review-templates").json()["items"] if item["code"] == "episode_render")
        render_review = client.post(
            f"/api/v1/subjects/EPISODE_RENDER_VERSION/{render.json()['render']['id']}/reviews",
            json={
                "template_version_id": render_template["id"],
                "decision": "APPROVED",
                "expected_subject_revision": 1,
                "checks": [{"item_id": item["id"], "result": "PASS"} for item in render_template["items"]],
                "comment": "整集渲染版本已完成正式审核",
            },
        )
        assert render_review.status_code == 201, render_review.text
        target = ConfigurationService(database).create_delivery_target(project_id, "brand-local", "Brand local", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/brand"})
        brand = client.post(f"/api/v1/projects/{project_id}/brand-kits", json={"code": "series", "title": "Series v1", "tokens": {"colors": {"primary": "#223344"}}})
        assert brand.status_code == 201, brand.text
        watermark = client.post(f"/api/v1/projects/{project_id}/watermark-profiles", json={"code": "corner", "title": "右下角水印", "config": {"text": "LOCAL STUDY", "position": "BOTTOM_RIGHT", "opacity": 0.8, "font_size": 18, "margin": 8, "color": "white"}})
        assert watermark.status_code == 201, watermark.text
        failing_policy = client.post(f"/api/v1/projects/{project_id}/compliance-policies", json={"code": "duration", "title": "时长上限", "rules": {"require_watermark": True, "max_duration_ms": 500}})
        assert failing_policy.status_code == 201, failing_policy.text
        blocked = client.post("/api/v1/delivery-packages", json={"episode_render_version_id": render.json()["render"]["id"], "target_version_id": target["version_id"], "watermark_profile_id": watermark.json()["watermark_profile"]["id"], "compliance_policy_id": failing_policy.json()["compliance_policy"]["id"]})
        assert blocked.status_code == 422
        assert blocked.json()["error"]["code"] == "COMPLIANCE_PREFLIGHT_FAILED"
        passing_policy = client.post(f"/api/v1/projects/{project_id}/compliance-policies", json={"code": "duration", "title": "时长上限 v2", "rules": {"require_watermark": True, "max_duration_ms": 1500, "require_human_review": True, "require_platform_review": True}})
        assert passing_policy.status_code == 201, passing_policy.text
        delivery = client.post("/api/v1/delivery-packages", json={"episode_render_version_id": render.json()["render"]["id"], "target_version_id": target["version_id"], "brand_kit_id": brand.json()["brand_kit"]["id"], "watermark_profile_id": watermark.json()["watermark_profile"]["id"], "compliance_policy_id": passing_policy.json()["compliance_policy"]["id"]})
        assert delivery.status_code == 201, delivery.text
        item = delivery.json()["delivery"]
        assert item["machine_preflight"]["status"] == "PASS"
        assert item["human_review_status"] == "PENDING" and item["platform_review_status"] == "PENDING"
        assert item["controls"]["brand_kit"]["version_no"] == 1
        assert item["controls"]["watermark_profile"]["version_no"] == 1
        assert item["controls"]["compliance_policy"]["version_no"] == 2
        controls = client.get(f"/api/v1/projects/{project_id}/brand-controls")
        assert controls.status_code == 200
        assert controls.json()["watermark_profiles"][0]["status"] == "ACTIVE"
        assert controls.json()["compliance_policies"][0]["status"] == "ACTIVE"
        verified = client.get(f"/api/v1/delivery-packages/{item['id']}:verify")
        assert verified.status_code == 200
        assert verified.json()["delivery"]["human_review_status"] == "PENDING"
        human_review = client.post(f"/api/v1/delivery-packages/{item['id']}:review", json={"reviewer_type": "HUMAN", "decision": "APPROVED", "note": "人工复核画面与本地授权范围"})
        assert human_review.status_code == 200, human_review.text
        assert human_review.json()["delivery"]["human_review_status"] == "APPROVED"
        platform_review = client.post(f"/api/v1/delivery-packages/{item['id']}:review", json={"reviewer_type": "PLATFORM", "decision": "APPROVED", "note": "平台规则人工确认"})
        assert platform_review.status_code == 200, platform_review.text
        assert platform_review.json()["delivery"]["platform_review_status"] == "APPROVED"


def _delivery_render_fixture(workspace, database, client, *, approve: bool) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    project = _project(workspace, database)
    project_id = str(project["id"])
    project_service = ProjectService(database, workspace.projects_root)
    season = project_service.list_seasons(project_id)[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    shot = project_service.create_shot(str(episode["id"]), "DEL-001", 1000)
    source = MediaService(database, workspace).import_file(project_id, _video(workspace), purpose="SHOT_VIDEO", owner_id=str(shot["id"]), media_kind="VIDEO")
    timeline = client.post(f"/api/v1/episodes/{episode['id']}/timeline-revisions", json={"items": [{"track_type": "VIDEO", "media_version_id": source["media_version_id"], "start_us": 0, "end_us": 1_000_000, "parameters": {}}], "input_snapshot": {"source": "delivery-requirements"}}).json()["timeline"]
    render = client.post(f"/api/v1/timeline-revisions/{timeline['id']}:render").json()["render"]
    if approve:
        template = next(item for item in client.get("/api/v1/review-templates").json()["items"] if item["code"] == "episode_render")
        response = client.post(f"/api/v1/subjects/EPISODE_RENDER_VERSION/{render['id']}/reviews", json={"template_version_id": template["id"], "decision": "APPROVED", "expected_subject_revision": 1, "checks": [{"item_id": item["id"], "result": "PASS"} for item in template["items"]], "comment": "交付需求测试批准"})
        assert response.status_code == 201, response.text
    target = ConfigurationService(database).create_delivery_target(project_id, "delivery-requirements", "Delivery requirements", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/delivery-requirements", "width": 320, "height": 180, "fps": 24, "bitrate": "1M", "audio_codec": "AAC", "subtitles": "SIDECAR"})
    return project, render, target


def test_delivery_candidate_requires_approved_render_and_target_versions_are_explicit(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        project, render, target = _delivery_render_fixture(workspace, database, client, approve=False)
        blocked = client.post("/api/v1/delivery-packages", json={"episode_render_version_id": render["id"], "target_version_id": target["version_id"]})
        assert blocked.status_code == 422
        assert blocked.json()["error"]["code"] == "EPISODE_RENDER_APPROVAL_REQUIRED"
        version = client.post(f"/api/v1/projects/{project['id']}/delivery-targets/{target['id']}/versions", json={"transport": "LOCAL_FILESYSTEM", "spec": {"path_rel": "06_delivery/delivery-requirements-v2", "width": 640, "height": 360, "fps": 30, "bitrate": "2M", "audio_codec": "AAC", "subtitles": "BURN_IN"}})
        assert version.status_code == 201, version.text
        target_v2 = version.json()["target"]
        assert target_v2["version_no"] == 2
        assert target_v2["version_id"] != target["version_id"]
        selected = client.post(f"/api/v1/delivery-target-versions/{target_v2['version_id']}:select?project_id={project['id']}")
        assert selected.status_code == 200, selected.text
        config = client.get(f"/api/v1/projects/{project['id']}/configuration").json()["configuration"]
        assert config["selected_delivery_target_version_id"] == target_v2["version_id"]


def test_delivery_manifest_history_verify_and_withdraw_preserve_files(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        project, render, target = _delivery_render_fixture(workspace, database, client, approve=True)
        first_response = client.post("/api/v1/delivery-packages", json={"episode_render_version_id": render["id"], "target_version_id": target["version_id"]})
        assert first_response.status_code == 201, first_response.text
        first = first_response.json()["delivery"]
        assert first["manifest_sha256"]
        assert first["rel_path"].endswith("EPISODE_001")
        manifest = workspace.projects_root / str(project["root_rel"]) / str(first["rel_path"]) / "manifest.json"
        manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
        assert manifest_payload["schema_version"] == "delivery-manifest.v3"
        assert manifest_payload["source"]["episode_render_version_id"] == render["id"]
        assert manifest_payload["target"]["target_version_id"] == target["version_id"]
        assert "encoding" in manifest_payload and "subtitles" in manifest_payload and "licenses" in manifest_payload
        assert client.get(f"/api/v1/delivery-packages/{first['id']}:verify").json()["delivery"]["status"] == "VERIFIED"
        details = client.get(f"/api/v1/delivery-packages/{first['id']}")
        assert details.status_code == 200, details.text
        assert len(details.json()["delivery"]["files"]) == 2
        files = client.get(f"/api/v1/delivery-packages/{first['id']}/files")
        assert files.status_code == 200 and len(files.json()["items"]) == 2
        download = client.get(f"/api/v1/delivery-packages/{first['id']}/download")
        assert download.status_code == 200 and len(download.content) > 0
        download_events = client.get(f"/api/v1/delivery-packages/{first['id']}").json()["delivery"]["events"]
        download_event = next(event for event in download_events if event["action"] == "DOWNLOAD")
        download_note = json.loads(download_event["note"])
        assert download_event["manifest_sha256"] == first["manifest_sha256"]
        assert set(download_note) == {"file_byte_size", "file_rel_path", "file_sha256", "transport"}
        assert download_note["file_rel_path"].endswith(".mp4")
        assert download_note["file_byte_size"] == len(download.content)
        second_response = client.post("/api/v1/delivery-packages", json={"episode_render_version_id": render["id"], "target_version_id": target["version_id"]})
        assert second_response.status_code == 201, second_response.text
        second = second_response.json()["delivery"]
        assert second["rel_path"] != first["rel_path"]
        first_bytes = (workspace.projects_root / str(project["root_rel"]) / str(first["rel_path"]) / "EPISODE_001.mp4").read_bytes()
        assert first_bytes
        withdrawn = client.post(f"/api/v1/delivery-packages/{first['id']}:withdraw", json={"reason": "发布版本替换"})
        assert withdrawn.status_code == 200 and withdrawn.json()["delivery"]["status"] == "WITHDRAWN"
        verified_withdrawn = client.post(f"/api/v1/delivery-packages/{first['id']}:verify")
        assert verified_withdrawn.status_code == 200 and verified_withdrawn.json()["delivery"]["status"] == "WITHDRAWN"
        history = client.get(f"/api/v1/episodes/{render['episode_id']}/delivery-packages")
        assert history.status_code == 200
        history_items = history.json()["items"]
        assert {item["id"] for item in history_items} >= {first["id"], second["id"]}

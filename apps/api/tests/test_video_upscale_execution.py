from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.video_upscale.batches import VideoUpscaleBatchService
from local_drama.application.video_upscale.cleanup import VideoUpscaleCleanupService, _tree_size_without_links
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app
from local_drama.model_platform.application.execution_job_links import ExecutionJobLinkService
from local_drama.model_platform.application.ncnn_video_upscale_execution import NcnnVideoUpscaleExecutor
from local_drama.model_platform.application.profile_publication import ProfilePublicationService, ProfileVersionDraft


def _fake_ncnn_profile(database, workspace) -> str:
    fake_script = workspace.work_root / "fake-ncnn-adapter.py"
    fake_script.write_text(
        """from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
source = Path(args[args.index('-i') + 1])
target = Path(args[args.index('-o') + 1])
target.mkdir(parents=True, exist_ok=True)
for path in sorted(source.glob('*.png')):
    shutil.copy2(path, target / path.name)
""",
        encoding="utf-8",
    )
    model_dir = workspace.work_root / "fake-ncnn-model"
    model_dir.mkdir()
    (model_dir / "realesr-animevideov3.param").write_text("FAKE_ADAPTER", encoding="utf-8")
    now = datetime.now(UTC).isoformat()
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "node",
            "runtime",
            "runtime_version",
            "family",
            "release",
            "model",
            "offering",
            "source_smoke",
            "source_evidence",
            "contract",
            "binding",
            "resource",
        )
    }
    schema = {
        "type": "object",
        "properties": {
            "tile_size": {"type": "integer", "minimum": 0, "maximum": 1024, "default": 0},
            "tta": {"type": "boolean", "default": False},
            "load_threads": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
            "proc_threads": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1},
            "save_threads": {"type": "integer", "minimum": 1, "maximum": 4, "default": 2},
        },
    }
    ui_schema = {"properties": {key: {"scopes": ["RUN"]} for key in schema["properties"]}}
    with database.transaction() as connection:
        capability_id = str(connection.execute("SELECT id FROM mp_capability_definitions WHERE code='UPSCALE_VIDEO'").fetchone()[0])
        connection.execute(
            """INSERT INTO mp_compute_nodes
            (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
            VALUES (?,?,?,?, '{}',?,?,?)""",
            (ids["node"], f"node-{ids['node']}", "Fake NCNN node", ids["node"], now, now, now),
        )
        connection.execute(
            """INSERT INTO mp_runtime_installations
            (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at)
            VALUES (?,?,?,'TOOL_PROCESS','SERVICE_MANAGED','Fake NCNN',?,?)""",
            (ids["runtime"], ids["node"], f"runtime-{ids['runtime']}", now, now),
        )
        connection.execute(
            """INSERT INTO mp_runtime_installation_versions
            (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,
             fingerprint,status,created_at,updated_at)
            VALUES (?,?,1,'ncnn.realesrgan.video.v1','v1','LOCAL_PROCESS',?,?,'ACTIVE',?,?)""",
            (
                ids["runtime_version"],
                ids["runtime"],
                json.dumps({"executable_path": sys.executable, "argv_prefix": [str(fake_script)]}),
                ids["runtime_version"],
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO mp_model_families (id,code,title,vendor,license_json,created_at,updated_at)
            VALUES (?,?,?,'test','{}',?,?)""",
            (ids["family"], f"family-{ids['family']}", "Fake Real-ESRGAN", now, now),
        )
        connection.execute(
            """INSERT INTO mp_model_releases
            (id,family_id,code,upstream_id,revision,format,quantization,metadata_json,created_at,updated_at)
            VALUES (?,?,?,'fake','v1','NCNN',NULL,'{}',?,?)""",
            (ids["release"], ids["family"], "realesr-animevideov3", now, now),
        )
        connection.execute(
            """INSERT INTO mp_runtime_model_installations
            (id,release_id,runtime_installation_version_id,native_locator,install_state,metadata_json,created_at,updated_at)
            VALUES (?,?,?,?,'READY','{}',?,?)""",
            (ids["model"], ids["release"], ids["runtime_version"], str(model_dir), now, now),
        )
        connection.execute(
            """INSERT INTO mp_capability_offerings
            (id,runtime_model_installation_id,capability_definition_id,native_metadata_json,validation_status,created_at,updated_at)
            VALUES (?,?,?,'{}','SMOKE_PASSED',?,?)""",
            (ids["offering"], ids["model"], capability_id, now, now),
        )
        connection.execute(
            """INSERT INTO mp_validation_runs
            (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
            VALUES (?,'CAPABILITY_OFFERING',?,'CAPABILITY_SMOKE','SMOKE_PASSED','{}',?,?,?,?)""",
            (ids["source_smoke"], ids["offering"], now, now, now, now),
        )
        connection.execute(
            """INSERT INTO mp_validation_evidence
            (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at)
            VALUES (?,?,'CAPABILITY_SMOKE',?,'{}',NULL,?,?)""",
            (ids["source_evidence"], ids["source_smoke"], ids["source_evidence"], now, now),
        )
        connection.execute(
            """INSERT INTO mp_parameter_contract_versions
            (id,capability_definition_id,version_no,schema_json,ui_schema_json,content_hash,created_at,updated_at)
            VALUES (?,?,1,?,?,?,?,?)""",
            (ids["contract"], capability_id, json.dumps(schema), json.dumps(ui_schema), ids["contract"], now, now),
        )
        connection.execute(
            """INSERT INTO mp_adapter_binding_contract_versions
            (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at)
            VALUES (?,'TOOL_PROCESS','ncnn.realesrgan.video.v1',1,'{}',?,?,?)""",
            (ids["binding"], ids["binding"], now, now),
        )
        connection.execute(
            """INSERT INTO mp_resource_policy_versions
            (id,code,version_no,policy_json,content_hash,created_at,updated_at)
            VALUES (?, ?,1,?, ?,?,?)""",
            (
                ids["resource"],
                f"resource-{ids['resource']}",
                json.dumps({"network_policy": {"mode": "LOCAL_ONLY"}}),
                ids["resource"],
                now,
                now,
            ),
        )
    publication = ProfilePublicationService(database)
    created = publication.create_candidate(
        ProfileVersionDraft(
            profile_code=f"fake-ncnn-{uuid.uuid4()}",
            profile_title="Fake NCNN test profile",
            capability_definition_id=capability_id,
            runtime_installation_version_id=ids["runtime_version"],
            parameter_contract_version_id=ids["contract"],
            adapter_binding_contract_version_id=ids["binding"],
            resource_policy_version_id=ids["resource"],
            payload={
                "runtime_model_installation_ids": [ids["model"]],
                "defaults": {"tile_size": 0, "tta": False, "load_threads": 1, "proc_threads": 1, "save_threads": 2},
                "allowed_override_fields": ["tile_size", "tta", "load_threads", "proc_threads", "save_threads"],
            },
        )
    )
    validation = publication.record_validation(
        created.profile_version_id,
        validation_kind="PROFILE_SMOKE",
        status="SMOKE_PASSED",
        result={
            "payload_hash": created.payload_hash,
            "adapter": "FAKE_ADAPTER",
            "output_contract_verified": True,
            "source_capability_validation_run_id": ids["source_smoke"],
        },
        evidence={"status": "pass", "adapter": "FAKE_ADAPTER"},
    )
    publication.publish(created.profile_version_id, validation_run_id=validation.validation_run_id, reason="fake adapter test only")
    return created.profile_version_id


def test_cleanup_tree_rejects_nested_symlink_or_junction(tmp_path) -> None:
    root = tmp_path / "owned-run"
    external = tmp_path / "source-must-survive"
    root.mkdir()
    external.mkdir()
    sentinel = external / "source.mp4"
    sentinel.write_bytes(b"source")
    link = root / "linked-source"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 策略不允许创建测试 symlink/junction")
    _, unsafe = _tree_size_without_links(root)
    assert unsafe is True
    assert sentinel.read_bytes() == b"source"


def _real_compose(workspace, database, episode_id: str, project_root) -> str:
    source = project_root / "05_outputs" / "source-640x360.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=2:duration=1.5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.5",
            "-map", "0:v:0", "-map", "1:a:0", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-metadata:s:a:0", "language=chi",
            "-disposition:a:0", "default", "-shortest", "-y", str(source),
        ],
        check=True,
        capture_output=True,
    )
    probe = MediaService(database, workspace).probe_output(source, "VIDEO")
    video_stream = next(
        (stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"),
        {},
    )
    duration_value = video_stream.get("duration") or probe.get("format", {}).get("duration")
    duration_ms = round(float(duration_value) * 1000)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    now = datetime.now(UTC).isoformat()
    timeline_id = str(uuid.uuid4())
    render_id = str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO timeline_revisions
            (id,episode_id,revision_no,content_json,input_snapshot_json,revision_hash,status,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,'{}','{}',?,'FROZEN',?,?,?,1,'v2')""",
            (timeline_id, episode_id, "a" * 64, now, now, "test"),
        )
        connection.execute(
            """INSERT INTO episode_render_versions
            (id,episode_id,timeline_revision_id,rel_path,sha256,probe_json,integrity_status,duration_ms,mime_type,
             input_snapshot_json,ffmpeg_command_json,execution_log_text,created_at,updated_at,created_by,revision,schema_version,render_kind)
            VALUES (?,?,?,?,?,?,'VERIFIED',?,'video/mp4','{}','{}','',?,?,?,1,'v2','COMPOSE')""",
            (
                render_id,
                episode_id,
                timeline_id,
                source.relative_to(project_root).as_posix(),
                digest,
                json.dumps(probe),
                duration_ms,
                now,
                now,
                "test",
            ),
        )
        connection.execute(
            """INSERT INTO review_decisions
            (id,subject_type,subject_id,review_template_version_id,decision,comment,supersedes_decision_id,
             subject_revision,is_stale,stale_reason,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,'EPISODE_RENDER_VERSION',?,'test-template','APPROVED','approved',NULL,1,0,NULL,?,?,?,1,'v2')""",
            (str(uuid.uuid4()), render_id, now, now, "test"),
        )
    return render_id


def test_fake_adapter_exercises_real_ffmpeg_batch_and_registers_derived_render(
    workspace,
    database,
    monkeypatch,
) -> None:
    """FAKE_ADAPTER proves orchestration only; it is not GPU/model acceptance evidence."""

    project = ProjectService(database, workspace.projects_root).create_project(
        code="upscale_executor",
        title="超分执行测试",
        episode_count=2,
        aspect_ratio="16:9",
        fps_num=2,
        fps_den=1,
        target_duration_ms=1500,
        allow_unconfigured_capabilities=True,
    )
    project_root = workspace.projects_root / str(project["root_rel"])
    season = ProjectService(database, workspace.projects_root).list_seasons(str(project["id"]))[0]
    project_episodes = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))
    episode = project_episodes[0]
    untouched_episode = project_episodes[1]
    compose_id = _real_compose(workspace, database, str(episode["id"]), project_root)
    profile_id = _fake_ncnn_profile(database, workspace)

    with TestClient(create_app(workspace)) as client:
        selected_response = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-selections:resolve",
            json={"mode": "EXPLICIT", "episode_ids": [episode["id"]]},
        )
        assert selected_response.status_code == 200, selected_response.text
        selected = selected_response.json()["selection"]
        plan_response = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-plans",
            json={
                "selection_hash": selected["selection_hash"],
                "episode_ids": [episode["id"]],
                "preset_version_id": "builtin-upscale-anime-1080-standard-v1",
                "execution_profile_version_id": profile_id,
            },
        )
        assert plan_response.status_code == 202, plan_response.text
        plan_id = plan_response.json()["plan"]["id"]
        checked = LocalMediaWorker(database, workspace).run_once("fake-upscale-preflight", ["CPU"])
        assert checked is not None and checked["result"]["job_state"] == "SUCCEEDED"
        plan = client.get(f"/api/v1/video-upscale-plans/{plan_id}").json()["plan"]
        assert plan["status"] == "READY", plan
        warning_ids = [
            f"{item['episode_id']}:{warning['code']}"
            for item in plan["items"]
            for warning in item["warnings"]
        ]

        preview_response = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-previews",
            headers={"Idempotency-Key": "fake-upscale-preview-1"},
            json={
                "plan_id": plan_id,
                "plan_hash": plan["plan_hash"],
                "episode_id": episode["id"],
                "start_ms": 0,
                "duration_ms": 1000,
                "acknowledged_warning_ids": warning_ids,
            },
        )
        assert preview_response.status_code == 202, preview_response.text
        preview = preview_response.json()["run"]
        assert preview["purpose"] == "PREVIEW"
        assert preview["job_state"] == "QUEUED"
        replay = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-previews",
            headers={"Idempotency-Key": "fake-upscale-preview-1"},
            json={
                "plan_id": plan_id,
                "plan_hash": plan["plan_hash"],
                "episode_id": episode["id"],
                "start_ms": 0,
                "duration_ms": 1000,
                "acknowledged_warning_ids": warning_ids,
            },
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["run"]["id"] == preview["id"]
        assert replay.json()["idempotent_replay"] is True
        conflict = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-previews",
            headers={"Idempotency-Key": "fake-upscale-preview-1"},
            json={
                "plan_id": plan_id,
                "plan_hash": plan["plan_hash"],
                "episode_id": episode["id"],
                "start_ms": 0,
                "duration_ms": 1200,
                "acknowledged_warning_ids": warning_ids,
            },
        )
        assert conflict.status_code == 409, conflict.text
        preview_execution = LocalMediaWorker(database, workspace).run_once("fake-upscale-preview-worker", ["GPU_H3"])
        assert preview_execution is not None
        assert preview_execution["result"]["job_state"] == "SUCCEEDED", preview_execution.get("error")
        completed_preview = client.get(f"/api/v1/video-upscale-runs/{preview['id']}").json()["run"]
        assert completed_preview["job_state"] == "SUCCEEDED"
        assert completed_preview["output_render_id"] is None
        assert completed_preview["content_url"].endswith(f"/{preview['id']}/content")
        assert completed_preview["source_content_url"].endswith(f"/{preview['id']}/source-content")
        assert completed_preview["sample_duration_ms"] == 1000
        content = client.get(completed_preview["content_url"])
        assert content.status_code == 200
        assert content.headers["content-type"].startswith("video/mp4")
        assert content.headers["content-disposition"].startswith("inline")
        source_content = client.get(completed_preview["source_content_url"])
        assert source_content.status_code == 200
        assert source_content.headers["content-disposition"].startswith("inline")
        source_sha256 = selected["items"][0]["source"]["source_sha256"]
        assert source_content.headers["etag"] == f'"{source_sha256}"'
        with database.connect() as connection:
            derived_before_full = int(
                connection.execute(
                    "SELECT COUNT(*) FROM episode_render_versions WHERE render_kind='SUPER_RESOLUTION'"
                ).fetchone()[0]
            )
        assert derived_before_full == 0

        batch_response = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-batches",
            headers={"Idempotency-Key": "fake-upscale-batch-1"},
            json={"plan_id": plan_id, "plan_hash": plan["plan_hash"], "title": "Fake adapter batch", "acknowledged_warning_ids": warning_ids},
        )
        assert batch_response.status_code == 202, batch_response.text
        batch = batch_response.json()["batch"]
        assert batch["aggregate"]["states"] == {"QUEUED": 1}
        batch_replay = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-batches",
            headers={"Idempotency-Key": "fake-upscale-batch-1"},
            json={"plan_id": plan_id, "plan_hash": plan["plan_hash"], "title": "Fake adapter batch", "acknowledged_warning_ids": warning_ids},
        )
        assert batch_replay.status_code == 202, batch_replay.text
        assert batch_replay.json()["batch"]["id"] == batch["id"]
        assert batch_replay.json()["idempotent_replay"] is True
        batch_conflict = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-batches",
            headers={"Idempotency-Key": "fake-upscale-batch-1"},
            json={"plan_id": plan_id, "plan_hash": plan["plan_hash"], "title": "Changed title", "acknowledged_warning_ids": warning_ids},
        )
        assert batch_conflict.status_code == 409, batch_conflict.text
        assert batch_conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"

        shared_response = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-batches",
            headers={"Idempotency-Key": "fake-upscale-batch-shared"},
            json={"plan_id": plan_id, "plan_hash": plan["plan_hash"], "title": "Shared run consumer", "acknowledged_warning_ids": warning_ids},
        )
        assert shared_response.status_code == 202, shared_response.text
        shared_batch = shared_response.json()["batch"]
        assert shared_batch["items"][0]["current_run_id"] == batch["items"][0]["current_run_id"]
        with database.connect() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM video_upscale_runs WHERE project_id=? AND purpose='FULL'",
                (project["id"],),
            ).fetchone()[0] == 1
        shared_cancel = client.post(
            f"/api/v1/video-upscale-batches/{shared_batch['id']}:control",
            json={"action": "CANCEL_UNFINISHED", "expected_revision": shared_batch["revision"]},
        )
        assert shared_cancel.status_code == 200, shared_cancel.text
        assert shared_cancel.json()["effects"][0]["job_action"] == "SHARED_RUN_CONTINUES"
        assert shared_cancel.json()["batch"]["items"][0]["effective_state"] == "CANCELLED_FOR_BATCH"
        assert client.get(f"/api/v1/video-upscale-batches/{batch['id']}").json()["batch"]["items"][0]["job_state"] == "QUEUED"

        paused_response = client.post(
            f"/api/v1/video-upscale-batches/{batch['id']}:control",
            json={"action": "PAUSE_PENDING", "expected_revision": batch["revision"]},
        )
        assert paused_response.status_code == 200, paused_response.text
        paused = paused_response.json()["batch"]
        assert paused["items"][0]["effective_state"] == "PAUSED_FOR_BATCH"
        assert LocalMediaWorker(database, workspace).run_once("paused-upscale-worker", ["GPU_H3"]) is None
        resumed_response = client.post(
            f"/api/v1/video-upscale-batches/{batch['id']}:control",
            json={"action": "RESUME", "expected_revision": paused["revision"]},
        )
        assert resumed_response.status_code == 200, resumed_response.text
        batch = resumed_response.json()["batch"]
        assert batch["aggregate"]["states"] == {"QUEUED": 1}

        fake_script = workspace.work_root / "fake-ncnn-adapter.py"
        working_script = fake_script.read_text(encoding="utf-8")
        fake_script.write_text("raise SystemExit(7)\n", encoding="utf-8")
        with database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET max_attempts=1 WHERE id=?",
                (batch["items"][0]["job_id"],),
            )
        failed_execution = LocalMediaWorker(database, workspace).run_once("failed-upscale-worker", ["GPU_H3"])
        assert failed_execution is not None
        assert failed_execution["result"]["job_state"] == "FAILED"
        failed_batch = client.get(f"/api/v1/video-upscale-batches/{batch['id']}").json()["batch"]
        assert failed_batch["aggregate"]["states"] == {"FAILED": 1}
        fake_script.write_text(working_script, encoding="utf-8")
        retried_response = client.post(
            f"/api/v1/video-upscale-batches/{batch['id']}:control",
            json={"action": "RETRY_FAILED", "expected_revision": failed_batch["revision"]},
        )
        assert retried_response.status_code == 200, retried_response.text
        batch = retried_response.json()["batch"]
        assert batch["aggregate"]["states"] == {"QUEUED": 1}
        failed_attempt = failed_execution["attempt"]
        fenced_executor = NcnnVideoUpscaleExecutor(
            database,
            workspace,
            attempt_context=lambda: (
                str(failed_attempt["id"]),
                str(failed_attempt["lease_token"]),
                "failed-upscale-worker",
            ),
        )
        with pytest.raises(DomainRuleError) as fenced:
            fenced_executor._assert_attempt_fence(str(batch["items"][0]["current_run_id"]))
        assert fenced.value.code == "UPSCALE_ATTEMPT_FENCED"

        original_register_output = NcnnVideoUpscaleExecutor._register_output

        def crash_after_final_publish(self, *args, **kwargs):
            raise DomainRuleError("FAULT_AFTER_FINAL_PUBLISH", "fault injection after final rename")

        monkeypatch.setattr(NcnnVideoUpscaleExecutor, "_register_output", crash_after_final_publish)
        interrupted_execution = LocalMediaWorker(database, workspace).run_once(
            "publish-crash-upscale-worker",
            ["GPU_H3"],
        )
        assert interrupted_execution is not None
        assert interrupted_execution["result"]["job_state"] == "FAILED"
        publish_receipt_path = (
            workspace.work_root
            / "jobs"
            / str(batch["items"][0]["job_id"])
            / "ncnn-upscale-publish.json"
        )
        assert publish_receipt_path.is_file()
        monkeypatch.setattr(NcnnVideoUpscaleExecutor, "_register_output", original_register_output)
        fake_script.write_text("raise SystemExit('MODEL_MUST_NOT_RUN_DURING_PUBLISH_RECOVERY')\n", encoding="utf-8")
        publish_failed_batch = client.get(
            f"/api/v1/video-upscale-batches/{batch['id']}"
        ).json()["batch"]
        publish_retry = client.post(
            f"/api/v1/video-upscale-batches/{batch['id']}:control",
            json={"action": "RETRY_FAILED", "expected_revision": publish_failed_batch["revision"]},
        )
        assert publish_retry.status_code == 200, publish_retry.text
        batch = publish_retry.json()["batch"]
        executed = LocalMediaWorker(database, workspace).run_once("publish-recovery-worker", ["GPU_H3"])
        assert executed is not None, "超分发布恢复 Job 未被领取"
        assert executed["result"]["job_state"] == "SUCCEEDED", executed
        published_recovery_receipt = json.loads(
            (workspace.work_root / executed["artifact"]["sandbox_rel_path"]).read_text(encoding="utf-8")
        )
        assert published_recovery_receipt["recovered_published_output"] is True
        assert not publish_receipt_path.exists()
        fake_script.write_text(working_script, encoding="utf-8")

        completed = client.get(f"/api/v1/video-upscale-batches/{batch['id']}").json()["batch"]
        assert completed["aggregate"]["states"] == {"SUCCEEDED": 1}
        output_render_id = completed["items"][0]["output_render_id"]
        assert output_render_id

    with database.connect() as connection:
        render = connection.execute(
            """SELECT id,render_kind,parent_render_version_id,upscale_run_id,rel_path,probe_json
            FROM episode_render_versions WHERE id=?""",
            (output_render_id,),
        ).fetchone()
        chunks = connection.execute(
            "SELECT state,frame_count FROM video_upscale_chunks WHERE run_id=? ORDER BY ordinal",
            (render["upscale_run_id"],),
        ).fetchall()
        qc = connection.execute(
            """SELECT run.status FROM machine_check_runs run
            WHERE run.subject_type='EPISODE_RENDER_VERSION' AND run.subject_id=?""",
            (output_render_id,),
        ).fetchone()
    assert render["render_kind"] == "SUPER_RESOLUTION"
    assert render["parent_render_version_id"] == compose_id
    output = project_root / str(render["rel_path"])
    assert output.is_file()
    output_probe = MediaService(database, workspace).probe_output(output, "VIDEO")
    video = next(stream for stream in output_probe["streams"] if stream["codec_type"] == "video")
    audio = [stream for stream in output_probe["streams"] if stream["codec_type"] == "audio"]
    assert (int(video["width"]), int(video["height"])) == (1920, 1080)
    assert video.get("sample_aspect_ratio") == "1:1"
    assert video.get("field_order") == "progressive"
    assert {
        key: video.get(key)
        for key in ("color_range", "color_space", "color_transfer", "color_primaries")
    } == {
        "color_range": "tv",
        "color_space": "bt709",
        "color_transfer": "bt709",
        "color_primaries": "bt709",
    }
    assert len(audio) == 1
    assert str((audio[0].get("tags") or {}).get("language")) == "chi"
    assert int((audio[0].get("disposition") or {}).get("default") or 0) == 1
    assert [(row["state"], int(row["frame_count"])) for row in chunks] == [("SUCCEEDED", 3)]
    assert qc["status"] == "PASS"

    fake_script = workspace.work_root / "fake-ncnn-adapter.py"
    fake_script.write_text("raise SystemExit('MODEL_MUST_NOT_RUN_DURING_RECOVERY')\n", encoding="utf-8")
    with database.transaction() as connection:
        connection.execute(
            """UPDATE jobs SET state='QUEUED',next_run_at=?,finished_at=NULL,
               last_error_code=NULL,last_error_detail_redacted=NULL WHERE id=?""",
            (datetime.now(UTC).isoformat(), batch["items"][0]["job_id"]),
        )
    recovered_execution = LocalMediaWorker(database, workspace).run_once(
        "recover-registered-output-worker",
        ["GPU_H3"],
    )
    assert recovered_execution is not None
    assert recovered_execution["result"]["job_state"] == "SUCCEEDED"
    recovery_receipt = json.loads(
        (workspace.work_root / recovered_execution["artifact"]["sandbox_rel_path"]).read_text(encoding="utf-8")
    )
    assert recovery_receipt["recovered_existing_output"] is True
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM episode_render_versions WHERE upscale_run_id=?",
            (render["upscale_run_id"],),
        ).fetchone()[0] == 1

    target = ConfigurationService(database).create_delivery_target(
        str(project["id"]),
        "upscale-1080",
        "超分 1080p",
        "LOCAL_FILESYSTEM",
        {
            "path_rel": "06_delivery/upscale-1080",
            "width": 1920,
            "height": 1080,
            "fps": 2,
            "bitrate": "4M",
            "audio_codec": "AAC",
            "subtitles": "NONE",
        },
    )
    now = datetime.now(UTC).isoformat()
    with database.transaction() as connection:
        template_id = connection.execute(
            "SELECT id FROM review_templates WHERE code='episode_upscale' ORDER BY version_no DESC LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO review_decisions
            (id,subject_type,subject_id,review_template_version_id,decision,comment,
             supersedes_decision_id,subject_revision,is_stale,stale_reason,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,'EPISODE_RENDER_VERSION',?,?,'APPROVED','fake adapter acceptance only',NULL,1,0,NULL,?,?,?,1,'v2')""",
            (str(uuid.uuid4()), output_render_id, template_id, now, now, "test"),
        )

    with TestClient(create_app(workspace)) as client:
        selection_item = {
            "episode_id": episode["id"],
            "target_slot": target["version_id"],
            "selected_render_id": output_render_id,
            "expected_selection_revision": 0,
        }
        adoption_plan = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:plan",
            json={"items": [selection_item]},
        )
        assert adoption_plan.status_code == 200, adoption_plan.text
        adopted = client.post(
            f"/api/v1/projects/{project['id']}/delivery-selections:commit",
            json={"items": [selection_item], "plan_hash": adoption_plan.json()["plan"]["plan_hash"]},
        )
        assert adopted.status_code == 200, adopted.text
        delivery_items = [{"episode_id": episode["id"], "target_version_id": target["version_id"]}]
        delivery_plan_response = client.post(
            f"/api/v1/projects/{project['id']}/delivery-build-batches:plan",
            json={"items": delivery_items},
        )
        assert delivery_plan_response.status_code == 200, delivery_plan_response.text
        delivery_plan = delivery_plan_response.json()["plan"]
        delivery_submit = client.post(
            f"/api/v1/projects/{project['id']}/delivery-build-batches:submit",
            headers={"Idempotency-Key": "fake-upscale-delivery-1"},
            json={
                "items": delivery_items,
                "plan_hash": delivery_plan["plan_hash"],
                "title": "Fake adapter delivery batch",
            },
        )
        assert delivery_submit.status_code == 202, delivery_submit.text
        delivery_batch = delivery_submit.json()["batch"]
        assert delivery_batch["aggregate"]["states"] == {"QUEUED": 1}
        delivery_job_id = str(delivery_batch["items"][0]["job_id"])
        # Simulate a hard process crash after the operation-owned directory was
        # published but before its database transaction committed.  The retry
        # may replace only this exact marked directory.
        interrupted_delivery_dir = project_root / "06_delivery" / "upscale-1080" / str(episode["code"])
        interrupted_delivery_dir.mkdir(parents=True)
        (interrupted_delivery_dir / ".local-drama-delivery-build.json").write_text(
            json.dumps(
                {
                    "schema_version": "localdrama.delivery-build-publish.v1",
                    "operation_id": delivery_job_id,
                    "episode_render_version_id": output_render_id,
                    "target_version_id": target["version_id"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (interrupted_delivery_dir / "incomplete.tmp").write_text("interrupted", encoding="utf-8")

        # Fail the first build attempt, then prove the batch-level retry only
        # requeues the failed item and can recover the interrupted publication.
        hidden_source = output.with_name(f".{output.name}.delivery-test-hidden")
        output.replace(hidden_source)
        with database.transaction() as connection:
            connection.execute("UPDATE jobs SET max_attempts=1 WHERE id=?", (delivery_job_id,))
        try:
            failed_delivery_work = LocalMediaWorker(database, workspace).run_once(
                "failed-upscale-delivery-worker",
                ["CPU"],
            )
        finally:
            hidden_source.replace(output)
        assert failed_delivery_work is not None
        assert failed_delivery_work["result"]["job_state"] == "FAILED"
        failed_delivery_batch = client.get(
            f"/api/v1/delivery-build-batches/{delivery_batch['id']}"
        ).json()["batch"]
        assert failed_delivery_batch["aggregate"]["states"] == {"FAILED": 1}
        retried_delivery = client.post(
            f"/api/v1/delivery-build-batches/{delivery_batch['id']}:retry-failed",
            json={"action": "RETRY_FAILED", "expected_revision": failed_delivery_batch["revision"]},
        )
        assert retried_delivery.status_code == 200, retried_delivery.text
        assert retried_delivery.json()["retried"] == 1
        assert retried_delivery.json()["batch"]["aggregate"]["states"] == {"QUEUED": 1}
        delivery_work = LocalMediaWorker(database, workspace).run_once("fake-upscale-delivery-worker", ["CPU"])
        assert delivery_work is not None
        assert delivery_work["result"]["job_state"] == "SUCCEEDED", delivery_work
        completed_delivery = client.get(
            f"/api/v1/delivery-build-batches/{delivery_batch['id']}"
        ).json()["batch"]
        assert completed_delivery["aggregate"]["states"] == {"SUCCEEDED": 1}
        assert completed_delivery["items"][0]["package_id"]
        assert completed_delivery["items"][0]["package_id"] == delivery_job_id
        assert not (interrupted_delivery_dir / ".local-drama-delivery-build.json").exists()
        assert not (interrupted_delivery_dir / "incomplete.tmp").exists()

        # Add a fault-injected second delivery item to the same batch and
        # prove RETRY_FAILED requeues only that item.  The already successful
        # package/job is not reset or rebuilt.
        failed_delivery_job = JobService(database, workspace).create_job(
            str(project["id"]),
            "DELIVERY_BUILD",
            "EPISODE_RENDER_VERSION",
            output_render_id,
            "CPU",
            {"fault_injected": True},
            f"delivery-fault-{uuid.uuid4()}",
            max_attempts=1,
            subject_kind="DELIVERY_PACKAGE",
            scope_kind="PROJECT",
            scope_project_id=str(project["id"]),
            scope_episode_id=str(untouched_episode["id"]),
            stage_code="VIDEO_UPSCALE_DELIVERY",
        )
        fault_now = datetime.now(UTC).isoformat()
        with database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET state='FAILED',finished_at=?,last_error_code='FAULT_INJECTED' WHERE id=?",
                (fault_now, failed_delivery_job["id"]),
            )
            source_batch_item_id = connection.execute(
                "SELECT id FROM video_upscale_batch_items WHERE batch_id=? AND episode_id=?",
                (batch["id"], episode["id"]),
            ).fetchone()[0]
            failed_link_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO video_upscale_delivery_links
                (id,batch_item_id,selected_render_id,target_version_id,delivery_fingerprint,
                 job_id,package_id,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,NULL,?,?,?,1,'video-upscale-delivery-link.v1')""",
                (
                    failed_link_id,
                    source_batch_item_id,
                    output_render_id,
                    target["version_id"],
                    "f" * 64,
                    failed_delivery_job["id"],
                    fault_now,
                    fault_now,
                    "test",
                ),
            )
            connection.execute(
                """INSERT INTO video_upscale_delivery_batch_items
                (id,delivery_batch_id,episode_id,ordinal,delivery_link_id,selected_render_id,target_version_id,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,1,'video-upscale-delivery-batch-item.v1')""",
                (
                    str(uuid.uuid4()),
                    delivery_batch["id"],
                    untouched_episode["id"],
                    2,
                    failed_link_id,
                    output_render_id,
                    target["version_id"],
                    fault_now,
                    fault_now,
                    "test",
                ),
            )
            successful_attempt_count = connection.execute(
                "SELECT COUNT(*) FROM job_attempts WHERE job_id=?",
                (delivery_job_id,),
            ).fetchone()[0]
        mixed_delivery_batch = client.get(
            f"/api/v1/delivery-build-batches/{delivery_batch['id']}"
        ).json()["batch"]
        assert mixed_delivery_batch["aggregate"]["states"] == {"SUCCEEDED": 1, "FAILED": 1}
        retry_only_failed = client.post(
            f"/api/v1/delivery-build-batches/{delivery_batch['id']}:retry-failed",
            json={"action": "RETRY_FAILED", "expected_revision": mixed_delivery_batch["revision"]},
        )
        assert retry_only_failed.status_code == 200, retry_only_failed.text
        assert retry_only_failed.json()["retried"] == 1
        assert retry_only_failed.json()["batch"]["aggregate"]["states"] == {
            "SUCCEEDED": 1,
            "QUEUED": 1,
        }
        with database.connect() as connection:
            successful_job = connection.execute(
                "SELECT state FROM jobs WHERE id=?",
                (delivery_job_id,),
            ).fetchone()
            assert successful_job["state"] == "SUCCEEDED"
            assert connection.execute(
                "SELECT COUNT(*) FROM job_attempts WHERE job_id=?",
                (delivery_job_id,),
            ).fetchone()[0] == successful_attempt_count
        with database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET state='CANCELLED',next_run_at=NULL,finished_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), failed_delivery_job["id"]),
            )

    with database.connect() as connection:
        delivered_video = connection.execute(
            """SELECT file.rel_path FROM delivery_files file
            WHERE file.delivery_package_id=? AND lower(file.rel_path) LIKE '%.mp4'
            ORDER BY file.rel_path LIMIT 1""",
            (completed_delivery["items"][0]["package_id"],),
        ).fetchone()
    assert delivered_video is not None
    delivered_path = project_root / str(delivered_video["rel_path"])
    delivered_stat = delivered_path.stat()
    # Simulate a worker crash after the package transaction committed but
    # before Job completion.  Re-execution must reuse the stable package and
    # leave the immutable official video byte-for-byte untouched.
    with database.transaction() as connection:
        connection.execute(
            """UPDATE jobs SET state='QUEUED',next_run_at=?,finished_at=NULL,
               last_error_code=NULL,last_error_detail_redacted=NULL WHERE id=?""",
            (datetime.now(UTC).isoformat(), delivery_job_id),
        )
    delivery_recovery = LocalMediaWorker(database, workspace).run_once(
        "recover-registered-delivery-worker",
        ["CPU"],
    )
    assert delivery_recovery is not None
    assert delivery_recovery["result"]["job_state"] == "SUCCEEDED"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM delivery_packages WHERE id=?",
            (delivery_job_id,),
        ).fetchone()[0] == 1
    assert delivered_path.stat().st_mtime_ns == delivered_stat.st_mtime_ns
    assert delivered_path.stat().st_size == delivered_stat.st_size
    delivery_probe = MediaService(database, workspace).probe_output(
        delivered_path,
        "VIDEO",
    )
    delivery_stream = next(stream for stream in delivery_probe["streams"] if stream["codec_type"] == "video")
    assert (int(delivery_stream["width"]), int(delivery_stream["height"])) == (1920, 1080)

    # Expired intermediate cleanup is plan/commit guarded.  Even an
    # inconsistent terminal Job is not eligible while an attempt lease is
    # active, and cleanup never touches the source, official render, receipt,
    # or formal delivery package outside the owned ncnn-upscale directory.
    upscale_job_id = str(batch["items"][0]["job_id"])
    run_root = workspace.work_root / "jobs" / upscale_job_id / "ncnn-upscale"
    receipt_path = workspace.work_root / "jobs" / upscale_job_id / "ncnn-upscale-receipt.json"
    assert run_root.is_dir() and receipt_path.is_file()
    expired = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET updated_at=?,finished_at=? WHERE id=?",
            (expired, expired, upscale_job_id),
        )
        last_attempt_id = connection.execute(
            "SELECT id FROM job_attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1",
            (upscale_job_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE job_attempts SET state='RUNNING',lease_expires_at=? WHERE id=?",
            (future, last_attempt_id),
        )
    cleanup = VideoUpscaleCleanupService(database, workspace)
    active_plan = cleanup.plan(str(project["id"]), retention_days=7)
    assert active_plan["candidate_count"] == 0
    with database.transaction() as connection:
        connection.execute(
            "UPDATE job_attempts SET state='SUCCEEDED',lease_expires_at=NULL WHERE id=?",
            (last_attempt_id,),
        )
    cleanup_plan = cleanup.plan(str(project["id"]), retention_days=7)
    assert cleanup_plan["candidate_count"] == 1
    cleanup_result = cleanup.commit(
        str(project["id"]),
        retention_days=7,
        eligible_before=str(cleanup_plan["eligible_before"]),
        plan_hash=str(cleanup_plan["plan_hash"]),
    )
    assert cleanup_result["deleted_count"] == 1
    assert not run_root.exists()
    assert receipt_path.is_file()
    assert output.is_file()
    assert delivered_path.is_file()


def test_two_episode_batch_rolls_back_every_fact_when_second_link_fails(workspace, database) -> None:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="upscale_atomic_rollback",
        title="超分原子回滚测试",
        episode_count=2,
        aspect_ratio="16:9",
        fps_num=2,
        fps_den=1,
        target_duration_ms=1500,
        allow_unconfigured_capabilities=True,
    )
    project_root = workspace.projects_root / str(project["root_rel"])
    season = ProjectService(database, workspace.projects_root).list_seasons(str(project["id"]))[0]
    episodes = ProjectService(database, workspace.projects_root).list_episodes(str(season["id"]))
    for episode in episodes:
        _real_compose(workspace, database, str(episode["id"]), project_root)
    profile_id = _fake_ncnn_profile(database, workspace)

    with TestClient(create_app(workspace)) as client:
        selected_response = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-selections:resolve",
            json={"mode": "EXPLICIT", "episode_ids": [episode["id"] for episode in episodes]},
        )
        assert selected_response.status_code == 200, selected_response.text
        selection = selected_response.json()["selection"]
        plan_response = client.post(
            f"/api/v1/projects/{project['id']}/video-upscale-plans",
            json={
                "selection_hash": selection["selection_hash"],
                "episode_ids": [episode["id"] for episode in episodes],
                "preset_version_id": "builtin-upscale-anime-1080-standard-v1",
                "execution_profile_version_id": profile_id,
            },
        )
        assert plan_response.status_code == 202, plan_response.text
        plan_id = plan_response.json()["plan"]["id"]
        checked = LocalMediaWorker(database, workspace).run_once("atomic-preflight-worker", ["CPU"])
        assert checked is not None and checked["result"]["job_state"] == "SUCCEEDED"
        plan = client.get(f"/api/v1/video-upscale-plans/{plan_id}").json()["plan"]
        assert plan["status"] == "READY"
        warning_ids = [
            f"{item['episode_id']}:{warning['code']}"
            for item in plan["items"]
            for warning in item["warnings"]
        ]

    class FailSecondLink:
        def __init__(self, db) -> None:
            self.delegate = ExecutionJobLinkService(db)
            self.calls = 0

        def link_in_transaction(self, connection, link) -> None:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("INJECTED_SECOND_LINK_FAILURE")
            self.delegate.link_in_transaction(connection, link)

    tables = {
        "video_upscale_batches": "SELECT COUNT(*) FROM video_upscale_batches WHERE project_id=?",
        "video_upscale_batch_items": """SELECT COUNT(*) FROM video_upscale_batch_items item
            JOIN video_upscale_batches batch ON batch.id=item.batch_id WHERE batch.project_id=?""",
        "video_upscale_runs": "SELECT COUNT(*) FROM video_upscale_runs WHERE project_id=?",
        "jobs": "SELECT COUNT(*) FROM jobs WHERE scope_project_id=? AND subject_kind='VIDEO_UPSCALE_RUN'",
        "snapshots": """SELECT COUNT(*) FROM mp_execution_snapshots snapshot
            JOIN mp_capability_definitions capability ON capability.id=snapshot.capability_definition_id
            WHERE capability.code='UPSCALE_VIDEO'""",
        "links": """SELECT COUNT(*) FROM mp_execution_job_links link
            JOIN jobs job ON job.id=link.job_id WHERE job.scope_project_id=? AND job.subject_kind='VIDEO_UPSCALE_RUN'""",
    }

    def counts() -> dict[str, int]:
        with database.connect() as connection:
            result: dict[str, int] = {}
            for name, sql in tables.items():
                parameters = () if name == "snapshots" else (project["id"],)
                result[name] = int(connection.execute(sql, parameters).fetchone()[0])
            return result

    before = counts()
    service = VideoUpscaleBatchService(database, workspace, link_factory=FailSecondLink)
    with pytest.raises(RuntimeError, match="INJECTED_SECOND_LINK_FAILURE"):
        service.create(
            str(project["id"]),
            plan_id=plan_id,
            plan_hash=str(plan["plan_hash"]),
            title="Must roll back",
            acknowledged_warning_ids=warning_ids,
            idempotency_key="atomic-rollback-1",
        )
    assert counts() == before

    gate = threading.Barrier(2)

    def submit_concurrently() -> dict:
        gate.wait(timeout=10)
        return VideoUpscaleBatchService(database, workspace).create(
            str(project["id"]),
            plan_id=plan_id,
            plan_hash=str(plan["plan_hash"]),
            title="Concurrent exact replay",
            acknowledged_warning_ids=warning_ids,
            idempotency_key="concurrent-exact-replay-1",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: submit_concurrently(), range(2)))
    assert results[0]["batch"]["id"] == results[1]["batch"]["id"]
    assert {bool(result["idempotent_replay"]) for result in results} == {False, True}
    after = counts()
    assert after["video_upscale_batches"] == before["video_upscale_batches"] + 1
    assert after["video_upscale_batch_items"] == before["video_upscale_batch_items"] + 2
    assert after["video_upscale_runs"] == before["video_upscale_runs"] + 2
    assert after["jobs"] == before["jobs"] + 2
    assert after["snapshots"] == before["snapshots"] + 2
    assert after["links"] == before["links"] + 2

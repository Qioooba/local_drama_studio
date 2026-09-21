"""Run the real NCNN video-upscale acceptance flow in a new isolated instance.

Prepare creates two short approved source fixtures (landscape and portrait),
publishes an evidence-gated NCNN Profile, runs preview and full batch jobs, and
writes a review session. Finalize is deliberately separate: a person must view
the generated videos before explicitly attesting review, adoption and delivery.
No production database or project root is opened by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
for import_root in (REPO_ROOT, API_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from fastapi.testclient import TestClient
from local_drama.application.configuration import ConfigurationService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database
from local_drama.main import create_app
from local_drama.model_platform.application.ncnn_video_upscale_profiles import (
    NcnnVideoUpscaleProfileService,
)

from scripts.migrate import migrate

_SCHEMA = "localdrama.video-upscale-windows-uat.v1"
_SESSION_SCHEMA = "localdrama.video-upscale-windows-uat-session.v1"
_ISOLATION_MARKER = ".localdrama-video-upscale-uat"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read JSON contract: {path.name}") from error
    if not isinstance(value, dict):
        raise TypeError(f"JSON contract must be an object: {path.name}")
    return value


def _required_path(value: object, *, kind: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{kind} path is required")
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"{kind} path does not exist") from error
    return path


def _settings(root: Path, *, ffmpeg: Path, ffprobe: Path) -> Settings:
    return Settings(
        environment="video-upscale-windows-uat",
        instance_id=f"video-upscale-uat-{root.name}",
        data_root=root / "data",
        projects_root=root / "projects",
        work_root=root / "work",
        cache_root=root / "cache",
        logs_root=root / "logs",
        backups_root=root / "backups",
        ffmpeg_override=ffmpeg,
        ffprobe_override=ffprobe,
        release_root=REPO_ROOT,
        instance_root=root,
    )


def _new_isolation_root(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("isolation_root is required")
    requested = Path(value)
    if not requested.is_absolute():
        raise RuntimeError("isolation_root must be absolute")
    root = requested.resolve()
    if root.exists():
        raise RuntimeError("isolation_root must not already exist; UAT never reuses or deletes a directory")
    root.mkdir(parents=True)
    (root / _ISOLATION_MARKER).write_text(_SCHEMA + "\n", encoding="utf-8")
    return root


def _existing_isolation_root(value: object) -> Path:
    root = _required_path(value, kind="isolation root")
    if not root.is_dir():
        raise RuntimeError("isolation root is not a directory")
    marker = root / _ISOLATION_MARKER
    try:
        marker_value = marker.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError("isolation root marker is missing") from error
    if marker_value != _SCHEMA:
        raise RuntimeError("isolation root marker is invalid")
    return root


def _owned_file(root: Path, relative_path: object, *, label: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise RuntimeError(f"{label} relative path is missing")
    try:
        path = (root / relative_path).resolve(strict=True)
        path.relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as error:
        raise RuntimeError(f"{label} escapes or is missing from the isolated project") from error
    if not path.is_file():
        raise RuntimeError(f"{label} is not a file")
    return path


def _tool(config: dict[str, Any], key: str, command: str) -> Path:
    raw = config.get(key)
    discovered = str(raw).strip() if isinstance(raw, str) and raw.strip() else shutil.which(command)
    if not discovered:
        raise RuntimeError(f"{command} is required")
    path = Path(discovered).resolve()
    if not path.is_file():
        raise RuntimeError(f"{command} is not a file")
    return path


def _run(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"local media command failed with exit code {completed.returncode}")
    return completed


def _fixture_video(ffmpeg: Path, target: Path, *, width: int, height: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            str(ffmpeg), "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=2:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=2",
            "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", "-y", str(target),
        ]
    )


def _register_approved_compose(
    database: Database,
    settings: Settings,
    *,
    episode_id: str,
    project_root: Path,
    source: Path,
) -> str:
    probe = MediaService(database, settings).probe_output(source, "VIDEO")
    video = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), None)
    if not isinstance(video, dict):
        raise TypeError("fixture has no video stream")
    duration = video.get("duration") or probe.get("format", {}).get("duration")
    if duration is None:
        raise RuntimeError("fixture has no duration")
    now = _now()
    timeline_id, render_id = str(uuid.uuid4()), str(uuid.uuid4())
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO timeline_revisions
            (id,episode_id,revision_no,content_json,input_snapshot_json,revision_hash,status,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,'{}','{}',?,'FROZEN',?,?,?,1,'v2')""",
            (timeline_id, episode_id, hashlib.sha256(f"{episode_id}:uat".encode()).hexdigest(), now, now, "windows-uat"),
        )
        connection.execute(
            """INSERT INTO episode_render_versions
            (id,episode_id,timeline_revision_id,rel_path,sha256,probe_json,integrity_status,duration_ms,mime_type,
             input_snapshot_json,ffmpeg_command_json,execution_log_text,created_at,updated_at,created_by,revision,schema_version,render_kind)
            VALUES (?,?,?,?,?,?,'VERIFIED',?,'video/mp4','{}','{}','windows-uat fixture',?,?,?,1,'v2','COMPOSE')""",
            (
                render_id,
                episode_id,
                timeline_id,
                source.relative_to(project_root).as_posix(),
                _sha256(source),
                json.dumps(probe, ensure_ascii=False),
                round(float(duration) * 1000),
                now,
                now,
                "windows-uat",
            ),
        )
        connection.execute(
            """INSERT INTO review_decisions
            (id,subject_type,subject_id,review_template_version_id,decision,comment,supersedes_decision_id,
             subject_revision,is_stale,stale_reason,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,'EPISODE_RENDER_VERSION',?,'windows-uat-fixture','APPROVED',
                    'isolated synthetic source fixture',NULL,1,0,NULL,?,?,?,1,'v2')""",
            (str(uuid.uuid4()), render_id, now, now, "windows-uat"),
        )
    return render_id


def _response(response: Any, label: str) -> dict[str, Any]:
    if response.status_code not in {200, 201, 202}:
        raise RuntimeError(f"{label} failed: HTTP {response.status_code} {response.text[:500]}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError(f"{label} returned a non-object response")
    return payload


def _drain_worker(database: Database, settings: Settings, channel: str, expected: int) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for index in range(expected):
        receipt = LocalMediaWorker(database, settings).run_once(f"windows-uat-{channel.lower()}-{index + 1}", [channel])
        if receipt is None:
            raise RuntimeError(f"expected {expected} {channel} jobs but queue was empty at {index + 1}")
        if receipt["result"]["job_state"] != "SUCCEEDED":
            raise RuntimeError(f"{channel} job failed: {receipt['result']}")
        receipts.append(receipt)
    return receipts


def prepare(config_path: Path) -> Path:
    config = _load_json(config_path)
    if config.get("schema_version") != _SCHEMA or config.get("confirm_isolated_test") is not True:
        raise RuntimeError(f"config requires schema_version={_SCHEMA} and confirm_isolated_test=true")
    if config.get("model_name", "realesr-animevideov3") != "realesr-animevideov3":
        raise RuntimeError("windows UAT v1 requires realesr-animevideov3 because the fixture exercises verified 3x geometry")
    executable = _required_path(config.get("executable"), kind="NCNN executable")
    model_dir = _required_path(config.get("model_dir"), kind="model directory")
    ffmpeg, ffprobe = _tool(config, "ffmpeg", "ffmpeg"), _tool(config, "ffprobe", "ffprobe")
    root = _new_isolation_root(config.get("isolation_root"))
    settings = _settings(root, ffmpeg=ffmpeg, ffprobe=ffprobe)
    settings.ensure_roots()
    migrate(settings.database_path)
    database = Database(settings.database_path)
    profile = NcnnVideoUpscaleProfileService(database, settings).configure_and_publish(
        executable_path=str(executable),
        model_directory=str(model_dir),
        model_name="realesr-animevideov3",
        gpu_device=int(config.get("gpu_device", 0)),
        tile_size=int(config.get("tile_size", 0)),
        load_threads=int(config.get("load_threads", 1)),
        proc_threads=int(config.get("proc_threads", 1)),
        save_threads=int(config.get("save_threads", 2)),
        actor="windows-uat",
    )
    project = ProjectService(database, settings.projects_root).create_project(
        code=f"video-upscale-uat-{uuid.uuid4().hex[:8]}",
        title="视频超分 Windows 隔离验收",
        episode_count=2,
        aspect_ratio="16:9",
        fps_num=2,
        fps_den=1,
        target_duration_ms=2000,
        allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    project_root = settings.resolve_project_root(str(project["root_rel"]))
    project_service = ProjectService(database, settings.projects_root)
    season = project_service.list_seasons(project_id)[0]
    episodes = project_service.list_episodes(str(season["id"]))
    source_specs = ((640, 360), (360, 640))
    source_renders: list[str] = []
    for episode, (width, height) in zip(episodes, source_specs, strict=True):
        source = project_root / "05_outputs" / "windows-uat" / f"{episode['code']}-{width}x{height}.mp4"
        _fixture_video(ffmpeg, source, width=width, height=height)
        source_renders.append(
            _register_approved_compose(
                database,
                settings,
                episode_id=str(episode["id"]),
                project_root=project_root,
                source=source,
            )
        )

    episode_ids = [str(item["id"]) for item in episodes]
    idempotency_key = f"windows-uat-{uuid.uuid4()}"
    with TestClient(create_app(settings)) as client:
        selection = _response(
            client.post(
                f"/api/v1/projects/{project_id}/video-upscale-selections:resolve",
                json={"mode": "EXPLICIT", "episode_ids": episode_ids, "source_policy": "APPROVED_COMPOSE"},
            ),
            "selection",
        )["selection"]
        plan_create = _response(
            client.post(
                f"/api/v1/projects/{project_id}/video-upscale-plans",
                json={
                    "selection_hash": selection["selection_hash"],
                    "episode_ids": episode_ids,
                    "preset_version_id": "builtin-upscale-anime-1080-standard-v1",
                    "execution_profile_version_id": profile.profile_version_id,
                },
            ),
            "plan create",
        )
        plan_id = str(plan_create["plan"]["id"])
        _drain_worker(database, settings, "CPU", 1)
        plan = _response(client.get(f"/api/v1/video-upscale-plans/{plan_id}"), "plan read")["plan"]
        if plan["status"] != "READY":
            raise RuntimeError(f"preflight did not become READY: {plan}")
        preview = _response(
            client.post(
                f"/api/v1/projects/{project_id}/video-upscale-previews",
                headers={"Idempotency-Key": f"{idempotency_key}-preview"},
                json={
                    "plan_id": plan_id,
                    "plan_hash": plan["plan_hash"],
                    "episode_id": episode_ids[0],
                    "start_ms": 0,
                    "duration_ms": 1000,
                },
            ),
            "preview submit",
        )["run"]
        _drain_worker(database, settings, "GPU_H3", 1)
        preview = _response(client.get(f"/api/v1/video-upscale-runs/{preview['id']}"), "preview read")["run"]
        if preview["job_state"] != "SUCCEEDED" or preview["output_render_id"] is not None:
            raise RuntimeError("preview did not finish as a non-render sample")
        batch_payload = {"plan_id": plan_id, "plan_hash": plan["plan_hash"], "title": "Windows UAT real NCNN batch"}
        batch = _response(
            client.post(
                f"/api/v1/projects/{project_id}/video-upscale-batches",
                headers={"Idempotency-Key": f"{idempotency_key}-batch"},
                json=batch_payload,
            ),
            "batch submit",
        )["batch"]
        replay = _response(
            client.post(
                f"/api/v1/projects/{project_id}/video-upscale-batches",
                headers={"Idempotency-Key": f"{idempotency_key}-batch"},
                json=batch_payload,
            ),
            "batch replay",
        )
        if replay["batch"]["id"] != batch["id"] or replay.get("idempotent_replay") is not True:
            raise RuntimeError("batch idempotency replay created or selected a different batch")
        paused = _response(
            client.post(
                f"/api/v1/video-upscale-batches/{batch['id']}:control",
                json={"action": "PAUSE_PENDING", "expected_revision": batch["revision"]},
            ),
            "pause pending",
        )["batch"]
        if LocalMediaWorker(database, settings).run_once("windows-uat-paused-proof", ["GPU_H3"]) is not None:
            raise RuntimeError("a paused pending job was incorrectly claimable")
        batch = _response(
            client.post(
                f"/api/v1/video-upscale-batches/{batch['id']}:control",
                json={"action": "RESUME", "expected_revision": paused["revision"]},
            ),
            "resume",
        )["batch"]
        gpu_receipts = _drain_worker(database, settings, "GPU_H3", 2)
        completed = _response(client.get(f"/api/v1/video-upscale-batches/{batch['id']}"), "batch read")["batch"]
        if completed["aggregate"]["states"] != {"SUCCEEDED": 2}:
            raise RuntimeError(f"full batch did not succeed: {completed['aggregate']}")

    render_evidence: list[dict[str, Any]] = []
    with database.connect() as connection:
        for item in completed["items"]:
            row = connection.execute(
                "SELECT id,episode_id,rel_path,sha256,probe_json,parent_render_version_id,upscale_run_id FROM episode_render_versions WHERE id=?",
                (item["output_render_id"],),
            ).fetchone()
            output = _owned_file(project_root, str(row["rel_path"]), label="upscale output")
            render_evidence.append(
                {
                    "episode_id": str(row["episode_id"]),
                    "render_id": str(row["id"]),
                    "run_id": str(row["upscale_run_id"]),
                    "source_render_id": str(row["parent_render_version_id"]),
                    "rel_path": str(row["rel_path"]),
                    "absolute_review_path": str(output),
                    "sha256": _sha256(output),
                    "registered_sha256": str(row["sha256"]),
                    "probe": json.loads(str(row["probe_json"])),
                }
            )
    session_path = root / "evidence" / "uat-session.json"
    session = {
        "schema_version": _SESSION_SCHEMA,
        "state": "AWAITING_HUMAN_VISUAL_REVIEW",
        "created_at": _now(),
        "isolation_root": str(root),
        "ffmpeg": str(ffmpeg),
        "ffprobe": str(ffprobe),
        "project_id": project_id,
        "project_root": str(project_root),
        "profile": {
            "profile_version_id": profile.profile_version_id,
            "runtime_model_installation_id": profile.runtime_model_installation_id,
            "validation_run_id": profile.validation_run_id,
            "model_name": profile.model_name,
            "verified_native_scales": list(profile.verified_native_scales),
            "runtime_sha256": profile.runtime_sha256,
            "model_bundle_sha256": profile.model_bundle_sha256,
        },
        "source_render_ids": source_renders,
        "selection_hash": selection["selection_hash"],
        "plan_id": plan_id,
        "plan_hash": plan["plan_hash"],
        "preview": preview,
        "batch_id": completed["id"],
        "batch_revision": completed["revision"],
        "idempotency_replay_verified": True,
        "pause_pending_verified": True,
        "gpu_job_receipts": gpu_receipts,
        "renders_for_review": render_evidence,
        "human_visual_review": {"status": "PENDING"},
        "delivery": {"status": "NOT_STARTED"},
    }
    _write_json(session_path, session)
    (root / "evidence" / "REVIEW_REQUIRED.txt").write_text(
        "请逐个查看 uat-session.json 的 renders_for_review.absolute_review_path。\n"
        "确认细线、文字、运动、暗部、闪烁、音画同步后，再运行 finalize。\n",
        encoding="utf-8",
    )
    return session_path


def _approve_render(database: Database, render_id: str, reviewer: str) -> None:
    now = _now()
    with database.transaction() as connection:
        template = connection.execute(
            "SELECT id FROM review_templates WHERE code='episode_upscale' ORDER BY version_no DESC LIMIT 1"
        ).fetchone()
        if template is None:
            raise RuntimeError("episode_upscale review template is missing")
        existing = connection.execute(
            """SELECT 1 FROM review_decisions WHERE subject_type='EPISODE_RENDER_VERSION'
               AND subject_id=? AND decision='APPROVED' AND is_stale=0""",
            (render_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO review_decisions
                (id,subject_type,subject_id,review_template_version_id,decision,comment,supersedes_decision_id,
                 subject_revision,is_stale,stale_reason,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,'EPISODE_RENDER_VERSION',?,?,'APPROVED',?,NULL,1,0,NULL,?,?,?,1,'v2')""",
                (
                    str(uuid.uuid4()),
                    render_id,
                    str(template["id"]),
                    "人工已查看 Windows UAT 输出并显式确认",
                    now,
                    now,
                    reviewer,
                ),
            )


def finalize(session_path: Path, *, reviewer: str, confirmation: str) -> Path:
    session = _load_json(session_path)
    if session.get("schema_version") != _SESSION_SCHEMA or session.get("state") != "AWAITING_HUMAN_VISUAL_REVIEW":
        raise RuntimeError("session is not awaiting visual review")
    if confirmation != "I_REVIEWED_EVERY_OUTPUT" or not reviewer.strip():
        raise RuntimeError("finalize requires --confirm I_REVIEWED_EVERY_OUTPUT and a non-empty --reviewer")
    root = _existing_isolation_root(session.get("isolation_root"))
    if session_path.resolve() != (root / "evidence" / "uat-session.json").resolve():
        raise RuntimeError("session path does not belong to its isolated root")
    ffmpeg = _required_path(session.get("ffmpeg"), kind="FFmpeg")
    ffprobe = _required_path(session.get("ffprobe"), kind="FFprobe")
    settings = _settings(root, ffmpeg=ffmpeg, ffprobe=ffprobe)
    database = Database(settings.database_path)
    project_id = str(session["project_id"])
    with database.connect() as connection:
        project = connection.execute("SELECT root_rel FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None:
        raise RuntimeError("isolated UAT project is missing")
    project_root = settings.resolve_project_root(str(project["root_rel"])).resolve(strict=True)
    if Path(str(session["project_root"])).resolve(strict=True) != project_root:
        raise RuntimeError("recorded project root does not match the isolated database")
    renders = session.get("renders_for_review")
    if not isinstance(renders, list) or len(renders) != 2:
        raise RuntimeError("session must contain exactly two reviewed outputs")
    for item in renders:
        output = _owned_file(project_root, item.get("rel_path"), label="reviewed output")
        if not output.is_file() or _sha256(output) != item["sha256"]:
            raise RuntimeError("a reviewed output changed after prepare")
        _approve_render(database, str(item["render_id"]), reviewer.strip())

    config = ConfigurationService(database)
    targets = {
        "LANDSCAPE": config.create_delivery_target(
            project_id,
            f"windows-uat-landscape-{uuid.uuid4().hex[:6]}",
            "Windows UAT 1920x1080",
            "LOCAL_FILESYSTEM",
            {"path_rel": "06_delivery/windows-uat-landscape", "width": 1920, "height": 1080, "fps": 2, "bitrate": "4M", "audio_codec": "AAC", "subtitles": "NONE"},
        ),
        "PORTRAIT": config.create_delivery_target(
            project_id,
            f"windows-uat-portrait-{uuid.uuid4().hex[:6]}",
            "Windows UAT 1080x1920",
            "LOCAL_FILESYSTEM",
            {"path_rel": "06_delivery/windows-uat-portrait", "width": 1080, "height": 1920, "fps": 2, "bitrate": "4M", "audio_codec": "AAC", "subtitles": "NONE"},
        ),
    }
    selection_items: list[dict[str, Any]] = []
    delivery_items: list[dict[str, str]] = []
    for item in renders:
        video = next(stream for stream in item["probe"]["streams"] if stream.get("codec_type") == "video")
        orientation = "LANDSCAPE" if int(video["width"]) > int(video["height"]) else "PORTRAIT"
        target_version_id = str(targets[orientation]["version_id"])
        selection_items.append(
            {
                "episode_id": item["episode_id"],
                "target_slot": target_version_id,
                "selected_render_id": item["render_id"],
                "expected_selection_revision": 0,
            }
        )
        delivery_items.append({"episode_id": item["episode_id"], "target_version_id": target_version_id})

    idempotency_key = f"windows-uat-finalize-{uuid.uuid4()}"
    with TestClient(create_app(settings)) as client:
        selection_plan = _response(
            client.post(f"/api/v1/projects/{project_id}/delivery-selections:plan", json={"items": selection_items}),
            "adoption plan",
        )["plan"]
        _response(
            client.post(
                f"/api/v1/projects/{project_id}/delivery-selections:commit",
                json={"items": selection_items, "plan_hash": selection_plan["plan_hash"]},
            ),
            "adoption commit",
        )
        delivery_plan = _response(
            client.post(f"/api/v1/projects/{project_id}/delivery-build-batches:plan", json={"items": delivery_items}),
            "delivery plan",
        )["plan"]
        delivery_batch = _response(
            client.post(
                f"/api/v1/projects/{project_id}/delivery-build-batches:submit",
                headers={"Idempotency-Key": idempotency_key},
                json={"items": delivery_items, "plan_hash": delivery_plan["plan_hash"], "title": "Windows UAT reviewed delivery"},
            ),
            "delivery submit",
        )["batch"]
        _drain_worker(database, settings, "CPU", 2)
        delivery_batch = _response(
            client.get(f"/api/v1/delivery-build-batches/{delivery_batch['id']}"),
            "delivery batch read",
        )["batch"]
    if delivery_batch["aggregate"]["states"] != {"SUCCEEDED": 2}:
        raise RuntimeError(f"delivery batch did not succeed: {delivery_batch['aggregate']}")

    packages: list[dict[str, Any]] = []
    with database.connect() as connection:
        for item in delivery_batch["items"]:
            files = connection.execute(
                "SELECT rel_path,sha256,byte_size FROM delivery_files WHERE delivery_package_id=? ORDER BY rel_path",
                (item["package_id"],),
            ).fetchall()
            package_files: list[dict[str, Any]] = []
            for row in files:
                path = _owned_file(project_root, str(row["rel_path"]), label="delivery file")
                evidence: dict[str, Any] = {
                    "rel_path": str(row["rel_path"]),
                    "sha256": _sha256(path),
                    "registered_sha256": str(row["sha256"]),
                    "byte_size": path.stat().st_size,
                    "registered_byte_size": int(row["byte_size"]),
                }
                if path.suffix.lower() == ".mp4":
                    evidence["probe"] = MediaService(database, settings).probe_output(path, "VIDEO")
                package_files.append(evidence)
            packages.append({"episode_id": item["episode_id"], "package_id": item["package_id"], "files": package_files})

    session["state"] = "COMPLETED"
    session["completed_at"] = _now()
    session["human_visual_review"] = {
        "status": "ATTESTED",
        "reviewer": reviewer.strip(),
        "attested_at": _now(),
        "confirmation": confirmation,
    }
    session["delivery"] = {"status": "SUCCEEDED", "batch": delivery_batch, "packages": packages}
    final_path = root / "evidence" / "uat-final.json"
    _write_json(final_path, session)
    session_path.write_text(json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return final_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    prepare_parser = subparsers.add_parser("prepare", help="Create and run an isolated real NCNN upscale batch")
    prepare_parser.add_argument("--config", required=True)
    finalize_parser = subparsers.add_parser("finalize", help="Attest visual review, adopt outputs and build delivery packages")
    finalize_parser.add_argument("--session", required=True)
    finalize_parser.add_argument("--reviewer", required=True)
    finalize_parser.add_argument("--confirm", required=True, help="Must equal I_REVIEWED_EVERY_OUTPUT")
    args = parser.parse_args()
    try:
        result = (
            prepare(Path(args.config).resolve())
            if args.mode == "prepare"
            else finalize(Path(args.session).resolve(), reviewer=args.reviewer, confirmation=args.confirm)
        )
    except Exception as error:  # noqa: BLE001 - CLI boundary returns a stable redacted failure receipt.
        print(json.dumps({"status": "FAIL", "mode": args.mode, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "PASS", "mode": args.mode, "evidence": str(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Prepare an isolated real raw-source-to-whole-drama production UAT.

The command takes an online SQLite backup, quiesces unfinished work only in
that isolated copy, creates a new project with explicit verified profile
bindings, and submits one persisted story pipeline run.  The pipeline is
authorized to apply its generated structure and then create/start a durable
WHOLE_DRAMA production session whose terminal automation boundary is
WAITING_REVIEW.

This is deliberately a preparation command.  It does not run a worker, write
human approvals, manufacture media, or claim that the UAT passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.commands.generation_preferences import (
    GenerationPreferenceCommandService,
)
from local_drama.application.projects import ProjectService
from local_drama.config import Settings
from local_drama.infrastructure.database.backup import online_backup
from local_drama.infrastructure.database.generation_preference_repository import (
    SqliteGenerationPreferenceRepository,
)
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.service_composition import build_pipeline_orchestrator

DEFAULT_SOURCE = """# 第一章 夜站

雨夜，年轻的快递员林夏独自站在废弃车站月台。她发现长椅下有一只发光的旧怀表，刚俯身拾起，停摆的站钟突然重新走动。

远处传来列车鸣笛。林夏握紧怀表望向黑暗隧道，一束白光逼近，她决定留下来查明真相。
"""

DEFAULT_PROFILES = {
    "LLM_STORY_PARSE": "98dae1f1-b625-57a3-aad2-1e448a1b38b9",
    "IMAGE_CHARACTER": "bcae6c29-ac73-4d19-9ddf-693f0f60df24",
    "IMAGE_SCENE": "a7b62b46-a800-4171-8a09-deb1210cabc3",
    "IMAGE_CONCEPT": "d892a257-9459-47bb-9df3-10a3e7428350",
    "VIDEO_I2V": "bfade67b-7ef2-4e3e-83a5-2ba77025e952",
}

ACTIVE_SESSION_STATUSES = (
    "READY",
    "RUNNING",
    "PAUSED",
    "WAITING_USER",
    "WAITING_REVIEW",
    "FAILED",
)
ACTIVE_JOB_STATES = (
    "QUEUED",
    "CLAIMED",
    "RUNNING",
    "WAITING_RETRY",
    "PAUSED",
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument(
        "--source-database",
        type=Path,
        default=ROOT / "data" / "local_drama.sqlite3",
    )
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--project-code")
    parser.add_argument("--project-title", default="原稿一键整部生产真实验收")
    parser.add_argument("--target-duration-seconds", type=int, default=30)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--llm-profile-version-id",
        default=DEFAULT_PROFILES["LLM_STORY_PARSE"],
    )
    parser.add_argument(
        "--image-character-profile-version-id",
        default=DEFAULT_PROFILES["IMAGE_CHARACTER"],
    )
    parser.add_argument(
        "--image-scene-profile-version-id",
        default=DEFAULT_PROFILES["IMAGE_SCENE"],
    )
    parser.add_argument(
        "--image-concept-profile-version-id",
        default=DEFAULT_PROFILES["IMAGE_CONCEPT"],
    )
    parser.add_argument(
        "--video-profile-version-id",
        default=DEFAULT_PROFILES["VIDEO_I2V"],
    )
    return parser.parse_args()


def _new_instance_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == ROOT or ROOT not in resolved.parents:
        raise RuntimeError("instance root must be a new directory below the repository")
    if resolved.exists():
        raise RuntimeError(f"instance root already exists: {resolved}")
    resolved.mkdir(parents=True)
    return resolved


def _profile_bindings(args: argparse.Namespace) -> list[dict[str, str]]:
    return [
        {
            "capability": "LLM_STORY_PARSE",
            "profile_version_id": args.llm_profile_version_id,
        },
        {
            "capability": "IMAGE_CHARACTER",
            "profile_version_id": args.image_character_profile_version_id,
        },
        {
            "capability": "IMAGE_SCENE",
            "profile_version_id": args.image_scene_profile_version_id,
        },
        {
            "capability": "IMAGE_CONCEPT",
            "profile_version_id": args.image_concept_profile_version_id,
        },
        {
            "capability": "VIDEO_I2V",
            "profile_version_id": args.video_profile_version_id,
        },
    ]


def _verify_profiles(database: Database, bindings: list[dict[str, str]]) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    with database.connect() as connection:
        for binding in bindings:
            row = connection.execute(
                """SELECT v.id,v.capability,v.status,v.workflow_version_id,
                          p.code,p.title,v.version_no
                   FROM execution_profile_versions v
                   JOIN execution_profiles p ON p.id=v.execution_profile_id
                   WHERE v.id=?""",
                (binding["profile_version_id"],),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"profile version not found: {binding['profile_version_id']}"
                )
            if str(row["status"]) != "PUBLISHED":
                raise RuntimeError(f"profile is not published: {row['id']}")
            if str(row["capability"]) != binding["capability"]:
                raise RuntimeError(
                    f"profile capability mismatch: {row['id']} is {row['capability']}"
                )
            verified.append(dict(row))
    return verified


def _write_generation_preferences(
    database: Database,
    project_id: str,
    bindings: list[dict[str, str]],
) -> list[dict[str, Any]]:
    written: list[dict[str, Any]] = []
    with database.transaction() as connection:
        service = GenerationPreferenceCommandService(
            SqliteGenerationPreferenceRepository(connection)
        )
        for binding in bindings:
            written.append(
                service.put(
                    project_id=project_id,
                    owner_type="PROJECT",
                    owner_id=project_id,
                    capability=binding["capability"],
                    resolution_mode="EXPLICIT",
                    execution_profile_version_id=binding["profile_version_id"],
                    settings={},
                    reason="隔离原稿一键整部生产真实验收：固定已验证 Profile",
                    actor="source-production-uat",
                )
            )
    return written


def _quiesce_clone(database: Database) -> dict[str, list[str]]:
    """Prevent cloned unfinished work from competing with the isolated UAT."""

    now = _now()
    with database.transaction() as connection:
        sessions = [
            str(row[0])
            for row in connection.execute(
                f"SELECT id FROM production_sessions WHERE status IN ({','.join('?' for _ in ACTIVE_SESSION_STATUSES)})",
                ACTIVE_SESSION_STATUSES,
            ).fetchall()
        ]
        jobs = [
            str(row[0])
            for row in connection.execute(
                f"SELECT id FROM jobs WHERE state IN ({','.join('?' for _ in ACTIVE_JOB_STATES)})",
                ACTIVE_JOB_STATES,
            ).fetchall()
        ]
        pipelines = [
            str(row[0])
            for row in connection.execute(
                "SELECT id FROM pipeline_runs WHERE state='RUNNING'"
            ).fetchall()
        ]
        if sessions:
            connection.execute(
                f"""UPDATE production_sessions
                    SET status='CANCELLED',finished_at=COALESCE(finished_at,?),
                        updated_at=?,revision=revision+1
                    WHERE id IN ({','.join('?' for _ in sessions)})""",
                (now, now, *sessions),
            )
        if jobs:
            connection.execute(
                f"""UPDATE jobs SET state='CANCELLED',finished_at=COALESCE(finished_at,?),
                    updated_at=?,cancel_requested_at=COALESCE(cancel_requested_at,?)
                    WHERE id IN ({','.join('?' for _ in jobs)})""",
                (now, now, now, *jobs),
            )
        if pipelines:
            connection.execute(
                f"""UPDATE pipeline_runs SET state='CANCELLED',stage='CANCELLED',
                    stage_label='隔离 UAT 已停止克隆中的旧流水线',updated_at=?,revision=revision+1
                    WHERE id IN ({','.join('?' for _ in pipelines)})""",
                (now, *pipelines),
            )
    return {
        "production_session_ids": sessions,
        "job_ids": jobs,
        "pipeline_run_ids": pipelines,
    }


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    instance_root = _new_instance_root(args.instance_root)
    source_database = args.source_database.expanduser().resolve()
    if not source_database.is_file():
        raise RuntimeError(f"source database not found: {source_database}")
    if not 10 <= args.target_duration_seconds <= 300:
        raise RuntimeError("target duration must be between 10 and 300 seconds")

    data_root = instance_root / "data"
    data_root.mkdir()
    backup_path = data_root / "local_drama.sqlite3"
    integrity = online_backup(source_database, backup_path)
    if str(integrity).lower() != "ok":
        raise RuntimeError(f"online backup integrity check failed: {integrity}")
    if (ROOT / "config" / "config.json").is_file():
        (instance_root / "config").mkdir()
        shutil.copy2(ROOT / "config" / "config.json", instance_root / "config" / "config.json")
    shared_llama_pid = ROOT / "logs" / "llama" / "llama_server.pid"
    copied_llama_pid = False
    if shared_llama_pid.is_file():
        isolated_llama_logs = instance_root / "logs" / "llama"
        isolated_llama_logs.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shared_llama_pid, isolated_llama_logs / shared_llama_pid.name)
        copied_llama_pid = True

    source_text = (
        args.source_file.expanduser().resolve().read_text(encoding="utf-8")
        if args.source_file
        else DEFAULT_SOURCE
    )
    if not source_text.strip():
        raise RuntimeError("source text must not be empty")
    suffix = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    project_code = args.project_code or f"source_uat_{suffix}_{uuid.uuid4().hex[:6]}"
    bindings = _profile_bindings(args)

    saved_env = {
        key: os.environ.get(key)
        for key in (
            "LOCAL_DRAMA_INSTANCE_ROOT",
            "LOCAL_DRAMA_COMFY_INPUT_ROOT",
            "LOCAL_DRAMA_COMFY_OUTPUT_ROOT",
        )
    }
    os.environ["LOCAL_DRAMA_INSTANCE_ROOT"] = str(instance_root)
    os.environ["LOCAL_DRAMA_COMFY_INPUT_ROOT"] = str(
        (ROOT / "work" / "comfy-production" / "input").resolve()
    )
    os.environ["LOCAL_DRAMA_COMFY_OUTPUT_ROOT"] = str(
        (ROOT / "work" / "comfy-production" / "output").resolve()
    )
    try:
        settings = Settings.from_env()
        settings.ensure_roots()
        database = Database(settings.database_path)
        quiesced = _quiesce_clone(database)
        verified_profiles = _verify_profiles(database, bindings)
        project = ProjectService(database, settings.projects_root).create_project(
            code=project_code,
            title=args.project_title,
            episode_count=1,
            aspect_ratio="9:16",
            width=480,
            height=854,
            fps_num=24,
            fps_den=1,
            target_duration_ms=args.target_duration_seconds * 1_000,
            primary_language="zh-CN",
            subtitle_mode="NONE",
            allow_unconfigured_capabilities=True,
            profile_bindings=bindings,
            actor="source-production-uat",
        )
        generation_preferences = _write_generation_preferences(
            database, str(project["id"]), bindings
        )
        run = build_pipeline_orchestrator(database, settings).start_pipeline(
            str(project["id"]),
            raw_text=source_text,
            visual_style="现代悬疑电影感漫剧，写实光影，人物造型稳定，竖屏构图",
            target_episode_duration_seconds=args.target_duration_seconds,
            voice_preset="DEFAULT_VOX_CPM2",
            application_authorization={
                "endpoint": "APPLY_SELECTED_SECTIONS",
                "sections": ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
            },
            production_authorization={
                "endpoint": "WAITING_REVIEW",
                "production_mode": "DRAFT",
                "checkpoint_policy": "ON_EXCEPTION",
                "tts_enabled": False,
                "max_parallel_episodes": 1,
                "min_free_disk_bytes": 1024 * 1024 * 1024,
                "max_duration_seconds": 4 * 60 * 60,
                "max_new_jobs": 300,
                "max_attempts_total": 600,
                "max_output_bytes": 20 * 1024 * 1024 * 1024,
                "max_queued_gpu_jobs": 1,
                "dispatch_shots_per_tick": 1,
            },
            capability_profile_version_id=args.llm_profile_version_id,
            actor="source-production-uat",
        )
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    evidence = {
        "schema_version": "production-session-from-source-uat-preparation.v1",
        "generated_at": _now(),
        "result": "READY",
        "isolated": True,
        "source_database": str(source_database),
        "instance_root": str(instance_root),
        "database": str(backup_path),
        "database_integrity": integrity,
        "project_id": str(project["id"]),
        "project_code": project_code,
        "pipeline_run_id": str(run["run_id"]),
        "pipeline_state": str(run["state"]),
        "source_character_count": len(source_text),
        "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "verified_profiles": verified_profiles,
        "generation_preferences": generation_preferences,
        "quiesced_cloned_work": quiesced,
        "shared_llama_pid_record_copied": copied_llama_pid,
        "authorized_terminal_boundary": "WAITING_REVIEW",
        "generation_started": False,
        "human_approval_written": False,
        "fixture_media_written": False,
    }
    output = args.output or instance_root / "from-source-uat-preparation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    evidence["evidence_path"] = str(output)
    return evidence


if __name__ == "__main__":
    print(json.dumps(prepare(_parse_args()), ensure_ascii=False, indent=2))

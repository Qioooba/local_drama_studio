"""Prepare an isolated, real two-episode WHOLE_DRAMA production UAT.

The command takes an online SQLite backup of the current instance and copies
one existing project tree into a newly created instance root.  All subsequent
mutations happen in that isolated copy through application services.  It
creates a second episode with one shot, binds the project's existing canonical
story asset, and creates a READY production session that can be started by an
isolated API/worker process.

This is deliberately a preparation command.  It never starts generation and
never writes a human approval or fabricated media evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.commands.generation_preferences import (
    GenerationPreferenceCommandService,
)
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.application.projects import ProjectService
from local_drama.application.shot_studio_commands import ShotStudioCommandService
from local_drama.application.story_assets import StoryAssetService
from local_drama.config import Settings
from local_drama.infrastructure.database.backup import online_backup
from local_drama.infrastructure.database.generation_preference_repository import (
    SqliteGenerationPreferenceRepository,
)
from local_drama.infrastructure.database.shot_studio_command_repository import (
    SqliteShotStudioCommandRepository,
)
from local_drama.infrastructure.database.sqlite import Database

ACTIVE_SESSION_STATUSES = {
    "READY",
    "RUNNING",
    "PAUSED",
    "WAITING_USER",
    "WAITING_REVIEW",
    "FAILED",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument(
        "--source-database",
        type=Path,
        default=ROOT / "data" / "local_drama.sqlite3",
    )
    parser.add_argument(
        "--source-projects-root",
        type=Path,
        default=ROOT / "projects",
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--episode-title", default="整部生产真实验收·第二集")
    parser.add_argument("--shot-duration-ms", type=int, default=4458)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _require_new_instance_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == ROOT or ROOT not in resolved.parents:
        raise RuntimeError("instance root must be a new directory below the repository")
    if resolved.exists():
        raise RuntimeError(f"instance root already exists: {resolved}")
    resolved.mkdir(parents=True)
    return resolved


def _project_facts(database: Database, project_id: str) -> dict[str, Any]:
    with database.connect() as connection:
        project = connection.execute(
            "SELECT id,code,title,root_rel FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project not found: {project_id}")
        seasons = connection.execute(
            "SELECT id,code,title FROM seasons WHERE project_id=? ORDER BY display_order,id",
            (project_id,),
        ).fetchall()
        assets = connection.execute(
            """SELECT id,code,name,canonical_media_version_id FROM story_assets
               WHERE project_id=? AND status='ACTIVE'
                 AND canonical_media_version_id IS NOT NULL
               ORDER BY created_at,id""",
            (project_id,),
        ).fetchall()
        source_shot = connection.execute(
            """SELECT sh.id,sh.current_revision_id,sr.fields_json
               FROM shots sh
               JOIN episodes e ON e.id=sh.episode_id
               JOIN seasons s ON s.id=e.season_id
               JOIN shot_revisions sr ON sr.id=sh.current_revision_id
               WHERE s.project_id=? AND sh.archived_at IS NULL
               ORDER BY s.display_order,e.display_order,CAST(sh.order_key AS REAL),sh.id
               LIMIT 1""",
            (project_id,),
        ).fetchone()
        source_preference = None
        if source_shot is not None:
            source_preference = SqliteGenerationPreferenceRepository(connection).current_preference(
                project_id,
                "SHOT",
                str(source_shot["id"]),
                "VIDEO_I2V",
            )
    if not seasons:
        raise RuntimeError("source project has no season")
    if not assets:
        raise RuntimeError("source project has no active canonical story asset")
    if source_shot is None:
        raise RuntimeError("source project has no production-ready source shot")
    if source_preference is None or not source_preference.get("execution_profile_version_id"):
        raise RuntimeError("source project has no explicit shot VIDEO_I2V preference")
    return {
        "project": dict(project),
        "season": dict(seasons[0]),
        "asset": dict(assets[0]),
        "source_shot": {
            **dict(source_shot),
            "fields": json.loads(str(source_shot["fields_json"] or "{}")),
        },
        "source_video_preference": source_preference,
    }


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    instance_root = _require_new_instance_root(args.instance_root)
    source_database = args.source_database.expanduser().resolve()
    source_projects_root = args.source_projects_root.expanduser().resolve()
    if not source_database.is_file():
        raise RuntimeError(f"source database not found: {source_database}")

    # Read source facts before switching Settings to the isolated instance.
    source_db = Database(source_database)
    source = _project_facts(source_db, args.project_id)
    root_rel = str(source["project"]["root_rel"])
    source_project_root = source_projects_root / root_rel
    if not source_project_root.is_dir():
        raise RuntimeError(f"source project root not found: {source_project_root}")

    data_root = instance_root / "data"
    projects_root = instance_root / "projects"
    data_root.mkdir()
    projects_root.mkdir()
    backup_path = data_root / "local_drama.sqlite3"
    integrity = online_backup(source_database, backup_path)
    if str(integrity).lower() != "ok":
        raise RuntimeError(f"online backup integrity check failed: {integrity}")
    shutil.copytree(source_project_root, projects_root / root_rel)

    previous_instance = os.environ.get("LOCAL_DRAMA_INSTANCE_ROOT")
    os.environ["LOCAL_DRAMA_INSTANCE_ROOT"] = str(instance_root)
    try:
        settings = Settings.from_env()
        settings.ensure_roots()
        database = Database(settings.database_path)
        projects = ProjectService(database, settings.projects_root)
        sessions = ProductionSessionService(database)
        story_assets = StoryAssetService(database, settings)

        cancelled_sessions: list[str] = []
        existing_sessions = sessions.list_sessions(args.project_id, cursor=0, limit=100)["items"]
        for existing in existing_sessions:
            if str(existing["status"]) not in ACTIVE_SESSION_STATUSES:
                continue
            sessions.control(
                str(existing["id"]),
                "CANCEL",
                {"expected_revision": int(existing["revision"]), "actor": "whole-drama-uat"},
                idempotency_key=f"whole-drama-uat-cancel-{existing['id']}",
            )
            cancelled_sessions.append(str(existing["id"]))

        appended = projects.append_episode(
            args.project_id,
            season_id=str(source["season"]["id"]),
            create_new_season=False,
            season_title=None,
            episode_title=args.episode_title,
            target_duration_ms=args.shot_duration_ms,
            actor="whole-drama-uat",
        )
        episode = appended["episode"]
        shot = projects.create_shot(
            str(episode["id"]),
            "SHOT_001",
            args.shot_duration_ms,
            "MEDIUM_CLOSE_UP",
        )
        shot_write = ShotStudioCommandService(
            SqliteShotStudioCommandRepository(database)
        ).mark_ready(
            str(shot["id"]),
            draft=dict(source["source_shot"]["fields"]),
            actor="whole-drama-uat",
        )
        source_video_preference = source["source_video_preference"]
        with database.transaction() as connection:
            video_preference = GenerationPreferenceCommandService(
                SqliteGenerationPreferenceRepository(connection)
            ).put(
                project_id=args.project_id,
                owner_type="SHOT",
                owner_id=str(shot["id"]),
                capability="VIDEO_I2V",
                resolution_mode="EXPLICIT",
                execution_profile_version_id=str(
                    source_video_preference["execution_profile_version_id"]
                ),
                settings=dict(source_video_preference.get("settings") or {}),
                reason="隔离整部生产真实验收：继承已验证镜头级 I2V Profile",
                actor="whole-drama-uat",
            )
        binding = story_assets.bind_asset_to_shot(
            str(shot["id"]),
            str(source["asset"]["id"]),
            role_in_shot="main",
            actor="whole-drama-uat",
        )

        request = {
            "scope_type": "WHOLE_DRAMA",
            "episode_ids": [],
            "production_mode": "DRAFT",
            "checkpoint_policy": "ON_EXCEPTION",
            "tts_enabled": False,
            "max_parallel_episodes": 1,
            "min_free_disk_bytes": 1024 * 1024 * 1024,
            "max_duration_seconds": 4 * 60 * 60,
            "max_new_jobs": 300,
            "max_attempts_total": 600,
            "max_output_bytes": 20 * 1024 * 1024 * 1024,
            "max_queued_gpu_jobs": 2,
            "dispatch_shots_per_tick": 1,
        }
        plan = sessions.plan(args.project_id, request)
        if int(plan["episode_count"]) != 2 or int(plan["total_shot_count"]) != 2:
            raise RuntimeError(
                "isolated whole-drama sample must contain exactly two episodes and two shots"
            )
        if any(int(item["missing_asset_binding_count"]) for item in plan["episodes"]):
            raise RuntimeError("each UAT shot must have an active story asset binding")
        created = sessions.create(
            args.project_id,
            {
                **request,
                "expected_plan_hash": plan["plan_hash"],
                "actor": "whole-drama-uat",
            },
            idempotency_key=f"whole-drama-uat-create-{plan['plan_hash']}",
        )
    finally:
        if previous_instance is None:
            os.environ.pop("LOCAL_DRAMA_INSTANCE_ROOT", None)
        else:
            os.environ["LOCAL_DRAMA_INSTANCE_ROOT"] = previous_instance

    evidence = {
        "schema_version": "production-session-whole-drama-uat-preparation.v1",
        "generated_at": _utc_now(),
        "result": "READY",
        "isolated": True,
        "source_database": str(source_database),
        "source_project_root": str(source_project_root),
        "instance_root": str(instance_root),
        "database": str(backup_path),
        "database_integrity": integrity,
        "project_id": args.project_id,
        "project_code": source["project"]["code"],
        "episode_ids": [item["episode_id"] for item in plan["episodes"]],
        "shot_ids": [
            str(shot["id"]),
        ],
        "new_episode_id": str(episode["id"]),
        "new_shot_id": str(shot["id"]),
        "new_shot_status": str(shot_write["shot"]["status"]),
        "video_preference": video_preference,
        "story_asset_id": str(source["asset"]["id"]),
        "story_asset_binding_id": str(binding["binding_id"]),
        "cancelled_cloned_session_ids": cancelled_sessions,
        "plan": plan,
        "session": created["session"],
        "generation_started": False,
        "human_approval_written": False,
        "fixture_media_written": False,
    }
    output = args.output or instance_root / "whole-drama-uat-preparation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    evidence["evidence_path"] = str(output)
    return evidence


if __name__ == "__main__":
    print(json.dumps(prepare(_parse_args()), ensure_ascii=False, indent=2))

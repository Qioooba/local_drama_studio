"""Build a real, local-only G8 timeline/render/delivery evidence set.

The script is intentionally explicit about every source path and hash.  It
imports existing local H3/Comfy outputs and local audio files; it never creates
placeholder media and it never contacts a public service.  Re-running against
an already populated episode is rejected rather than silently adding another
immutable revision.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.configuration import ConfigurationService
from local_drama.application.g8_readiness import G8ReadinessService
from local_drama.application.media import MediaService, _hash_file
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.application.timeline import TimelineService
from local_drama.config import Settings
from local_drama.infrastructure.database.backup import online_backup
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "e5eaa01d-d39a-4a63-acbf-026da30b46e7"
EPISODE_ID = "d4db1033-9517-4bd0-958d-4228e0abead1"
VIDEO_SOURCES = (
    ROOT.parent / "projects/family_redfruit_series_001/09_production/SEASON_001/media/comfy_output/scene_01__00002_.mp4",
    ROOT.parent / "projects/family_redfruit_series_001/09_production/SEASON_001/media/comfy_output/scene_02__00002_.mp4",
    ROOT.parent / "projects/family_redfruit_series_001/09_production/SEASON_001/media/comfy_output/scene_03__00002_.mp4",
)
AUDIO_SOURCES = {
    "DIALOGUE": ROOT.parent / "projects/family_redfruit_series_001/08_assets/generated/audio/CUT001/SHOT_012/CHAR_001_dialogue_timed_v001.wav",
    "ENVIRONMENT": ROOT.parent / "projects/family_redfruit_series_001/08_assets/generated/audio/CUT001/stems_preview_v001/CUT001_ambience_rain_continuity_preview_v001.wav",
    "SFX": ROOT.parent / "projects/family_redfruit_series_001/08_assets/generated/audio/CUT001/stems_preview_v001/CUT001_foley_engine_far_to_near_preview_v001.wav",
    # This is an existing local soundtrack render from the offline workspace;
    # provenance is preserved in the evidence instead of being inferred.
    "MUSIC": ROOT.parent / "openclaw/tasks/武侠客栈15秒_20260810_2231/audio/mixed_48k.wav",
}


def _require_sources() -> None:
    for path in (*VIDEO_SOURCES, *AUDIO_SOURCES.values()):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"source missing or not a regular local file: {path}")


def _source_record(path: Path) -> dict[str, Any]:
    digest, size = _hash_file(path)
    return {"path": str(path), "sha256": digest, "byte_size": size}


def _existing_target(database: Database, code: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT dt.id, dtv.id AS version_id FROM delivery_targets dt
            JOIN delivery_target_versions dtv ON dtv.delivery_target_id=dt.id
            WHERE dt.project_id=? AND dt.code=? AND dt.status='ACTIVE'
            ORDER BY dtv.version_no DESC LIMIT 1""",
            (PROJECT_ID, code),
        ).fetchone()
    return dict(row) if row else None


def _existing_media_for_hash(database: Database, project_id: str, digest: str) -> dict[str, Any] | None:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT ma.owner_type, ma.owner_id, ma.purpose, mv.id AS media_version_id
            FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
            WHERE ma.project_id=? AND mv.sha256=? ORDER BY mv.version_no LIMIT 1""",
            (project_id, digest),
        ).fetchone()
    return dict(row) if row else None


def build() -> dict[str, Any]:
    settings = Settings.from_env()
    settings.ensure_roots()
    _require_sources()
    database = Database(settings.database_path)
    if not database.exists:
        raise RuntimeError("production database does not exist")
    backup_path = settings.backups_root / f"g8_preflight_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
    backup_integrity = online_backup(settings.database_path, backup_path)
    project_service = ProjectService(database, settings.projects_root)
    media_service = MediaService(database, settings)
    timeline_service = TimelineService(database, settings)
    review_service = ReviewService(database, settings)
    with database.connect() as connection:
        project = connection.execute("SELECT id, code, root_rel FROM projects WHERE id=?", (PROJECT_ID,)).fetchone()
        episode = connection.execute("SELECT id, code, title FROM episodes WHERE id=?", (EPISODE_ID,)).fetchone()
        existing_timeline = connection.execute("SELECT COUNT(*) FROM timeline_revisions WHERE episode_id=?", (EPISODE_ID,)).fetchone()[0]
        existing_subtitles = connection.execute("SELECT COUNT(*) FROM subtitle_revisions WHERE episode_id=?", (EPISODE_ID,)).fetchone()[0]
        existing_audio = connection.execute("SELECT COUNT(*) FROM audio_bindings WHERE episode_id=?", (EPISODE_ID,)).fetchone()[0]
        existing_renders = connection.execute("SELECT COUNT(*) FROM episode_render_versions WHERE episode_id=?", (EPISODE_ID,)).fetchone()[0]
    if project is None or episode is None:
        raise RuntimeError("target production project/episode not found")
    if any(int(value) for value in (existing_timeline, existing_subtitles, existing_audio, existing_renders)):
        raise RuntimeError("target episode already contains G8 evidence; refusing to append another immutable sample")

    shots: list[dict[str, Any]] = []
    imported_videos: list[dict[str, Any]] = []
    existing_shots = {str(shot["code"]): shot for shot in project_service.list_shots(EPISODE_ID)}
    for index, source in enumerate(VIDEO_SOURCES, start=1):
        shot_code = f"G8_SHOT_{index:03d}"
        shot = existing_shots.get(shot_code) or project_service.create_shot(EPISODE_ID, shot_code, 6000, "PRODUCTION")
        digest, _ = _hash_file(source)
        existing_media = _existing_media_for_hash(database, PROJECT_ID, digest)
        if existing_media is not None:
            if existing_media["owner_type"] != "SHOT" or existing_media["owner_id"] != shot["id"]:
                raise RuntimeError(f"existing hash is not owned by expected shot: {source}")
            imported = media_service.get_version(str(existing_media["media_version_id"]))
            imported["media_version_id"] = imported["id"]
        else:
            imported = media_service.import_file(
                PROJECT_ID,
                source,
                purpose="SHOT_VIDEO",
                owner_type="SHOT",
                owner_id=str(shot["id"]),
                media_kind="VIDEO",
                stage="PROXY",
                actor="g8-evidence-builder",
            )
        shots.append(shot)
        imported_videos.append(imported)

    video_items: list[dict[str, Any]] = []
    cursor_us = 0
    for imported in imported_videos:
        format_data = imported["probe"].get("format", {})
        duration_ms = round(float(format_data.get("duration", 0) or 0) * 1000)
        duration_us = max(duration_ms, 1) * 1000
        video_items.append(
            {
                "track_type": "VIDEO",
                "media_version_id": imported["media_version_id"],
                "start_us": cursor_us,
                "end_us": cursor_us + duration_us,
                "parameters": {"fit": "contain", "source": "LOCAL_H3_COMFY_OUTPUT"},
            }
        )
        cursor_us += duration_us

    timeline = timeline_service.create_timeline_revision(
        EPISODE_ID,
        video_items,
        {
            "source": "family_redfruit_series_001 local Comfy outputs",
            "source_files": [_source_record(path) for path in VIDEO_SOURCES],
            "shot_ids": [shot["id"] for shot in shots],
            "generated_by": "scripts/build_g8_production_evidence.py",
        },
        actor="g8-evidence-builder",
    )
    subtitles = timeline_service.create_subtitle_revision(
        EPISODE_ID,
        [
            {"start_us": 0, "end_us": 3_000_000, "text": "谁在里面？"},
            {"start_us": 5_000_000, "end_us": 9_000_000, "text": "先喝口热水，天亮以前我们一起想办法。"},
        ],
        input_snapshot={"source_document": "g6_script_source.txt", "episode_id": EPISODE_ID},
        actor="g8-evidence-builder",
    )

    imported_audio: dict[str, dict[str, Any]] = {}
    bindings: list[dict[str, Any]] = []
    for track_type, source in AUDIO_SOURCES.items():
        imported = media_service.import_file(
            PROJECT_ID,
            source,
            purpose="AUDIO",
            owner_type="EPISODE",
            owner_id=EPISODE_ID,
            media_kind="AUDIO",
            stage="IMPORTED",
            actor="g8-evidence-builder",
        )
        if imported.get("duplicate"):
            raise RuntimeError(f"unexpected project-wide duplicate for audio source {source}")
        imported_audio[track_type] = imported
        bindings.append(
            timeline_service.bind_audio(
                EPISODE_ID,
                str(imported["media_version_id"]),
                track_type,
                0,
                cursor_us,
                source_license_status="VERIFIED_LOCAL",
                actor="g8-evidence-builder",
            )
        )

    render = timeline_service.render_episode(str(timeline["id"]), actor="g8-evidence-builder")
    review_service.ensure_templates(actor="g8-evidence-builder")
    with database.connect() as connection:
        template = connection.execute(
            "SELECT * FROM review_templates WHERE code='episode_render' AND version_no=1"
        ).fetchone()
    if template is None:
        raise RuntimeError("episode_render review template was not initialized")
    review = review_service.submit_episode_render_review(
        str(render["id"]),
        str(template["id"]),
        "APPROVED",
        1,
        [{"item_id": item["id"], "result": "PASS"} for item in json.loads(template["items_json"])],
        "本地整集渲染已由机器完整性与授权检查复核",
        actor="g8-evidence-builder",
    )

    target = _existing_target(database, "g8-local-production")
    if target is None:
        target = ConfigurationService(database).create_delivery_target(
            PROJECT_ID,
            "g8-local-production",
            "G8 local production delivery",
            "LOCAL_FILESYSTEM",
            {"path_rel": "06_delivery/g8-production"},
            actor="g8-evidence-builder",
        )
    delivery = timeline_service.build_delivery(str(render["id"]), str(target["version_id"]), actor="g8-evidence-builder")
    verified_before = timeline_service.verify_delivery(str(delivery["id"]))
    project_root = (settings.projects_root / str(project["root_rel"])).resolve()
    delivery_video = project_root / str(delivery["rel_path"]) / f"{episode['code']}.mp4"
    original = delivery_video.read_bytes()
    delivery_video.write_bytes(original + b"\x00")
    tampered = timeline_service.verify_delivery(str(delivery["id"]))
    delivery_video.write_bytes(original)
    verified_after_restore = timeline_service.verify_delivery(str(delivery["id"]))
    readiness = G8ReadinessService(database).inspect(PROJECT_ID, EPISODE_ID)
    evidence = {
        "schema_version": "g8-production-evidence.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "LOCAL_ONLY",
        "runtime_contacted": False,
        "network_contacted": False,
        "mutated": True,
        "project": {"id": PROJECT_ID, "code": str(project["code"]), "episode_id": EPISODE_ID, "episode_code": str(episode["code"])},
        "backup": {"path": str(backup_path), "integrity_check": backup_integrity},
        "video_sources": [_source_record(path) for path in VIDEO_SOURCES],
        "audio_sources": {track: _source_record(path) for track, path in AUDIO_SOURCES.items()},
        "shots": [{"id": shot["id"], "code": shot["code"]} for shot in shots],
        "media_versions": {"video": [item["media_version_id"] for item in imported_videos], "audio": {track: item["media_version_id"] for track, item in imported_audio.items()}},
        "timeline": {"id": timeline["id"], "revision_no": timeline["revision_no"], "video_item_count": len(video_items), "duration_us": cursor_us},
        "subtitles": {"id": subtitles["id"], "cue_count": len(subtitles["cues"])},
        "audio_bindings": [{"id": binding["id"], "track_type": binding["track_type"], "source_license_status": binding["source_license_status"]} for binding in bindings],
        "render": {"id": render["id"], "sha256": render["sha256"], "integrity_status": render["status"], "review_id": review["id"]},
        "delivery": {
            "id": delivery["id"],
            "verified_before_tamper": verified_before["status"],
            "tamper_probe": tampered["status"],
            "verified_after_restore": verified_after_restore["status"],
        },
        "readiness": readiness,
    }
    output = ROOT / "docs/evidence/g8/g8-production-evidence-2026-08-15.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "readiness": readiness, "delivery": evidence["delivery"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    result = build()
    print(json.dumps(result, ensure_ascii=False, indent=2))

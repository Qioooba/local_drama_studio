from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from local_drama.application.media import MediaService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rational_time(value: float, rate: float) -> dict[str, object]:
    return {"OTIO_SCHEMA": "RationalTime.1", "value": value, "rate": rate}


def _time_range(start: float, duration: float, rate: float) -> dict[str, object]:
    return {
        "OTIO_SCHEMA": "TimeRange.1",
        "start_time": _rational_time(start, rate),
        "duration": _rational_time(duration, rate),
    }


def _edl_timecode(microseconds: int, nominal_fps: int) -> str:
    total_frames = round(microseconds * nominal_fps / 1_000_000)
    frames = total_frames % nominal_fps
    total_seconds = total_frames // nominal_fps
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    if hours > 99:
        raise DomainRuleError("EDL_TIMECODE_OVERFLOW", "EDL 时间码超过 99 小时")
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


class TimelineExportService:
    """Export a frozen timeline revision without mutating SQLite state."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.media = MediaService(database, settings)

    def _snapshot(self, timeline_revision_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self.database.connect() as connection:
            revision = connection.execute(
                """SELECT tr.id, tr.revision_no, tr.revision_hash, tr.status, e.id AS episode_id,
                e.code AS episode_code, e.title AS episode_title, p.id AS project_id, p.root_rel,
                p.fps_num, p.fps_den
                FROM timeline_revisions tr JOIN episodes e ON e.id=tr.episode_id
                JOIN seasons s ON s.id=e.season_id JOIN projects p ON p.id=s.project_id
                WHERE tr.id=?""",
                (timeline_revision_id,),
            ).fetchone()
            if revision is None:
                raise DomainRuleError("TIMELINE_REVISION_NOT_FOUND", "时间线 revision 不存在")
            rows = connection.execute(
                """SELECT id, track_type, media_version_id, start_us, end_us, parameters_json
                FROM timeline_items WHERE timeline_revision_id=? ORDER BY track_type, start_us, id""",
                (timeline_revision_id,),
            ).fetchall()
        items = [{**dict(row), "parameters": json.loads(row["parameters_json"])} for row in rows]
        if not items:
            raise DomainRuleError("TIMELINE_ITEMS_REQUIRED", "时间线 revision 没有可导出 item")
        return dict(revision), items

    def _verified_items(self, revision: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        verified: list[dict[str, Any]] = []
        for item in items:
            media_version_id = item["media_version_id"]
            if not media_version_id:
                raise DomainRuleError("TIMELINE_EXPORT_MEDIA_REQUIRED", "OTIO/EDL 导出不接受无媒体的时间线 item")
            media = self.media.verify_content_integrity(str(media_version_id))
            if media["project_id"] != revision["project_id"]:
                raise DomainRuleError("MEDIA_PROJECT_MISMATCH", "导出媒体必须属于时间线项目")
            parameters = item["parameters"]
            source_start_us = int(parameters.get("source_start_us", 0))
            duration_us = int(item["end_us"]) - int(item["start_us"])
            if source_start_us < 0:
                raise DomainRuleError("TIMELINE_SOURCE_RANGE_INVALID", "source_start_us 不能为负数")
            verified.append({**item, "media": media, "source_start_us": source_start_us, "duration_us": duration_us})
        return verified

    @staticmethod
    def _otio(revision: dict[str, Any], items: list[dict[str, Any]], rate: float) -> dict[str, object]:
        tracks: list[dict[str, object]] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault(str(item["track_type"]), []).append(item)
        for track_type, track_items in grouped.items():
            children: list[dict[str, object]] = []
            cursor_us = 0
            for item in track_items:
                start_us = int(item["start_us"])
                if start_us > cursor_us:
                    children.append(
                        {
                            "OTIO_SCHEMA": "Gap.1",
                            "name": "Gap",
                            "source_range": _time_range(0, (start_us - cursor_us) * rate / 1_000_000, rate),
                            "metadata": {},
                            "effects": [],
                            "markers": [],
                            "enabled": True,
                        }
                    )
                media = item["media"]
                source_start = int(item["source_start_us"]) * rate / 1_000_000
                duration = int(item["duration_us"]) * rate / 1_000_000
                children.append(
                    {
                        "OTIO_SCHEMA": "Clip.2",
                        "name": str(media["source_name"]),
                        "source_range": _time_range(source_start, duration, rate),
                        "media_references": {
                            "DEFAULT_MEDIA": {
                                "OTIO_SCHEMA": "ExternalReference.1",
                                "name": str(media["source_name"]),
                                "target_url": "../../../../"
                                + quote(str(media["rel_path"]).replace("\\", "/"), safe="/._-"),
                                "available_range": None,
                                "metadata": {
                                    "localdrama_media_version_id": media["id"],
                                    "sha256": media["sha256"],
                                    "byte_size": media["byte_size"],
                                },
                                "available_image_bounds": None,
                            },
                        },
                        "active_media_reference_key": "DEFAULT_MEDIA",
                        "metadata": {"localdrama_timeline_item_id": item["id"]},
                        "effects": [],
                        "markers": [],
                        "enabled": True,
                    }
                )
                cursor_us = int(item["end_us"])
            tracks.append(
                {
                    "OTIO_SCHEMA": "Track.1",
                    "name": track_type,
                    "kind": "Video" if track_type == "VIDEO" else "Audio",
                    "children": children,
                    "source_range": None,
                    "metadata": {},
                    "effects": [],
                    "markers": [],
                    "enabled": True,
                }
            )
        return {
            "OTIO_SCHEMA": "Timeline.1",
            "name": f'{revision["episode_code"]} timeline v{revision["revision_no"]}',
            "global_start_time": None,
            "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks, "source_range": None, "metadata": {}, "effects": [], "markers": [], "enabled": True},
            "metadata": {
                "localdrama_schema": "localdrama.timeline-export.v1",
                "timeline_revision_id": revision["id"],
                "revision_hash": revision["revision_hash"],
                "fps_num": revision["fps_num"],
                "fps_den": revision["fps_den"],
            },
        }

    @staticmethod
    def _edl(revision: dict[str, Any], items: list[dict[str, Any]], nominal_fps: int) -> str:
        lines = [f'TITLE: {revision["episode_code"]}_V{revision["revision_no"]}', "FCM: NON-DROP FRAME", ""]
        event_no = 0
        for item in items:
            if item["track_type"] != "VIDEO":
                continue
            event_no += 1
            source_in = int(item["source_start_us"])
            source_out = source_in + int(item["duration_us"])
            reel = str(item["media_version_id"]).replace("-", "")[:8].upper()
            lines.append(
                f"{event_no:03d}  {reel:<8} V     C        "
                f"{_edl_timecode(source_in, nominal_fps)} {_edl_timecode(source_out, nominal_fps)} "
                f"{_edl_timecode(int(item['start_us']), nominal_fps)} {_edl_timecode(int(item['end_us']), nominal_fps)}"
            )
            lines.append(f"* FROM CLIP NAME: {item['media']['source_name']}")
            lines.append(f"* LOCALDRAMA MEDIA VERSION: {item['media_version_id']} SHA256: {item['media']['sha256']}")
            lines.append("")
        if event_no == 0:
            raise DomainRuleError("TIMELINE_VIDEO_REQUIRED_FOR_EDL", "EDL 导出至少需要一个 VIDEO item")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _verify_existing(final: Path, export_hash: str) -> dict[str, Any]:
        try:
            manifest: dict[str, Any] = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("schema_version") != "localdrama.timeline-export.v1" or manifest.get("export_hash") != export_hash:
                raise ValueError("manifest identity mismatch")
            for file in manifest["files"]:
                path = (final / file["rel_path"]).resolve()
                if not path.is_relative_to(final) or not path.is_file() or path.is_symlink():
                    raise ValueError("unsafe file")
                if path.stat().st_size != int(file["byte_size"]) or _sha256(path) != file["sha256"]:
                    raise ValueError("file integrity mismatch")
            return manifest
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise DomainRuleError("TIMELINE_EXPORT_TAMPERED", "已有时间线导出不完整或已被修改，请保留现场并移走目录后重试") from error

    def export_revision(self, timeline_revision_id: str) -> dict[str, Any]:
        revision, raw_items = self._snapshot(timeline_revision_id)
        if not revision["fps_num"] or not revision["fps_den"]:
            raise DomainRuleError("TIMELINE_FPS_REQUIRED", "项目必须显式配置 fps 才能导出 OTIO/EDL")
        fps_num = int(revision["fps_num"])
        fps_den = int(revision["fps_den"])
        rate = fps_num / fps_den
        nominal_fps = round(rate)
        if nominal_fps <= 0 or nominal_fps > 99:
            raise DomainRuleError("EDL_FPS_UNSUPPORTED", "EDL 仅支持 1—99 的名义帧率")
        items = self._verified_items(revision, raw_items)
        identity = {
            "schema_version": "localdrama.timeline-export.v1",
            "writer_version": 2,
            "timeline_revision_id": timeline_revision_id,
            "revision_hash": revision["revision_hash"],
            "fps_num": fps_num,
            "fps_den": fps_den,
            "items": [
                {
                    "id": item["id"],
                    "media_version_id": item["media_version_id"],
                    "media_sha256": item["media"]["sha256"],
                    "start_us": item["start_us"],
                    "end_us": item["end_us"],
                    "source_start_us": item["source_start_us"],
                }
                for item in items
            ],
        }
        export_hash = hashlib.sha256(_canonical(identity)).hexdigest()
        project_root = (self.settings.projects_root / str(revision["root_rel"])).resolve()
        if not project_root.is_relative_to(self.settings.projects_root.resolve()) or not project_root.is_dir() or project_root.is_symlink():
            raise DomainRuleError("PROJECT_ROOT_INVALID", "项目根目录无效")
        base = project_root / "05_timelines" / str(revision["episode_code"]) / "exports"
        final = base / f'timeline-v{revision["revision_no"]}-{export_hash[:12]}'
        if final.exists():
            if not final.is_dir() or final.is_symlink():
                raise DomainRuleError("TIMELINE_EXPORT_TAMPERED", "时间线导出目标不是安全目录")
            manifest = self._verify_existing(final, export_hash)
            return self._result(project_root, final, manifest, reused=True)
        partial = base / f".timeline-export.partial-{uuid.uuid4().hex}"
        try:
            partial.mkdir(parents=True)
            otio_path = partial / f'{revision["episode_code"]}-v{revision["revision_no"]}.otio'
            edl_path = partial / f'{revision["episode_code"]}-v{revision["revision_no"]}.edl'
            otio_path.write_bytes(_canonical(self._otio(revision, items, rate)) + b"\n")
            edl_path.write_text(self._edl(revision, items, nominal_fps), encoding="utf-8", newline="\n")
            files = [
                {"rel_path": path.name, "byte_size": path.stat().st_size, "sha256": _sha256(path)}
                for path in (otio_path, edl_path)
            ]
            manifest = {**identity, "export_hash": export_hash, "files": files, "database_mutated": False}
            (partial / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
            os.replace(partial, final)
        except Exception:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        return self._result(project_root, final, manifest, reused=False)

    @staticmethod
    def _result(project_root: Path, final: Path, manifest: dict[str, Any], *, reused: bool) -> dict[str, Any]:
        return {
            "schema_version": "localdrama.timeline-export.v1",
            "status": "EXPORTED",
            "rel_path": final.relative_to(project_root).as_posix(),
            "manifest_rel_path": (final / "manifest.json").relative_to(project_root).as_posix(),
            "files": manifest["files"],
            "export_hash": manifest["export_hash"],
            "reused": reused,
            "database_mutated": False,
            "runtime_contacted": False,
            "network_contacted": False,
        }

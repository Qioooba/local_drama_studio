from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from local_drama.application.export_archives import materialize_verified_export_archive
from local_drama.application.local_artifacts import local_artifact_reference
from local_drama.application.media import MediaService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.atomic import replace_path
from local_drama.infrastructure.filesystem.path_policy import controlled_path


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


def _video_probe_size(media: dict[str, Any]) -> tuple[int | None, int | None]:
    """Best-effort pixel dimensions from the immutable ffprobe snapshot."""
    probe = media.get("probe") or {}
    video_stream: dict[str, Any] = next(
        (stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"), {}
    )
    try:
        return int(video_stream["width"]), int(video_stream["height"])
    except (KeyError, TypeError, ValueError):
        return None, None


class TimelineExportService:
    """Export a frozen timeline revision without mutating SQLite state.

    Supported export formats:

    * ``standard`` (default; aliases ``otio`` / ``edl``): the historical
      OTIO + CMX 3600 EDL interchange package.  OTIO and EDL are always
      written as a pair; the alias only selects the package flavour and is
      kept for callers that ask for a specific interchange file.
    * ``jianying``: a best-effort CapCut/Jianying ``draft_content.json``
      package (community-reversed schema, see :meth:`_jianying`) with the
      referenced media copied into the draft folder so the package is
      self-contained and can be opened by the Jianying desktop app.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.media = MediaService(database, settings)

    def _snapshot(self, timeline_revision_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self.database.connect() as connection:
            revision = connection.execute(
                """SELECT tr.id, tr.revision_no, tr.revision_hash, tr.status, e.id AS episode_id,
                e.code AS episode_code, e.title AS episode_title, p.id AS project_id, p.root_rel,
                p.fps_num, p.fps_den, p.width, p.height, p.aspect_ratio
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
                raise DomainRuleError("TIMELINE_EXPORT_MEDIA_REQUIRED", "时间线导出不接受无媒体的时间线 item")
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
                path = controlled_path(
                    final,
                    str(file["rel_path"]),
                    must_exist=True,
                    require_file=True,
                    code="TIMELINE_EXPORT_TAMPERED",
                )
                if path.stat().st_size != int(file["byte_size"]) or _sha256(path) != file["sha256"]:
                    raise ValueError("file integrity mismatch")
            return manifest
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, DomainRuleError) as error:
            raise DomainRuleError("TIMELINE_EXPORT_TAMPERED", "已有时间线导出不完整或已被修改，请保留现场并移走目录后重试") from error

    def export_revision(
        self,
        timeline_revision_id: str,
        format: str = "standard",
        subtitle_revision_id: str | None = None,
    ) -> dict[str, Any]:
        """Export a frozen timeline revision in the requested format.

        ``format`` accepts ``standard`` (default), ``otio`` and ``edl`` as
        aliases of the historical OTIO+EDL package, plus ``jianying`` for the
        CapCut/Jianying draft.  Unknown formats are rejected.
        """
        normalized_format = (format or "standard").lower()
        if normalized_format in {"standard", "otio", "edl"}:
            return self._export_standard(timeline_revision_id)
        if normalized_format == "jianying":
            return self._export_jianying(timeline_revision_id, subtitle_revision_id=subtitle_revision_id)
        raise DomainRuleError(
            "TIMELINE_EXPORT_FORMAT_UNSUPPORTED",
            f"不支持的导出格式：{format}；支持 standard/otio/edl/jianying",
        )

    def download_archive(self, timeline_revision_id: str, rel_path: str) -> Path:
        revision, _items = self._snapshot(timeline_revision_id)
        project_root = controlled_path(
            self.settings.projects_root,
            str(revision["root_rel"]),
            must_exist=True,
            code="PROJECT_ROOT_INVALID",
        )
        allowed = (project_root / "05_timelines" / str(revision["episode_code"]) / "exports").resolve()
        candidate = controlled_path(
            project_root,
            rel_path,
            must_exist=True,
            code="TIMELINE_EXPORT_DOWNLOAD_NOT_ALLOWED",
        )
        if candidate.parent != allowed or not candidate.name.startswith(("timeline-v", "jianying-v")):
            raise DomainRuleError("TIMELINE_EXPORT_DOWNLOAD_NOT_ALLOWED", "只能下载当前时间线已注册的导出")
        try:
            export_hash = str(json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))["export_hash"])
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise DomainRuleError("TIMELINE_EXPORT_TAMPERED", "时间线导出 manifest 无效") from error
        self._verify_existing(candidate, export_hash)
        return materialize_verified_export_archive(candidate)

    def _export_standard(self, timeline_revision_id: str) -> dict[str, Any]:
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
        project_root = self.settings.resolve_project_root(str(revision["root_rel"]))
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
            replace_path(partial, final)
        except Exception:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        return self._result(project_root, final, manifest, reused=False)

    def _subtitle_revision(self, revision: dict[str, Any], subtitle_revision_id: str) -> dict[str, Any]:
        """Read a frozen subtitle revision (read-only) for the jianying text track."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, episode_id, revision_no, content_hash FROM subtitle_revisions WHERE id=?", (subtitle_revision_id,)
            ).fetchone()
            if row is None:
                raise DomainRuleError("SUBTITLE_REVISION_NOT_FOUND", "字幕 revision 不存在")
            if str(row["episode_id"]) != str(revision["episode_id"]):
                raise DomainRuleError("SUBTITLE_EPISODE_MISMATCH", "剪映导出要求字幕 revision 与时间线属于同一集")
            cue_rows = connection.execute(
                "SELECT cue_no, start_us, end_us, text, style_json FROM subtitle_cues WHERE subtitle_revision_id=? ORDER BY cue_no",
                (subtitle_revision_id,),
            ).fetchall()
        cues = [{**dict(cue), "style": json.loads(cue["style_json"])} for cue in cue_rows]
        if not cues:
            raise DomainRuleError("SUBTITLE_CUES_REQUIRED", "字幕 revision 没有可导出的 cue")
        return {"id": str(row["id"]), "revision_no": int(row["revision_no"]), "content_hash": str(row["content_hash"]), "cues": cues}

    @staticmethod
    def _jianying(
        revision: dict[str, Any],
        items: list[dict[str, Any]],
        subtitle: dict[str, Any] | None,
        media_entries: dict[str, dict[str, Any]],
        *,
        draft_fold_path: str,
        draft_name: str,
    ) -> dict[str, Any]:
        """Build a best-effort CapCut/Jianying ``draft_content.json``.

        The Jianying draft layout is reverse-engineered from the community and
        is NOT an official schema.  Field names follow the widely documented
        ``draft_content.json`` shape (``canvas_config`` / ``materials`` /
        ``tracks`` with per-segment ``target_timerange`` / ``source_timerange``)
        and times are integer microseconds; media ``path`` values are relative
        to the draft folder (``./media/...``).  Exact CapCut-version
        compatibility must be verified on a real machine — treat this as
        best-effort until then.
        """
        videos: list[dict[str, Any]] = []
        audios: list[dict[str, Any]] = []
        video_segments: list[dict[str, Any]] = []
        audio_segments: list[dict[str, Any]] = []
        duration_us = 0
        for item in items:
            track_type = str(item["track_type"]).upper()
            entry = media_entries.get(str(item["media_version_id"]))
            if entry is None:
                raise DomainRuleError("TIMELINE_EXPORT_MEDIA_MAP_MISSING", "剪映导出缺少媒体映射")
            start_us = int(item["start_us"])
            end_us = int(item["end_us"])
            duration_us = max(duration_us, end_us)
            segment = {
                "id": f"segment-{uuid.uuid4().hex[:24]}",
                "material_id": entry["material_id"],
                "target_timerange": {"start": start_us, "end": end_us},
                "source_timerange": {"duration": int(item["duration_us"]), "start": int(item["source_start_us"])},
            }
            if track_type == "VIDEO":
                video_segments.append(segment)
            elif track_type in {"AUDIO", "DIALOGUE", "BGM", "SFX", "MUSIC", "ENVIRONMENT"}:
                audio_segments.append(segment)
            else:
                raise DomainRuleError("TIMELINE_TRACK_TYPE_UNSUPPORTED", f"剪映导出不支持的轨道类型：{track_type}")
        for entry in media_entries.values():
            media = entry["media"]
            material: dict[str, Any] = {
                "id": entry["material_id"],
                "path": entry["path"],
                "duration": int(media["duration_ms"] or 0) * 1000,
                "material_name": str(media["source_name"]),
            }
            if entry["kind"] == "video":
                width, height = _video_probe_size(media)
                if width is not None and height is not None:
                    material["width"] = width
                    material["height"] = height
                videos.append(material)
            else:
                audios.append(material)
        texts: list[dict[str, Any]] = []
        text_segments: list[dict[str, Any]] = []
        if subtitle is not None:
            for cue in subtitle["cues"]:
                material_id = f"text-{uuid.uuid4().hex[:24]}"
                start_us = int(cue["start_us"])
                end_us = int(cue["end_us"])
                duration_us = max(duration_us, end_us)
                style = cue.get("style")
                texts.append(
                    {
                        "id": material_id,
                        "content": str(cue["text"]),
                        "style": style if isinstance(style, dict) else {},
                        "time": {"start": start_us, "duration": end_us - start_us},
                    }
                )
                text_segments.append(
                    {
                        "id": f"segment-{uuid.uuid4().hex[:24]}",
                        "material_id": material_id,
                        "target_timerange": {"start": start_us, "end": end_us},
                        "source_timerange": {"duration": end_us - start_us},
                    }
                )
        tracks: list[dict[str, Any]] = []
        if video_segments:
            tracks.append({"type": "video", "segments": video_segments})
        if audio_segments:
            tracks.append({"type": "audio", "segments": audio_segments})
        if text_segments:
            tracks.append({"type": "text", "segments": text_segments})
        if not tracks:
            raise DomainRuleError("TIMELINE_ITEMS_REQUIRED", "剪映导出至少需要一个可导出轨道")
        fps_num = int(revision["fps_num"]) if revision["fps_num"] else None
        fps_den = int(revision["fps_den"]) if revision["fps_den"] else None
        canvas: dict[str, Any] = {
            "width": int(revision["width"]) if revision["width"] else 0,
            "height": int(revision["height"]) if revision["height"] else 0,
            "ratio": str(revision.get("aspect_ratio") or ""),
        }
        if fps_num and fps_den:
            canvas["fps"] = fps_num / fps_den
        return {
            "schema_version": "localdrama.jianying-draft.v1",
            "best_effort": True,
            "canvas_config": canvas,
            "duration": duration_us,
            "draft_fold_path": draft_fold_path,
            "draft_name": draft_name,
            "materials": {"videos": videos, "audios": audios, "texts": texts},
            "tracks": tracks,
            "localdrama": {
                "schema": "localdrama.timeline-export.v1",
                "writer_version": 3,
                "timeline_revision_id": revision["id"],
                "revision_hash": revision["revision_hash"],
                "fps_num": fps_num,
                "fps_den": fps_den,
                "subtitle_revision_id": subtitle["id"] if subtitle is not None else None,
                "subtitle_revision_hash": subtitle["content_hash"] if subtitle is not None else None,
            },
        }

    def _export_jianying(self, timeline_revision_id: str, *, subtitle_revision_id: str | None = None) -> dict[str, Any]:
        revision, raw_items = self._snapshot(timeline_revision_id)
        fps_num = int(revision["fps_num"]) if revision["fps_num"] else None
        fps_den = int(revision["fps_den"]) if revision["fps_den"] else None
        items = self._verified_items(revision, raw_items)
        subtitle = self._subtitle_revision(revision, subtitle_revision_id) if subtitle_revision_id else None
        identity = {
            "schema_version": "localdrama.timeline-export.v1",
            "writer_version": 3,
            "format": "jianying",
            "timeline_revision_id": timeline_revision_id,
            "revision_hash": revision["revision_hash"],
            "fps_num": fps_num,
            "fps_den": fps_den,
            "subtitle_revision_id": subtitle["id"] if subtitle is not None else None,
            "subtitle_revision_hash": subtitle["content_hash"] if subtitle is not None else None,
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
        project_root = self.settings.resolve_project_root(str(revision["root_rel"]))
        if not project_root.is_relative_to(self.settings.projects_root.resolve()) or not project_root.is_dir() or project_root.is_symlink():
            raise DomainRuleError("PROJECT_ROOT_INVALID", "项目根目录无效")
        base = project_root / "05_timelines" / str(revision["episode_code"]) / "exports"
        final = base / f'timeline-v{revision["revision_no"]}-{export_hash[:12]}'
        if final.exists():
            if not final.is_dir() or final.is_symlink():
                raise DomainRuleError("TIMELINE_EXPORT_TAMPERED", "时间线导出目标不是安全目录")
            manifest = self._verify_existing(final, export_hash)
            return self._result(project_root, final, manifest, reused=True)
        draft_name = f'{revision["episode_code"]}-v{revision["revision_no"]}'
        draft_folder_name = f"{draft_name}.draft"
        final_draft_dir = (final / draft_folder_name).resolve()
        partial = base / f".timeline-export.partial-{uuid.uuid4().hex}"
        try:
            partial.mkdir(parents=True)
            draft_dir = partial / draft_folder_name
            media_dir = draft_dir / "media"
            media_dir.mkdir(parents=True)
            media_entries: dict[str, dict[str, Any]] = {}
            for item in items:
                media_version_id = str(item["media_version_id"])
                if media_version_id in media_entries:
                    continue
                track_type = str(item["track_type"]).upper()
                if track_type not in {"VIDEO", "AUDIO", "DIALOGUE", "BGM", "SFX", "MUSIC", "ENVIRONMENT"}:
                    raise DomainRuleError("TIMELINE_TRACK_TYPE_UNSUPPORTED", f"剪映导出不支持的轨道类型：{track_type}")
                kind = "video" if track_type == "VIDEO" else "audio"
                _, source_path = self.media.content_path(media_version_id)
                suffix = Path(str(item["media"]["rel_path"])).suffix or ".bin"
                filename = f"{kind}_{len(media_entries) + 1:02d}{suffix}"
                media_entries[media_version_id] = {
                    "kind": kind,
                    "media": item["media"],
                    "source_path": source_path,
                    "filename": filename,
                    "path": f"./media/{filename}",
                    "material_id": f"material-{kind}-{uuid.uuid4().hex[:24]}",
                    "manifest_rel_path": f"{draft_folder_name}/media/{filename}",
                }
            files: list[dict[str, Any]] = []
            for entry in media_entries.values():
                destination = media_dir / entry["filename"]
                shutil.copy2(entry["source_path"], destination)
                files.append(
                    {"rel_path": entry["manifest_rel_path"], "byte_size": destination.stat().st_size, "sha256": _sha256(destination)}
                )
            draft = self._jianying(
                revision,
                items,
                subtitle,
                media_entries,
                draft_fold_path=str(final_draft_dir),
                draft_name=draft_name,
            )
            draft_path = draft_dir / "draft_content.json"
            draft_path.write_bytes(_canonical(draft) + b"\n")
            files.append(
                {"rel_path": f"{draft_folder_name}/draft_content.json", "byte_size": draft_path.stat().st_size, "sha256": _sha256(draft_path)}
            )
            manifest = {
                **identity,
                "export_hash": export_hash,
                "files": files,
                "database_mutated": False,
                "media_copy": "BUNDLED",
            }
            (partial / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
            replace_path(partial, final)
        except Exception:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        return self._result(project_root, final, manifest, reused=False)

    @staticmethod
    def _result(project_root: Path, final: Path, manifest: dict[str, Any], *, reused: bool) -> dict[str, Any]:
        rel_path = final.relative_to(project_root).as_posix()
        timeline_revision_id = str(manifest["timeline_revision_id"])
        is_jianying = str(manifest.get("format") or "") == "jianying" or final.name.startswith("jianying-v")
        artifact = local_artifact_reference(
            root=project_root,
            path=final,
            scope="PROJECT",
            kind="DIRECTORY",
            display_name=f"{'剪映草稿' if is_jianying else '专业剪辑交换包'} · {final.name}",
            download_url=f"/api/v1/timeline-revisions/{quote(timeline_revision_id, safe='')}/export:download?rel_path={quote(rel_path, safe='')}",
            download_filename=f"{final.name}.zip",
            error_code="TIMELINE_EXPORT_PATH_INVALID",
        )
        return {
            "schema_version": "localdrama.timeline-export.v1",
            "status": "EXPORTED",
            "artifact": artifact,
            "rel_path": rel_path,
            "manifest_rel_path": (final / "manifest.json").relative_to(project_root).as_posix(),
            "files": manifest["files"],
            "export_hash": manifest["export_hash"],
            "reused": reused,
            "database_mutated": False,
            "runtime_contacted": False,
            "network_contacted": False,
        }

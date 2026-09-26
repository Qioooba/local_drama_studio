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
from local_drama.application.timeline import TimelineService
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


#: Audio-track parameters that the frozen timeline actually applies at render
#: time.  A base editing interchange format has no portable equivalent, so the
#: export must say so explicitly instead of writing ``effects=[]`` and letting a
#: user believe the mix survived.
_AUDIO_MIX_PARAMETERS = ("gain_db", "fade_in_us", "fade_out_us", "loop_enabled", "source_start_us")

_TRANSITION_OTIO_TYPE = {"DISSOLVE": "SMPTE_Dissolve", "FADE": "SMPTE_Dissolve"}

#: What each writer can actually reproduce.  A capability that only one writer
#: implements must never make another writer claim it too (TM-06).
_WRITER_TRANSITION_CAPABILITY = {
    "OTIO": True,
    "EDL": True,
    "JIANYING": False,
}
_WRITER_VERSIONS = {"OTIO": 4, "JIANYING": 4}


def _transition_frames(item: dict[str, Any], rate: float) -> int:
    parameters = item.get("parameters") or {}
    seconds = float(parameters.get("transition_duration_seconds") or 0.0)
    if seconds <= 0:
        return 0
    frames = max(1, round(seconds * rate))
    # A dissolve cannot be longer than half of either neighbour, otherwise one
    # clip disappears entirely and the timeline length is no longer the sum of
    # its parts.
    limit = max(1, int(item["duration_us"] * rate / 1_000_000) // 2)
    return min(frames, max(1, limit))


def _otio_transition(item: dict[str, Any], rate: float) -> dict[str, object] | None:
    """A real OTIO ``Transition`` between two adjacent clips (TM-06).

    The previous writer emitted a ``LinearTimeWarp`` with ``time_scalar=1.0``,
    which is OTIO's *speed change* object — it carries no cross-dissolve meaning
    at all, so every official adapter read the export back as two hard-cut clips.
    ``Transition`` with in/out offsets is the first-class object for this, and the
    offsets are the handles each neighbour gives up.
    """
    kind = str((item.get("parameters") or {}).get("transition_in") or "CUT").upper()
    if kind not in _TRANSITION_OTIO_TYPE:
        return None
    frames = _transition_frames(item, rate)
    if frames <= 0:
        return None
    half = frames / 2
    return {
        "OTIO_SCHEMA": "Transition.1",
        "name": f"localdrama_{kind.lower()}",
        "transition_type": _TRANSITION_OTIO_TYPE[kind],
        "in_offset": _rational_time(half, rate),
        "out_offset": _rational_time(half, rate),
        "metadata": {
            "localdrama_transition": kind,
            "duration_frames": frames,
            "duration_seconds": frames / rate,
        },
    }


def _audio_item_losses(item: dict[str, Any], rel_path: str) -> list[dict[str, Any]]:
    """List the audio-mix parameters that a base exchange export cannot carry."""
    parameters = item.get("parameters") or {}
    losses: list[dict[str, Any]] = []
    labels = {
        "gain_db": "相对音量",
        "fade_in_us": "淡入",
        "fade_out_us": "淡出",
        "loop_enabled": "循环",
        "source_start_us": "源入点",
    }
    for name in _AUDIO_MIX_PARAMETERS:
        value = parameters.get(name)
        if value in (None, 0, 0.0, False):
            continue
        losses.append(
            {
                "rel_path": rel_path,
                "timeline_item_id": str(item["id"]),
                "track_type": str(item["track_type"]),
                "feature": f"AUDIO_{name.upper()}",
                "label": labels.get(name, name),
                "value": value,
                "reason": "基础剪辑交换格式没有可移植的混音参数表达；原始值保留在导出 manifest 的 localdrama_item_parameters 中",
                "recoverable_from_manifest": True,
            }
        )
    return losses


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

    def _frozen_transition_seconds(
        self, revision: dict[str, Any], video_items: list[dict[str, Any]]
    ) -> dict[int, float]:
        """The transition window the RENDER actually applies, per target item index.

        TM-06: the OTIO/EDL writers used to read ``transition_duration_seconds``
        straight out of the user's item parameters.  A timeline whose dissolve came
        from the editor's rule rather than from an explicit number therefore
        exported with no transition at all, while the same revision's MP4 really
        contained one — the exchange package and the rendered film disagreed about
        the episode's length and about what happens at the cut.
        """
        fps_num = int(revision.get("fps_num") or 0)
        fps_den = int(revision.get("fps_den") or 0)
        if fps_num <= 0 or fps_den <= 0 or not video_items:
            return {}
        starts = [int(item["start_us"]) for item in video_items]
        transitions, _frames = TimelineService._transition_plan(
            video_items, fps_num=fps_num, fps_den=fps_den, leading_blank_us=max(0, min(starts))
        )
        return {int(entry["to_item_index"]): float(entry["duration_seconds"]) for entry in transitions}

    def _verified_items(self, revision: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        video_items = [item for item in items if str(item["track_type"]).upper() == "VIDEO" and item["media_version_id"]]
        planned = self._frozen_transition_seconds(revision, video_items)
        video_order = {str(item["id"]): index for index, item in enumerate(video_items)}
        verified: list[dict[str, Any]] = []
        for item in items:
            media_version_id = item["media_version_id"]
            if not media_version_id:
                raise DomainRuleError("TIMELINE_EXPORT_MEDIA_REQUIRED", "时间线导出不接受无媒体的时间线 item")
            media = self.media.verify_content_integrity(str(media_version_id))
            if media["project_id"] != revision["project_id"]:
                raise DomainRuleError("MEDIA_PROJECT_MISMATCH", "导出媒体必须属于时间线项目")
            parameters = dict(item["parameters"] or {})
            source_start_us = int(parameters.get("source_start_us", 0))
            duration_us = int(item["end_us"]) - int(item["start_us"])
            if source_start_us < 0:
                raise DomainRuleError("TIMELINE_SOURCE_RANGE_INVALID", "source_start_us 不能为负数")
            index = video_order.get(str(item["id"]))
            if index is not None and index in planned:
                # The plan wins over a missing or stale authored number.
                parameters["transition_duration_seconds"] = planned[index]
            verified.append(
                {
                    **item,
                    "parameters": parameters,
                    "media": media,
                    "source_start_us": source_start_us,
                    "duration_us": duration_us,
                }
            )
        return verified

    @staticmethod
    def _otio(revision: dict[str, Any], items: list[dict[str, Any]], rate: float, losses: list[dict[str, Any]] | None = None) -> dict[str, object]:
        tracks: list[dict[str, object]] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault(str(item["track_type"]), []).append(item)
        for track_type, track_items in grouped.items():
            children: list[dict[str, object]] = []
            cursor_us = 0
            # TM-06: the official reader sums the children's source ranges PLUS each
            # transition's in/out offsets, so the old writer's 0.5 s dissolve exported
            # as a 4.0 s timeline while the rendered film was 3.5 s.  ``xfade`` moves
            # the incoming picture T/2 earlier, so a dissolve takes ``in_offset`` from
            # the clip that precedes it and ``out_offset`` from the clip that follows.
            # Trimming those handles makes the reader's duration equal the rendered
            # film's; this was verified against the official OpenTimelineIO 0.18.1
            # reader.  Everything is computed in ONE linear pass over an explicit
            # sequence, so the two sides of a transition can never be swapped.
            sequence: list[tuple[str, Any]] = []
            for item in track_items:
                transition = (
                    _otio_transition(item, rate)
                    if track_type == "VIDEO" and TimelineExportService._transition_kind(item) != "CUT"
                    else None
                )
                if transition is not None:
                    sequence.append(("transition", transition))
                sequence.append(("clip", item))
            trims: dict[str, dict[str, float]] = {}
            for position, (kind, payload) in enumerate(sequence):
                if kind != "transition":
                    continue
                in_offset = float(payload["in_offset"]["value"])
                out_offset = float(payload["out_offset"]["value"])
                if position > 0:
                    trims.setdefault(str(sequence[position - 1][1]["id"]), {})["out"] = in_offset
                if position + 1 < len(sequence):
                    trims.setdefault(str(sequence[position + 1][1]["id"]), {})["in"] = out_offset
            for kind, payload in sequence:
                if kind == "transition":
                    children.append(payload)
                    continue
                item = payload
                handles = trims.get(str(item["id"]), {})
                half_in = float(handles.get("in", 0.0))
                half_out = float(handles.get("out", 0.0))
                frames = int(item["duration_us"]) * rate / 1_000_000
                visible = frames - half_in - half_out
                if visible <= 0:
                    raise DomainRuleError(
                        "TIMELINE_EXPORT_TRANSITION_TOO_LONG",
                        "转场重叠超过相邻镜头时长，交换包无法表达该时间线",
                        {"timeline_item_id": str(item["id"]), "rate": rate},
                    )
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
                source_start = int(item["source_start_us"]) * rate / 1_000_000 + half_in
                clip_metadata: dict[str, Any] = {
                    "localdrama_timeline_item_id": item["id"],
                    # The authored parameters travel with the clip so a human can
                    # restore what this base interchange format cannot express.
                    "localdrama_item_parameters": dict(item.get("parameters") or {}),
                }
                if track_type != "VIDEO":
                    clip_metadata["localdrama_audio_mix_parameters"] = {
                        name: (item.get("parameters") or {}).get(name) for name in _AUDIO_MIX_PARAMETERS
                    }
                clip_metadata["localdrama_transition_handles"] = {
                    "in_frames": half_in,
                    "out_frames": half_out,
                }
                children.append(
                    {
                        "OTIO_SCHEMA": "Clip.2",
                        "name": str(media["source_name"]),
                        "source_range": _time_range(source_start, visible, rate),
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
                        "metadata": clip_metadata,
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
                "localdrama_export_losses": losses or [],
            },
        }

    @staticmethod
    def _transition_kind(item: dict[str, Any]) -> str:
        return str((item.get("parameters") or {}).get("transition_in") or "CUT").upper()

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
            # CMX 3600 has no native transition event.  The event is emitted as a
            # `D` (dissolve) with an explicit duration in frames when the frozen
            # timeline carries one, instead of always writing a hard `C`.
            transition_kind = TimelineExportService._transition_kind(item)
            transition_seconds = float(((item.get("parameters") or {}).get("transition_duration_seconds") or 0.0))
            if transition_kind != "CUT" and transition_seconds > 0:
                transition_frames = max(1, round(transition_seconds * nominal_fps))
                lines.append(
                    f"{event_no:03d}  {reel:<8} V     D    {transition_frames:03d} "
                    f"{_edl_timecode(source_in, nominal_fps)} {_edl_timecode(source_out, nominal_fps)} "
                    f"{_edl_timecode(int(item['start_us']), nominal_fps)} {_edl_timecode(int(item['end_us']), nominal_fps)}"
                )
            else:
                lines.append(
                    f"{event_no:03d}  {reel:<8} V     C        "
                    f"{_edl_timecode(source_in, nominal_fps)} {_edl_timecode(source_out, nominal_fps)} "
                    f"{_edl_timecode(int(item['start_us']), nominal_fps)} {_edl_timecode(int(item['end_us']), nominal_fps)}"
                )
            lines.append(f"* FROM CLIP NAME: {item['media']['source_name']}")
            lines.append(f"* LOCALDRAMA MEDIA VERSION: {item['media_version_id']} SHA256: {item['media']['sha256']}")
            if transition_kind != "CUT":
                lines.append(f"* LOCALDRAMA TRANSITION: {transition_kind} {transition_seconds:.6f}s")
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
        losses = self._export_losses(items, format_label="OTIO/EDL", writer="OTIO")
        identity = {
            "schema_version": "localdrama.timeline-export.v1",
            "writer_version": _WRITER_VERSIONS["OTIO"],
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
                    # TM-06: a writer change must invalidate the cached package, so
                    # the effects that decide the OTIO output are part of the hash.
                    "parameters": dict(item.get("parameters") or {}),
                }
                for item in items
            ],
            "losses": losses,
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
            otio_path.write_bytes(_canonical(self._otio(revision, items, rate, losses)) + b"\n")
            edl_path.write_text(self._edl(revision, items, nominal_fps), encoding="utf-8", newline="\n")
            files = [
                {"rel_path": path.name, "byte_size": path.stat().st_size, "sha256": _sha256(path)}
                for path in (otio_path, edl_path)
            ]
            manifest = {
                **identity,
                "export_hash": export_hash,
                "files": files,
                "database_mutated": False,
                # MED-09: an exchange export is "base editing interchange"; the
                # effects it cannot express are listed explicitly instead of
                # being silently dropped from effects=[] / a hard-coded `C`.
                "losses": losses,
                "unsupported_features": sorted({str(item["feature"]) for item in losses}),
                "localdrama_item_parameters": [
                    {"id": item["id"], "track_type": str(item["track_type"]), "parameters": dict(item.get("parameters") or {})}
                    for item in items
                ],
            }
            (partial / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
            replace_path(partial, final)
        except Exception:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        return self._result(project_root, final, manifest, reused=False)

    @staticmethod
    def _export_losses(
        items: list[dict[str, Any]],
        *,
        format_label: str,
        writer: str,
    ) -> list[dict[str, Any]]:
        """List every frozen effect THIS writer cannot reproduce.

        Two audit defects live here.  The old rule treated "a positive transition
        duration exists" as "the transition was preserved" for every format at
        once, so a base interchange writer's OTIO capability silently covered the
        Jianying writer as well, and both reported ``losses=[]``.  A writer's
        capability is now declared per writer, and each loss names the format it
        belongs to.  ``FADE`` is never silently downgraded to a dissolve.
        """
        losses: list[dict[str, Any]] = []
        can_encode_transition = _WRITER_TRANSITION_CAPABILITY.get(writer, False)
        transition_expressible = {"DISSOLVE"} if can_encode_transition else set()
        for item in items:
            rel_path = str(item["media"]["rel_path"])
            track_type = str(item["track_type"])
            parameters = item.get("parameters") or {}
            if track_type == "VIDEO":
                kind = TimelineExportService._transition_kind(item)
                if kind != "CUT" and kind not in transition_expressible:
                    duration = float(parameters.get("transition_duration_seconds") or 0.0)
                    if kind in {"DISSOLVE", "FADE"}:
                        reason = (
                            f"{format_label} 的转场写入口尚未实现或尚未在目标客户端验证；"
                            "文件写出不等于转场保真，恢复信息保留在导出 manifest 中"
                        )
                    else:
                        reason = (
                            f"{format_label} 无 {kind} 的原生表达；"
                            "只有 DISSOLVE 被声明为已实现能力"
                        )
                    losses.append(
                        {
                            "rel_path": rel_path,
                            "timeline_item_id": str(item["id"]),
                            "track_type": track_type,
                            "format": format_label,
                            "feature": f"TRANSITION_{kind}",
                            "label": f"入场转场 {kind}",
                            "value": {"kind": kind, "duration_seconds": duration},
                            "reason": reason,
                            "recoverable_from_manifest": True,
                        }
                    )
                for name, label in (("lut", "LUT"), ("speed", "变速"), ("mask", "遮罩")):
                    if parameters.get(name):
                        losses.append(
                            {
                                "rel_path": rel_path,
                                "timeline_item_id": str(item["id"]),
                                "track_type": track_type,
                                "format": format_label,
                                "feature": name.upper(),
                                "label": label,
                                "value": parameters.get(name),
                                "reason": f"{format_label} 基础交换不承载该效果；需要烘焙后的画面才能保留",
                                "recoverable_from_manifest": False,
                            }
                        )
            else:
                for loss in _audio_item_losses(item, rel_path):
                    losses.append({**loss, "format": format_label})
        return losses

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
                "writer_version": _WRITER_VERSIONS["JIANYING"],
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
            "writer_version": _WRITER_VERSIONS["JIANYING"],
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
                    # The frozen transition data decides what the draft says about
                    # every cut, so a change there must invalidate the cached package.
                    "parameters": dict(item.get("parameters") or {}),
                }
                for item in items
            ],
        }
        export_hash = hashlib.sha256(_canonical(identity)).hexdigest()
        project_root = self.settings.resolve_project_root(str(revision["root_rel"]))
        if not project_root.is_relative_to(self.settings.projects_root.resolve()) or not project_root.is_dir() or project_root.is_symlink():
            raise DomainRuleError("PROJECT_ROOT_INVALID", "项目根目录无效")
        base = project_root / "05_timelines" / str(revision["episode_code"]) / "exports"
        # The download allowlist, the artifact title and the ``is_jianying`` detection
        # all read this prefix, so a Jianying package must not be named like an OTIO
        # package.
        final = base / f'jianying-v{revision["revision_no"]}-{export_hash[:12]}'
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
            losses = self._export_losses(items, format_label="jianying", writer="JIANYING")
            manifest = {
                **identity,
                "export_hash": export_hash,
                "files": files,
                "database_mutated": False,
                "media_copy": "BUNDLED",
                # 剪映 keeps the repository's declared best-effort boundary: the
                # draft is produced, and everything it cannot carry is listed
                # rather than implied to be preserved.
                "best_effort": True,
                "losses": losses,
                "unsupported_features": sorted({str(item["feature"]) for item in losses}),
                "localdrama_item_parameters": [
                    {"id": item["id"], "track_type": str(item["track_type"]), "parameters": dict(item.get("parameters") or {})}
                    for item in items
                ],
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
            # MED-09: the caller must be able to show "what was dropped" before
            # the user treats the exported project as a faithful copy.
            "losses": list(manifest.get("losses") or []),
            "unsupported_features": list(manifest.get("unsupported_features") or []),
            "fidelity": "BASE_EDITING_INTERCHANGE" if not manifest.get("losses") else "BASE_EDITING_INTERCHANGE_WITH_LOSSES",
            "reused": reused,
            "database_mutated": False,
            "runtime_contacted": False,
            "network_contacted": False,
        }

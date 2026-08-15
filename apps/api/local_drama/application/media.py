"""Immutable media registration, probing, cache generation and range reads."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
DOCUMENT_EXTENSIONS = {".txt", ".md", ".markdown", ".docx"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _safe_name(name: str) -> str:
    candidate = Path(name).name
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate)
    if candidate in {"", ".", ".."}:
        raise DomainRuleError("INVALID_SOURCE_NAME", "导入文件名无效")
    return candidate[:180]


def infer_media_kind(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in IMAGE_EXTENSIONS:
        return "IMAGE"
    if extension in VIDEO_EXTENSIONS:
        return "VIDEO"
    if extension in AUDIO_EXTENSIONS:
        return "AUDIO"
    if extension in DOCUMENT_EXTENSIONS:
        return "DOCUMENT"
    return "OTHER"


def _video_metadata(probe: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    video_stream: dict[str, Any] = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), {})
    duration_value = video_stream.get("duration") or probe.get("format", {}).get("duration")
    try:
        duration_ms = round(float(duration_value) * 1000) if duration_value is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    rate = str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "")
    try:
        parsed_num, parsed_den = (int(value) for value in rate.split("/", 1))
        fps_num, fps_den = (parsed_num, parsed_den) if parsed_den else (None, None)
    except (TypeError, ValueError):
        fps_num = fps_den = None
    return duration_ms, fps_num, fps_den


class MediaService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def _project_root(self, project_id: str) -> Path:
        with self.database.connect() as connection:
            row = connection.execute("SELECT root_rel FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        root = (self.settings.projects_root / str(row["root_rel"])).resolve()
        if not root.is_relative_to(self.settings.projects_root.resolve()):
            raise DomainRuleError("PATH_ESCAPE", "项目目录超出受控 projects_root")
        return root

    def _copy_into_project(self, project_root: Path, source: Path, original_name: str) -> tuple[str, Path]:
        imports = project_root / "00_admin" / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_name(original_name)
        rel = Path("00_admin") / "imports" / f"{uuid.uuid4().hex}-{safe_name}"
        destination = project_root / rel
        partial = destination.with_name(f".partial-{destination.name}")
        try:
            with source.open("rb") as input_file, partial.open("wb") as output_file:
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            os.replace(partial, destination)
        except OSError:
            if partial.exists():
                partial.unlink()
            raise
        return rel.as_posix(), destination

    def _probe(self, path: Path, kind: str) -> dict[str, Any]:
        if kind not in {"IMAGE", "VIDEO", "AUDIO"}:
            return {"probe_status": "NOT_APPLICABLE"}
        ffprobe = self.settings.ffprobe_path
        if not ffprobe or not Path(ffprobe).exists():
            return {"probe_status": "BLOCKED", "reason": "ffprobe_not_found"}
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"probe_status": "FAIL", "reason": type(error).__name__}
        if result.returncode != 0:
            return {"probe_status": "FAIL", "stderr_redacted": result.stderr[-500:]}
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"probe_status": "FAIL", "reason": "invalid_ffprobe_json"}
        return {"probe_status": "PASS", "format": payload.get("format", {}), "streams": payload.get("streams", [])}

    def import_file(
        self,
        project_id: str,
        source_path: str | Path,
        *,
        purpose: str = "IMPORT",
        owner_type: str = "PROJECT",
        owner_id: str | None = None,
        media_kind: str | None = None,
        stage: str = "IMPORTED",
        actor: str = "local-user",
    ) -> dict[str, Any]:
        source = Path(source_path)
        try:
            resolved_source = source.resolve(strict=True)
        except OSError as error:
            raise DomainRuleError("SOURCE_NOT_FOUND", "导入源文件不存在或无法读取", {"source_path": str(source)}) from error
        if not resolved_source.is_file() or resolved_source.is_symlink():
            raise DomainRuleError("INVALID_SOURCE_FILE", "导入源必须是普通本地文件")
        project_root = self._project_root(project_id)
        digest, size = _hash_file(resolved_source)
        kind = media_kind or infer_media_kind(resolved_source)
        mime = mimetypes.guess_type(resolved_source.name)[0] or "application/octet-stream"
        with self.database.transaction() as connection:
            duplicate = connection.execute(
                """SELECT ma.id AS media_asset_id, mv.id AS media_version_id, mv.rel_path, mv.sha256
                FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id = ma.id
                WHERE ma.project_id = ? AND mv.sha256 = ? ORDER BY mv.version_no LIMIT 1""",
                (project_id, digest),
            ).fetchone()
            if duplicate is not None:
                return {
                    "duplicate": True,
                    "media_asset_id": duplicate["media_asset_id"],
                    "media_version_id": duplicate["media_version_id"],
                    "rel_path": duplicate["rel_path"],
                    "sha256": duplicate["sha256"],
                }
            rel_path, destination = self._copy_into_project(project_root, resolved_source, resolved_source.name)
            probe = self._probe(destination, kind)
            duration_ms, fps_num, fps_den = _video_metadata(probe)
            asset_id = str(uuid.uuid4())
            version_id = str(uuid.uuid4())
            now = _utc_now()
            connection.execute(
                """INSERT INTO media_assets
                (id, project_id, owner_type, owner_id, purpose, media_kind, version_counter, metadata_json,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 1, 'v2')""",
                (
                    asset_id,
                    project_id,
                    owner_type,
                    owner_id or project_id,
                    purpose,
                    kind,
                    _json({"source_name": resolved_source.name, "source_path_not_retained": True}),
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                """INSERT INTO media_versions
                (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type, byte_size, sha256,
                source_name, import_source, probe_json, duration_ms, fps_num, fps_den,
                integrity_status, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?, ?, 'LOCAL_FILE', ?, ?, ?, ?, 'VERIFIED', ?, ?, ?, 1, 'v2')""",
                (version_id, asset_id, stage, rel_path, mime, size, digest, resolved_source.name, _json(probe), duration_ms, fps_num, fps_den, now, now, actor),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'MEDIA_IMPORTED', 'media_asset', ?, ?, ?)",
                (actor, asset_id, "导入并注册媒体版本", _json({"media_version_id": version_id, "sha256": digest, "media_kind": kind})),
            )
        return {
            "duplicate": False,
            "media_asset_id": asset_id,
            "media_version_id": version_id,
            "rel_path": rel_path,
            "mime_type": mime,
            "byte_size": size,
            "sha256": digest,
            "probe": probe,
        }

    def create_keyframe_candidate(
        self, source_media_version_id: str, shot_id: str, actor: str = "local-user"
    ) -> dict[str, Any]:
        """Create an immutable shot-owned KEYFRAME candidate from a verified image."""
        source = self.get_version(source_media_version_id)
        if source["media_kind"] != "IMAGE" or source["integrity_status"] != "VERIFIED":
            raise DomainRuleError("KEYFRAME_SOURCE_INVALID", "关键帧候选必须来自 VERIFIED 图片版本")
        self.verify_content_integrity(source_media_version_id)
        with self.database.connect() as connection:
            shot = connection.execute(
                """SELECT sh.id, s.project_id FROM shots sh
                JOIN episodes e ON e.id=sh.episode_id
                JOIN seasons s ON s.id=e.season_id
                WHERE sh.id=?""",
                (shot_id,),
            ).fetchone()
            existing = connection.execute(
                """SELECT mv.id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                WHERE mv.parent_version_id=? AND ma.owner_type='SHOT' AND ma.owner_id=?
                AND ma.purpose='KEYFRAME' AND mv.stage='KEYFRAME' ORDER BY mv.created_at LIMIT 1""",
                (source_media_version_id, shot_id),
            ).fetchone()
        if shot is None:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
        if str(shot["project_id"]) != str(source["project_id"]):
            raise DomainRuleError("KEYFRAME_PROJECT_MISMATCH", "关键帧源图片与镜头必须属于同一项目")
        if existing is not None:
            return {"duplicate": True, **self.get_version(str(existing["id"]))}
        _, source_path = self.content_path(source_media_version_id)
        project_root = self._project_root(str(source["project_id"]))
        rel_path, destination = self._copy_into_project(project_root, source_path, str(source["source_name"] or source_path.name))
        digest, size = _hash_file(destination)
        asset_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        now = _utc_now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO media_assets
                    (id, project_id, owner_type, owner_id, purpose, media_kind, version_counter, metadata_json,
                    created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, 'SHOT', ?, 'KEYFRAME', 'IMAGE', 1, ?, ?, ?, ?, 1, 'v2')""",
                    (asset_id, source["project_id"], shot_id, _json({"source_media_version_id": source_media_version_id}), now, now, actor),
                )
                connection.execute(
                    """INSERT INTO media_versions
                    (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type, byte_size, sha256,
                    parent_version_id, source_name, import_source, probe_json, integrity_status,
                    created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, 1, 1, 'KEYFRAME', ?, ?, ?, ?, ?, ?, 'DERIVED', ?, 'VERIFIED', ?, ?, ?, 1, 'v2')""",
                    (version_id, asset_id, rel_path, source["mime_type"], size, digest, source_media_version_id, source["source_name"], _json(source["probe"]), now, now, actor),
                )
                connection.execute(
                    """INSERT INTO audit_events
                    (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                    VALUES (?, 'producer', 'KEYFRAME_CANDIDATE_CREATED', 'media_version', ?, ?, ?)""",
                    (actor, version_id, "从真实图片版本创建镜头关键帧候选", _json({"source_media_version_id": source_media_version_id, "shot_id": shot_id})),
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return {"duplicate": False, **self.get_version(version_id)}

    def promote_job_artifact(
        self,
        artifact_id: str,
        *,
        purpose: str = "GENERATED_OUTPUT",
        media_kind: str | None = None,
        stage: str = "PROXY",
        actor: str = "worker",
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM media_versions WHERE source_artifact_id=?", (artifact_id,)
            ).fetchone()
            row = connection.execute(
                """SELECT a.*, ja.state AS attempt_state, j.state AS job_state, j.project_id,
                j.subject_type, j.subject_id
                FROM artifacts a JOIN job_attempts ja ON ja.id=a.job_attempt_id
                JOIN jobs j ON j.id=ja.job_id WHERE a.id=?""",
                (artifact_id,),
            ).fetchone()
        if existing is not None:
            with self.database.transaction() as connection:
                lineage = connection.execute(
                    """SELECT j.subject_type, j.subject_id FROM artifacts a
                    JOIN job_attempts ja ON ja.id=a.job_attempt_id JOIN jobs j ON j.id=ja.job_id
                    WHERE a.id=?""",
                    (artifact_id,),
                ).fetchone()
                if lineage is not None and lineage["subject_type"] == "GENERATION_VARIANT":
                    connection.execute(
                        "UPDATE generation_variants SET status='SUCCEEDED', updated_at=? WHERE id=? AND status!='SUCCEEDED'",
                        (_utc_now(), lineage["subject_id"]),
                    )
            return {"duplicate": True, **self.get_version(str(existing["id"]))}
        if row is None:
            raise DomainRuleError("ARTIFACT_NOT_FOUND", "Job artifact 不存在", {"artifact_id": artifact_id})
        if row["status"] != "VERIFIED" or row["attempt_state"] != "SUCCEEDED" or row["job_state"] != "SUCCEEDED":
            raise DomainRuleError(
                "ARTIFACT_NOT_PROMOTABLE",
                "只有成功 Attempt 的 VERIFIED artifact 可以登记为媒体",
                {"artifact_status": row["status"], "attempt_state": row["attempt_state"], "job_state": row["job_state"]},
            )
        work_root = self.settings.work_root.resolve()
        source = (work_root / str(row["sandbox_rel_path"])).resolve()
        if not source.is_relative_to(work_root) or not source.is_file() or source.is_symlink():
            raise DomainRuleError("ARTIFACT_FILE_MISSING", "Artifact 文件缺失或路径越界", {"artifact_id": artifact_id})
        actual_sha256, actual_size = _hash_file(source)
        if actual_sha256 != str(row["sha256"]):
            raise DomainRuleError("ARTIFACT_INTEGRITY_FAILED", "Artifact 文件与已登记 hash 不一致", {"artifact_id": artifact_id})

        project_id = str(row["project_id"])
        project_root = self._project_root(project_id)
        rel_path, destination = self._copy_into_project(project_root, source, source.name)
        kind = media_kind or infer_media_kind(source)
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        probe = self._probe(destination, kind)
        duration_ms, fps_num, fps_den = _video_metadata(probe)
        asset_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        now = _utc_now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO media_assets
                    (id, project_id, owner_type, owner_id, purpose, media_kind, version_counter, metadata_json,
                     created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 1, 'v2')""",
                    (
                        asset_id,
                        project_id,
                        str(row["subject_type"]),
                        str(row["subject_id"]),
                        purpose,
                        kind,
                        _json({"source_artifact_id": artifact_id, "source_path_not_retained": True}),
                        now,
                        now,
                        actor,
                    ),
                )
                if row["subject_type"] == "GENERATION_VARIANT":
                    connection.execute(
                        "UPDATE generation_variants SET status='SUCCEEDED', updated_at=? WHERE id=?",
                        (now, row["subject_id"]),
                    )
                connection.execute(
                    """INSERT INTO media_versions
                    (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type, byte_size, sha256,
                    source_name, import_source, probe_json, duration_ms, fps_num, fps_den,
                    source_job_attempt_id, source_artifact_id,
                     integrity_status, created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, 1, 1, ?, ?, ?, ?, ?, ?, 'JOB_ARTIFACT', ?, ?, ?, ?, ?, ?, 'VERIFIED', ?, ?, ?, 1, 'v2')""",
                    (
                        version_id,
                        asset_id,
                        stage,
                        rel_path,
                        mime,
                        actual_size,
                        actual_sha256,
                        source.name,
                        _json(probe),
                        duration_ms,
                        fps_num,
                        fps_den,
                        str(row["job_attempt_id"]),
                        artifact_id,
                        now,
                        now,
                        actor,
                    ),
                )
                connection.execute(
                    """INSERT INTO audit_events
                    (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                    VALUES (?, 'producer', 'JOB_ARTIFACT_PROMOTED', 'media_version', ?, ?, ?)""",
                    (
                        actor,
                        version_id,
                        "将已验证生成产物登记为不可变媒体版本",
                        _json({"artifact_id": artifact_id, "job_attempt_id": row["job_attempt_id"], "sha256": actual_sha256}),
                    ),
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return {
            "duplicate": False,
            "media_asset_id": asset_id,
            "media_version_id": version_id,
            "source_artifact_id": artifact_id,
            "source_job_attempt_id": str(row["job_attempt_id"]),
            "rel_path": rel_path,
            "mime_type": mime,
            "byte_size": actual_size,
            "sha256": actual_sha256,
            "probe": probe,
        }

    def derive_version(self, media_asset_id: str, parent_version_id: str, stage: str, actor: str = "local-user") -> dict[str, Any]:
        parent = self.get_version(parent_version_id)
        if parent["media_asset_id"] != media_asset_id:
            raise DomainRuleError("MEDIA_PARENT_MISMATCH", "派生版本的父版本不属于该媒体资产")
        now = _utc_now()
        version_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            asset = connection.execute("SELECT id FROM media_assets WHERE id=?", (media_asset_id,)).fetchone()
            if asset is None:
                raise DomainRuleError("MEDIA_ASSET_NOT_FOUND", "媒体资产不存在", {"media_asset_id": media_asset_id})
            row = connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 AS next_no FROM media_versions WHERE media_asset_id=?", (media_asset_id,)
            ).fetchone()
            next_no = int(row["next_no"])
            connection.execute(
                """INSERT INTO media_versions
                (id, media_asset_id, version_no, take_no, stage, rel_path, mime_type, byte_size, sha256,
                 source_name, import_source, probe_json, duration_ms, fps_num, fps_den,
                 parent_version_id, integrity_status, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DERIVED_LOCAL', ?, ?, ?, ?, ?, 'VERIFIED', ?, ?, ?, 1, 'v2')""",
                (
                    version_id,
                    media_asset_id,
                    next_no,
                    next_no,
                    stage,
                    parent["rel_path"],
                    parent["mime_type"],
                    parent["byte_size"],
                    parent["sha256"],
                    parent.get("source_name"),
                    _json(parent["probe"]),
                    parent.get("duration_ms"),
                    parent.get("fps_num"),
                    parent.get("fps_den"),
                    parent_version_id,
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'MEDIA_VERSION_DERIVED', 'media_version', ?, ?, ?)",
                (actor, version_id, "派生媒体版本", _json({"parent_version_id": parent_version_id, "stage": stage})),
            )
        return {
            **parent,
            "id": version_id,
            "version_no": next_no,
            "take_no": next_no,
            "stage": stage,
            "parent_version_id": parent_version_id,
        }

    def get_version(self, media_version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT mv.*, ma.project_id, ma.owner_type, ma.owner_id, ma.purpose, ma.media_kind,
                ma.selected_version_id, ma.approved_version_id, p.root_rel
                FROM media_versions mv JOIN media_assets ma ON ma.id = mv.media_asset_id
                JOIN projects p ON p.id = ma.project_id WHERE mv.id = ?""",
                (media_version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MEDIA_VERSION_NOT_FOUND", "媒体版本不存在", {"media_version_id": media_version_id})
        item = dict(row)
        item["probe"] = json.loads(item.pop("probe_json"))
        return item

    def get_asset(self, media_asset_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM media_assets WHERE id=?", (media_asset_id,)).fetchone()
        if row is None:
            raise DomainRuleError("MEDIA_ASSET_NOT_FOUND", "媒体资产不存在", {"media_asset_id": media_asset_id})
        return dict(row)

    def list_asset_versions(self, media_asset_id: str) -> list[dict[str, Any]]:
        self.get_asset(media_asset_id)
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM media_versions WHERE media_asset_id=? ORDER BY version_no", (media_asset_id,)).fetchall()
        return [{**dict(row), "probe": json.loads(row["probe_json"])} for row in rows]

    def clear_selection(self, media_asset_id: str, actor: str = "local-user") -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            asset = connection.execute("SELECT * FROM media_assets WHERE id=?", (media_asset_id,)).fetchone()
            if asset is None:
                raise DomainRuleError("MEDIA_ASSET_NOT_FOUND", "媒体资产不存在")
            connection.execute("UPDATE media_assets SET selected_version_id=NULL, revision=revision+1, updated_at=? WHERE id=?", (now, media_asset_id))
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, before_revision, after_revision, summary, metadata_redacted_json) VALUES (?, 'producer', 'MEDIA_SELECTION_CLEARED', 'media_asset', ?, ?, ?, '清除媒体选择', '{}')",
                (actor, media_asset_id, asset["revision"], asset["revision"] + 1),
            )
        return {**dict(asset), "selected_version_id": None, "revision": asset["revision"] + 1}

    def content_path(self, media_version_id: str) -> tuple[dict[str, Any], Path]:
        item = self.get_version(media_version_id)
        root = (self.settings.projects_root / item["root_rel"]).resolve()
        path = (root / item["rel_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise DomainRuleError("MEDIA_FILE_MISSING", "媒体文件缺失或路径越界", {"media_version_id": media_version_id})
        return item, path

    def verify_content_integrity(self, media_version_id: str, *, connection: Any | None = None) -> dict[str, Any]:
        item, path = self.content_path(media_version_id)
        actual_sha256, actual_size = _hash_file(path)
        verified = actual_sha256 == str(item["sha256"]) and actual_size == int(item["byte_size"])
        status = "VERIFIED" if verified else "CORRUPT"
        if str(item["integrity_status"]) != status:
            if connection is not None:
                connection.execute(
                    "UPDATE media_versions SET integrity_status=?, revision=revision+1, updated_at=? WHERE id=?",
                    (status, _utc_now(), media_version_id),
                )
            else:
                with self.database.transaction() as write_connection:
                    write_connection.execute(
                        "UPDATE media_versions SET integrity_status=?, revision=revision+1, updated_at=? WHERE id=?",
                        (status, _utc_now(), media_version_id),
                    )
        if not verified:
            raise DomainRuleError(
                "SOURCE_INTEGRITY_FAILED",
                "媒体文件 hash 或大小与不可变 MediaVersion 不一致",
                {
                    "media_version_id": media_version_id,
                    "expected_sha256": str(item["sha256"]),
                    "actual_sha256": actual_sha256,
                    "expected_byte_size": int(item["byte_size"]),
                    "actual_byte_size": actual_size,
                },
            )
        return {**item, "integrity_status": "VERIFIED", "actual_sha256": actual_sha256, "actual_byte_size": actual_size}

    def _run_ffmpeg(self, args: list[str]) -> None:
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg or not Path(ffmpeg).exists():
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用")
        try:
            result = subprocess.run([ffmpeg, *args], capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DomainRuleError("MEDIA_CACHE_FAILED", "媒体缓存生成失败", {"reason": type(error).__name__}) from error
        if result.returncode != 0:
            raise DomainRuleError("MEDIA_CACHE_FAILED", "媒体缓存生成失败", {"stderr_redacted": result.stderr[-500:]})

    def _cache_entry(self, media_version_id: str, kind: str, rel_path: str, source_sha: str, preset_hash: str) -> None:
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO media_cache_entries (id, media_version_id, cache_kind, rel_path, source_sha256, preset_hash, status, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, 'READY', ?, ?, 'system', 1, 'v2')
                ON CONFLICT(media_version_id, cache_kind, preset_hash) DO UPDATE SET rel_path=excluded.rel_path,
                source_sha256=excluded.source_sha256, status='READY', updated_at=excluded.updated_at,
                revision=media_cache_entries.revision+1""",
                (str(uuid.uuid4()), media_version_id, kind, rel_path, source_sha, preset_hash, now, now),
            )

    def thumbnail(self, media_version_id: str, size: str = "small", frame: str = "poster") -> tuple[Path, str]:
        item, source = self.content_path(media_version_id)
        if item["media_kind"] not in {"IMAGE", "VIDEO"}:
            raise DomainRuleError("THUMBNAIL_UNSUPPORTED", "该媒体类型不支持缩略图")
        normalized_frame = str(frame or "poster").strip().lower()
        frame_aliases = {"poster": "first", "start": "first", "first_frame": "first", "middle_frame": "middle", "end": "last", "last_frame": "last"}
        normalized_frame = frame_aliases.get(normalized_frame, normalized_frame)
        if normalized_frame not in {"first", "middle", "last"}:
            raise DomainRuleError(
                "THUMBNAIL_FRAME_UNSUPPORTED",
                "缩略图 frame 仅支持 first、middle、last（poster 等价于 first）",
                {"frame": frame},
            )
        if item["media_kind"] == "IMAGE" and normalized_frame != "first":
            raise DomainRuleError("THUMBNAIL_FRAME_UNSUPPORTED", "图片只有 first/poster 缩略图")
        self.verify_content_integrity(media_version_id)
        preset = f"thumbnail-v2:{size}:{normalized_frame}"
        preset_hash = hashlib.sha256(preset.encode()).hexdigest()
        extension = ".webp"
        relative = Path("thumbnails") / media_version_id / size / f"{item['sha256']}_{preset_hash[:16]}{extension}"
        destination = self.settings.cache_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            partial = destination.with_suffix(".partial.webp")
            scale = "320:-1" if size == "small" else "960:-1"
            seek: list[str] = []
            if item["media_kind"] == "VIDEO":
                duration_ms = int(item.get("duration_ms") or 0)
                if normalized_frame != "first" and duration_ms <= 0:
                    raise DomainRuleError("THUMBNAIL_FRAME_UNRESOLVED", "视频缺少有效 duration，无法定位缩略图帧")
                offset_ms = {"first": 0, "middle": duration_ms // 2, "last": max(0, duration_ms - 1)}[normalized_frame]
                seek = ["-ss", f"{offset_ms / 1000:.3f}"]
            self._run_ffmpeg([*seek, "-i", str(source), "-frames:v", "1", "-vf", f"scale={scale}", "-c:v", "libwebp", "-y", str(partial)])
            os.replace(partial, destination)
            self._cache_entry(media_version_id, "THUMBNAIL", relative.as_posix(), item["sha256"], preset_hash)
        return destination, "image/webp"

    def filmstrip(self, media_version_id: str) -> tuple[Path, str]:
        item, source = self.content_path(media_version_id)
        if item["media_kind"] != "VIDEO":
            raise DomainRuleError("FILMSTRIP_UNSUPPORTED", "只有视频支持 filmstrip")
        self.verify_content_integrity(media_version_id)
        preset = "filmstrip-v1:5x1:320"
        preset_hash = hashlib.sha256(preset.encode()).hexdigest()
        relative = Path("filmstrips") / media_version_id / f"{item['sha256']}_{preset_hash[:16]}.webp"
        destination = self.settings.cache_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            partial = destination.with_suffix(".partial.webp")
            self._run_ffmpeg(["-i", str(source), "-vf", "fps=1/2,scale=320:-1,tile=5x1", "-frames:v", "1", "-c:v", "libwebp", "-y", str(partial)])
            os.replace(partial, destination)
            self._cache_entry(media_version_id, "FILMSTRIP", relative.as_posix(), item["sha256"], preset_hash)
        return destination, "image/webp"

    def waveform(self, media_version_id: str) -> tuple[Path, str]:
        item, source = self.content_path(media_version_id)
        if item["media_kind"] not in {"AUDIO", "VIDEO"}:
            raise DomainRuleError("WAVEFORM_UNSUPPORTED", "该媒体类型不支持波形")
        self.verify_content_integrity(media_version_id)
        preset = "waveform-v2:640x128"
        preset_hash = hashlib.sha256(preset.encode()).hexdigest()
        relative = Path("waveforms") / media_version_id / f"{item['sha256']}_{preset_hash[:16]}.png"
        destination = self.settings.cache_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            partial = destination.with_suffix(".partial.png")
            self._run_ffmpeg(["-i", str(source), "-filter_complex", "showwavespic=s=640x128:colors=0x2dd4bf", "-frames:v", "1", "-y", str(partial)])
            os.replace(partial, destination)
            self._cache_entry(media_version_id, "WAVEFORM", relative.as_posix(), item["sha256"], preset_hash)
        return destination, "image/png"

    def audio_qc_metrics(self, media_version_id: str) -> dict[str, float | bool | str]:
        item, source = self.content_path(media_version_id)
        if item["media_kind"] != "AUDIO":
            raise DomainRuleError("AUDIO_QC_REQUIRES_AUDIO", "响度与削波检查只支持 AUDIO MediaVersion")
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg or not Path(ffmpeg).is_file():
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用")
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-nostats", "-i", str(source), "-filter_complex", "ebur128=peak=true,astats=metadata=1:reset=0", "-f", "null", "-"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DomainRuleError("AUDIO_QC_FAILED", "音频技术检查执行失败", {"reason": type(error).__name__}) from error
        if result.returncode != 0:
            raise DomainRuleError("AUDIO_QC_FAILED", "FFmpeg 音频技术检查失败", {"stderr_redacted": result.stderr[-500:]})
        summary = result.stderr.rsplit("Summary:", 1)[-1]
        lufs_match = re.search(r"Integrated loudness:[\s\S]*?I:\s*(-?\d+(?:\.\d+)?)\s+LUFS", summary)
        true_peak_match = re.search(r"True peak:[\s\S]*?Peak:\s*(-?\d+(?:\.\d+)?)\s+dBFS", summary)
        peak_matches = re.findall(r"Peak level dB:\s*(-?\d+(?:\.\d+)?)", result.stderr)
        if not lufs_match or not true_peak_match or not peak_matches:
            raise DomainRuleError("AUDIO_QC_PARSE_FAILED", "无法从本机 FFmpeg 输出解析响度或峰值")
        integrated_lufs = float(lufs_match.group(1))
        true_peak_dbfs = float(true_peak_match.group(1))
        peak_dbfs = float(peak_matches[-1])
        return {
            "policy_version": "g8_audio_qc_v1",
            "integrated_lufs": integrated_lufs,
            "true_peak_dbfs": true_peak_dbfs,
            "peak_dbfs": peak_dbfs,
            "clipping_detected": peak_dbfs >= -0.1,
        }

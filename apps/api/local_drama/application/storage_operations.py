"""Recoverable staged media ingest backed by an explicit operation ledger.

Filesystem rename and SQLite commit cannot be one ACID transaction.  This
service therefore persists each boundary (STAGED -> FINALIZING ->
FILE_COMMITTED -> COMMITTED), making every crash window inspectable and
replayable.  Unknown files are quarantined, never silently deleted.
"""

from __future__ import annotations

import errno
import hashlib
import json
import mimetypes
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.atomic import replace_path
from local_drama.infrastructure.filesystem.path_policy import (
    canonical_relative_path,
    controlled_path,
    is_reparse_point,
    safe_filename,
)

CHUNK_SIZE = 1024 * 1024
RECOVERABLE_STATES = ("STAGING", "STAGED", "FINALIZING", "FILE_COMMITTED", "NEEDS_ATTENTION")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stream_hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _stream_copy(source: Path, destination: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        while chunk := input_file.read(CHUNK_SIZE):
            output_file.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        output_file.flush()
        os.fsync(output_file.fileno())
    return digest.hexdigest(), size


class StorageOperationService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        fault_injector: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self._fault_injector = fault_injector

    @property
    def staging_root(self) -> Path:
        return self._internal_root("storage-staging")

    @property
    def quarantine_root(self) -> Path:
        return self._internal_root("storage-quarantine")

    def _internal_root(self, name: str) -> Path:
        work_root = self.settings.work_root.resolve()
        candidate = work_root / name
        if is_reparse_point(candidate):
            raise DomainRuleError("SYMLINK_REJECTED", f"{name} 不能是 symlink")
        candidate.mkdir(parents=True, exist_ok=True)
        resolved = candidate.resolve()
        if not resolved.is_relative_to(work_root):
            raise DomainRuleError("PATH_ESCAPE", f"{name} 超出 work_root")
        return resolved

    def _checkpoint(self, name: str, operation: dict[str, Any]) -> None:
        if self._fault_injector is not None:
            self._fault_injector(name, operation)

    def _project_root(self, project_id: str) -> Path:
        with self.database.connect() as connection:
            row = connection.execute("SELECT root_rel FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        return self.settings.resolve_project_root(str(row["root_rel"]))

    @staticmethod
    def _reject_symlink_chain(path: Path) -> None:
        absolute = path.absolute()
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current /= part
            if is_reparse_point(current):
                raise DomainRuleError("SYMLINK_REJECTED", "StorageOperation 不接受 symlink 路径")

    def _source_file(self, source_path: str | Path) -> Path:
        raw = Path(source_path)
        if ".." in raw.parts:
            raise DomainRuleError("PATH_TRAVERSAL_REJECTED", "StorageOperation source 不接受路径穿越")
        self._reject_symlink_chain(raw)
        try:
            source = raw.resolve(strict=True)
        except OSError as error:
            raise DomainRuleError("SOURCE_NOT_FOUND", "导入源文件不存在或无法读取", {"source_path": str(raw)}) from error
        if not source.is_file() or source.is_symlink():
            raise DomainRuleError("INVALID_SOURCE_FILE", "导入源必须是普通本地文件")
        return source

    @staticmethod
    def _controlled_child(root: Path, relative: str | Path, *, must_exist: bool = False) -> Path:
        canonical = canonical_relative_path(relative, code="PATH_TRAVERSAL_REJECTED")
        current = root
        for part in canonical.split("/"):
            current /= part
            if current.exists() and is_reparse_point(current):
                raise DomainRuleError("SYMLINK_REJECTED", "StorageOperation 目标路径不能包含 symlink")
        return controlled_path(root, canonical, must_exist=must_exist, code="PATH_ESCAPE")

    def _operation(self, row: Any, *, replay: bool = False) -> dict[str, Any]:
        result = dict(row)
        result["request"] = json.loads(str(result.pop("request_json") or "{}"))
        result["idempotent_replay"] = replay
        return result

    @staticmethod
    def _validate_idempotent_replay(
        replay: dict[str, Any],
        *,
        project_id: str,
        stable_fields: dict[str, Any],
        source: Path,
    ) -> dict[str, Any]:
        stable_request = replay["request"]
        if str(replay.get("project_id") or "") != project_id or any(
            stable_request.get(field) != value for field, value in stable_fields.items()
        ):
            raise DomainRuleError(
                "STORAGE_IDEMPOTENCY_PAYLOAD_MISMATCH",
                "相同 StorageOperation idempotency key 的请求不一致",
            )
        if replay.get("actual_sha256"):
            replay_hash, replay_size = _stream_hash(source)
            if replay_hash != replay["actual_sha256"] or replay_size != int(replay["actual_byte_size"]):
                raise DomainRuleError(
                    "STORAGE_IDEMPOTENCY_PAYLOAD_MISMATCH",
                    "相同 StorageOperation idempotency key 的源内容不一致",
                )
        return replay

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM storage_operations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            raise DomainRuleError("STORAGE_OPERATION_NOT_FOUND", "StorageOperation 不存在", {"operation_id": operation_id})
        return self._operation(row)

    def _set_error(self, operation_id: str, status: str, code: str, detail: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE storage_operations SET status=?,last_error_code=?,last_error_redacted=?,
                updated_at=?,revision=revision+1 WHERE id=? AND status!='COMMITTED'""",
                (status, code, detail[:500], _utc_now(), operation_id),
            )

    def _quarantine(self, operation_id: str, path: Path, reason: str) -> str | None:
        if not path.exists() and not path.is_symlink():
            self._set_error(operation_id, "QUARANTINED", reason, "待隔离文件已缺失；保留 ledger 供人工检查")
            return None
        destination = self.quarantine_root / f"{operation_id}-{uuid.uuid4().hex[:8]}-{safe_filename(path.name)}"
        try:
            shutil.move(str(path), str(destination))
        except OSError as error:
            self._set_error(operation_id, "NEEDS_ATTENTION", "QUARANTINE_MOVE_FAILED", type(error).__name__)
            return None
        relative = destination.relative_to(self.settings.work_root.resolve()).as_posix()
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE storage_operations SET status='QUARANTINED',quarantine_rel_path=?,
                last_error_code=?,last_error_redacted=?,updated_at=?,revision=revision+1 WHERE id=?""",
                (relative, reason, "文件已移动到隔离区，未删除", _utc_now(), operation_id),
            )
        return relative

    def _quarantine_orphan(self, path: Path, reason: str, *, project_id: str | None = None) -> str:
        orphan_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO storage_operations
                (id,project_id,operation_type,idempotency_key,status,source_name,request_json,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?, 'ORPHAN_QUARANTINE',?,'STAGING',?,'{}',?,?,'reconciler',1,'v1')""",
                (orphan_id, project_id, f"orphan-quarantine:{orphan_id}", path.name, now, now),
            )
        self._quarantine(orphan_id, path, reason)
        return orphan_id

    def staging_path(self, operation_id: str) -> Path:
        operation = self.get_operation(operation_id)
        relative = operation.get("staging_rel_path")
        if not relative:
            raise DomainRuleError("STORAGE_STAGE_MISSING", "StorageOperation 没有 staging 路径")
        path = self._controlled_child(self.settings.work_root.resolve(), str(relative), must_exist=True)
        if not path.is_file() or path.is_symlink():
            raise DomainRuleError("STORAGE_STAGE_INVALID", "StorageOperation staging 不是普通文件")
        return path

    def stage_media_ingest(
        self,
        project_id: str,
        source_path: str | Path,
        *,
        purpose: str = "IMPORT",
        owner_type: str = "PROJECT",
        owner_id: str | None = None,
        media_kind: str = "OTHER",
        stage: str = "IMPORTED",
        mime_type: str | None = None,
        expected_sha256: str | None = None,
        expected_byte_size: int | None = None,
        idempotency_key: str | None = None,
        actor: str = "local-user",
        import_source: str = "LOCAL_FILE",
        source_job_attempt_id: str | None = None,
        source_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        source = self._source_file(source_path)
        self._project_root(project_id)
        operation_id = str(uuid.uuid4())
        key = idempotency_key or f"media-ingest:{project_id}:{operation_id}"
        if not key or len(key) > 200:
            raise DomainRuleError("STORAGE_IDEMPOTENCY_KEY_INVALID", "StorageOperation idempotency key 必须为 1—200 字符")
        safe_source_name = safe_filename(source.name)
        relative = Path("storage-staging") / operation_id / safe_source_name
        request = {
            "purpose": purpose,
            "owner_type": owner_type,
            "owner_id": owner_id or project_id,
            "media_kind": media_kind,
            "stage": stage,
            "mime_type": mime_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            "source_name": source.name,
            "import_source": import_source,
            "source_job_attempt_id": source_job_attempt_id,
            "source_artifact_id": source_artifact_id,
            "probe": {"probe_status": "PENDING"},
            "duration_ms": None,
            "fps_num": None,
            "fps_den": None,
            "planned_media_asset_id": str(uuid.uuid4()),
            "planned_media_version_id": str(uuid.uuid4()),
        }
        stable_fields = {
            "purpose": purpose,
            "owner_type": owner_type,
            "owner_id": owner_id or project_id,
            "media_kind": media_kind,
            "stage": stage,
            "mime_type": request["mime_type"],
            "source_name": source.name,
            "import_source": import_source,
            "source_job_attempt_id": source_job_attempt_id,
            "source_artifact_id": source_artifact_id,
        }
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM storage_operations WHERE idempotency_key=?", (key,),
            ).fetchone()
        if existing is not None:
            replay = self._operation(existing, replay=True)
            return self._validate_idempotent_replay(
                replay, project_id=project_id, stable_fields=stable_fields, source=source,
            )
        now = _utc_now()
        raced_replay: dict[str, Any] | None = None
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM storage_operations WHERE idempotency_key=?", (key,),
            ).fetchone()
            if existing is not None:
                raced_replay = self._operation(existing, replay=True)
            else:
                connection.execute(
                    """INSERT INTO storage_operations
                    (id,project_id,operation_type,idempotency_key,status,source_name,staging_rel_path,
                     expected_sha256,expected_byte_size,request_json,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?, 'MEDIA_INGEST',?,'STAGING',?,?, ?,?,?, ?,?,?,1,'v1')""",
                    (
                        operation_id,
                        project_id,
                        key,
                        source.name,
                        relative.as_posix(),
                        expected_sha256,
                        expected_byte_size,
                        _json(request),
                        now,
                        now,
                        actor,
                    ),
                )
        if raced_replay is not None:
            return self._validate_idempotent_replay(
                raced_replay, project_id=project_id, stable_fields=stable_fields, source=source,
            )
        destination = self._controlled_child(self.settings.work_root.resolve(), relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".partial-{destination.name}")
        try:
            actual_sha256, actual_size = _stream_copy(source, partial)
            replace_path(partial, destination)
            operation = self.get_operation(operation_id)
            self._checkpoint("after_staging_copy", operation)
            if expected_sha256 is not None and actual_sha256 != expected_sha256:
                self._quarantine(operation_id, destination, "STORAGE_HASH_MISMATCH")
                raise DomainRuleError("STORAGE_HASH_MISMATCH", "staging hash 与预期不一致")
            if expected_byte_size is not None and actual_size != expected_byte_size:
                self._quarantine(operation_id, destination, "STORAGE_SIZE_MISMATCH")
                raise DomainRuleError("STORAGE_SIZE_MISMATCH", "staging byte_size 与预期不一致")
            with self.database.transaction() as connection:
                duplicate = connection.execute(
                    """SELECT ma.id AS media_asset_id,mv.id AS media_version_id,mv.rel_path,mv.sha256
                    FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
                    WHERE ma.project_id=? AND mv.sha256=? ORDER BY mv.version_no,mv.id LIMIT 1""",
                    (project_id, actual_sha256),
                ).fetchone()
                if duplicate is not None:
                    connection.execute(
                        """UPDATE storage_operations SET status='COMMITTED',actual_sha256=?,actual_byte_size=?,
                        media_asset_id=?,media_version_id=?,finalized_at=?,updated_at=?,revision=revision+1 WHERE id=?""",
                        (
                            actual_sha256,
                            actual_size,
                            duplicate["media_asset_id"],
                            duplicate["media_version_id"],
                            _utc_now(),
                            _utc_now(),
                            operation_id,
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE storage_operations SET status='STAGED',actual_sha256=?,actual_byte_size=?,
                        updated_at=?,revision=revision+1 WHERE id=?""",
                        (actual_sha256, actual_size, _utc_now(), operation_id),
                    )
            result = self.get_operation(operation_id)
            if result["status"] == "COMMITTED":
                destination.unlink(missing_ok=True)
            return result
        except DomainRuleError:
            raise
        except OSError as error:
            code = "DISK_FULL" if error.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)} else "STORAGE_STAGE_IO_FAILED"
            self._set_error(operation_id, "FAILED", code, type(error).__name__)
            raise DomainRuleError(code, "StorageOperation staging 写入失败", {"operation_id": operation_id}) from error
        except Exception as error:
            self._set_error(operation_id, "NEEDS_ATTENTION", "STORAGE_STAGE_FAILED", type(error).__name__)
            raise

    def set_media_probe(
        self,
        operation_id: str,
        probe: dict[str, Any],
        *,
        duration_ms: int | None,
        fps_num: int | None,
        fps_den: int | None,
    ) -> dict[str, Any]:
        operation = self.get_operation(operation_id)
        if operation["status"] == "COMMITTED":
            return operation
        request = dict(operation["request"])
        request.update({"probe": probe, "duration_ms": duration_ms, "fps_num": fps_num, "fps_den": fps_den})
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE storage_operations SET request_json=?,updated_at=?,revision=revision+1 WHERE id=?",
                (_json(request), _utc_now(), operation_id),
            )
        return self.get_operation(operation_id)

    def _destination(self, operation: dict[str, Any]) -> tuple[str, Path]:
        project_id = str(operation.get("project_id") or "")
        if not project_id:
            raise DomainRuleError("STORAGE_PROJECT_MISSING", "StorageOperation 缺少 project_id")
        project_root = self._project_root(project_id)
        relative = operation.get("destination_rel_path")
        if not relative:
            relative = (Path("00_admin") / "imports" / f"{operation['id']}-{safe_filename(str(operation['source_name']))}").as_posix()
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE storage_operations SET status='FINALIZING',destination_rel_path=?,
                    updated_at=?,revision=revision+1 WHERE id=? AND status IN ('STAGED','FINALIZING','FILE_COMMITTED','NEEDS_ATTENTION')""",
                    (relative, _utc_now(), operation["id"]),
                )
        destination = self._controlled_child(project_root, str(relative))
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_chain(destination.parent)
        if destination.is_symlink():
            raise DomainRuleError("SYMLINK_REJECTED", "StorageOperation destination 不能是 symlink")
        return str(relative), destination

    def _committed_result(self, operation: dict[str, Any], *, replay: bool) -> dict[str, Any]:
        media_version_id = str(operation.get("media_version_id") or "")
        media_asset_id = str(operation.get("media_asset_id") or "")
        if not media_version_id or not media_asset_id:
            raise DomainRuleError("STORAGE_COMMIT_INCOMPLETE", "StorageOperation COMMITTED 但媒体引用缺失")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT rel_path,mime_type,byte_size,sha256,probe_json FROM media_versions WHERE id=?",
                (media_version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("STORAGE_COMMIT_INCOMPLETE", "StorageOperation 引用的 MediaVersion 不存在")
        return {
            "duplicate": replay or not bool(operation.get("destination_rel_path")),
            "storage_operation_id": operation["id"],
            "media_asset_id": media_asset_id,
            "media_version_id": media_version_id,
            "rel_path": row["rel_path"],
            "mime_type": row["mime_type"],
            "byte_size": row["byte_size"],
            "sha256": row["sha256"],
            "probe": json.loads(str(row["probe_json"] or "{}")),
            "idempotent_replay": replay,
        }

    def finalize_media_ingest(self, operation_id: str) -> dict[str, Any]:
        operation = self.get_operation(operation_id)
        if operation["status"] == "COMMITTED":
            return self._committed_result(operation, replay=True)
        if operation["status"] in {"FAILED", "QUARANTINED"}:
            raise DomainRuleError(
                "STORAGE_OPERATION_NOT_FINALIZABLE",
                "StorageOperation 当前不能 finalize",
                {"status": operation["status"], "last_error_code": operation.get("last_error_code")},
            )
        if operation["status"] not in RECOVERABLE_STATES:
            raise DomainRuleError("STORAGE_OPERATION_STATE_INVALID", "StorageOperation 状态无效")
        with self.database.connect() as connection:
            duplicate_before_copy = connection.execute(
                """SELECT ma.id AS media_asset_id,mv.id AS media_version_id
                FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
                WHERE ma.project_id=? AND mv.sha256=? ORDER BY mv.version_no,mv.id LIMIT 1""",
                (operation["project_id"], operation["actual_sha256"]),
            ).fetchone()
        if duplicate_before_copy is not None:
            now = _utc_now()
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE storage_operations SET status='COMMITTED',destination_rel_path=NULL,
                    media_asset_id=?,media_version_id=?,finalized_at=?,updated_at=?,
                    last_error_code=NULL,last_error_redacted=NULL,revision=revision+1 WHERE id=?""",
                    (
                        duplicate_before_copy["media_asset_id"],
                        duplicate_before_copy["media_version_id"],
                        now,
                        now,
                        operation_id,
                    ),
                )
            try:
                self.staging_path(operation_id).unlink()
            except (OSError, DomainRuleError):
                pass
            return self._committed_result(self.get_operation(operation_id), replay=False)
        relative, destination = self._destination(operation)
        operation = self.get_operation(operation_id)
        expected_hash = str(operation.get("actual_sha256") or "")
        expected_size = int(operation.get("actual_byte_size") or -1)
        try:
            if destination.exists():
                if destination.is_symlink() or not destination.is_file():
                    raise DomainRuleError("STORAGE_DESTINATION_INVALID", "destination 不是普通文件")
                actual_hash, actual_size = _stream_hash(destination)
            else:
                staged = self.staging_path(operation_id)
                partial = destination.with_name(f".partial-{operation_id}-{destination.name}")
                partial.unlink(missing_ok=True)
                actual_hash, actual_size = _stream_copy(staged, partial)
                if actual_hash != expected_hash or actual_size != expected_size:
                    self._quarantine(operation_id, partial, "STORAGE_FINALIZE_HASH_MISMATCH")
                    raise DomainRuleError("STORAGE_FINALIZE_HASH_MISMATCH", "finalize copy hash/大小不一致")
                self._checkpoint("after_copy_before_replace", operation)
                replace_path(partial, destination)
                self._checkpoint("after_destination_replace", operation)
            if actual_hash != expected_hash or actual_size != expected_size:
                self._quarantine(operation_id, destination, "STORAGE_DESTINATION_HASH_MISMATCH")
                raise DomainRuleError("STORAGE_DESTINATION_HASH_MISMATCH", "destination hash/大小不一致")
            with self.database.transaction() as connection:
                connection.execute(
                    """UPDATE storage_operations SET status='FILE_COMMITTED',destination_rel_path=?,
                    last_error_code=NULL,last_error_redacted=NULL,updated_at=?,revision=revision+1 WHERE id=?""",
                    (relative, _utc_now(), operation_id),
                )
            operation = self.get_operation(operation_id)
            self._checkpoint("after_file_state_commit", operation)
        except DomainRuleError:
            raise
        except OSError as error:
            code = "DISK_FULL" if error.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)} else "STORAGE_FINALIZE_IO_FAILED"
            self._set_error(operation_id, "NEEDS_ATTENTION", code, type(error).__name__)
            raise DomainRuleError(code, "StorageOperation finalize 文件阶段失败") from error

        request = dict(operation["request"])
        asset_id = str(request["planned_media_asset_id"])
        version_id = str(request["planned_media_version_id"])
        now = _utc_now()
        duplicate_after_file = False
        try:
            with self.database.transaction() as connection:
                current = connection.execute("SELECT * FROM storage_operations WHERE id=?", (operation_id,)).fetchone()
                if current is None:
                    raise DomainRuleError("STORAGE_OPERATION_NOT_FOUND", "StorageOperation 不存在")
                if str(current["status"]) == "COMMITTED":
                    return self._committed_result(self._operation(current), replay=True)
                duplicate = connection.execute(
                    """SELECT ma.id AS media_asset_id,mv.id AS media_version_id
                    FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
                    WHERE ma.project_id=? AND mv.sha256=? ORDER BY mv.version_no,mv.id LIMIT 1""",
                    (operation["project_id"], expected_hash),
                ).fetchone()
                if duplicate is not None:
                    asset_id = str(duplicate["media_asset_id"])
                    version_id = str(duplicate["media_version_id"])
                    duplicate_after_file = True
                else:
                    connection.execute(
                        """INSERT INTO media_assets
                        (id,project_id,owner_type,owner_id,purpose,media_kind,version_counter,metadata_json,
                         created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,1,?, ?,?,?,1,'v2')""",
                        (
                            asset_id,
                            operation["project_id"],
                            request["owner_type"],
                            request["owner_id"],
                            request["purpose"],
                            request["media_kind"],
                            _json({
                                "source_name": request["source_name"],
                                "source_path_not_retained": True,
                                "storage_operation_id": operation_id,
                                "source_artifact_id": request.get("source_artifact_id"),
                            }),
                            now,
                            now,
                            operation["created_by"],
                        ),
                    )
                    connection.execute(
                        """INSERT INTO media_versions
                        (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,sha256,
                         source_name,import_source,probe_json,duration_ms,fps_num,fps_den,
                         source_job_attempt_id,source_artifact_id,integrity_status,
                         created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,1,1,?,?,?,?,?, ?,?,?,?,?,?,?,?,'VERIFIED', ?,?,?,1,'v2')""",
                        (
                            version_id,
                            asset_id,
                            request["stage"],
                            relative,
                            request["mime_type"],
                            expected_size,
                            expected_hash,
                            request["source_name"],
                            request["import_source"],
                            _json(request.get("probe") or {}),
                            request.get("duration_ms"),
                            request.get("fps_num"),
                            request.get("fps_den"),
                            request.get("source_job_attempt_id"),
                            request.get("source_artifact_id"),
                            now,
                            now,
                            operation["created_by"],
                        ),
                    )
                    if request.get("source_artifact_id") and request.get("owner_type") == "GENERATION_VARIANT":
                        connection.execute(
                            "UPDATE generation_variants SET status='SUCCEEDED',updated_at=? WHERE id=?",
                            (now, request["owner_id"]),
                        )
                    connection.execute(
                        """INSERT INTO audit_events
                        (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                        VALUES (?,'producer','STORAGE_MEDIA_COMMITTED','media_version',?,'staged media 已原子登记',?)""",
                        (
                            operation["created_by"],
                            version_id,
                            _json({"storage_operation_id": operation_id, "sha256": expected_hash}),
                        ),
                    )
                connection.execute(
                    """UPDATE storage_operations SET status='COMMITTED',media_asset_id=?,media_version_id=?,
                    destination_rel_path=CASE WHEN ? THEN NULL ELSE destination_rel_path END,
                    finalized_at=?,updated_at=?,last_error_code=NULL,last_error_redacted=NULL,
                    revision=revision+1 WHERE id=?""",
                    (asset_id, version_id, int(duplicate_after_file), now, now, operation_id),
                )
                self._checkpoint("before_database_commit", operation)
            committed = self.get_operation(operation_id)
            self._checkpoint("after_database_commit", committed)
        except DomainRuleError:
            raise
        except Exception as error:
            self._set_error(operation_id, "NEEDS_ATTENTION", "STORAGE_DATABASE_FINALIZE_FAILED", type(error).__name__)
            raise DomainRuleError(
                "STORAGE_DATABASE_FINALIZE_FAILED",
                "文件已持久化但数据库 finalize 失败；ledger 可安全重试",
                {"operation_id": operation_id},
            ) from error
        if duplicate_after_file and destination.exists():
            self._quarantine_orphan(
                destination,
                "CONCURRENT_DUPLICATE_DESTINATION",
                project_id=str(operation["project_id"]),
            )
        try:
            staged = self._controlled_child(self.settings.work_root.resolve(), str(committed.get("staging_rel_path") or ""))
            if staged.is_file() and not staged.is_symlink():
                staged.unlink()
        except (OSError, DomainRuleError):
            # The ledger already classifies this staging copy.  Cleanup failure
            # is not a commit failure and the reconciler will quarantine it as
            # a referenced leftover rather than silently deleting unknown data.
            pass
        return self._committed_result(committed, replay=False)

    def reconcile(self) -> dict[str, Any]:
        recovered: list[str] = []
        quarantined: list[str] = []
        attention: list[str] = []
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id FROM storage_operations
                WHERE status IN ('STAGING','STAGED','FINALIZING','FILE_COMMITTED','NEEDS_ATTENTION')
                ORDER BY created_at,id"""
            ).fetchall()
        for row in rows:
            operation_id = str(row["id"])
            operation = self.get_operation(operation_id)
            try:
                if operation["status"] == "STAGING":
                    staging_candidate = self.staging_path(operation_id)
                    actual_hash, actual_size = _stream_hash(staging_candidate)
                    if operation.get("expected_sha256") and actual_hash != operation["expected_sha256"]:
                        self._quarantine(operation_id, staging_candidate, "STORAGE_HASH_MISMATCH")
                        quarantined.append(operation_id)
                        continue
                    with self.database.transaction() as connection:
                        connection.execute(
                            """UPDATE storage_operations SET status='STAGED',actual_sha256=?,actual_byte_size=?,
                            updated_at=?,revision=revision+1 WHERE id=?""",
                            (actual_hash, actual_size, _utc_now(), operation_id),
                        )
                self.finalize_media_ingest(operation_id)
                recovered.append(operation_id)
            except DomainRuleError as error:
                refreshed = self.get_operation(operation_id)
                if refreshed["status"] == "QUARANTINED":
                    quarantined.append(operation_id)
                else:
                    self._set_error(operation_id, "NEEDS_ATTENTION", "STORAGE_RECONCILE_FAILED", error.code)
                    attention.append(operation_id)

        # Validate the filesystem side of already committed operations.  A
        # committed DB row with missing/tampered bytes is not healthy; preserve
        # bad bytes in quarantine and mark the immutable MediaVersion corrupt.
        with self.database.connect() as connection:
            committed_rows = connection.execute(
                "SELECT id FROM storage_operations WHERE status='COMMITTED' ORDER BY created_at,id"
            ).fetchall()
        for row in committed_rows:
            operation_id = str(row["id"])
            operation = self.get_operation(operation_id)
            destination_relative = operation.get("destination_rel_path")
            committed_staging: Path | None = None
            if operation.get("staging_rel_path"):
                try:
                    committed_staging = self._controlled_child(
                        self.settings.work_root.resolve(), str(operation["staging_rel_path"]),
                    )
                except DomainRuleError:
                    committed_staging = None
            if not destination_relative:
                if committed_staging is not None and (committed_staging.exists() or committed_staging.is_symlink()):
                    quarantined.append(self._quarantine_orphan(committed_staging, "COMMITTED_STAGING_LEFTOVER", project_id=operation.get("project_id")))
                continue
            try:
                project_root = self._project_root(str(operation["project_id"]))
                destination = self._controlled_child(project_root, str(destination_relative))
                if not destination.exists():
                    if committed_staging is None or not committed_staging.is_file() or committed_staging.is_symlink():
                        self._set_error(operation_id, "NEEDS_ATTENTION", "STORAGE_COMMITTED_FILE_MISSING", "COMMITTED destination 与 staging 均缺失")
                        attention.append(operation_id)
                        continue
                    partial = destination.with_name(f".reconcile-{operation_id}-{destination.name}")
                    partial.unlink(missing_ok=True)
                    actual_hash, actual_size = _stream_copy(committed_staging, partial)
                    if actual_hash != operation["actual_sha256"] or actual_size != int(operation["actual_byte_size"]):
                        self._quarantine(operation_id, partial, "STORAGE_RESTORE_HASH_MISMATCH")
                        quarantined.append(operation_id)
                        continue
                    replace_path(partial, destination)
                    recovered.append(operation_id)
                actual_hash, actual_size = _stream_hash(destination)
                if actual_hash != operation["actual_sha256"] or actual_size != int(operation["actual_byte_size"]):
                    self._quarantine(operation_id, destination, "STORAGE_COMMITTED_HASH_MISMATCH")
                    with self.database.transaction() as connection:
                        if operation.get("media_version_id"):
                            connection.execute(
                                "UPDATE media_versions SET integrity_status='CORRUPT',updated_at=?,revision=revision+1 WHERE id=?",
                                (_utc_now(), operation["media_version_id"]),
                            )
                    quarantined.append(operation_id)
                    continue
                if committed_staging is not None and committed_staging.is_file() and not committed_staging.is_symlink():
                    quarantined.append(self._quarantine_orphan(committed_staging, "COMMITTED_STAGING_LEFTOVER", project_id=operation.get("project_id")))
            except (DomainRuleError, OSError) as error:
                self._set_error(operation_id, "NEEDS_ATTENTION", "STORAGE_COMMITTED_VERIFY_FAILED", type(error).__name__)
                attention.append(operation_id)

        # Anything under storage-staging that has no ledger reference is
        # classified and moved to quarantine.  It is never auto-deleted.
        with self.database.connect() as connection:
            referenced = {
                str(row["staging_rel_path"])
                for row in connection.execute(
                    "SELECT staging_rel_path FROM storage_operations WHERE staging_rel_path IS NOT NULL"
                ).fetchall()
            }
        for operation_dir in list(self.staging_root.iterdir()):
            for candidate in list(operation_dir.iterdir()) if operation_dir.is_dir() and not operation_dir.is_symlink() else [operation_dir]:
                relative = candidate.relative_to(self.settings.work_root.resolve()).as_posix()
                if relative in referenced:
                    continue
                orphan_id = self._quarantine_orphan(candidate, "UNCLASSIFIED_ORPHAN")
                quarantined.append(orphan_id)
        return {"recovered_operation_ids": recovered, "quarantined_operation_ids": quarantined, "attention_operation_ids": attention}

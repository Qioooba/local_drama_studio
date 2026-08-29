"""Host-only executor for a hash-bound offline model import plan.

This module is deliberately not an HTTP service.  The API may create a plan,
but only an administrator-operated Windows Host command can consume a bundle
from the service-owned staging area and mutate a model library.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, cast

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.path_policy import controlled_path, is_reparse_point, iter_controlled_files


@dataclass(frozen=True, slots=True)
class OfflineImportExecutionResult:
    install_plan_id: str
    install_job_id: str
    status: str
    verified_artifact_count: int
    imported_artifact_count: int
    quarantined: bool


class OfflineImportIntegrityError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class HostOfflineImportExecutor:
    """Consume exactly one approved plan from a service-owned staging folder."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def execute(self, install_plan_id: str) -> OfflineImportExecutionResult:
        if self.settings.model_root is None:
            raise DomainRuleError("MP_MODEL_ROOT_NOT_CONFIGURED", "未配置 ModelRoot，不能执行离线模型导入。")
        plan = self._plan(install_plan_id)
        if plan["status"] != "AWAITING_OFFLINE_IMPORT":
            raise DomainRuleError("MP_OFFLINE_IMPORT_PLAN_NOT_PENDING", "离线导入计划不处于可执行状态。")
        bundle_reference = _required_string(_json_object(plan["source_json"]), "bundle_reference")
        expected = _expected_artifacts(_json_object(plan["expected_json"]))
        target_root = self._configured_target_library(str(plan["target_library_id"]), str(plan["root_path_local"]))
        staging_root = self.settings.model_root / "staging"
        bundle_root = controlled_path(staging_root, bundle_reference, must_exist=True, code="MP_OFFLINE_BUNDLE_PATH_INVALID")
        if not bundle_root.is_dir() or is_reparse_point(bundle_root):
            raise DomainRuleError("MP_OFFLINE_BUNDLE_PATH_INVALID", "离线包必须是 staging 中的普通目录。")

        job_id = str(uuid.uuid4())
        self._start(job_id, install_plan_id, len(expected))
        try:
            self._verify_bundle(bundle_root, expected)
        except OfflineImportIntegrityError as error:
            self._quarantine(bundle_root, install_plan_id)
            self._finish_failure(job_id, install_plan_id, error.code, quarantined=True)
            return OfflineImportExecutionResult(install_plan_id, job_id, "QUARANTINED", 0, 0, True)

        copied: list[Path] = []
        try:
            for artifact in expected:
                source = controlled_path(
                    bundle_root,
                    cast(str, artifact["relative_path"]),
                    must_exist=True,
                    require_file=True,
                    code="MP_OFFLINE_BUNDLE_PATH_INVALID",
                )
                destination = self._prepare_destination(target_root, artifact["relative_path"])
                self._copy_new_file(source, destination)
                copied.append(destination)
                digest, byte_count = _hash_file(destination)
                if digest != artifact["sha256"] or byte_count != artifact["size_bytes"]:
                    raise OfflineImportIntegrityError("MP_OFFLINE_IMPORT_DESTINATION_INTEGRITY_FAILED")
            self._finish_success(job_id, install_plan_id, len(expected))
            return OfflineImportExecutionResult(install_plan_id, job_id, "IMPORTED", len(expected), len(expected), False)
        except OfflineImportIntegrityError as error:
            self._remove_created_files(copied)
            self._quarantine(bundle_root, install_plan_id)
            self._finish_failure(job_id, install_plan_id, error.code, quarantined=True)
            return OfflineImportExecutionResult(install_plan_id, job_id, "QUARANTINED", 0, 0, True)
        except (OSError, DomainRuleError) as error:
            self._remove_created_files(copied)
            self._finish_failure(job_id, install_plan_id, _error_code(error), quarantined=False)
            raise DomainRuleError("MP_OFFLINE_IMPORT_WRITE_FAILED", "离线模型导入未完成；已回滚本次创建的目标文件。") from error

    def _plan(self, install_plan_id: str) -> sqlite3.Row:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT plan.id,plan.target_library_id,plan.source_json,plan.expected_json,plan.status,
                          library.root_path_local
                   FROM mp_install_plans plan
                   JOIN mp_model_libraries library ON library.id=plan.target_library_id
                   WHERE plan.id=?""",
                (install_plan_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_OFFLINE_IMPORT_PLAN_NOT_FOUND", "离线导入计划不存在。")
        return cast(sqlite3.Row, row)

    def _configured_target_library(self, library_id: str, root_text: str) -> Path:
        try:
            root = Path(root_text).resolve()
        except OSError as error:
            raise DomainRuleError("MP_INSTALL_TARGET_LIBRARY_STALE", "目标模型库路径无法解析。") from error
        configured = {item.resolve() for item in self.settings.model_library_roots}
        if root not in configured or not root.is_dir() or is_reparse_point(root):
            raise DomainRuleError("MP_INSTALL_TARGET_LIBRARY_STALE", "目标模型库已不属于当前安全服务配置。")
        with self.database.connect() as connection:
            row = connection.execute("SELECT read_only FROM mp_model_libraries WHERE id=?", (library_id,)).fetchone()
        if row is None or bool(row["read_only"]):
            raise DomainRuleError("MP_INSTALL_TARGET_LIBRARY_READ_ONLY", "目标模型库不可写。")
        return root

    def _verify_bundle(self, bundle_root: Path, expected: tuple[Mapping[str, object], ...]) -> None:
        expected_paths = {str(item["relative_path"]) for item in expected}
        actual_paths = {path.relative_to(bundle_root).as_posix() for path in iter_controlled_files(bundle_root)}
        if actual_paths != expected_paths:
            raise OfflineImportIntegrityError("MP_OFFLINE_BUNDLE_CONTENTS_MISMATCH")
        for item in expected:
            path = controlled_path(
                bundle_root,
                str(item["relative_path"]),
                must_exist=True,
                require_file=True,
                code="MP_OFFLINE_BUNDLE_PATH_INVALID",
            )
            digest, byte_count = _hash_file(path)
            if digest != item["sha256"] or byte_count != item["size_bytes"]:
                raise OfflineImportIntegrityError("MP_OFFLINE_BUNDLE_INTEGRITY_FAILED")

    def _prepare_destination(self, target_root: Path, relative_path: object) -> Path:
        destination = controlled_path(target_root, str(relative_path), code="MP_OFFLINE_IMPORT_TARGET_PATH_INVALID")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = controlled_path(target_root, str(relative_path), code="MP_OFFLINE_IMPORT_TARGET_PATH_INVALID")
        if destination.exists():
            raise DomainRuleError("MP_OFFLINE_IMPORT_TARGET_EXISTS", "目标模型库已有同名组件，拒绝覆盖。")
        return destination

    @staticmethod
    def _copy_new_file(source: Path, destination: Path) -> None:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())

    @staticmethod
    def _remove_created_files(paths: list[Path]) -> None:
        for path in reversed(paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # The job record carries the failure.  Never escalate cleanup into
                # recursive deletion of a model library.
                continue

    def _quarantine(self, bundle_root: Path, plan_id: str) -> None:
        assert self.settings.model_root is not None
        quarantine_root = self.settings.model_root / "quarantine"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(quarantine_root):
            raise DomainRuleError("MP_QUARANTINE_ROOT_INVALID", "隔离目录不能是 symlink 或 Windows junction。")
        destination = quarantine_root / f"{plan_id}-{uuid.uuid4().hex}"
        os.replace(bundle_root, destination)

    def _start(self, job_id: str, plan_id: str, expected_count: int) -> None:
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute("UPDATE mp_install_plans SET status='IMPORTING',updated_at=? WHERE id=?", (now, plan_id))
            connection.execute(
                """INSERT INTO mp_install_jobs
                (id,install_plan_id,status,progress_json,error_redacted,started_at,finished_at,created_at,updated_at)
                VALUES (?,?,? ,?,?,?,NULL,?,?)""",
                (job_id, plan_id, "RUNNING", _json({"expected_artifact_count": expected_count, "verified_artifact_count": 0, "imported_artifact_count": 0}), None, now, now, now),
            )

    def _finish_success(self, job_id: str, plan_id: str, count: int) -> None:
        now = _utc_now()
        progress = _json({"expected_artifact_count": count, "verified_artifact_count": count, "imported_artifact_count": count})
        with self.database.transaction() as connection:
            connection.execute("UPDATE mp_install_plans SET status='IMPORTED',updated_at=? WHERE id=?", (now, plan_id))
            connection.execute(
                "UPDATE mp_install_jobs SET status='SUCCEEDED',progress_json=?,finished_at=?,updated_at=? WHERE id=?",
                (progress, now, now, job_id),
            )

    def _finish_failure(self, job_id: str, plan_id: str, code: str, *, quarantined: bool) -> None:
        now = _utc_now()
        status = "QUARANTINED" if quarantined else "FAILED"
        progress = _json({"verified_artifact_count": 0, "imported_artifact_count": 0, "quarantined": quarantined})
        with self.database.transaction() as connection:
            connection.execute("UPDATE mp_install_plans SET status=?,updated_at=? WHERE id=?", (status, now, plan_id))
            connection.execute(
                "UPDATE mp_install_jobs SET status=?,progress_json=?,error_redacted=?,finished_at=?,updated_at=? WHERE id=?",
                (status, progress, code, now, now, job_id),
            )


def _expected_artifacts(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    raw = payload.get("artifacts")
    if not isinstance(raw, list) or not raw:
        raise DomainRuleError("MP_OFFLINE_IMPORT_PLAN_INVALID", "离线导入计划缺少预期组件。")
    result: list[Mapping[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise DomainRuleError("MP_OFFLINE_IMPORT_PLAN_INVALID", "离线导入计划组件格式无效。")
        relative_path = item.get("relative_path")
        sha256 = item.get("sha256")
        size_bytes = item.get("size_bytes")
        if not isinstance(relative_path, str) or not isinstance(sha256, str) or not isinstance(size_bytes, int) or size_bytes <= 0:
            raise DomainRuleError("MP_OFFLINE_IMPORT_PLAN_INVALID", "离线导入计划组件不完整。")
        result.append({"relative_path": relative_path, "sha256": sha256, "size_bytes": size_bytes})
    return tuple(result)


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DomainRuleError("MP_OFFLINE_IMPORT_PLAN_INVALID", "离线导入计划来源不完整。")
    return value.strip()


def _json_object(value: object) -> Mapping[str, object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    return digest.hexdigest(), byte_count


def _error_code(error: Exception) -> str:
    return getattr(error, "code", type(error).__name__)[:120]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()

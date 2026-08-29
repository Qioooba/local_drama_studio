"""Host-only executor for durable, hash-bound trusted HTTPS download plans."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.trusted_download_execution import (
    HostTrustedDownloadExecutor,
    TrustedDownloadRequest,
)

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class TrustedDownloadPlanExecutionResult:
    install_plan_id: str
    install_job_id: str
    status: str
    downloaded_artifact_count: int


class HostTrustedDownloadPlanExecutor:
    """Move a persisted plan through downloads → staging, never into a Library."""

    def __init__(self, database: Database, settings: Settings, *, downloader: HostTrustedDownloadExecutor | None = None) -> None:
        self.database = database
        self.settings = settings
        self.downloader = downloader or HostTrustedDownloadExecutor(settings)

    def execute(self, install_plan_id: str) -> TrustedDownloadPlanExecutionResult:
        if self.settings.model_root is None:
            raise DomainRuleError("MP_MODEL_ROOT_NOT_CONFIGURED", "未配置 ModelRoot，不能执行可信模型下载。")
        plan = self._plan(install_plan_id)
        if str(plan["status"]) != "AWAITING_TRUSTED_DOWNLOAD":
            raise DomainRuleError("MP_TRUSTED_DOWNLOAD_PLAN_NOT_PENDING", "可信下载计划不处于可执行状态。")
        bundle_reference, artifacts = _download_contract(plan["source_json"], plan["expected_json"])
        job_id = str(uuid.uuid4())
        self._start(job_id, install_plan_id, len(artifacts))
        downloaded = 0
        try:
            for artifact in artifacts:
                self.downloader.download(
                    TrustedDownloadRequest(
                        source_url=str(artifact["source_url"]),
                        bundle_reference=bundle_reference,
                        relative_path=str(artifact["relative_path"]),
                        sha256=str(artifact["sha256"]),
                        size_bytes=int(artifact["size_bytes"]),
                    )
                )
                downloaded += 1
                self._progress(job_id, downloaded, len(artifacts))
            self.downloader.promote_bundle_to_staging(bundle_reference)
        except (DomainRuleError, OSError) as error:
            self._finish_failure(job_id, install_plan_id, _error_code(error), downloaded, len(artifacts))
            raise DomainRuleError(
                "MP_TRUSTED_DOWNLOAD_FAILED",
                "可信模型下载未完成；下载区不会自动导入模型库。",
            ) from error
        self._finish_success(job_id, install_plan_id, len(artifacts))
        return TrustedDownloadPlanExecutionResult(install_plan_id, job_id, "STAGED", len(artifacts))

    def _plan(self, install_plan_id: str) -> sqlite3.Row:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id,source_json,expected_json,status FROM mp_install_plans WHERE id=?", (install_plan_id,)
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_TRUSTED_DOWNLOAD_PLAN_NOT_FOUND", "可信下载计划不存在。")
        return row

    def _start(self, job_id: str, plan_id: str, expected_count: int) -> None:
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute("UPDATE mp_install_plans SET status='DOWNLOADING',updated_at=? WHERE id=?", (now, plan_id))
            connection.execute(
                """INSERT INTO mp_install_jobs
                (id,install_plan_id,status,progress_json,error_redacted,started_at,finished_at,created_at,updated_at)
                VALUES (?,?,? ,?,?,?,NULL,?,?)""",
                (job_id, plan_id, "RUNNING", _json(_progress(0, expected_count)), None, now, now, now),
            )

    def _progress(self, job_id: str, downloaded: int, expected: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE mp_install_jobs SET progress_json=?,updated_at=? WHERE id=?",
                (_json(_progress(downloaded, expected)), _utc_now(), job_id),
            )

    def _finish_success(self, job_id: str, plan_id: str, count: int) -> None:
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE mp_install_plans SET status='AWAITING_OFFLINE_IMPORT',updated_at=? WHERE id=?", (now, plan_id)
            )
            connection.execute(
                "UPDATE mp_install_jobs SET status='SUCCEEDED',progress_json=?,finished_at=?,updated_at=? WHERE id=?",
                (_json({**_progress(count, count), "staged": True}), now, now, job_id),
            )

    def _finish_failure(self, job_id: str, plan_id: str, code: str, downloaded: int, expected: int) -> None:
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute("UPDATE mp_install_plans SET status='DOWNLOAD_FAILED',updated_at=? WHERE id=?", (now, plan_id))
            connection.execute(
                "UPDATE mp_install_jobs SET status='FAILED',progress_json=?,error_redacted=?,finished_at=?,updated_at=? WHERE id=?",
                (_json(_progress(downloaded, expected)), code, now, now, job_id),
            )


def _download_contract(source_value: object, expected_value: object) -> tuple[str, tuple[Mapping[str, object], ...]]:
    source = _json_object(source_value)
    expected = _json_object(expected_value)
    if source.get("kind") != "TRUSTED_HTTPS":
        raise DomainRuleError("MP_TRUSTED_DOWNLOAD_PLAN_INVALID", "安装计划不是可信 HTTPS 下载计划。")
    bundle_reference = source.get("bundle_reference")
    raw_sources = source.get("artifacts")
    raw_expected = expected.get("artifacts")
    if not isinstance(bundle_reference, str) or not bundle_reference.strip() or not isinstance(raw_sources, list) or not isinstance(raw_expected, list):
        raise DomainRuleError("MP_TRUSTED_DOWNLOAD_PLAN_INVALID", "可信下载计划缺少受控组件合同。")
    source_by_path: dict[str, str] = {}
    for item in raw_sources:
        if not isinstance(item, dict) or not isinstance(item.get("relative_path"), str) or not isinstance(item.get("source_url"), str):
            raise DomainRuleError("MP_TRUSTED_DOWNLOAD_PLAN_INVALID", "可信下载来源组件无效。")
        path = item["relative_path"]
        if path in source_by_path:
            raise DomainRuleError("MP_TRUSTED_DOWNLOAD_PLAN_INVALID", "可信下载来源组件重复。")
        source_by_path[path] = item["source_url"]
    artifacts: list[Mapping[str, object]] = []
    for item in raw_expected:
        if not isinstance(item, dict):
            raise DomainRuleError("MP_TRUSTED_DOWNLOAD_PLAN_INVALID", "可信下载预期组件无效。")
        relative_path, sha256, size_bytes = item.get("relative_path"), item.get("sha256"), item.get("size_bytes")
        if (
            not isinstance(relative_path, str)
            or not isinstance(sha256, str)
            or not _SHA256.fullmatch(sha256)
            or not isinstance(size_bytes, int)
            or size_bytes <= 0
            or relative_path not in source_by_path
        ):
            raise DomainRuleError("MP_TRUSTED_DOWNLOAD_PLAN_INVALID", "可信下载组件必须有来源、哈希和大小。")
        artifacts.append({"relative_path": relative_path, "source_url": source_by_path.pop(relative_path), "sha256": sha256, "size_bytes": size_bytes})
    if not artifacts or source_by_path:
        raise DomainRuleError("MP_TRUSTED_DOWNLOAD_PLAN_INVALID", "可信下载来源与预期组件必须精确匹配。")
    return bundle_reference.strip(), tuple(artifacts)


def _progress(downloaded: int, expected: int) -> dict[str, int]:
    return {"expected_artifact_count": expected, "downloaded_artifact_count": downloaded}


def _json_object(value: object) -> Mapping[str, object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _error_code(error: Exception) -> str:
    return getattr(error, "code", type(error).__name__)[:120]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
